#!/usr/bin/env python3
"""Audit public 69shuba chapter links; optionally collect verified RAW with rights.

No logins, anti-bot bypass, redirects, unapproved hosts or automatic retries.
The default run is metadata only. Notion synchronization/translation is separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BOOK_ID = "77297"
TITLE = "被退婚后，隔壁全村肠子悔青了"
BASE = f"https://www.69shuba.com/book/{BOOK_ID}/"
CHAPTER_PATH = re.compile(rf"/txt/{BOOK_ID}/([0-9]+)(?:\.html?)?/?$")
CHAPTER_LABEL = re.compile(r"(?:^|[.。\s])第\s*(\d+)\s*章\s*(.*)")
UA = "NovelResearchCollector/1.0 (public pages; respects robots.txt)"
BODY_SELECTORS = ("#content", "#chaptercontent", ".txtnav #content", ".txtnav", ".txt", ".chapter-content", ".read-content")
BLOCK_TEXT = ("验证码", "人机验证", "请先登录", "购买本章", "章节已锁定", "正在验证浏览器", "captcha", "access denied", "just a moment", "cloudflare")


class AuditError(Exception):
    def __init__(self, code: str, reason: str):
        self.code = code
        super().__init__(reason)


def safe_url(href: str, *, chapter: bool = False) -> str:
    url = urljoin(BASE, href)
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname != "www.69shuba.com" or p.username
            or p.password or p.port not in (None, 443) or p.query or p.fragment):
        raise AuditError("unsafe_url", url)
    if chapter:
        if not CHAPTER_PATH.fullmatch(p.path):
            raise AuditError("unsafe_url", url)
    elif p.path != f"/book/{BOOK_ID}/":
        raise AuditError("unsafe_url", url)
    return url


def parse_catalog(html: str) -> dict[int, dict]:
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if TITLE not in page_title and TITLE not in soup.get_text(" ", strip=True):
        raise AuditError("wrong_book", "The expected Chinese novel title is missing")
    found: dict[int, dict] = {}
    for a in soup.select("a[href]"):
        try:
            url = safe_url(a["href"], chapter=True)
        except AuditError:
            continue
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        m = CHAPTER_LABEL.search(label)
        if not m:
            continue
        number = int(m.group(1))
        if number < 1 or number > 10000:
            continue
        title = m.group(2).strip() or f"第{number}章"
        item = {"number": number, "title": title, "url": url,
                "source_id": CHAPTER_PATH.fullmatch(urlsplit(url).path).group(1)}
        prev = found.get(number)
        if prev and prev["source_id"] != item["source_id"]:
            raise AuditError("catalog_conflict", f"Chapter {number} has conflicting source IDs")
        if prev is None or len(title) > len(prev["title"]):
            found[number] = item
    if not found:
        raise AuditError("catalog_missing", "No matching numbered chapter URLs in catalog")
    return dict(sorted(found.items()))


def parse_body(html: str, chapter_number: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    visible = soup.get_text(" ", strip=True).lower()
    if any(marker in visible[:1500] for marker in BLOCK_TEXT):
        raise AuditError("blocked", "Locked/challenge content rather than a chapter")
    headings = [soup.title] if soup.title else []
    headings.extend(soup.select("h1, h2"))
    numbers = []
    for h in headings:
        m = CHAPTER_LABEL.search(h.get_text(" ", strip=True))
        if m:
            numbers.append(int(m.group(1)))
    if numbers and any(number != chapter_number for number in numbers):
        raise AuditError("wrong_chapter", f"Expected {chapter_number}; observed {numbers}")
    node = next((x for selector in BODY_SELECTORS if (x := soup.select_one(selector))), None)
    if node is None:
        raise AuditError("missing_body", "No known chapter text container")
    for junk in node.select("script,style,iframe,noscript,nav,header,footer,form,button,.ad,.ads,.advertisement,.pager,.chapter-nav"):
        junk.decompose()
    parts = [p.get_text(" ", strip=True) for p in node.select("p")]
    text = "\n\n".join(p for p in parts if p) if len(parts) >= 2 else node.get_text("\n", strip=True)
    text = re.sub(r"\r\n?", "\n", text).strip()
    if any(marker in text.lower() for marker in BLOCK_TEXT):
        raise AuditError("blocked", "Challenge/locked text detected")
    if len(re.findall(r"[\u3400-\u9fff]", text)) < 180:
        raise AuditError("too_short", "Fewer than 180 CJK ideographs")
    if len(text) > 800_000:
        raise AuditError("invalid_body", "Chapter implausibly large")
    return text + "\n"


class Client:
    def __init__(self, delay: float = 3.0):
        if delay < 2:
            raise ValueError("Delay must be >= 2 seconds")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        self.last = 0.0
        self.delay = delay
        self.robot = None

    def _get(self, url: str):
        time.sleep(max(0.0, self.delay - (time.monotonic() - self.last)))
        try:
            r = self.session.get(url, allow_redirects=False, timeout=(10, 25))
        except requests.RequestException as exc:
            raise AuditError("network_error", type(exc).__name__) from exc
        finally:
            self.last = time.monotonic()
        if 300 <= r.status_code < 400 or r.status_code in (401, 402, 403, 429, 451):
            raise AuditError("blocked", f"HTTP {r.status_code}")
        if r.status_code not in (200, 404):
            raise AuditError("http_error", f"HTTP {r.status_code}")
        if len(r.content) > 3_000_000:
            raise AuditError("oversize", "Response exceeds 3 MB")
        return r

    def html(self, url: str) -> str:
        safe_url(url, chapter=urlsplit(url).path.startswith(f"/txt/{BOOK_ID}/"))
        if self.robot is None:
            r = self._get("https://www.69shuba.com/robots.txt")
            self.robot = RobotFileParser()
            if r.status_code == 404:
                self.robot.parse(["User-agent: *", "Allow: /"])
            elif "text" in r.headers.get("content-type", "").lower() and "<html" not in r.text[:500].lower():
                self.robot.parse(r.text.splitlines())
            else:
                raise AuditError("robots_error", "robots.txt is not valid text")
            crawl_delay = self.robot.crawl_delay(UA)
            if crawl_delay is not None:
                self.delay = max(self.delay, float(crawl_delay))
        if not self.robot.can_fetch(UA, url):
            raise AuditError("robots_disallowed", "robots.txt prohibits this URL")
        r = self._get(url)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "").lower():
            raise AuditError("invalid_response", f"Expected HTML 200, got {r.status_code}")
        if r.encoding is None or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text


def run(start: int, end: int, output: Path, *, save_text: bool = False,
        rights_confirmed: bool = False, delay: float = 3.0, client=None) -> dict:
    if not 328 <= start <= end <= 553:
        raise ValueError("This project permits only chapters 328..553")
    if save_text and end - start + 1 > 10:
        raise ValueError("Save at most 10 chapters per job")
    if save_text and not rights_confirmed:
        raise ValueError("Saving full text requires --rights-confirmed")
    if delay < 2:
        raise ValueError("Minimum delay is 2 seconds")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    client = client or Client(delay)
    result = {"book_id": BOOK_ID, "book_title": TITLE, "book_url": BASE,
              "requested": {"start": start, "end": end},
              "source": "third_party_mirror", "mode": "raw" if save_text else "catalog_only",
              "status": "NOT_STARTED", "catalog_count": 0, "catalog_gaps": [],
              "chapters": [], "raw_ok_count": 0, "notion_sync": "not_performed",
              "translation_count": 0}
    try:
        catalog = parse_catalog(client.html(BASE))
        result["catalog_count"] = len(catalog)
        result["catalog_gaps"] = [n for n in range(328, 554) if n not in catalog]
        for n in range(start, end + 1):
            row = dict(catalog[n]) if n in catalog else {"number": n}
            row["status"] = "in_catalog" if n in catalog else "missing_from_catalog"
            result["chapters"].append(row)
        result["status"] = "CATALOG_ONLY" if not result["catalog_gaps"] else "CATALOG_PARTIAL"
        if save_text:
            (output / "raw").mkdir()
            known_hashes = {}
            for row in result["chapters"]:
                if row["status"] != "in_catalog":
                    continue
                try:
                    body = parse_body(client.html(row["url"]), row["number"])
                    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                    if digest in known_hashes:
                        raise AuditError("duplicate_body", f"Duplicate of chapter {known_hashes[digest]}")
                    known_hashes[digest] = row["number"]
                    name = f"raw/{row['number']:04d}.txt"
                    (output / name).write_text(body, encoding="utf-8")
                    row.update(status="ok", chars=len(body), sha256=digest, raw_file=name)
                    result["raw_ok_count"] += 1
                except AuditError as exc:
                    row.update(status=exc.code, error=str(exc))
                    if exc.code in {"blocked", "robots_disallowed", "robots_error", "network_error"}:
                        break
            result["status"] = "PASS" if result["raw_ok_count"] == end - start + 1 else "PARTIAL"
    except AuditError as exc:
        result["status"] = "BLOCKED" if exc.code in {"blocked", "robots_disallowed", "robots_error", "network_error"} else "CATALOG_ERROR"
        result["error"] = {"code": exc.code, "message": str(exc)}
    (output / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# 69shuba 77297 catalog / chapter receipt", "", f"- Status: **{result['status']}**",
              f"- Chapter range: {start}..{end}", f"- Book: {TITLE}",
              f"- Entries: {result['catalog_count']}", f"- Missing 328..553: {len(result['catalog_gaps'])}",
              f"- Stored RAW: {result['raw_ok_count']}",
              "- Notion sync: **NOT PERFORMED**", "- Russian translation: **NOT PERFORMED**"]
    if result.get("error"):
        report.append(f"- Error: {result['error']['code']}: {result['error']['message']}")
    for row in result["chapters"]:
        report.append(f"- {row['number']}: {row['status']} — {row.get('url','')}")
    (output / "summary.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=int, default=328)
    p.add_argument("--end", type=int, default=553)
    p.add_argument("--out-dir", type=Path, default=Path("out/shuba-77297"))
    p.add_argument("--delay", type=float, default=3.0)
    p.add_argument("--save-text", action="store_true")
    p.add_argument("--rights-confirmed", action="store_true")
    a = p.parse_args()
    try:
        d = run(a.start, a.end, a.out_dir, save_text=a.save_text,
                rights_confirmed=a.rights_confirmed, delay=a.delay)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print((a.out_dir / "summary.md").read_text(encoding="utf-8"))
    return 0 if d["status"] in {"CATALOG_ONLY", "PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
