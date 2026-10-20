#!/usr/bin/env python3
"""Prepare a user-supplied, authorized Chinese source corpus for Russian translation.

This script does NOT download books from websites. It processes a directory or ZIP
of chapter TXT files supplied by the user, preserves full text, and produces:
  raw/NNN.txt, manifest.jsonl, QA_REPORT.md, corpus_raw.zip (when any valid files exist).
It never claims that a source is complete without checking each expected chapter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib
import re
import sys
import zipfile
from collections import defaultdict

CHAPTER = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
CHINESE = re.compile(r"第\s*(\d{1,4})\s*章")
CJK = re.compile(r"[\u3400-\u9fff]")
MAX_ITEM_BYTES = 5_000_000
MIN_CJK = 300

def extract_number(path: str) -> int | None:
    name = pathlib.PurePosixPath(path.replace("\\", "/")).stem
    match = CHINESE.search(name) or CHAPTER.search(name)
    if not match:
        return None
    return int(match.group(1))

def decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            pass
    raise ValueError("Cannot decode as UTF-8, GB18030, or Big5")

def inputs(path: pathlib.Path):
    if path.is_dir():
        for file in sorted(path.rglob("*")):
            if file.is_file() and file.suffix.lower() == ".txt":
                if file.stat().st_size > MAX_ITEM_BYTES:
                    yield file.relative_to(path).as_posix(), None, "File over size limit"
                else:
                    yield file.relative_to(path).as_posix(), file.read_bytes(), None
    elif path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for entry in sorted(z.infolist(), key=lambda x:x.filename):
                if entry.is_dir() or not entry.filename.lower().endswith(".txt"):
                    continue
                if entry.file_size > MAX_ITEM_BYTES:
                    yield entry.filename, None, "File over size limit"
                    continue
                yield entry.filename, z.read(entry), None
    else:
        raise ValueError("Use a folder of chapter .txt files or a ZIP containing .txt files")

def run(input_path: pathlib.Path, output_dir: pathlib.Path, expected: int, min_cjk: int):
    source = defaultdict(list)
    ignored, rejected = [], []
    for name, raw, err in inputs(input_path):
        num = extract_number(name)
        if num is None or num < 1 or num > expected:
            ignored.append(name)
            continue
        if err or raw is None:
            rejected.append({"file":name,"number":num,"reason":err or "No bytes"})
            continue
        try:
            text, enc = decode(raw)
        except ValueError as e:
            rejected.append({"file":name,"number":num,"reason":str(e)})
            continue
        # Preserve paragraphs exactly except cross-platform line endings and BOM.
        text = text.replace("\r\n","\n").replace("\r","\n").lstrip("\ufeff")
        cjk_count = len(CJK.findall(text))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        source[num].append(dict(filename=name, text=text, encoding=enc,
                                char_count=len(text), cjk_count=cjk_count,
                                sha256=digest))
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest, missing, short, conflicting = [], [], [], []
    for num in range(1, expected+1):
        candidates=source.get(num,[])
        record={"chapter":num,"status":"missing","source_files":[x["filename"] for x in candidates],
                "raw_path":None,"sha256":None,"characters":0,"cjk_chars":0}
        if not candidates:
            missing.append(num)
        elif len({x["sha256"] for x in candidates})>1:
            record["status"]="conflicting_sources"
            conflicting.append(num)
        else:
            c=candidates[0]
            record.update(status="present_unverified",raw_path=f"raw/{num:03d}.txt",
                          sha256=c["sha256"],characters=c["char_count"],cjk_chars=c["cjk_count"])
            # Only this user's supplied source is written to disk.
            (raw_dir / f"{num:03d}.txt").write_text(c["text"],encoding="utf-8",newline="\n")
            if c["cjk_count"]<min_cjk:
                record["status"]="suspiciously_short"
                short.append(num)
        manifest.append(record)
    with (output_dir/"manifest.jsonl").open("w",encoding="utf-8") as f:
        for record in manifest:
            f.write(json.dumps(record,ensure_ascii=False)+"\n")
    ok=sum(m["status"]=="present_unverified" for m in manifest)
    report=f"""# Chinese RAW chapter import: 超级提取

- Input: {input_path.name}
- Expected chapters: {expected}
- Chapter files with enough Chinese characters (not yet reviewed by a person): {ok}
- Suspiciously short chapters: {len(short)} — {short}
- Missing chapters: {len(missing)} — {missing}
- Conflicting duplicate filenames/contents: {len(conflicting)} — {conflicting}
- Skipped non-chapter files: {len(ignored)}
- Rejected files: {len(rejected)} — {rejected}

## Important
This script only imports **user-supplied source files**. It cannot verify that
the files contain the complete published chapters or that adjacent chapters
form a continuous story. Status \`present_unverified\` is NOT \`RAW verified\`.
Do not mark a chapter ready for translation until you have checked its
beginning, ending, and continuity. Original text is not automatically published.
"""
    (output_dir/"QA_REPORT.md").write_text(report,encoding="utf-8")
    if any(r["raw_path"] for r in manifest):
        with zipfile.ZipFile(output_dir/"corpus_raw.zip","w",compression=zipfile.ZIP_DEFLATED) as z:
            for p in sorted(raw_dir.glob("*.txt")):
                z.write(p,arcname=f"raw/{p.name}")
            z.write(output_dir/"manifest.jsonl",arcname="manifest.jsonl")
            z.write(output_dir/"QA_REPORT.md",arcname="QA_REPORT.md")
    print(json.dumps({"expected":expected,"present_unverified":ok,"missing":len(missing),
                      "short":len(short),"conflicting":len(conflicting),
                      "report":str(output_dir/"QA_REPORT.md")},ensure_ascii=False))
    return 0 if ok==expected and not(short or conflicting or rejected) else 2

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",type=pathlib.Path,required=True,
                    help="Your own authorized ZIP or directory of Chinese chapter .txt files")
    ap.add_argument("--out-dir",type=pathlib.Path,default=pathlib.Path("out/chaojitiqu"))
    ap.add_argument("--expected",type=int,default=297)
    ap.add_argument("--min-cjk",type=int,default=MIN_CJK)
    args=ap.parse_args()
    if not 1 <= args.expected <= 10000:
        ap.error("--expected must be from 1 to 10000")
    sys.exit(run(args.input,args.out_dir,args.expected,args.min_cjk))
