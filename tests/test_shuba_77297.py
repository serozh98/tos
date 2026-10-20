"""Offline tests for public 69shuba 77297 catalog auditor."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

p = Path(__file__).resolve().parents[1] / "tools/novel_ingest/shuba_77297.py"
spec = importlib.util.spec_from_file_location("shuba_77297", p)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

def catalog(numbers):
    links = "".join(f'<a href="/txt/77297/{n+39000000}">第{n}章 章节{n}</a>' for n in numbers)
    return f"<title>{mod.TITLE}</title>{links}"

class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
    def html(self, url):
        return self.mapping[url]

class AuditorTests(unittest.TestCase):
    def test_safe_urls(self):
        self.assertEqual(mod.safe_url("/txt/77297/39337353", chapter=True),
                         "https://www.69shuba.com/txt/77297/39337353")
        for url in ("http://www.69shuba.com/txt/77297/1",
                    "https://evil.example/txt/77297/1",
                    "https://www.69shuba.com.evil.example/txt/77297/1",
                    "https://www.69shuba.com/txt/99999/1",
                    "https://www.69shuba.com/txt/77297/1?x=1"):
            with self.assertRaises(mod.AuditError):
                mod.safe_url(url, chapter=True)

    def test_catalog_identity_and_conflict(self):
        got = mod.parse_catalog(catalog([328, 329]) +
                                '<a href="/txt/99999/7">第330章 错误</a>')
        self.assertEqual(sorted(got), [328, 329])
        with self.assertRaises(mod.AuditError):
            mod.parse_catalog(catalog([328]) +
                              '<a href="/txt/77297/999991">第328章 冲突</a>')

    def test_wrong_book(self):
        with self.assertRaises(mod.AuditError):
            mod.parse_catalog('<title>Other book</title>')

    def test_body_and_wrong_number(self):
        body = "这是完整中文章节正文，包含人物对话和事件发展。" * 24
        html = f'<h1>第328章 组建商队</h1><div id="content">{body}</div>'
        self.assertIn("事件", mod.parse_body(html, 328))
        with self.assertRaises(mod.AuditError):
            mod.parse_body(html, 329)

    def test_blocked_body(self):
        with self.assertRaises(mod.AuditError):
            mod.parse_body('<h1>第328章</h1><div id="content">验证码</div>', 328)

    def test_catalog_only_no_raw(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)/"run"
            result = mod.run(328, 331, out, client=FakeClient({mod.BASE: catalog(range(328,554))}))
            self.assertEqual(result["status"], "CATALOG_ONLY")
            self.assertEqual(result["raw_ok_count"], 0)
            self.assertFalse((out/"raw").exists())
            self.assertEqual(len(json.loads((out/"manifest.json").read_text())["chapters"]), 4)

    def test_guard_limits(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                mod.run(327,328,Path(td)/"invalid")
            with self.assertRaises(ValueError):
                mod.run(328,339,Path(td)/"oversize",save_text=True,rights_confirmed=True)
            with self.assertRaises(ValueError):
                mod.run(328,328,Path(td)/"rights",save_text=True)

if __name__ == "__main__":
    unittest.main()
