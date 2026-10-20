"""Offline tests for authorized CZBooks RAW collection and batch staging."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"tools"/"novel_ingest"))
import czbooks_77297 as cz

spec=importlib.util.spec_from_file_location(
    "czbooks_authorized_raw_77297",
    root/"tools"/"novel_ingest"/"czbooks_authorized_raw_77297.py")
collector=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=collector
spec.loader.exec_module(collector)


def fake_book(numbers):
    return (f"<title>{cz.TITLE}</title>"+
            "".join(f'<a href="/n/s6d6hl/s{100000+n}?chapterNumber={n-1}">'
                    f'第{n}章 测试标题{n}</a>' for n in numbers))


def url(n):
    return f"https://czbooks.net/n/s6d6hl/s{100000+n}?chapterNumber={n-1}"


def fake_chapter(n):
    text=(f"这是第{n}章的中文故事正文，其中包含人物交流和事件发展。"*33)
    return (f"<title>{cz.TITLE} 第{n}章</title>"
            f"<h1>第{n}章 测试</h1><div id='content'>{text}</div>")


class FakeClient:
    def __init__(self, mapping):
        self.mapping=mapping
        self.requests=[]
    def html(self, href):
        self.requests.append(href)
        return self.mapping[href]


def manifest(path):
    path.write_text(json.dumps({"chapters":[{"number":n,"url":url(n)}
                                          for n in range(328,554)]}),
                    encoding="utf-8")


class Tests(unittest.TestCase):
    def test_rights_mandatory(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td)
            manifest(home/"catalog.json")
            with self.assertRaises(ValueError):
                collector.run(328,331,catalog_file=home/"catalog.json",
                              output=home/"out",rights_confirmed=False)

    def test_four_distinct_chapters_persist_and_batch(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td)
            manifest(home/"catalog.json")
            client=FakeClient({cz.ROOT:fake_book(range(328,332)),
                               **{url(n):fake_chapter(n) for n in range(328,332)}})
            result=collector.run(328,331,catalog_file=home/"catalog.json",
                                 output=home/"out",rights_confirmed=True,
                                 client=client)
            self.assertEqual(result["status"],"PASS")
            self.assertEqual(result["verified_raw"],4)
            self.assertEqual(result["ready_translation_batches"],1)
            self.assertEqual(len(list((home/"out"/"raw").glob("*.txt"))),4)
            self.assertTrue((home/"out"/"batches"/"0328-0331.txt").exists())
            self.assertIn("第328章", (home/"out"/"batches"/"0328-0331.txt").read_text(encoding="utf-8"))
            self.assertEqual(len(client.requests),5)
            self.assertEqual(result["notion_sync"],"not_performed")

    def test_first_block_stops_without_bypass(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td)
            manifest(home/"catalog.json")
            client=FakeClient({cz.ROOT:fake_book(range(328,332)),
                               url(328):f"<title>{cz.TITLE} 第328章</title>"
                               "<h1>第328章</h1><div id='content'>验证码</div>"})
            result=collector.run(328,331,catalog_file=home/"catalog.json",
                                 output=home/"out",rights_confirmed=True,
                                 client=client)
            self.assertEqual(result["status"],"BLOCKED")
            self.assertEqual(result["verified_raw"],0)
            self.assertEqual(len(client.requests),2)

    def test_catalog_mismatch_rejects_before_fetching_text(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td)
            manifest(home/"catalog.json")
            client=FakeClient({cz.ROOT:fake_book([328,329,330])})
            result=collector.run(328,331,catalog_file=home/"catalog.json",
                                 output=home/"out",rights_confirmed=True,
                                 client=client)
            self.assertEqual(result["status"],"CATALOG_MISMATCH")
            self.assertEqual(len(client.requests),1)

    def test_unsafe_delay(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td)
            manifest(home/"catalog.json")
            with self.assertRaises(ValueError):
                collector.run(328,331,catalog_file=home/"catalog.json",
                              output=home/"out",rights_confirmed=True,delay=1.0)


if __name__=="__main__":
    unittest.main()
