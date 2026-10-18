"""Offline tests: czbooks novel 77297, metadata audit and safety gates."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "tools/novel_ingest/czbooks_77297.py"
spec = importlib.util.spec_from_file_location("czbooks_77297", source)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def fake_catalog(numbers):
    body = "".join(
        f'<a href="/n/s6d6hl/s{100000+n}?chapterNumber={n-1}">'
        f'{n}.第{n}章 测试标题{n}</a>'
        for n in numbers
    )
    return f"<title>{mod.TITLE}</title>" + body


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
    def html(self, url):
        return self.mapping[url]


class CzBooksTests(unittest.TestCase):
    def test_exact_safe_hosts_paths_and_queries(self):
        sample = "/n/s6d6hl/s660f1h8?chapterNumber=327"
        self.assertEqual(mod.safe_url(sample, chapter=True),
                         "https://czbooks.net" + sample)
        for bad in (
            "http://czbooks.net/n/s6d6hl/s660f1h8",
            "https://other.example/n/s6d6hl/s660f1h8",
            "https://czbooks.net.evil.example/n/s6d6hl/s660f1h8",
            "https://czbooks.net/n/not_this_book/s660f1h8",
            "https://czbooks.net/n/s6d6hl/s660f1h8?foo=bar",
            "https://czbooks.net/n/s6d6hl/s660f1h8?chapterNumber=327&x=1",
        ):
            with self.subTest(bad=bad), self.assertRaises(mod.AuditError):
                mod.safe_url(bad, chapter=True)

    def test_parse_catalog_and_no_invented_urls(self):
        catalog = mod.parse_catalog(fake_catalog([328, 329]) +
                                    '<a href="/n/different/one">第330章 外部</a>')
        self.assertEqual(list(catalog), [328, 329])
        self.assertTrue(catalog[328]["url"].endswith("chapterNumber=327"))
        self.assertEqual(catalog[328]["source_id"], "s100328")

    def test_reject_wrong_chapternumber_and_conflicts(self):
        bad_index = fake_catalog([328]) + (
            '<a href="/n/s6d6hl/sother?chapterNumber=20">第329章 不匹配</a>'
        )
        self.assertEqual(list(mod.parse_catalog(bad_index)), [328])
        conflict = fake_catalog([328]) + (
            '<a href="/n/s6d6hl/sdifferent?chapterNumber=327">第328章 重複</a>'
        )
        with self.assertRaises(mod.AuditError):
            mod.parse_catalog(conflict)

    def test_wrong_book(self):
        with self.assertRaises(mod.AuditError):
            mod.parse_catalog("<title>Completely different novel</title>")

    def test_validate_chapter_and_challenge(self):
        body = "这个章节的正文记载了人物之间的对话和事情的经过。" * 22
        html = (f"<title>{mod.TITLE} 第328章 起商队</title>"
                f"<h1>第328章</h1><div id='content'><p>{body}</p></div>")
        self.assertIn("事情", mod.parse_body(html, 328))
        with self.assertRaises(mod.AuditError):
            mod.parse_body(html, 329)
        with self.assertRaises(mod.AuditError):
            mod.parse_body(f"<title>{mod.TITLE} 第328章</title>"
                           "<div id='content'>验证码</div>", 328)

    def test_default_catalog_only_all_226(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "audit"
            receipt = mod.run(328, 553, folder, client=FakeClient({
                mod.ROOT: fake_catalog(range(328, 554)),
            }))
            self.assertEqual(receipt["catalog_count"], 226)
            self.assertEqual(receipt["catalog_gaps"], [])
            self.assertEqual(receipt["status"], "CATALOG_ONLY")
            self.assertEqual(receipt["raw_ok_count"], 0)
            self.assertFalse((folder / "raw").exists())
            self.assertEqual(len(json.loads(
                (folder / "manifest.json").read_text(encoding="utf-8"))["chapters"]), 226)

    def test_guards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                mod.run(327, 328, root / "outside")
            with self.assertRaises(ValueError):
                mod.run(328, 340, root / "many", save_text=True, rights_confirmed=True)
            with self.assertRaises(ValueError):
                mod.run(328, 328, root / "rights", save_text=True)
            with self.assertRaises(ValueError):
                mod.run(328, 328, root / "delay", delay=1.0)


if __name__ == "__main__":
    unittest.main()
