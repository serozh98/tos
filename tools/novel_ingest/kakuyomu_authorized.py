#!/usr/bin/env python3
"""Prepare authorized Japanese originals for private translation (Kakuyomu)."""
import csv
import hashlib
import json
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

WORK_ID = "822139838502050041"
ROOT = f"https://kakuyomu.jp/works/{WORK_ID}"
OUT = Path("out/kakuyomu-822139838502050041")
USER_AGENT = "AuthorizedTranslationResearch/1.0"
DELAY = 2.0


def read_page(session, url):
    parts = urlparse(url)
    if parts.scheme != "https" or parts.netloc != "kakuyomu.jp" or not parts.path.startswith(f"/works/{WORK_ID}"):
        raise ValueError("Unexpected external URL")
    response = session.get(url, timeout=(15, 35), allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {url}")
    if len(response.content) > 3_000_000:
        raise RuntimeError("Oversized page")
    return BeautifulSoup(response.text, "html.parser")


def chapters_from_work(soup):
    node = soup.select_one("#__NEXT_DATA__")
    if not node:
        raise RuntimeError("No Next.js catalog data")
    state = json.loads(node.get_text()).get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__", {})
    work = state.get(f"Work:{WORK_ID}", {})
    items = []
    for item in state.values():
        if isinstance(item, dict) and item.get("__typename") == "Episode" and str(item.get("id", "")).isdigit():
            items.append(item)
    # Episodes have unique publishing timestamps in the platform's catalog.
    items.sort(key=lambda e: (e.get("publishedAt", ""), str(e["id"])))
    title = work.get("title") or soup.title.get_text(" ", strip=True)
    expected = work.get("publicEpisodeCount") or work.get("episodeCount")
    if expected is not None and int(expected) != len(items):
        raise RuntimeError(f"Catalog mismatch: {len(items)} found, {expected} declared")
    if len(items) < 129:
        raise RuntimeError(f"Incomplete catalog: {len(items)} episodes, expected at least 129")
    return title, items


def episode_text(soup):
    body = soup.select_one("div.widget-episodeBody.js-episode-body, div.widget-episodeBody")
    if body is None:
        raise RuntimeError("No episode text element")
    for tag in list(body.select("script,style,form,nav,button,noscript")):
        tag.decompose()
    for ruby in list(body.select("ruby")):
        reading = "".join(t.get_text("", strip=False) for t in ruby.select("rt"))
        clone = BeautifulSoup(str(ruby), "html.parser")
        for tag in clone.select("rt,rp"):
            tag.decompose()
        base = clone.get_text("", strip=False)
        ruby.replace_with(f"{base}《{reading}》" if reading else base)
    for tag in list(body.select("br")):
        tag.replace_with("\n")
    paras = body.select("p")
    text = "\n".join(p.get_text("", strip=False).rstrip() for p in paras) if paras else body.get_text("\n")
    text = re.sub(r"\n{5,}", "\n\n\n\n", text.replace("\r", "")).strip()
    if len(text) < 80:
        raise RuntimeError(f"Episode too short: {len(text)} characters")
    return text


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    originals = OUT / "original_ja"
    originals.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept-Language"] = "ja"
    report = dict(work=ROOT, checked_at=datetime.now(timezone.utc).isoformat(), status="STARTED", episodes=[], errors=[])
    try:
        robots_response = session.get("https://kakuyomu.jp/robots.txt", timeout=20)
        if robots_response.status_code != 200:
            raise RuntimeError(f"Cannot verify robots.txt: HTTP {robots_response.status_code}")
        robots = RobotFileParser()
        robots.parse(robots_response.text.splitlines())
        if not robots.can_fetch(USER_AGENT, ROOT):
            raise RuntimeError("Site disallows automatic access to this work")
        title, episodes = chapters_from_work(read_page(session, ROOT))
        report["title"] = title
        report["catalog_count"] = len(episodes)
        known = set()
        for number, chapter in enumerate(episodes, 1):
            url = f"{ROOT}/episodes/{chapter['id']}"
            if not robots.can_fetch(USER_AGENT, url):
                report["errors"].append(dict(number=number, error="robots.txt disallow"))
                break
            time.sleep(DELAY)
            try:
                body = episode_text(read_page(session, url))
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                if digest in known:
                    raise RuntimeError("Repeated chapter body")
                known.add(digest)
                filename = f"original_ja/{number:03}.txt"
                (OUT / filename).write_text(f"{chapter.get('title', '')}\n\n{body}\n", encoding="utf-8")
                report["episodes"].append(dict(number=number, id=chapter["id"], title=chapter.get("title", ""),
                                                 url=url, file=filename, chars=len(body), sha256=digest))
                print(f"OK {number}/{len(episodes)}: {len(body)} characters", flush=True)
            except Exception as e:
                report["errors"].append(dict(number=number, id=chapter["id"], error=str(e)))
                print(f"FAIL {number}: {e}", flush=True)
                if "HTTP 403" in str(e) or "HTTP 429" in str(e):
                    break
        report["status"] = "COMPLETE" if len(report["episodes"]) == len(episodes) and not report["errors"] else "PARTIAL"
    except Exception as e:
        report["status"] = "FAILED"
        report["errors"].append(dict(error=str(e)))
    (OUT / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "status.txt").write_text(
        f"Status: {report['status']}\nCollected full Japanese texts: {len(report['episodes'])}\nCatalog: {report.get('catalog_count', '?')}\nErrors: {json.dumps(report['errors'], ensure_ascii=False)}\n", encoding="utf-8")
    with zipfile.ZipFile(OUT.parent / "kakuyomu_822139838502050041_original_ja.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in OUT.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(OUT))
    print((OUT / "status.txt").read_text(), flush=True)
    return 0 if report["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
