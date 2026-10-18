#!/usr/bin/env python3
"""Audit czbooks 77297 chapter links, with optional authorized RAW collection.

Default: catalog metadata only (328..553). Fail closed on robots restrictions,
redirects, challenge pages, wrong book, conflicting chapter URLs, and HTTP errors.
Does not bypass login or protections, translate chapters, or sync Notion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

TITLE = "被退婚後，隔壁全村腸子悔青了"
BOOK = "s6d6hl"
ROOT = "https://czbooks.net/n/s6d6hl"
UA = "NovelResearchCollector/1.0 (metadata audit; robots compliant)"
LABEL = re.compile(r"第\s*(\d+)\s*章\s*(.*)")
CHAPTER_PATH = re.compile(r"/n/s6d6hl/([a-zA-Z0-9]+)(?:/)?$")
BODY_SELECTORS = (
    "#content", "#chaptercontent", ".chapter-content", ".reading-content",
    ".read-content", ".novel-content", "#chapter", ".content",
)
BLOCKED_MARKERS = (
    "驗證碼", "验证码", "人機驗證", "人机验证", "登入後閱讀",
    "章節已鎖定", "正在驗證瀏覽器", "captcha",
    "access denied", "just a moment", "cloudflare", "checking your browser",
)


class AuditError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def safe_url(href: str, *, chapter: bool = False) -> str:
    url = urljoin(ROOT, href)
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname != "czbooks.net" or
            p.username or p.password or p.port not in (None, 443) or p.fragment):
        raise AuditError("unsafe_url", url)
    if chapter:
        if not CHAPTER_PATH.fullmatch(p.path):
            raise AuditError("unsafe_url", url)
        query = parse_qs(p.query, keep_blank_values=True)
        if query and (set(query) != {"chapterNumber"} or len(query["chapterNumber"]) != 1
                      or not re.fullmatch(r"\d{1,5}", query["chapterNumber"][0])):
            raise AuditError("unsafe_url", url)
    elif p.path.rstrip("/") != "/n/s6d6hl" or p.query:
        raise AuditError("unsafe_url", url)
    return url


def parse_catalog(html: str) -> dict[int, dict]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if TITLE not in title and TITLE not in soup.get_text(" ", strip=True):
        raise AuditError("wrong_book", "Expected novel title not found")
    chapters = {}
    for link in soup.select("a[href]"):
        label = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
        m = LABEL.search(label)
        if not m:
            continue
        number = int(m.group(1))
        if not 328 <= number <= 553:
            continue
        try:
            url = safe_url(link["href"], chapter=True)
        except AuditError:
            continue
        query = parse_qs(urlsplit(url).query)
        if query and int(query["chapterNumber"][0]) != number - 1:
            # czbooks uses a 0-based chapterNumber query, not the story ordinal.
            continue
        ident = CHAPTER_PATH.fullmatch(urlsplit(url).path).group(1)
        item = {"number": number, "title_zh": m.group(2).strip(),
                "url": url, "source_id": ident}
        old = chapters.get(number)
        if old and old["source_id"] != ident:
            raise AuditError("catalog_conflict", f"Chapter {number} maps to two source IDs")
        if old is None or len(item["title_zh"]) > len(old["title_zh"]):
            chapters[number] = item
    if not chapters:
        raise AuditError("catalog_missing", "No numbered matching chapter links")
    return dict(sorted(chapters.items()))


def parse_body(html: str, number: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    head = soup.title.get_text(" ", strip=True) if soup.title else ""
    titles = [head] + [h.get_text(" ", strip=True) for h in soup.select("h1,h2")]
    observed = [int(m.group(1)) for s in titles if (m := LABEL.search(s))]
    if not observed or any(n != number for n in observed):
        raise AuditError("wrong_chapter", f"Expected {number}, observed {observed}")
    if TITLE not in " ".join(titles):
        raise AuditError("wrong_book", "Expected novel title not found in chapter heading")
    visible = soup.get_text(" ", strip=True).lower()
    if any(marker in visible[:1200] for marker in BLOCKED_MARKERS):
        raise AuditError("blocked", "Challenge or locked chapter")
    node = next((n for sel in BODY_SELECTORS if (n := soup.select_one(sel))), None)
    if node is None:
        raise AuditError("missing_body", "Cannot isolate the chapter content container")
    for junk in node.select("script,style,iframe,noscript,nav,footer,header,form,button,.ad,.ads,.pager,.chapter-nav"):
        junk.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in node.select("p")]
    body = "\n\n".join(p for p in paragraphs if p) if len(paragraphs) >= 2 else node.get_text("\n", strip=True)
    body = body.replace("\r\n", "\n").strip()
    if any(marker in body.lower() for marker in BLOCKED_MARKERS):
        raise AuditError("blocked", "Locked/challenge content in body")
    if len(re.findall(r"[\u3400-\u9fff]", body)) < 180:
        raise AuditError("too_short", "Less than 180 Chinese ideographs")
    if len(body) > 800_000:
        raise AuditError("invalid_body", "Oversized chapter body")
    return body + "\n"


class Client:
    def __init__(self, delay: float = 3.0):
        if delay < 2.0:
            raise ValueError("At least 2 seconds between requests")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
        self.delay = delay
        self.last_request = 0.0
        self.robot = None

    def _get(self, url):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        try:
            response = self.session.get(url, timeout=(10, 25), allow_redirects=False)
        except requests.RequestException as exc:
            raise AuditError("network_error", type(exc).__name__) from exc
        finally:
            self.last_request = time.monotonic()
        code = response.status_code
        if 300 <= code < 400 or code in (401, 402, 403, 429, 451):
            raise AuditError("blocked", f"HTTP {code}")
        if code not in (200, 404):
            raise AuditError("http_error", f"HTTP {code}")
        if len(response.content) > 3_000_000:
            raise AuditError("oversize", "Response over 3 MB")
        return response

    def html(self, url: str) -> str:
        safe_url(url, chapter=(urlsplit(url).path.rstrip("/") != "/n/s6d6hl"))
        if self.robot is None:
            robots = self._get("https://czbooks.net/robots.txt")
            self.robot = RobotFileParser()
            if robots.status_code == 404:
                self.robot.parse(["User-agent: *", "Allow: /"])
            elif (robots.status_code == 200 and
                  "text" in robots.headers.get("content-type", "").lower() and
                  "<html" not in robots.text[:500].lower()):
                self.robot.parse(robots.text.splitlines())
            else:
                raise AuditError("robots_error", "robots.txt not readable as text")
            crawl_delay = self.robot.crawl_delay(UA)
            if crawl_delay is not None:
                self.delay = max(self.delay, float(crawl_delay))
        if not self.robot.can_fetch(UA, url):
            raise AuditError("robots_disallowed", "robots.txt prohibits this URL")
        response = self._get(url)
        if response.status_code != 200 or "html" not in response.headers.get("content-type", "").lower():
            raise AuditError("invalid_response", f"Expected HTML 200, got {response.status_code}")
        if response.encoding is None or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text


def run(start: int, end: int, output: Path, *, save_text: bool = False,
        rights_confirmed: bool = False, delay: float = 3.0, client=None) -> dict:
    if not 328 <= start <= end <= 553:
        raise ValueError("The permitted project range is 328..553")
    if save_text and end - start + 1 > 10:
        raise ValueError("Maximum 10 chapters for an authorized RAW batch")
    if save_text and not rights_confirmed:
        raise ValueError("Storing full text requires --rights-confirmed")
    if delay < 2.0:
        raise ValueError("At least 2 seconds between requests")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    client = client or Client(delay)
    receipt = {
        "source": "czbooks", "source_type": "third_party_mirror",
        "book": TITLE, "book_url": ROOT,
        "requested": {"start": start, "end": end},
        "mode": "raw" if save_text else "catalog_only",
        "status": "NOT_STARTED", "catalog_count": 0, "catalog_gaps": [],
        "chapters": [], "raw_ok_count": 0,
        "notion_sync": "not_performed", "russian_translation": "not_performed",
    }
    try:
        catalog = parse_catalog(client.html(ROOT))
        receipt["catalog_count"] = len(catalog)
        receipt["catalog_gaps"] = [n for n in range(328, 554) if n not in catalog]
        for number in range(start, end + 1):
            row = dict(catalog[number]) if number in catalog else {"number": number}
            row["status"] = "in_catalog" if number in catalog else "missing_from_catalog"
            receipt["chapters"].append(row)
        receipt["status"] = "CATALOG_ONLY" if not receipt["catalog_gaps"] else "CATALOG_PARTIAL"
        if save_text:
            (output / "raw").mkdir()
            seen_hashes = {}
            for item in receipt["chapters"]:
                if item["status"] != "in_catalog":
                    continue
                try:
                    chapter = parse_body(client.html(item["url"]), item["number"])
                    sha = hashlib.sha256(chapter.encode("utf-8")).hexdigest()
                    if sha in seen_hashes:
                        raise AuditError("duplicate_body", f"Duplicate of {seen_hashes[sha]}")
                    seen_hashes[sha] = item["number"]
                    path = f"raw/{item['number']:04d}.txt"
                    (output / path).write_text(chapter, encoding="utf-8")
                    item.update(status="ok", raw_file=path, sha256=sha, chars=len(chapter))
                    receipt["raw_ok_count"] += 1
                except AuditError as exc:
                    item.update(status=exc.code, error=str(exc))
                    if exc.code in {"blocked", "robots_disallowed", "network_error"}:
                        break
            receipt["status"] = ("PASS" if receipt["raw_ok_count"] == end-start+1
                                 else "PARTIAL")
    except AuditError as exc:
        receipt["status"] = ("BLOCKED" if exc.code in
                             {"blocked", "robots_disallowed", "robots_error", "network_error"}
                             else "CATALOG_ERROR")
        receipt["error"] = {"code": exc.code, "detail": str(exc)}
    (output / "manifest.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# czbooks — chapter 328–553 catalog audit", "",
             f"- Status: **{receipt['status']}**", f"- Catalog range: {start}–{end}",
             f"- Chapter links found: {receipt['catalog_count']}",
             f"- Gaps (328–553): {len(receipt['catalog_gaps'])}",
             f"- Verified full chapter text saved: {receipt['raw_ok_count']}",
             "- Notion sync: NOT PERFORMED", "- Russian translation: NOT PERFORMED"]
    if "error" in receipt:
        lines.append(f"- Error: {receipt['error']['code']} ({receipt['error']['detail']})")
    for row in receipt["chapters"]:
        lines.append(f"- {row['number']}: {row['status']} {row.get('url','')}")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=int, default=328)
    p.add_argument("--end", type=int, default=553)
    p.add_argument("--delay", type=float, default=3.0)
    p.add_argument("--out-dir", type=Path, default=Path("out/czbooks-77297"))
    p.add_argument("--save-text", action="store_true")
    p.add_argument("--rights-confirmed", action="store_true")
    a = p.parse_args()
    try:
        result = run(a.start, a.end, a.out_dir, save_text=a.save_text,
                     rights_confirmed=a.rights_confirmed, delay=a.delay)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print((a.out_dir / "summary.md").read_text(encoding="utf-8"))
    return 0 if result["status"] in {"CATALOG_ONLY", "PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
