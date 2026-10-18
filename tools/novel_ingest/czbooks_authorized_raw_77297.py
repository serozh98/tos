#!/usr/bin/env python3
"""Acquire authorized Chinese RAW for novel 77297 (chapters 328-553).

Uses the existing public CZBooks adapter with robots.txt enforcement, at least
3 seconds between requests, no authentication, no redirected fetches, and no
protection bypass. Verifies live catalog against the audited 226-link manifest.
Stores RAW only in the workflow's temporary, short-retention artifact (never in
a Git commit). Reports failure and leaves partial receipts on the first block.
Run only with explicit rights confirmation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from czbooks_77297 import AuditError, Client, ROOT, parse_body, parse_catalog

STOP = {"blocked", "robots_disallowed", "network_error", "robots_error"}
CJK = re.compile(r"[\u3400-\u9fff]")


def run(start: int, end: int, *, catalog_file: Path, output: Path,
        rights_confirmed: bool, delay: float = 3.0, client=None) -> dict:
    if not rights_confirmed:
        raise ValueError("Must affirm permission to obtain and store Chinese chapter text")
    if not 328 <= start <= end <= 553:
        raise ValueError("Allowed range is chapters 328..553")
    if delay < 3.0:
        raise ValueError("Use at least three seconds between requests")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output folder must be empty to prevent stale data")
    catalog_doc = json.loads(catalog_file.read_text(encoding="utf-8"))
    expected = catalog_doc.get("chapters", [])
    if (len(expected) != 226 or
            [c["number"] for c in expected] != list(range(328, 554)) or
            len({c["url"] for c in expected}) != 226):
        raise ValueError("Trusted 226-link catalog is missing, duplicated or unordered")
    url_by_num = {c["number"]: c["url"] for c in expected}
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw").mkdir()
    (output / "batches").mkdir()
    session = client if client is not None else Client(delay=delay)
    state = {
        "schema_version": 1,
        "book_id": "77297",
        "source": "czbooks.net",
        "source_url": ROOT,
        "requested": {"start": start, "end": end},
        "rights_confirmed": True,
        "status": "STARTED",
        "verified_raw": 0,
        "requested_count": end-start+1,
        "unique_bodies": 0,
        "ready_translation_batches": 0,
        "notion_sync": "not_performed",
        "russian_translation": "not_performed",
        "chapters": [],
        "errors": []
    }
    def persist():
        (output / "manifest.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        lines = [
            "# Chinese RAW chapters 328-553: authorized export",
            "",f"- Status: **{state['status']}**",
            f"- Requested: {start}-{end} ({state['requested_count']})",
            f"- Verified RAW files: **{state['verified_raw']}**",
            f"- Translation batches (four chapters): **{state['ready_translation_batches']}**",
            "- Russian translation: not performed",
            "- Notion synchronization: not performed",
            "- Texts are in temporary GitHub Actions artifacts, not repository commits.",
            "",
            "## Individual chapters",
        ]
        for rec in state["chapters"]:
            lines.append(
                f"- {rec['number']}: {rec['status']}; "
                f"CJK={rec.get('cjk_chars',0)}; SHA256={rec.get('sha256','')}; "
                f"{rec.get('error','')}")
        (output / "summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")

    persist()
    try:
        live = parse_catalog(session.html(ROOT))
    except AuditError as exc:
        state["status"] = "BLOCKED" if exc.code in STOP else "CATALOG_ERROR"
        state["errors"].append({"stage": "catalog", "code": exc.code, "error": str(exc)})
        persist()
        return state

    divergences = [
        n for n in range(start, end+1)
        if n not in live or live[n]["url"] != url_by_num[n]
    ]
    if divergences:
        state["status"] = "CATALOG_MISMATCH"
        state["errors"].append({"stage": "catalog", "numbers": divergences})
        persist()
        return state

    hashes: dict[str,int] = {}
    bodies: dict[int,str] = {}
    for n in range(start,end+1):
        item = live[n]
        rec = {"number": n, "title_zh": item["title_zh"],
               "source_url": item["url"], "status": "not_started"}
        try:
            body = parse_body(session.html(item["url"]), n)
            cjk = len(CJK.findall(body))
            if cjk < 450:
                raise AuditError("too_short", f"Only {cjk} CJK ideographs")
            sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if sha in hashes:
                raise AuditError("duplicate_body", f"Exact duplicate of chapter {hashes[sha]}")
            hashes[sha] = n
            filename = f"raw/{n:04d}.txt"
            (output / filename).write_text(body, encoding="utf-8")
            bodies[n] = body
            rec.update(status="raw_checked", cjk_chars=cjk, sha256=sha,
                       raw_file=filename)
            state["verified_raw"] += 1
            state["unique_bodies"] += 1
        except AuditError as exc:
            rec.update(status=exc.code, error=str(exc))
            state["errors"].append({"number":n,"code":exc.code,"error":str(exc)})
        except (OSError,UnicodeError) as exc:
            rec.update(status="write_error", error=type(exc).__name__)
            state["errors"].append({"number":n,"code":"write_error",
                                    "error":type(exc).__name__})
        state["chapters"].append(rec)
        state["status"] = "IN_PROGRESS"
        persist()
        if rec["status"] in STOP:
            state["status"] = "BLOCKED"
            break
        # Emit a handoff file after a verified four-chapter group, never with gaps.
        if (n-start+1) % 4 == 0 or n == end:
            first = n-((n-start) % 4)
            block = list(range(first,n+1))
            if all(k in bodies for k in block):
                out = output / "batches" / f"{first:04d}-{n:04d}.txt"
                text = "\n\n".join(
                    f"第{k}章 {live[k]['title_zh']}\n\n{bodies[k].strip()}"
                    for k in block)+"\n"
                out.write_text(text,encoding="utf-8")
                state["ready_translation_batches"] += 1
                persist()
            # Avoid holding duplicate text of already archived groups in RAM.
            for k in block:
                bodies.pop(k,None)

    if state["status"] != "BLOCKED":
        state["status"] = "PASS" if state["verified_raw"] == (end-start+1) else "PARTIAL"
    persist()
    return state


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start",type=int,default=328)
    p.add_argument("--end",type=int,default=553)
    p.add_argument("--catalog-file",type=Path,
                   default=Path("data/czbooks-77297/catalog_328-553.json"))
    p.add_argument("--out-dir",type=Path,default=Path("out/czbooks-authorized-raw"))
    p.add_argument("--delay",type=float,default=3.0)
    p.add_argument("--rights-confirmed",action="store_true",required=True)
    a=p.parse_args()
    try:
        result=run(a.start,a.end,catalog_file=a.catalog_file,
                   output=a.out_dir,rights_confirmed=a.rights_confirmed,
                   delay=a.delay)
    except (ValueError,OSError,KeyError) as exc:
        print(str(exc),file=sys.stderr);return 2
    print(f"Collection status={result['status']} "
          f"raw={result['verified_raw']}/{result['requested_count']} "
          f"batches={result['ready_translation_batches']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
