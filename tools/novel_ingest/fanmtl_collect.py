#!/usr/bin/env python3
"""Audit FanMTL's public catalog, or collect an explicitly authorized MTL batch.

Only the fixed book "Sailing: I Am The Strongest Creature In The World".
No login, protection bypass, mirror rotation, automatic retries, or publishing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

SLUG = "sailing-i-am-the-strongest-creature-in-the-world"
CHINESE_TITLE = "航海：吾乃世界最强生物"
ENGLISH_TITLE = "Sailing: I Am The Strongest Creature In The World"
HOST = "www.fanmtl.com"  # Primary and legacy default for backwards compatibility.
SOURCE_HOSTS = {"fanmtl": "www.fanmtl.com", "wuxiabox": "www.wuxiabox.com"}
CATALOG_URL = f"https://{HOST}/novel/{SLUG}.html"
ROBOTS_URL = f"https://{HOST}/robots.txt"
USER_AGENT = "NovelResearchCollector/1.0 (respectful public-page research)"
MAX_NUMBER = 234  # Number in the catalog as checked on 2026-10-14; not verified text.
MAX_BATCH = 10
MIN_DELAY = 3.0
CHAPTER_PATH = re.compile(rf"/novel/{re.escape(SLUG)}_([1-9][0-9]*)\.html\Z")
CHAPTER_HEADING = re.compile(r"\bChapter\s+(\d+)\s*[:.\-–—]?", re.I)
BLOCKED_WORDS = (
    "captcha", "access denied", "just a moment", "verify you are human",
    "security check", "sign in to read", "login to continue",
    "you have been blocked", "cloudflare ray id",
)
CONTENT_SELECTORS = (
    "#chapter-content", ".chapter-content", ".read-content", ".entry-content",
    ".j_readContent", ".reader-content", ".read-body", "#content",
    "article .content", "article",
)
REMOVE_SELECTORS = (
    "script,style,noscript,iframe,form,button,input,textarea,nav,footer,header,"
    "aside,.ads,.advertisement,.chapter-nav,.read-nav,.page-nav,.breadcrumb,"
    ".recommend,.related,.comments,.comment,.share,.toolbar,.pagination,"
    ".chapter-title,.novel-title,.post-title,.novel-recommend"
)
AD_LINES = (
    "celebrate the 7-day long holiday of national day",
    "immediate recharge (event time:",
    "tap the screen to use advanced tools",
    "recharge 100 and get 500",
)


class CollectorError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def source_host(source: str) -> str:
    if source not in SOURCE_HOSTS:
        raise ValueError(f"Unknown MTL source: {source}")
    return SOURCE_HOSTS[source]


def catalog_url(source: str = "fanmtl") -> str:
    return f"https://{source_host(source)}/novel/{SLUG}.html"


def robots_url(source: str = "fanmtl") -> str:
    return f"https://{source_host(source)}/robots.txt"


def chapter_url(number: int, source: str = "fanmtl") -> str:
    if not 1 <= number <= MAX_NUMBER:
        raise ValueError(f"Chapter must be 1..{MAX_NUMBER}")
    return f"https://{source_host(source)}/novel/{SLUG}_{number}.html"


def validate_url(url: str, *, number: int | None = None, source: str = "fanmtl") -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != source_host(source) or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise CollectorError("UNSAFE_URL", "Only HTTPS URLs of the configured FanMTL host are allowed")
    if number is None:
        allowed = parsed.path == urlsplit(catalog_url(source)).path
    else:
        match = CHAPTER_PATH.fullmatch(parsed.path)
        allowed = match is not None and int(match.group(1)) == number
    if not allowed:
        raise CollectorError("UNSAFE_URL", "URL does not belong to this novel/chapter")
    return url


def tidy(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_catalog(html: str, source: str = "fanmtl") -> dict:
    soup = BeautifulSoup(html, "html.parser")
    h1 = tidy(soup.h1.get_text(" ", strip=True)) if soup.h1 else ""
    if ENGLISH_TITLE.casefold() not in h1.casefold():
        raise CollectorError("WRONG_BOOK", "Catalog does not display the expected English book title")
    if CHINESE_TITLE not in soup.get_text(" ", strip=True):
        raise CollectorError("WRONG_BOOK", "Chinese original title absent from catalog")

    chapters = {}
    for anchor in soup.select("a[href]"):
        url = urljoin(catalog_url(source), anchor.get("href", ""))
        try:
            parsed = urlsplit(url)
            validate_url(url, number=int(CHAPTER_PATH.fullmatch(parsed.path).group(1)), source=source)
        except (ValueError, TypeError, AttributeError, CollectorError):
            continue
        number = int(CHAPTER_PATH.fullmatch(parsed.path).group(1))
        if number > MAX_NUMBER:
            continue
        label = tidy(anchor.get_text(" ", strip=True))
        # Catalog may contain a leading list ordinal: "55 Chapter 55: ...".
        if label and not re.search(rf"\bChapter\s+{number}\b", label, re.I):
            continue
        if number in chapters and chapters[number]["url"] != url:
            raise CollectorError("CATALOG_CONFLICT", f"Conflicting chapter {number} URLs")
        chapters[number] = {"number": number, "url": url, "title": label}

    text = soup.get_text(" ", strip=True)
    totals = re.findall(r"\b(\d{1,4})\s+Chapters\b", text, flags=re.I)
    claimed = int(totals[0]) if totals else None
    if not chapters:
        raise CollectorError("CATALOG_EMPTY", "No chapter links identified in public catalog")
    if 1 not in chapters:
        raise CollectorError("CATALOG_INCOMPLETE", "Catalog does not contain chapter 1")
    return {"claimed_total": claimed, "chapters_seen_on_this_page": len(chapters),
            "chapter_numbers_seen": sorted(chapters), "chapters": chapters}


def parse_chapter(html: str, *, number: int) -> tuple[str, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    if any(term in page_text.casefold() for term in BLOCKED_WORDS):
        raise CollectorError("BLOCKED", "Page appears to be a challenge/login/blocking page")
    if ENGLISH_TITLE.casefold() not in page_text.casefold():
        raise CollectorError("WRONG_BOOK", "Chapter page is not linked to expected novel")

    candidates = []
    for selector in ("h2", "h3", "h1", ".chapter-title", "title"):
        for node in soup.select(selector):
            header = tidy(node.get_text(" ", strip=True))
            match = CHAPTER_HEADING.search(header)
            if match:
                candidates.append((int(match.group(1)), header))
    if not candidates:
        raise CollectorError("MISSING_TITLE", "Cannot find a chapter number in a title element")
    matching = [header for n, header in candidates if n == number]
    if not matching:
        raise CollectorError("WRONG_CHAPTER", f"Expected chapter {number}, titles say {[n for n, _ in candidates]}")
    title = min(matching, key=len)

    selector_used = None
    body = None
    for selector in CONTENT_SELECTORS:
        matches = soup.select(selector)
        choices = [m for m in matches if len(tidy(m.get_text(" ", strip=True))) >= 400]
        if choices:
            body = max(choices, key=lambda n: len(n.get_text(" ", strip=True)))
            selector_used = selector
            break
    if body is None:
        raise CollectorError("MISSING_BODY", "No sufficiently long recognized reader container")

    # Reparse a copy: do not mutate the soup used for title/book identification.
    fragment = BeautifulSoup(str(body), "html.parser")
    for tag in fragment.select(REMOVE_SELECTORS):
        tag.decompose()
    for a in fragment.select("a"):
        a.unwrap()
    paragraphs = [tidy(p.get_text(" ", strip=True)) for p in fragment.select("p")]
    if paragraphs:
        pieces = paragraphs
    else:
        pieces = [tidy(s) for s in fragment.get_text("\n", strip=True).splitlines()]
    pieces = [p for p in pieces if p and not any(ad in p.casefold() for ad in AD_LINES)]
    # Content may include chapter title as first line; don't duplicate it.
    if pieces and CHAPTER_HEADING.match(pieces[0]) and re.search(rf"\bChapter\s+{number}\b", pieces[0], re.I):
        pieces.pop(0)
    content = "\n\n".join(pieces).strip()
    if len(content) < 500 or len(re.findall(r"[A-Za-z]", content)) < 300:
        raise CollectorError("TOO_SHORT", "Reader content too short or not English")
    if any(term in content.casefold() for term in BLOCKED_WORDS):
        raise CollectorError("BLOCKED", "Reader content contains a challenge/login notice")
    if len(content) > 200_000:
        raise CollectorError("TOO_LARGE", "Extracted text exceeds expected chapter size")
    return title, content + "\n", selector_used


class Client:
    def __init__(self, delay: float = MIN_DELAY, source: str = "fanmtl"):
        self.source = source
        self.host = source_host(source)
        self.robots_url = robots_url(source)
        if delay < MIN_DELAY:
            raise ValueError(f"Minimum delay is {MIN_DELAY} seconds")
        self.delay = delay
        self.last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"})
        self.robots: RobotFileParser | None = None

    def _fetch(self, url: str):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last_request_at)))
        try:
            res = self.session.get(url, timeout=(12, 25), allow_redirects=False)
        except requests.RequestException as exc:
            raise CollectorError("NETWORK_ERROR", f"Connection failed: {exc.__class__.__name__}") from exc
        finally:
            self.last_request_at = time.monotonic()
        if 300 <= res.status_code < 400:
            raise CollectorError("REDIRECT", "Site redirected; refusing to change destination automatically")
        if res.status_code in (401, 402, 403, 429, 451):
            raise CollectorError("BLOCKED", f"HTTP {res.status_code}")
        if res.status_code != 200 and not (url == self.robots_url and res.status_code == 404):
            raise CollectorError("HTTP_ERROR", f"HTTP {res.status_code}")
        if len(res.content) > 2_000_000:
            raise CollectorError("TOO_LARGE", "Remote response is unusually large")
        return res

    def html(self, url: str):
        parsed = urlsplit(url)
        match = CHAPTER_PATH.fullmatch(parsed.path)
        validate_url(url, number=int(match.group(1)) if match else None, source=self.source)
        if self.robots is None:
            result = self._fetch(self.robots_url)
            self.robots = RobotFileParser()
            if result.status_code == 404:
                self.robots.parse([])
            else:
                if "text" not in result.headers.get("content-type", "text/plain").lower():
                    raise CollectorError("ROBOTS_ERROR", "Cannot parse robots.txt safely")
                self.robots.parse(result.text.splitlines())
        if not self.robots.can_fetch(USER_AGENT, url):
            raise CollectorError("ROBOTS_DISALLOWED", "Access disallowed in robots.txt")
        crawl_delay = self.robots.crawl_delay(USER_AGENT)
        if crawl_delay:
            self.delay = max(self.delay, float(crawl_delay))
        response = self._fetch(url)
        ctype = response.headers.get("content-type", "text/html")
        if "html" not in ctype.lower():
            raise CollectorError("INVALID_CONTENT_TYPE", "Expected an HTML chapter/catalog page")
        response.encoding = response.apparent_encoding if response.encoding in (None, "ISO-8859-1") else response.encoding
        return response.text


def collect(start: int, end: int, output: Path, *, save_text: bool = False,
            rights_confirmed: bool = False, delay: float = MIN_DELAY, source: str = "fanmtl",
            probe_number: int | None = None, client=None) -> dict:
    source_host(source)
    if not 1 <= start <= end <= MAX_NUMBER or end - start + 1 > MAX_BATCH:
        raise ValueError(f"Choose {MAX_BATCH} or fewer consecutive chapters within 1..{MAX_NUMBER}")
    if delay < MIN_DELAY:
        raise ValueError(f"Minimum delay is {MIN_DELAY} seconds")
    if save_text and not rights_confirmed:
        raise ValueError("--save-text requires --rights-confirmed")
    if probe_number is not None and (save_text or not start <= probe_number <= end):
        raise ValueError("--probe-number must be in the requested range and cannot be used with --save-text")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty (no overwrite)")
    output.mkdir(parents=True, exist_ok=True)
    client = client or Client(delay, source=source)
    data = {
        "work": CHINESE_TITLE, "english_title": ENGLISH_TITLE,
        "source": catalog_url(source), "source_name": source,
        "source_kind": "third_party_english_mtl",
        "original_verified": False, "rights_publication_verified": False,
        "requested_range": [start, end], "mode": "text" if save_text else "catalog",
        "status": "NOT_STARTED", "chapters_in_catalog_page": 0,
        "claimed_total_from_site": None, "catalog_numbers_seen": [],
        "chapters": [], "saved_text_count": 0, "probe": None,
        "ru_translated_count": 0, "notion_sync": "not_performed",
    }
    stop_codes = {"BLOCKED", "ROBOTS_DISALLOWED", "REDIRECT", "ROBOTS_ERROR", "NETWORK_ERROR"}
    try:
        c = parse_catalog(client.html(catalog_url(source)), source=source)
        data["chapters_in_catalog_page"] = c["chapters_seen_on_this_page"]
        data["catalog_numbers_seen"] = c["chapter_numbers_seen"]
        data["claimed_total_from_site"] = c["claimed_total"]
        if not save_text:
            data["status"] = "CATALOG_ONLY"
            if probe_number is not None:
                url = chapter_url(probe_number, source=source)
                try:
                    title, text, selector = parse_chapter(client.html(url), number=probe_number)
                    data["probe"] = {
                        "number": probe_number, "url": url, "status": "OK",
                        "title": title, "characters": len(text), "content_selector": selector,
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    }
                    data["status"] = "PROBE_OK"
                except CollectorError as exc:
                    data["probe"] = {"number": probe_number, "url": url,
                                     "status": exc.code, "message": str(exc)}
                    data["status"] = "PROBE_FAILED"
        else:
            (output / "mtl_en").mkdir()
            for number in range(start, end + 1):
                row = {"number": number, "source_url": chapter_url(number, source=source), "status": "NOT_STARTED"}
                try:
                    title, text, selector = parse_chapter(client.html(row["source_url"]), number=number)
                    file = f"mtl_en/{number:04d}.txt"
                    (output / file).write_text(text, encoding="utf-8")
                    row.update({"status": "SAVED", "title": title, "file": file,
                                "content_selector": selector, "characters": len(text),
                                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
                    data["saved_text_count"] += 1
                except CollectorError as exc:
                    row.update({"status": exc.code, "message": str(exc)})
                data["chapters"].append(row)
                if row["status"] in stop_codes:
                    break
            data["status"] = "PASS" if data["saved_text_count"] == end - start + 1 else "PARTIAL"
    except CollectorError as exc:
        data["status"] = (exc.code if exc.code in stop_codes else "CATALOG_ERROR")
        data["error"] = {"code": exc.code, "message": str(exc)}
    (output / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# {ENGLISH_TITLE}", "", f"- Source: {source}", f"- Status: {data['status']}",
             f"- Selected chapters: {start}-{end}",
             f"- Catalog items observed on fetched page: {data['chapters_in_catalog_page']}",
             f"- Total chapters stated by site (unverified): {data['claimed_total_from_site']}",
             f"- MTL texts saved: {data['saved_text_count']}",
             "- Chinese RAW checked: 0", "- RU translated: 0", "- Notion sync: not performed"]
    if data["probe"] is not None:
        lines.append(f"- Chapter probe (no text saved): {data['probe']['number']}: {data['probe']['status']}")
        if data["probe"].get("message"):
            lines.append(f"- Probe error: {data['probe']['message']}")
    if "error" in data:
        lines.append(f"- Error: {data['error']['code']}: {data['error']['message']}")
    for row in data["chapters"]:
        lines.append(f"- Chapter {row['number']}: {row['status']}")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(SOURCE_HOSTS), default="fanmtl",
                        help="Select the independent MTL site explicitly; no automatic fallback")
    parser.add_argument("--start", type=int, default=55)
    parser.add_argument("--end", type=int, default=58)
    parser.add_argument("--out-dir", type=Path, default=Path("out/fanmtl-1239043"))
    parser.add_argument("--probe-number", type=int, default=None, help="Check one public chapter without saving its text")
    parser.add_argument("--delay", type=float, default=MIN_DELAY)
    parser.add_argument("--save-text", action="store_true", help="Save MTL bodies in addition to catalog audit")
    parser.add_argument("--rights-confirmed", action="store_true", help="Confirm the necessary rights to obtain/store MTL")
    args = parser.parse_args()
    try:
        data = collect(args.start, args.end, args.out_dir,
                       save_text=args.save_text, rights_confirmed=args.rights_confirmed,
                       delay=args.delay, source=args.source, probe_number=args.probe_number)
    except (ValueError, OSError) as exc:
        print(f"Invalid request: {exc}", file=sys.stderr)
        return 2
    print((args.out_dir / "summary.md").read_text(encoding="utf-8"))
    return 0 if data["status"] in ("PASS", "CATALOG_ONLY", "PROBE_OK") else 1


if __name__ == "__main__":
    raise SystemExit(main())
