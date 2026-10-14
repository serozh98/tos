#!/usr/bin/env python3
"""Sequential, rights-confirmed MTL collection for Faloo 1239043.

One GitHub Actions job, one HTTP session, one robots.txt request, one cached
catalog request, and >=3 seconds between all requests. No retries or bypass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fanmtl_collect import Client, collect, catalog_url, CHINESE_TITLE

MIN_DELAY_SECONDS = 3.0
BATCH_SIZE = 10


class CachedCatalogClient:
    """Cache only the public catalog, preserving a single rate-limited HTTP session."""
    def __init__(self, delegate, source):
        self.delegate = delegate
        self.url = catalog_url(source)
        self.cache = None

    def html(self, url):
        if url == self.url and self.cache is not None:
            return self.cache
        page = self.delegate.html(url)
        if url == self.url:
            self.cache = page
        return page


def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("Request must be a JSON object")
    allowed = {"book", "source", "start", "end", "delay_seconds", "rights_confirmed"}
    if set(data) != allowed:
        raise ValueError("Request fields must be exactly: " + ", ".join(sorted(allowed)))
    if data["book"] != CHINESE_TITLE:
        raise ValueError("Wrong Chinese title")
    if data["source"] != "wuxiabox":
        raise ValueError("This verified collection accepts Wuxiabox only")
    if type(data["start"]) is not int or type(data["end"]) is not int:
        raise ValueError("Chapter range must use integers")
    if not 55 <= data["start"] <= data["end"] <= 234:
        raise ValueError("Expected a chapter range within 55..234")
    delay = data["delay_seconds"]
    if type(delay) not in (int, float) or not MIN_DELAY_SECONDS <= delay <= 120:
        raise ValueError("Minimum request spacing is 3 seconds")
    if data["rights_confirmed"] is not True:
        raise ValueError("Saving MTL text requires explicit rights confirmation")
    return dict(data)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_report(root, summary):
    (root / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# English MTL batch - Faloo 1239043", "",
        "- Source: Wuxiabox",
        f"- Requested: {summary['request']['start']}-{summary['request']['end']}",
        f"- Minimum interval between HTTP requests: {summary['request']['delay_seconds']} seconds",
        f"- Status: {summary['status']}",
        f"- Chapters saved as TXT: {summary['saved_count']} / {summary['requested_count']}",
        f"- Batches processed: {len(summary['batches'])}",
        f"- Started UTC: {summary['started_at']}",
        f"- Updated UTC: {summary['updated_at']}",
        "- Chinese RAW verification: not performed",
        "- Russian translation: not performed",
        "- Notion sync: not performed",
    ]
    if summary.get("stop_reason"):
        lines.append("- Stop reason: " + summary["stop_reason"])
    for item in summary["chapters"]:
        lines.append(f"- Chapter {item['number']}: {item['status']}")
    (root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_collection(request, destination, client=None):
    request = validate_request(request)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Refusing to overwrite a nonempty output directory")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "mtl_en").mkdir()
    client = CachedCatalogClient(
        client or Client(delay=request["delay_seconds"], source=request["source"]),
        request["source"],
    )
    count = request["end"] - request["start"] + 1
    manifest = {
        "work": request["book"],
        "source": catalog_url(request["source"]),
        "source_kind": "third_party_english_mtl",
        "original_chinese_verified": False,
        "publication_rights_verified_by_maintainer": False,
        "download_rights_confirmed_by_user": True,
        "request": request,
        "status": "RUNNING",
        "requested_count": count,
        "saved_count": 0,
        "chapters": [],
        "batches": [],
        "stop_reason": None,
        "started_at": timestamp(),
        "updated_at": timestamp(),
        "notion_sync": "not_performed",
        "ru_translated_count": 0,
    }
    save_report(destination, manifest)

    try:
        with tempfile.TemporaryDirectory(prefix="mtl1239043_") as tmp:
            for first in range(request["start"], request["end"] + 1, BATCH_SIZE):
                last = min(first + BATCH_SIZE - 1, request["end"])
                part = Path(tmp) / f"{first:04d}-{last:04d}"
                print(f"Collecting MTL chapters {first}-{last}; delay >= {request['delay_seconds']} s", flush=True)
                result = collect(
                    first, last, part, save_text=True, rights_confirmed=True,
                    delay=request["delay_seconds"], source=request["source"], client=client
                )
                for row in result["chapters"]:
                    item = {
                        "number": row["number"],
                        "url": row["source_url"],
                        "status": row["status"],
                    }
                    if row["status"] == "SAVED":
                        filename = f"{row['number']:04d}.txt"
                        source = part / "mtl_en" / filename
                        if not source.is_file():
                            raise RuntimeError("Expected verified chapter file missing: " + filename)
                        payload = source.read_bytes()
                        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
                            raise RuntimeError("Chapter checksum mismatch: " + filename)
                        shutil.copyfile(source, destination / "mtl_en" / filename)
                        item.update({
                            "file": f"mtl_en/{filename}",
                            "title": row["title"],
                            "sha256": row["sha256"],
                            "characters": row["characters"],
                        })
                        manifest["saved_count"] += 1
                    else:
                        item["error"] = row.get("message", "Chapter not available")
                    manifest["chapters"].append(item)
                manifest["batches"].append({
                    "range": [first, last],
                    "status": result["status"],
                    "saved_count": result["saved_text_count"],
                    "requested_count": last - first + 1,
                    "error": result.get("error"),
                })
                manifest["updated_at"] = timestamp()
                if result["status"] != "PASS":
                    manifest["status"] = "PARTIAL"
                    failed = next((x for x in manifest["chapters"] if x["status"] != "SAVED"), None)
                    manifest["stop_reason"] = (
                        f"Stopped after the first incomplete batch {first}-{last}; "
                        + (f"chapter {failed['number']}: {failed['status']}" if failed else result["status"])
                    )
                    save_report(destination, manifest)
                    print(manifest["stop_reason"], flush=True)
                    break
                save_report(destination, manifest)
                print(f"Saved {manifest['saved_count']} / {count} chapters", flush=True)
            else:
                manifest["status"] = "COMPLETE" if manifest["saved_count"] == count else "PARTIAL"
    except Exception as exc:
        manifest["status"] = "ERROR"
        manifest["stop_reason"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        raise
    finally:
        manifest["updated_at"] = timestamp()
        save_report(destination, manifest)

    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("out/mtl-1239043"))
    args = parser.parse_args()
    try:
        config = json.loads(args.request.read_text(encoding="utf-8"))
        result = run_collection(config, args.out_dir)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Invalid request: {exc}", file=sys.stderr)
        return 2
    print((args.out_dir / "summary.md").read_text(encoding="utf-8"), flush=True)
    return 0 if result["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
