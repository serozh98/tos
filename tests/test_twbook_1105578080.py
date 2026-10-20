import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1] / "tools" / "twbook"
SPEC = importlib.util.spec_from_file_location("twbook_1105578080", HERE / "twbook_1105578080.py")
tw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tw
SPEC.loader.exec_module(tw)

class TWBookTests(unittest.TestCase):
    def test_catalog_preserves_repeated_printed_number(self):
        html = '<a href="/1105578080/1.html">第1章 A</a><a href="/1105578080/2.html">第186章 B</a><a href="/1105578080/3.html">第186章 B</a>'
        items = tw.catalog_entries(html)
        self.assertEqual([i.position for i in items], [1,2,3])
        self.assertEqual([i.printed_number for i in items], [1,186,186])

    def test_catalog_gap_fails(self):
        with self.assertRaisesRegex(ValueError,"missing"):
            tw.catalog_entries('<a href="/1105578080/1.html">A</a><a href="/1105578080/3.html">B</a>')

    def test_clean_ads(self):
        title = '<div class="chapter-content"><h1>第1章 A</h1><div class="content"><p>正文開始</p><div class="adBlock">AD</div><p>下一段</p></div></div>'
        body = tw.body_from_html(title)
        self.assertIn("正文開始",body)
        self.assertNotIn("AD",body)

    def test_merge_rejects_masked_and_conflict(self):
        self.assertEqual(tw.merge_variants(["甲가乙","甲大乙"]),"甲大乙")
        with self.assertRaisesRegex(ValueError,"different visible"):
            tw.merge_variants(["甲大乙","甲小乙"])

    def test_url_restricted(self):
        self.assertTrue(tw.allowed("https://www.twbook.cc/1105578080/1.html"))
        self.assertFalse(tw.allowed("https://other.example/1105578080/1.html"))
        self.assertFalse(tw.allowed("http://www.twbook.cc/1105578080/1.html"))

    def test_delay_floor(self):
        with self.assertRaisesRegex(ValueError, "minimum delay"):
            tw.PublicClient(1.9,1)

    def test_text_requires_rights_flag(self):
        args=SimpleNamespace(start=1,end=4,delay=2,timeout=1,max_renderings=1,out_dir="/tmp/unused",save_text=True,rights_confirmed=False,catalog_only=False)
        with self.assertRaisesRegex(ValueError,"rights-confirmed"):
            tw.run(args)

    def test_block_stops(self):
        fake = SimpleNamespace(get=lambda url: (_ for _ in ()).throw(PermissionError("HTTP 403")))
        receipt = tw.probe_chapter(fake,tw.Entry(1,1,"第一章","https://www.twbook.cc/1105578080/1.html"),Path("/tmp"),1,False)
        self.assertEqual(receipt.status,"blocked")

if __name__=="__main__":
    unittest.main()
