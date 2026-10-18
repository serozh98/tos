#!/usr/bin/env python3
"""Fetch publicly readable TWBook chapters with strict validation and rate limits.

Book: 1105578080. Catalog position is the stable key, not the displayed
chapter number (which repeats for this title). No login, CAPTCHA, proxies,
access-control bypass, or text committed to Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BOOK = "1105578080"
SITE = "https://www.twbook.cc"
CATALOG = f"{SITE}/{BOOK}/dir"
AGENT = "TOSNovelIngest/1.0 (public chapters; contact via GitHub repository)"
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
MASK = re.compile(r"[\uac00-\ud7a3]")
CHAP_NO = re.compile(r"第\s*(\d+)\s*章")
DENIED = ("captcha", "cloudflare", "access denied", "验证浏览器", "驗證瀏覽器", "请先登录")

@dataclass
class Entry:
    position: int
    printed_number: int | None
    title: str
    url: str

@dataclass
class Receipt:
    position: int
    printed_number: int | None
    title: str
    url: str
    status: str
    characters: int = 0
    chinese_characters: int = 0
    sha256: str = ""
    filename: str = ""
    detail: str = ""

def allowed(url: str) -> bool:
    p = urlsplit(url)
    return (p.scheme == "https" and p.netloc == "www.twbook.cc"
            and not p.query and not p.fragment
            and bool(re.fullmatch(rf"/{BOOK}/(?:dir|\d+(?:_\d+)*\.html)", p.path)))

def catalog_entries(html: str) -> list[Entry]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[int, Entry] = {}
    for a in soup.select("a[href]"):
        url = urljoin(CATALOG, a["href"].strip())
        if not allowed(url):
            continue
        m = re.fullmatch(rf"/{BOOK}/(\d+)\.html", urlsplit(url).path)
        if m is None:
            continue
        position = int(m.group(1))
        title = " ".join(a.get_text(" ", strip=True).split())
        match = CHAP_NO.search(title)
        number = int(match.group(1)) if match else None
        item = Entry(position, number, title, url)
        if position in found and found[position] != item:
            raise ValueError(f"conflicting links for catalog position {position}")
        found[position] = item
    rows = sorted(found.values(), key=lambda item: item.position)
    if [r.position for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError("catalog links are missing positions or contain gaps")
    if not rows:
        raise ValueError("no chapter links found")
    return rows

def body_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("div.chapter-content div.content") or soup.select_one("div.chapter-content")
    if container is None:
        flat = soup.get_text(" ", strip=True).lower()
        if any(word in flat for word in DENIED):
            raise PermissionError("site served a restricted/challenge page")
        raise ValueError("no chapter body container")
    node = BeautifulSoup(str(container), "html.parser")
    for garbage in node.select(
        "script,style,noscript,iframe,svg,form,button,nav,header,footer,"
        ".adBlock,.gadBlock,.foot-nav,.advertisement"
    ):
        garbage.decompose()
    for p in list(node.select("p")):
        if "溫馨提示" in p.get_text(" ", strip=True) or "温馨提示" in p.get_text(" ", strip=True):
            p.decompose()
    lines = [re.sub(r"[\t\u00a0\u3000 ]+", " ", line).strip()
             for line in node.get_text("\n", strip=True).splitlines()]
    return "\n".join(line for line in lines if line)

def merge_variants(variants: list[str]) -> str:
    if not variants:
        raise ValueError("no chapter variants")
    lengths = {len(s) for s in variants}
    if len(lengths) != 1:
        raise ValueError("different chapter bodies; cannot safely align variants")
    chars = []
    for i in range(len(variants[0])):
        plain = {v[i] for v in variants if not MASK.fullmatch(v[i])}
        if len(plain) > 1:
            raise ValueError(f"different visible characters at offset {i}")
        chars.append(next(iter(plain)) if plain else variants[0][i])
    return "".join(chars)

class PublicClient:
    def __init__(self, delay: float, timeout: float):
        if delay < 2:
            raise ValueError("minimum delay is 2 seconds")
        self.delay = delay
        self.timeout = timeout
        self.last_request = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": AGENT, "Accept-Language": "zh-TW,zh;q=0.9"})

    def get(self, url: str, robots: bool = False) -> str:
        if url != f"{SITE}/robots.txt" if robots else not allowed(url):
            raise ValueError(f"disallowed URL: {url}")
        if self.last_request is not None:
            time.sleep(max(0.0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
        if response.status_code in (401, 403, 429):
            raise PermissionError(f"HTTP {response.status_code}; stop without retry")
        if response.status_code >= 300:
            raise ValueError(f"HTTP {response.status_code}")
        response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        return response.text

    def robots_check(self) -> None:
        try:
            robots_txt = self.get(f"{SITE}/robots.txt", robots=True)
        except ValueError as exc:
            if str(exc) == "HTTP 404":
                return
            raise
        rp = RobotFileParser()
        rp.parse(robots_txt.splitlines())
        for url in (CATALOG, f"{SITE}/{BOOK}/1.html"):
            if not rp.can_fetch(AGENT, url):
                raise PermissionError(f"robots.txt disallows {url}")

def write_reports(folder: Path, catalog: list[Entry], receipts: list[Receipt]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "catalog.tsv").write_text(
        "position\tprinted_number\ttitle\turl\n" + "".join(
            f"{e.position}\t{e.printed_number if e.printed_number is not None else ''}\t{e.title}\t{e.url}\n"
            for e in catalog), encoding="utf-8")
    (folder / "manifest.jsonl").write_text(
        "".join(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in receipts),
        encoding="utf-8")
    counts = Counter(r.status for r in receipts)
    lines = [f"# TWBook {BOOK} audit", "", f"Catalog entries: {len(catalog)}",
             f"Requested/attempted: {len(receipts)}", ""]
    lines.extend(f"- {status}: {count}" for status, count in sorted(counts.items()))
    lines.extend(["", "## Failures", ""])
    lines.extend(f"- {r.position}: {r.status}, {r.detail}" for r in receipts if r.status != "ok")
    (folder / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

def probe_chapter(client: PublicClient, entry: Entry, folder: Path,
                  max_renderings: int, save_text: bool) -> Receipt:
    result = Receipt(entry.position, entry.printed_number, entry.title, entry.url, "error")
    variants: list[str] = []
    for attempt in range(max_renderings):
        try:
            candidate = body_from_html(client.get(entry.url))
            if len(candidate) < 300 or len(CJK.findall(candidate)) + len(MASK.findall(candidate)) < 120:
                raise ValueError("short/placeholder chapter body")
            variants.append(candidate)
            body = merge_variants(variants)
            if MASK.search(body):
                if attempt + 1 < max_renderings:
                    continue
                result.status = "needs_check"
                result.detail = "unresolved Hangul after permitted renderings"
                return result
            n_cjk = len(CJK.findall(body))
            if n_cjk < 120:
                raise ValueError("too few Chinese characters")
            result.status = "ok"
            result.characters = len(body)
            result.chinese_characters = n_cjk
            result.sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if save_text:
                target = folder / "chapters" / f"{entry.position:03d}.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(entry.title + "\n\n" + body + "\n", encoding="utf-8")
                result.filename = str(target.relative_to(folder))
            return result
        except PermissionError as exc:
            result.status = "blocked"
            result.detail = str(exc)
            return result
        except (ValueError, requests.RequestException) as exc:
            result.detail = str(exc)
            return result
    result.status = "needs_check"
    result.detail = "no complete rendering"
    return result

def run(args: argparse.Namespace) -> int:
    if not (1 <= args.start <= args.end <= 206 and args.end - args.start < 20):
        raise ValueError("select 1–20 chapters in the range 1–206")
    if not 1 <= args.max_renderings <= 12:
        raise ValueError("max_renderings must be 1–12")
    if args.save_text and not args.rights_confirmed:
        raise ValueError("--save-text requires --rights-confirmed")
    folder = Path(args.out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    client = PublicClient(args.delay, args.timeout)
    client.robots_check()
    raw_catalog = client.get(CATALOG)
    entries = catalog_entries(raw_catalog)
    if args.end > len(entries):
        raise ValueError(f"only {len(entries)} catalog entries; requested through {args.end}")
    selected = entries[args.start - 1:args.end]
    results: list[Receipt] = []
    if not args.catalog_only:
        for entry in selected:
            receipt = probe_chapter(client, entry, folder, args.max_renderings, args.save_text)
            results.append(receipt)
            print(f"{entry.position:03d}: {receipt.status}: {receipt.detail}", flush=True)
            if receipt.status == "blocked":
                break
    write_reports(folder, entries, results)
    if args.catalog_only:
        return 0
    return 0 if len(results) == len(selected) and all(r.status == "ok" for r in results) else 5

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=4)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--max-renderings", type=int, default=4)
    ap.add_argument("--out-dir", default="out/twbook-1105578080")
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--save-text", action="store_true")
    ap.add_argument("--rights-confirmed", action="store_true")
    args = ap.parse_args()
    try:
        return run(args)
    except (ValueError, PermissionError, requests.RequestException, OSError) as exc:
        folder = Path(args.out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "error.json").write_text(
            json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print("ERROR:", exc, file=sys.stderr)
        return 3

if __name__ == "__main__":
    raise SystemExit(main())
