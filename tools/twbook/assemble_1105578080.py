#!/usr/bin/env python3
"""Validate and assemble 206 TWBook chapters from GitHub Actions shard artifacts.

Only validated Chinese RAW texts are included. Every catalog ordinal receives
a translation-queue record; missing or damaged chapters stay clearly flagged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

EXPECTED = 206
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
HANGUL = re.compile(r"[\uac00-\ud7a3]")

def validate_raw(data: str, receipt: dict) -> str:
    if "\n\n" not in data:
        raise ValueError("chapter has no title/body separator")
    title, body = data.split("\n\n", 1)
    body = body.rstrip("\r\n")
    if title.strip() != receipt["title"]:
        raise ValueError("title differs from catalog receipt")
    if len(body) != receipt["characters"]:
        raise ValueError("character count mismatch")
    if len(CJK.findall(body)) != receipt["chinese_characters"]:
        raise ValueError("Chinese character count mismatch")
    if HANGUL.search(body):
        raise ValueError("unresolved Hangul remains")
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != receipt["sha256"]:
        raise ValueError("SHA-256 mismatch")
    if len(body) < 300 or len(CJK.findall(body)) < 120:
        raise ValueError("insufficient chapter text")
    return body

def assemble(root: Path, output: Path, expected: int = EXPECTED) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    manifests = sorted(root.rglob("manifest.jsonl"))
    if not manifests:
        raise ValueError(f"no manifest.jsonl in {root}")
    receipts: dict[int, tuple[dict, Path]] = {}
    for manifest in manifests:
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw)
            ordinal = int(record["position"])
            if ordinal < 1 or ordinal > expected:
                raise ValueError(f"invalid position {ordinal}")
            if ordinal in receipts:
                raise ValueError(f"duplicate position {ordinal} in manifests")
            receipts[ordinal] = (record, manifest.parent)
    catalog_files = sorted(root.rglob("catalog.tsv"))
    if catalog_files:
        shutil.copyfile(catalog_files[0], output / "catalog.tsv")
    combined: list[str] = []
    queue: list[dict] = []
    ready = 0
    for position in range(1, expected + 1):
        record, part = receipts.get(position, (None, None))
        issue = None
        if record is None:
            issue = "manifest_missing"
        elif record.get("status") != "ok":
            issue = record.get("status", "error")
        elif not record.get("filename"):
            issue = "raw_not_saved"
        else:
            file_path = part / str(record["filename"])
            if not file_path.exists():
                issue = "file_missing"
            else:
                try:
                    raw_data = file_path.read_text(encoding="utf-8")
                    validate_raw(raw_data, record)
                except (ValueError, OSError, KeyError) as exc:
                    issue = f"invalid_file: {exc}"
                else:
                    chapter_path = output / "chapters" / f"{position:03d}.txt"
                    chapter_path.parent.mkdir(exist_ok=True)
                    chapter_path.write_text(raw_data, encoding="utf-8")
                    combined.append(raw_data.rstrip())
                    ready += 1
        item = {
            "position": position,
            "printed_number": record.get("printed_number") if record else None,
            "chinese_title": record.get("title", "") if record else "",
            "source": record.get("url", "") if record else "",
            "sha256": record.get("sha256", "") if record else "",
            "characters": record.get("characters", 0) if record else 0,
            "status": "ready_for_translation" if issue is None else "needs_source_check",
            "problem": issue or "",
            "chinese_raw_file": f"chapters/{position:03d}.txt" if issue is None else "",
            "russian_translation_file": f"translations/{position:03d}.txt",
        }
        queue.append(item)

    (output / "translation_queue.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue),
        encoding="utf-8",
    )
    if combined:
        (output / "all_chinese_for_translation.txt").write_text(
            ("\n\n" + "=" * 70 + "\n\n").join(combined) + "\n", encoding="utf-8",
        )
    pending = [row["position"] for row in queue if row["status"] != "ready_for_translation"]
    summary = {
        "expected": expected,
        "manifest_files": len(manifests),
        "records": len(receipts),
        "ready_for_translation": ready,
        "missing_or_unverified": len(pending),
        "pending_positions": pending,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "summary.md").write_text(
        "# TWBook 1105578080: full RAW compilation\n\n"
        f"- Expected catalog positions: {expected}\n"
        f"- Shard manifests: {len(manifests)}\n"
        f"- Received chapter receipts: {len(receipts)}\n"
        f"- Validated Chinese RAW chapters: {ready}/{expected}\n"
        f"- Missing or unresolved: {len(pending)}\n"
        f"- Pending positions: {', '.join(map(str, pending)) if pending else 'none'}\n"
        "\nNo generated Russian literary translations are included.\n",
        encoding="utf-8",
    )
    return summary

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", type=Path, default=Path("downloaded"))
    parser.add_argument("--out-dir", type=Path, default=Path("out/twbook-full"))
    parser.add_argument("--expected", type=int, default=206)
    args = parser.parse_args()
    try:
        summary = assemble(args.from_dir, args.out_dir, expected=args.expected)
    except (OSError, ValueError, KeyError) as exc:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "error.txt").write_text(str(exc) + "\n", encoding="utf-8")
        print("ASSEMBLY ERROR:", exc)
        return 3
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ready_for_translation"] == args.expected else 5

if __name__ == "__main__":
    raise SystemExit(main())
