#!/usr/bin/env python3
"""Check chapter body availability without storing copyrighted book text.

This metadata-only probe uses public czbooks pages, honours robots.txt,
has no redirects or protection bypass and emits a translation-readiness report.
No chapter body, excerpt, HTML, personal data or Russian translation is saved.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from dataclasses import asdict, dataclass

from czbooks_77297 import AuditError, Client, ROOT, parse_body, parse_catalog

@dataclass
class Result:
    number: int
    title_zh: str
    url: str
    status: str
    cjk_chars: int = 0
    utf8_sha256: str = ""
    error: str = ""


def run(start: int, end: int, out_dir: Path, client=None) -> dict:
    if not (328 <= start <= end <= 553):
        raise ValueError("range must be inside 328..553")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("output folder must be new/empty; never mix stale receipts")
    out_dir.mkdir(parents=True, exist_ok=True)
    client = client or Client(delay=3.0)
    records = []
    try:
        catalog = parse_catalog(client.html(ROOT))
    except AuditError as exc:
        meta = dict(status="BLOCKED" if exc.code in ("blocked","robots_disallowed","network_error","robots_error") else "CATALOG_ERROR",
                    catalog_count=0, catalog_gaps=list(range(start,end+1)),
                    checked=0, readable=0, rejected=0, unexamined=end-start+1,
                    error={"code":exc.code,"detail":str(exc)},chapters=[])
    else:
        stopped = False
        for number in range(start,end+1):
            chapter = catalog.get(number)
            if not chapter:
                records.append(asdict(Result(number=number,title_zh="",url="",status="missing_from_catalog")))
                continue
            entry = Result(number=number,title_zh=chapter["title_zh"],url=chapter["url"],status="not_checked")
            if stopped:
                records.append(asdict(entry))
                continue
            try:
                body = parse_body(client.html(chapter["url"]), number)
                cjk_chars = sum("\u3400" <= ch <= "\u9fff" for ch in body)
                entry.status = "body_validated"
                entry.cjk_chars = cjk_chars
                entry.utf8_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
                # Never serialize chapter text, even temporarily.
            except AuditError as exc:
                entry.status = exc.code
                entry.error = str(exc)
                if exc.code in {"blocked","robots_disallowed","network_error","robots_error"}:
                    stopped = True
            records.append(asdict(entry))
        ok = sum(x["status"]=="body_validated" for x in records)
        check = sum(x["status"]!="not_checked" for x in records)
        rejects = sum(x["status"] not in ("body_validated","not_checked") for x in records)
        meta = {
            "status": "PROBE_PASS" if ok == end-start+1 else "PROBE_PARTIAL",
            "catalog_count": len(catalog),
            "catalog_gaps": [n for n in range(328,554) if n not in catalog],
            "checked": check, "readable": ok, "rejected": rejects,
            "unexamined": len(records)-check, "chapters": records,
        }
    meta.update({
        "schema_version":1,"novel_id":"77297","source":"czbooks.net",
        "requested":{"start":start,"end":end},
        "full_chapter_bodies_stored":0,"translations_stored":0,
        "notion_sync":"not_performed",
        "note":"body_validated means a detectable full-length Chinese-looking chapter container, not that the text was preserved or independently checked for completeness."
    })
    (out_dir/"manifest.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with (out_dir/"translation_queue.csv").open("w",newline="",encoding="utf-8-sig") as h:
        w=csv.DictWriter(h,fieldnames=["number","title_zh","url","status","cjk_chars","utf8_sha256","error"])
        w.writeheader()
        w.writerows(meta["chapters"])
    info=[
        "# CZBooks 77297 — Chinese chapter content availability",
        "",f"- Requested: {start}–{end}",f"- Status: **{meta['status']}**",
        f"- Chapter catalog count: {meta['catalog_count']}",
        f"- Body inspection succeeded: {meta['readable']}/{end-start+1}",
        f"- Rejected: {meta['rejected']}",
        f"- Not examined: {meta['unexamined']}",
        "- Full Chinese text saved: **0**",
        "- Russian translations saved: **0**",
        "- Notion sync: **NOT PERFORMED**",
        "- Source text never placed into the report or public Git repository."
    ]
    if "error" in meta:
        info.append(f"- Error: {meta['error']['code']}: {meta['error']['detail']}")
    for row in meta["chapters"]:
        info.append(f"- {row['number']}: {row['status']} (CJK={row['cjk_chars']})")
    (out_dir/"summary.md").write_text("\n".join(info)+"\n",encoding="utf-8")
    return meta


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start",type=int,default=328)
    parser.add_argument("--end",type=int,default=331)
    parser.add_argument("--out-dir",type=Path,default=Path("out/czbooks-body-probe"))
    args=parser.parse_args()
    try:
        result=run(args.start,args.end,args.out_dir)
    except (ValueError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        return 2
    print((args.out_dir/"summary.md").read_text(encoding="utf-8"))
    return 0 if result["status"] == "PROBE_PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
