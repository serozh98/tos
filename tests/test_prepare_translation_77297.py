"""Validate the 226-chapter translation handoff manifest without text copying."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

src=Path(__file__).resolve().parents[1]/"tools"/"novel_ingest"/"prepare_translation_77297.py"
spec=importlib.util.spec_from_file_location("prepare_translation_77297",src)
mod=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=mod
spec.loader.exec_module(mod)


def make_manifest(path):
    payload={"chapters":[{"chapter":n,"zh_title":f"标题{n}",
                          "source_url":f"https://czbooks.net/n/s6d6hl/test{n}",
                          "notion_url":f"https://notion.so/page{n}"}
                         for n in range(328,554)]}
    path.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")


class Tests(unittest.TestCase):
    def test_plan_only_all_226_no_body_files(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            make_manifest(root/"manifest.json")
            result=mod.prepare(root/"manifest.json",root/"raw",root/"out")
            self.assertEqual(len(result["chapters"]),226)
            self.assertEqual(len(result["batches"]),57)
            self.assertEqual(result["ready_batches"],0)
            self.assertEqual(result["fully_verified_chapters"],0)
            self.assertFalse(list((root/"out").glob("batch_*.txt")))

    def test_authorized_four_raw_ready(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);make_manifest(root/"manifest.json")
            (root/"raw").mkdir()
            for n in range(328,332):
                (root/"raw"/f"{n:04d}.txt").write_text(f"这是一则章节故事编号{n}。"*35,encoding="utf-8")
            result=mod.prepare(root/"manifest.json",root/"raw",root/"out",authorized_input=True)
            self.assertEqual(result["ready_batches"],1)
            self.assertEqual(result["fully_verified_chapters"],4)
            self.assertTrue((root/"out"/"batch_0328_0331.txt").exists())
            self.assertFalse((root/"out"/"batch_0332_0335.txt").exists())

    def test_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);make_manifest(root/"manifest.json")
            (root/"raw").mkdir()
            for n in range(328,332):
                (root/"raw"/f"{n:04d}.txt").write_text("这是一份重复的中国故事文字。"*35,encoding="utf-8")
            result=mod.prepare(root/"manifest.json",root/"raw",root/"out",authorized_input=True)
            self.assertEqual(result["ready_batches"],0)

    def test_gap_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);make_manifest(root/"manifest.json")
            payload=json.loads((root/"manifest.json").read_text())
            payload["chapters"].pop()
            (root/"manifest.json").write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                mod.prepare(root/"manifest.json",root/"raw",root/"out")


if __name__=="__main__":
    unittest.main()
