"""Checks for the metadata-only CZBooks chapter body probe."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"tools"/"novel_ingest"))
import czbooks_77297 as shared  # noqa: E402
src=root/"tools"/"novel_ingest"/"czbooks_body_probe_77297.py"
spec=importlib.util.spec_from_file_location("czbooks_body_probe_77297",src)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)


def catalog():
    return f"<title>{shared.TITLE}</title>"+ "".join(
        f'<a href="/n/s6d6hl/s{100000+n}?chapterNumber={n-1}">第{n}章 测试标题</a>'
        for n in range(328,332)
    )


def page(n):
    body="一个已经公布的普通中文故事正文。"*40
    return f'<title>{shared.TITLE} 第{n}章 标题</title><h1>第{n}章</h1><div id="content">{body}</div>'


class Fake:
    def __init__(self,mapping):
        self.mapping=mapping
    def html(self,url):
        return self.mapping[url]


class Tests(unittest.TestCase):
    def test_valid_probe_records_hash_without_raw(self):
        from tempfile import TemporaryDirectory
        chapters=shared.parse_catalog(catalog())
        client=Fake({shared.ROOT:catalog(),**{chapters[n]["url"]:page(n) for n in chapters}})
        with TemporaryDirectory() as td:
            out=Path(td)/"new"
            result=module.run(328,331,out,client=client)
            self.assertEqual(result["status"],"PROBE_PASS")
            self.assertEqual(result["readable"],4)
            self.assertEqual(result["full_chapter_bodies_stored"],0)
            self.assertFalse((out/"raw").exists())
            self.assertEqual(len(json.loads((out/"manifest.json").read_text())["chapters"]),4)
            for row in result["chapters"]:
                self.assertEqual(row["status"],"body_validated")
                self.assertEqual(len(row["utf8_sha256"]),64)
                self.assertGreater(row["cjk_chars"],180)
            for artifact in ("summary.md","manifest.json","translation_queue.csv"):
                self.assertNotIn("一个已经公布", (out/artifact).read_text(encoding="utf-8-sig"))

    def test_blocked_first_chapter_does_not_retry(self):
        catalog_items=shared.parse_catalog(catalog())
        bad=f"<title>{shared.TITLE} 第328章</title><div id='content'>验证码</div>"
        fake=Fake({shared.ROOT:catalog(),catalog_items[328]["url"]:bad})
        with tempfile.TemporaryDirectory() as td:
            r=module.run(328,331,Path(td)/"run",client=fake)
            self.assertEqual(r["status"],"PROBE_PARTIAL")
            self.assertEqual(r["chapters"][0]["status"],"blocked")
            self.assertEqual([z["status"] for z in r["chapters"][1:]],["not_checked"]*3)

    def test_range_guard(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                module.run(327,554,Path(td)/"invalid")


if __name__=="__main__":
    unittest.main()
