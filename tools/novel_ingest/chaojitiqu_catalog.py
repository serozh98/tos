#!/usr/bin/env python3
"""Сверка оглавления 超级提取: только общедоступные метаданные, без скачивания книги."""
from __future__ import annotations
import collections
import csv
import html
from html.parser import HTMLParser
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

INDEX = "https://ixdzs8.com/read/117771/"
OUT = pathlib.Path("data/novels/chaojitiqu")
UA = "Mozilla/5.0 (compatible; NovelCatalogAudit/1.0; metadata-only)"
CHAPTER_RE = re.compile(r"^第\s*([零〇一二三四五六七八九十百千万0-9]+)\s*章", re.UNICODE)
PATH_RE = re.compile(r"^/read/117771/p(\d+)\.html$", re.I)
VALUES = dict(zip("零〇一二三四五六七八九", [0,0,1,2,3,4,5,6,7,8,9]))

def cn_number(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if not s or any(c not in VALUES and c not in "十百千" for c in s):
        return None
    if all(c in VALUES for c in s):
        return int("".join(str(VALUES[c]) for c in s))
    total, part, digit = 0, 0, 0
    for c in s:
        if c in VALUES:
            digit = VALUES[c]
        elif c in "十百千":
            unit = {"十":10,"百":100,"千":1000}[c]
            part += (digit or 1) * unit
            digit = 0
    total += part + digit
    return total

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = None
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        u = dict(attrs).get("href", "")
        path = urllib.parse.urlparse(urllib.parse.urljoin(INDEX, u)).path
        m = PATH_RE.fullmatch(path)
        if m:
            self.active = {"position": int(m.group(1)), "url": urllib.parse.urljoin(INDEX,u), "text": []}
    def handle_data(self, data):
        if self.active:
            self.active["text"].append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self.active:
            self.active["title"] = html.unescape("".join(self.active.pop("text"))).strip()
            self.links.append(self.active)
            self.active = None

def retrieve(url: str) -> tuple[int,str,str]:
    request = urllib.request.Request(url, headers={"User-Agent":UA,"Accept-Language":"zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=25) as r:
        body = r.read(2_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
        return r.status, body.decode(charset,errors="replace"), r.geturl()

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    summary = {"book":"超级提取","source":INDEX,"metadata_only":True,"rights_notice":"No full copyrighted chapters downloaded; access controls never bypassed.","status":"not_run"}
    try:
        status, page, final_url = retrieve(INDEX)
        summary.update({"status":"source_opened","http_status":status,"final_url":final_url,"html_length":len(page)})
        if any(x in page for x in ["正在进行安全验证","正在验证浏览器","Just a moment...","Cloudflare"]):
            raise ValueError("Catalog blocked by verification; no bypass attempted")
        p = LinkParser()
        p.feed(page)
        positions = {}
        for x in p.links:
            if x["title"] and x["position"] not in positions:
                positions[x["position"]] = x
        if len(positions)<250:
            raise ValueError(f"Only {len(positions)} catalog links parsed; refusing partial overwrite")
        all_entries = []
        for seq, (pos, item) in enumerate(sorted(positions.items()),start=1):
            title = re.sub(r"\s+"," ",item["title"])
            match = CHAPTER_RE.search(title)
            number = cn_number(match.group(1)) if match else None
            kind = "chapter" if number is not None else "notice_or_ending"
            all_entries.append({"sequence":seq,"site_position":pos,"chapter_number":number,"chinese_title":title,
                                "source_url":item["url"],"kind":kind,"raw_verified":False,"translated":False})
        chapter_entries = [e for e in all_entries if e["kind"]=="chapter"]
        grouped = collections.defaultdict(list)
        for x in chapter_entries: grouped[x["chapter_number"]].append(x)
        duplicates = {str(n):[x["site_position"] for x in a] for n,a in grouped.items() if len(a)>1}
        highest=max(grouped,default=0)
        missing=[n for n in range(1,highest+1) if n not in grouped]
        jump=[{"previous":a["chapter_number"],"next":b["chapter_number"],"site_positions":[a["site_position"],b["site_position"]]} for a,b in zip(chapter_entries,chapter_entries[1:]) if b["chapter_number"] != a["chapter_number"]+1]
        summary.update({"status":"catalog_extracted","unique_urls":len(all_entries),"numbered_entries":len(chapter_entries),
                        "other_entries":len(all_entries)-len(chapter_entries),"max_number":highest,
                        "missing_numbers":missing,"repeated_numbers":duplicates,
                        "sequence_anomalies":jump,"notes":[x for x in all_entries if x["kind"]!="chapter"]})
        with (OUT/"index.json").open("w",encoding="utf-8") as f:
            json.dump(all_entries,f,ensure_ascii=False,indent=2)
        with (OUT/"index.tsv").open("w",encoding="utf-8",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=list(all_entries[0]),delimiter="\t")
            writer.writeheader()
            writer.writerows(all_entries)
        for pos in (2,150,319):
            try:
                code, body, _ = retrieve(f"https://ixdzs8.com/read/117771/p{pos}.html")
                challenged = any(x in body for x in ["正在进行安全验证","正在验证浏览器","Just a moment..."])
                summary.setdefault("sample_access",[]).append({"site_position":pos,"http_status":code,"html_length":len(body),"challenge":challenged,"full_raw_verified":False})
            except Exception as exc:
                summary.setdefault("sample_access",[]).append({"site_position":pos,"error":str(exc)[:250],"full_raw_verified":False})
    except Exception as exc:
        summary.update({"status":"blocked_or_incomplete","error":str(exc)})
    with (OUT/"audit.json").open("w",encoding="utf-8") as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)
    print("AUDIT_SUMMARY "+json.dumps({k:v for k,v in summary.items() if k not in ("sequence_anomalies","notes")},ensure_ascii=False))
    return 0 if summary["status"]=="catalog_extracted" else 2
if __name__=="__main__":
    sys.exit(main())
