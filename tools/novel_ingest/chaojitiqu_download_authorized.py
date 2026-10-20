#!/usr/bin/env python3
"""Download only the site's publicly advertised whole-book TXT ZIP for user-authorized work.
Do not bypass access controls. Store text in a short-lived workflow artifact, never
in the repository itself. Store only status metadata in the public repository.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
import pathlib
import re
import traceback
import urllib.error
import urllib.request
import zipfile

URL = "https://down7.ixdzs8.com/117771.zip"
ROOT = pathlib.Path("out/chaojitiqu")
REPORT = pathlib.Path("data/novels/chaojitiqu/fetch_status.json")
MAX_BYTES = 45_000_000
MAX_EXPANDED = 90_000_000
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
HEAD_RE = re.compile(r"(?m)^[ \t\u3000]*(?:第[0-9０-９零〇一二两三四五六七八九十百千万]+[章节回])(?=.{0,80}(?:\n|$))")
USER_AGENT = "Mozilla/5.0 (compatible; PersonalAuthorizedArchive/1.0)"

def save_report(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/"REPORT.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def main():
    report = dict(title="超级提取",reference_url="https://ixdzs8.com/read/117771/",
                  advertised_txt_url=URL,run_id=os.environ.get("GITHUB_RUN_ID"),
                  status="attempting",source_sha256=None,source_bytes=0,
                  text_files=[],raw_verified=0,translation_ready=0)
    ROOT.mkdir(parents=True,exist_ok=True)
    try:
        req=urllib.request.Request(URL,headers={"User-Agent":USER_AGENT,"Accept":"application/zip,application/octet-stream,*/*","Referer":"https://ixdzs8.com/read/117771/"})
        with urllib.request.urlopen(req,timeout=55) as response:
            report["http_status"]=response.status
            report["actual_url"]=response.geturl()
            payload=response.read(MAX_BYTES+1)
            if len(payload)>MAX_BYTES: raise ValueError("Download exceeds 45MB safety limit")
        report["source_bytes"]=len(payload)
        report["source_sha256"]=hashlib.sha256(payload).hexdigest()
        if not zipfile.is_zipfile(io.BytesIO(payload)):
            report["first_bytes_hex"]=payload[:25].hex()
            raise ValueError("Server response is not a ZIP archive")
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            good=z.infolist()
            if len(good)>3500 or sum(x.file_size for x in good)>MAX_EXPANDED:
                raise ValueError("ZIP size or number of entries exceeds limit")
            bad=z.testzip()
            if bad: raise ValueError("ZIP CRC failed for " + bad)
            for entry in good:
                if entry.is_dir(): continue
                info=dict(name=entry.filename,bytes=entry.file_size)
                if entry.filename.lower().endswith((".txt",".text")):
                    b=z.read(entry)
                    text=None
                    for enc in ("utf-8-sig","utf-8","gb18030","big5","utf-16"):
                        try:
                            candidate=b.decode(enc)
                            if len(CHINESE_RE.findall(candidate))>=15:
                                text=candidate;info["encoding"]=enc;break
                        except (UnicodeError,LookupError):
                            continue
                    if text is not None:
                        info["cjk_chars"]=len(CHINESE_RE.findall(text))
                        info["detected_chapter_headings"]=len(HEAD_RE.findall(text))
                        # Full source material remains in artifact; not in public git.
                        if entry.file_size<=25_000_000:
                            safe_name=f"source_{len(report['text_files'])+1:03d}.txt"
                            (ROOT/safe_name).write_text(text,encoding="utf-8")
                            info["artifact_file"]=safe_name
                report["text_files"].append(info)
        # Keep exact original archive for downstream parsing.
        (ROOT/"downloaded_original.zip").write_bytes(payload)
        report["status"]="downloaded_unverified"
        report["translation_ready"]=0
    except Exception as err:
        report["status"]="failed"
        report["error"]=str(err)[:700]
        report["error_kind"]=type(err).__name__
    save_report(report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["status"]=="downloaded_unverified" else 2

if __name__=="__main__":
    raise SystemExit(main())
