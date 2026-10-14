"""Offline tests for respectful sequential MTL collection."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "novel_ingest"))
import fanmtl_collect as c
import mtl_bulk_1239043 as bulk

ROOT_URL = c.catalog_url("wuxiabox")
LINE = "Cross Kahn was sailing the Grand Line, and Nico Robin was reading beside him. "
BODY = LINE * 15
CATALOG = f"""<html><h1>{c.ENGLISH_TITLE}</h1><div>{c.CHINESE_TITLE}</div>
<p>234 Chapters</p><a href="/novel/{c.SLUG}_1.html">Chapter 1: West Blue</a>
<a href="/novel/{c.SLUG}_55.html">Chapter 55: Trial</a></html>"""


def html_chapter(number):
    return f"""<html><title>Chapter {number}: Trial - {c.ENGLISH_TITLE}</title>
    <h1>{c.ENGLISH_TITLE}</h1><h2>Chapter {number}: Trial</h2>
    <div class="chapter-content"><p>{BODY}</p></div></html>"""


def request(start=55, end=55, delay=3):
    return {"book": c.CHINESE_TITLE, "source": "wuxiabox", "start": start, "end": end,
            "delay_seconds": delay, "rights_confirmed": True}


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def html(self, url):
        self.calls.append(url)
        value = self.mapping[url]
        if isinstance(value, Exception):
            raise value
        return value


class TestBulk(unittest.TestCase):
    def test_range_and_rights(self):
        for bad in (
            dict(request(), rights_confirmed=False),
            dict(request(), source="fanmtl"),
            dict(request(), delay_seconds=2),
            request(54, 55),
            request(55, 235),
            dict(request(), book="wrong book"),
            dict(request(), source="wuxiabox", start=True),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bulk.validate_request(bad)

    def test_one_chapter_is_saved_and_has_hash(self):
        url = c.chapter_url(55, source="wuxiabox")
        client = FakeClient({ROOT_URL: CATALOG, url: html_chapter(55)})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            result = bulk.run_collection(request(), out, client=client)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["saved_count"], 1)
            content = (out / "mtl_en/0055.txt").read_bytes()
            self.assertEqual(result["chapters"][0]["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(client.calls, [ROOT_URL, url])
            self.assertIn("Chapter 55: SAVED", (out / "summary.md").read_text())

    def test_single_catalog_fetch_multiple_batches(self):
        urls = {ROOT_URL: CATALOG}
        for number in range(55, 66):
            urls[c.chapter_url(number, source="wuxiabox")] = html_chapter(number)
        client = FakeClient(urls)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "batch"
            result = bulk.run_collection(request(55, 65), out, client=client)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["saved_count"], 11)
            self.assertEqual(len(result["batches"]), 2)
            self.assertEqual(client.calls.count(ROOT_URL), 1)
            self.assertEqual(len(list((out / "mtl_en").glob("*.txt"))), 11)
            self.assertEqual(json.loads((out / "manifest.json").read_text())["saved_count"], 11)

    def test_blocked_stops_without_retry(self):
        url55 = c.chapter_url(55, source="wuxiabox")
        url56 = c.chapter_url(56, source="wuxiabox")
        client = FakeClient({
            ROOT_URL: CATALOG,
            url55: html_chapter(55),
            url56: c.CollectorError("BLOCKED", "HTTP 429")
        })
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "partial"
            result = bulk.run_collection(request(55, 75), out, client=client)
            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(result["saved_count"], 1)
            self.assertEqual([r["status"] for r in result["chapters"]], ["SAVED", "BLOCKED"])
            self.assertEqual(client.calls, [ROOT_URL, url55, url56])
            self.assertTrue((out / "mtl_en/0055.txt").exists())
            self.assertFalse((out / "mtl_en/0057.txt").exists())

    def test_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "manifest.json").write_text("important")
            with self.assertRaises(ValueError):
                bulk.run_collection(request(), out, client=FakeClient({}))
            self.assertEqual((out / "manifest.json").read_text(), "important")


if __name__ == "__main__":
    unittest.main()
