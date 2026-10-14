#!/usr/bin/env python3
"""Shuhaige 360588: public catalog and optional verified RAW. No access bypass."""
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

BOOK = "360588"
ROOT = f"https://m.shuhaige.net/{BOOK}/"
TITLE = "穿书反派，圣女小姐别偷听"
AUTHOR = "鱼鱼没睡醒"
UA = "NovelResearchCollector/1.0 (public pages; respects robots.txt)"
NUMBER = re.compile(r"第\s*(\d+)\s*[章回节]\s*(.*)")
CHAPTER = re.compile(rf"/{BOOK}/(\d+)(?:_(\d+))?\.html")
CONTENT = ("div.content", "#chaptercontent", "#content", ".chapter-content", ".read-content")
BLOCKED = ("验证码", "人机验证", "请先登录", "购买本章", "章节已锁定", "captcha", "access denied", "just a moment")


class CollectionError(Exception):
    def __init__(self, kind, message):
        self.kind = kind
        super().__init__(message)


def safe_url(href, chapter=False, source_id=None):
    url = urljoin(ROOT, href)
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != "m.shuhaige.net" or p.port not in (None, 443) or p.username or p.password or p.query or p.fragment:
        raise CollectionError("unsafe_url", url)
    match = CHAPTER.fullmatch(p.path)
    if chapter:
        if not match or (source_id and match.group(1) != source_id):
            raise CollectionError("unsafe_url", url)
    elif not (p.path == f"/{BOOK}/" or re.fullmatch(rf"/{BOOK}/index[_-]?\d+\.html", p.path)):
        raise CollectionError("unsafe_url", url)
    return url


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def title_ok(soup):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if TITLE not in title and TITLE not in soup.get_text(" ", strip=True):
        raise CollectionError("wrong_book", "Expected Chinese book title not found")


def catalog_page(html):
    soup = BeautifulSoup(html, "html.parser")
    title_ok(soup)
    chapters, pages = {}, []
    for a in soup.select("a[href]"):
        label = clean(a.get_text(" ", strip=True))
        m = NUMBER.match(label)
        if m:
            try:
                url = safe_url(a["href"], chapter=True)
            except CollectionError:
                continue
            number = int(m.group(1))
            if not 1 <= number <= 100000:
                continue
            ident = CHAPTER.fullmatch(urlsplit(url).path).group(1)
            previous = chapters.get(number)
            if previous and previous["id"] != ident:
                raise CollectionError("catalog_conflict", f"Chapter {number} has conflicting URLs")
            chapters[number] = {"number": number, "title": m.group(2), "url": url, "id": ident}
        elif any(x in label for x in ("下一页", "尾页")) or re.fullmatch(r"第\d+页", label):
            try:
                url = safe_url(a["href"])
                if url not in pages:
                    pages.append(url)
            except CollectionError:
                pass
    return chapters, pages


def chapter_page(html, number, ident):
    soup = BeautifulSoup(html, "html.parser")
    title_ok(soup)
    for h in soup.select("h1"):
        m = NUMBER.match(clean(h.get_text(" ", strip=True)))
        if m and int(m.group(1)) != number:
            raise CollectionError("wrong_chapter", f"Expected {number}, found {m.group(1)}")
    node = next((soup.select_one(s) for s in CONTENT if soup.select_one(s)), None)
    if not node:
        raise CollectionError("missing_body", "No verified chapter text element")
    for junk in node.select("script,style,noscript,iframe,form,nav,button,.ad,.ads,.pager,.chapter-nav"):
        junk.decompose()
    lines = [clean(x.get_text(" ", strip=True)) for x in node.select("p")]
    text = "\n\n".join(x for x in lines if x) if lines else node.get_text("\n", strip=True)
    if any(marker in text.lower() for marker in BLOCKED):
        raise CollectionError("blocked", "Locked or challenge content")
    if not text.strip():
        raise CollectionError("missing_body", "Empty chapter")
    more = None
    for a in soup.select(".pager a[href], .page a[href]"):
        label = clean(a.get_text(" ", strip=True))
        if "下一页" in label and "下一章" not in label:
            more = safe_url(a["href"], chapter=True, source_id=ident)
            break
    return text.strip(), more


class Client:
    def __init__(self, delay=3):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        self.last = 0.0
        self.delay = delay
        self.robot = None

    def _request(self, url):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        try:
            response = self.session.get(url, timeout=(10, 25), allow_redirects=False)
        except requests.RequestException as exc:
            raise CollectionError("network_error", str(exc)) from exc
        finally:
            self.last = time.monotonic()
        if 300 <= response.status_code < 400 or response.status_code in (401, 402, 403, 429, 451):
            raise CollectionError("blocked", f"HTTP {response.status_code}")
        if response.status_code not in (200, 404):
            raise CollectionError("http_error", f"HTTP {response.status_code}")
        if len(response.content) > 2_000_000:
            raise CollectionError("too_large", "Oversized response")
        return response

    def html(self, url):
        safe_url(url, chapter=bool(CHAPTER.fullmatch(urlsplit(url).path)))
        if self.robot is None:
            result = self._request("https://m.shuhaige.net/robots.txt")
            if result.status_code != 404 and "text" not in result.headers.get("content-type", "").lower():
                raise CollectionError("robots_error", "Invalid robots response")
            self.robot = RobotFileParser()
            self.robot.parse([] if result.status_code == 404 else result.text.splitlines())
        if not self.robot.can_fetch(UA, url):
            raise CollectionError("robots_disallowed", "Disallowed by robots.txt")
        r = self._request(url)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "").lower():
            raise CollectionError("invalid_response", f"Not an HTML page: HTTP {r.status_code}")
        if r.encoding is None or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        return r.text


def run(start, end, output, save_text=False, rights_confirmed=False, delay=3, client=None):
    if not 1 <= start <= end or end - start + 1 > 10:
        raise ValueError("Range must contain 1..10 chapters")
    if delay < 2:
        raise ValueError("Delay must be at least two seconds")
    if save_text and not rights_confirmed:
        raise ValueError("Full text requires --rights-confirmed")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output folder must be empty")
    output.mkdir(parents=True, exist_ok=True)
    client = client or Client(delay)
    data = {"book": TITLE, "author": AUTHOR, "book_url": ROOT,
            "source_type": "third_party_mirror", "original_confirmed": False,
            "rulate_200365_match_confirmed": False,
            "requested": [start, end], "mode": "raw" if save_text else "catalog_only",
            "status": "NOT_STARTED", "catalog_count": 0,
            "chapters": [], "raw_ok_count": 0, "notion_sync": "not_performed",
            "ru_translated_count": 0}
    try:
        seen, pending, chapters = set(), [ROOT], {}
        while pending and len(seen) < 12:
            url = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            part, pages = catalog_page(client.html(url))
            for num, entry in part.items():
                if num in chapters and chapters[num]["id"] != entry["id"]:
                    raise CollectionError("catalog_conflict", f"Chapter {num} differs")
                chapters[num] = entry
            pending.extend(u for u in pages if u not in seen and u not in pending)
        if not chapters:
            raise CollectionError("catalog_missing", "No numbered chapter links")
        data["catalog"] = [chapters[n] for n in sorted(chapters)]
        data["catalog_count"] = len(chapters)
        data["catalog_pages_scanned"] = len(seen)
        data["catalog_truncated"] = bool(pending)
        data["catalog_gaps"] = [n for n in range(1, max(chapters) + 1) if n not in chapters]
        if not save_text:
            data["status"] = "CATALOG_ONLY"
        else:
            (output / "raw").mkdir()
            for n in range(start, end + 1):
                item = chapters.get(n)
                if item is None:
                    data["chapters"].append({"number": n, "status": "missing_from_catalog"})
                    continue
                row = dict(item)
                try:
                    current, seen_pages, pieces = item["url"], set(), []
                    while current:
                        if current in seen_pages or len(seen_pages) >= 5:
                            raise CollectionError("pagination_error", "Repeated or too many pages")
                        seen_pages.add(current)
                        text, current = chapter_page(client.html(current), n, item["id"])
                        pieces.append(text)
                    body = "\n\n".join(pieces).strip() + "\n"
                    if len(re.sub(r"\s+", "", body)) < 250 or len(re.findall(r"[\u3400-\u9fff]", body)) < 100:
                        raise CollectionError("too_short", "Unexpectedly short or non-Chinese chapter")
                    if len(set(pieces)) != len(pieces):
                        raise CollectionError("duplicate_part", "Duplicate paginated chapter text")
                    filename = f"raw/{n:04d}.txt"
                    (output / filename).write_text(body, encoding="utf-8")
                    row.update(status="ok", raw_file=filename, source_pages=list(seen_pages),
                               chars=len(re.sub(r"\s+", "", body)),
                               sha256=hashlib.sha256(body.encode("utf-8")).hexdigest())
                    data["raw_ok_count"] += 1
                except CollectionError as exc:
                    row.update(status=exc.kind, error=str(exc))
                data["chapters"].append(row)
                if row["status"] in ("blocked", "robots_disallowed"):
                    break
            data["status"] = "PASS" if data["raw_ok_count"] == end - start + 1 else "PARTIAL"
    except CollectionError as exc:
        if exc.kind in ("blocked", "robots_disallowed", "robots_error"):
            data["status"] = "BLOCKED"
        elif exc.kind == "network_error":
            data["status"] = "NETWORK_ERROR"
        else:
            data["status"] = "CATALOG_ERROR"
        data["error"] = {"code": exc.kind, "message": str(exc)}
    (output / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = [f"# {TITLE}", "", f"- Status: {data['status']}",
               f"- Catalog chapters found: {data['catalog_count']}",
               f"- Verified RAW stored: {data['raw_ok_count']}",
               "- Original publisher: unconfirmed", "- Rulate 200365 match: unconfirmed",
               "- Notion sync and translation: not performed"]
    if data.get("error"):
        summary.append(f"- Error: {data['error']}")
    for entry in data["chapters"]:
        summary.append(f"- Chapter {entry['number']}: {entry['status']}")
    (output / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=int, default=1)
    p.add_argument("--end", type=int, default=5)
    p.add_argument("--out-dir", type=Path, default=Path("out/shuhaige-360588"))
    p.add_argument("--delay", type=float, default=3)
    p.add_argument("--save-text", action="store_true")
    p.add_argument("--rights-confirmed", action="store_true")
    args = p.parse_args()
    try:
        data = run(args.start, args.end, args.out_dir, args.save_text, args.rights_confirmed, args.delay)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print((args.out_dir / "summary.md").read_text(encoding="utf-8"))
    return 0 if data["status"] in ("PASS", "CATALOG_ONLY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
