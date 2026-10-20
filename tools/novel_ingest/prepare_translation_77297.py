#!/usr/bin/env python3
"""Build deterministic 4-chapter Russian translation handoff for novel 77297.

Without --authorized-input: produces metadata-only batch plans.
With --authorized-input: validates a locally supplied lawful Chinese RAW corpus,
preserves content verbatim and prepares TXT batch files for private translation.
This script never downloads any chapter or uploads book text to GitHub/Notion.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

CJK=re.compile(r"[\u3400-\u9fff]")
CHALLENGES=("验证码","人机验证","正在验证浏览器","captcha","cloudflare","购买本章","章节已锁定")


def prepare(manifest_path:Path,raw_dir:Path,out_dir:Path,authorized_input:bool=False)->dict:
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    rows=manifest["chapters"]
    if (len(rows)!=226 or
        [r["chapter"] for r in rows]!=list(range(328,554)) or
        len({r["source_url"] for r in rows})!=226):
        raise ValueError("Expected unique, gapless chapter links 328..553")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("Output folder must be empty")
    out_dir.mkdir(parents=True,exist_ok=True)
    all_batches=[]
    receipts=[]
    for offset in range(0,len(rows),4):
        group=rows[offset:offset+4]
        start,end=group[0]["chapter"],group[-1]["chapter"]
        group_report=[]
        group_bodies=[]
        ready=True
        hashes=set()
        for ch in group:
            num=ch["chapter"]
            row={"chapter":num,"zh_title":ch["zh_title"],"source_url":ch["source_url"],
                 "notion_url":ch["notion_url"],"raw_verified":False,
                 "raw_sha256":None,"raw_cjk_chars":0,"translation_status":"not_started"}
            # In plan-only mode, do not open full chapter files.
            if authorized_input:
                path=raw_dir/f"{num:04d}.txt"
                if path.is_file() and not path.is_symlink():
                    body=path.read_text(encoding="utf-8-sig").replace("\r\n","\n").replace("\r","\n").strip()
                    chars=len(CJK.findall(body))
                    if (chars>=180 and not any(mark in body.lower() for mark in CHALLENGES)
                         and len(body)<=750000):
                        digest=hashlib.sha256((body+"\n").encode("utf-8")).hexdigest()
                        if digest not in hashes:
                            hashes.add(digest)
                            row.update(raw_verified=True,raw_sha256=digest,raw_cjk_chars=chars)
                            group_bodies.append((num,ch["zh_title"],body))
            if not row["raw_verified"]:
                ready=False
            group_report.append(row)
            receipts.append(row)
        batch={"start":start,"end":end,"status":"READY" if ready else "WAITING_FOR_RAW",
               "raw_verified":sum(row["raw_verified"] for row in group_report),
               "chapter_count":len(group),"chapters":group_report}
        all_batches.append(batch)
        if ready:
            # Only explicitly authorized input can enter this branch.
            text="\n\n".join(f"第{n}章 {title}\n\n{body}" for n,title,body in group_bodies)+"\n"
            filename=f"batch_{start:04d}_{end:04d}.txt"
            (out_dir/filename).write_text(text,encoding="utf-8")
            batch["private_text_file"]=filename
    state={"schema_version":1,"book_id":"77297",
           "range":[328,553],"authorized_input":authorized_input,
           "chapters":receipts,"batches":all_batches,
           "ready_batches":sum(b["status"]=="READY" for b in all_batches),
           "waiting_batches":sum(b["status"]!="READY" for b in all_batches),
           "fully_verified_chapters":sum(r["raw_verified"] for r in receipts),
           "russian_translation_count":0,"notion_sync":"not_performed"}
    (out_dir/"translation_readiness.json").write_text(
        json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with (out_dir/"translation_readiness.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(receipts[0]))
        writer.writeheader();writer.writerows(receipts)
    lines=["# Chinese RAW -> Russian translation preparation","",
           f"- Chapters: {len(receipts)}",
           f"- Batches of up to four: {len(all_batches)}",
           f"- Authorized source text supplied: {'yes' if authorized_input else 'no'}",
           f"- Verified Chinese RAW chapters: {state['fully_verified_chapters']}",
           f"- Ready batches: {state['ready_batches']}",
           "- Russian translations: 0","- Notion synchronization: not performed",
           "","## Translation batches"]
    lines.extend(f"- {b['start']}–{b['end']}: {b['status']} ({b['raw_verified']}/{b['chapter_count']})"
                 for b in all_batches)
    (out_dir/"summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return state


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",type=Path,default=Path("data/czbooks-77297/translation_manifest_328-553.json"))
    p.add_argument("--raw-dir",type=Path,default=Path("raw/czbooks-77297"))
    p.add_argument("--out-dir",type=Path,default=Path("out/translation-preparation"))
    p.add_argument("--authorized-input",action="store_true",help="I have permission to process local original texts")
    args=p.parse_args()
    try:
        result=prepare(args.manifest,args.raw_dir,args.out_dir,args.authorized_input)
    except (ValueError,OSError,KeyError) as e:
        print(str(e),file=sys.stderr);return 2
    print((args.out_dir/"summary.md").read_text(encoding="utf-8"))
    return 0 if not args.authorized_input or result["ready_batches"] else 1


if __name__=="__main__":
    raise SystemExit(main())
