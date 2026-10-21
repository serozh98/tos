"""Tests run offline using Chinese HTML fixtures."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

src = Path(__file__).resolve().parents[1] / "tools" / "novel_ingest" / "shuhaige_collect.py"
spec = importlib.util.spec_from_file_location("shuhaige_collect", src)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

BOOK_HTML = f'<title>{mod.TITLE}</title>'
BODY = "唐安忽然惊醒，发现自己来到了一个全然陌生的修仙世界。" * 24


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
    def html(self, url):
        return self.mapping[url]


class Tests(unittest.TestCase):
    def test_safe_urls(self):
        self.assertEqual(mod.safe_url("/360588/101.html", chapter=True), mod.ROOT + "101.html")
        for url in ("http://m.shuhaige.net/360588/101.html",
                    "https://evil.test/360588/101.html",
                    "https://m.shuhaige.net.evil.test/360588/101.html",
                    "https://m.shuhaige.net/360589/101.html"):
            with self.assertRaises(mod.CollectionError):
                mod.safe_url(url, chapter=True)

    def test_parse_catalog_and_reject_other_book(self):
        html = BOOK_HTML + ('<a href="/360588/101.html">第1章 第一章</a>'
                            '<a href="/360589/102.html">第2章 别人的书</a>'
                            '<a href="/360588/index_2.html">下一页</a>')
        chapters, pages = mod.catalog_page(html)
        self.assertEqual(list(chapters), [1])
        self.assertEqual(pages, [mod.ROOT + "index_2.html"])

    def test_catalog_conflict(self):
        html = BOOK_HTML + ('<a href="/360588/101.html">第1章 一</a>'
                            '<a href="/360588/202.html">第1章 二</a>')
        with self.assertRaises(mod.CollectionError):
            mod.catalog_page(html)

    def test_chapter_body_and_wrong_number(self):
        html = BOOK_HTML + f'<h1>第1章 初见</h1><div class="content"><p>{BODY}</p></div>'
        body, more = mod.chapter_page(html, 1, "101")
        self.assertIn("唐安", body)
        self.assertIsNone(more)
        with self.assertRaises(mod.CollectionError):
            mod.chapter_page(html, 2, "101")

    def test_catalog_only_is_not_raw(self):
        catalog = BOOK_HTML + '<a href="/360588/101.html">第1章 初见</a>'
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            result = mod.run(1, 1, out, client=FakeClient({mod.ROOT: catalog}))
            self.assertEqual(result["status"], "CATALOG_ONLY")
            self.assertEqual(result["raw_ok_count"], 0)
            self.assertFalse((out / "raw").exists())

    def test_raw_and_manifest(self):
        catalog = BOOK_HTML + '<a href="/360588/101.html">第1章 初见</a>'
        chapter = BOOK_HTML + f'<h1>第1章 初见</h1><div class="content"><p>{BODY}</p></div>'
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            result = mod.run(1, 1, out, save_text=True, rights_confirmed=True,
                             client=FakeClient({mod.ROOT: catalog, mod.ROOT + "101.html": chapter}))
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["raw_ok_count"], 1)
            raw = (out / "raw/0001.txt").read_text(encoding="utf-8")
            self.assertIn("唐安", raw)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["notion_sync"], "not_performed")
            self.assertIn("sha256", manifest["chapters"][0])

    def test_partial(self):
        catalog = BOOK_HTML + '<a href="/360588/101.html">第1章 初见</a>'
        chapter = BOOK_HTML + f'<h1>第1章 初见</h1><div class="content"><p>{BODY}</p></div>'
        with tempfile.TemporaryDirectory() as tmp:
            result = mod.run(1, 2, Path(tmp) / "run", save_text=True, rights_confirmed=True,
                             client=FakeClient({mod.ROOT: catalog, mod.ROOT + "101.html": chapter}))
            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(result["chapters"][-1]["status"], "missing_from_catalog")

    def test_remote_disconnect_has_explicit_network_status(self):
        class Disconnected:
            def html(self, url):
                raise mod.CollectionError("network_error", "RemoteDisconnected")

        with tempfile.TemporaryDirectory() as tmp:
            result = mod.run(1, 5, Path(tmp) / "run", client=Disconnected())
            self.assertEqual(result["status"], "NETWORK_ERROR")
            self.assertEqual(result["raw_ok_count"], 0)
            self.assertEqual(result["error"]["code"], "network_error")

    def test_rights_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                mod.run(1, 11, Path(tmp) / "one")
            with self.assertRaises(ValueError):
                mod.run(1, 1, Path(tmp) / "two", save_text=True)


if __name__ == "__main__":
    unittest.main()
