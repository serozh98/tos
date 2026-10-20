import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "twbook" / "assemble_1105578080.py"
SPEC = importlib.util.spec_from_file_location("twbook_assemble_1105578080", PATH)
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)

class AssembleTests(unittest.TestCase):
    def test_all_valid_two_chapters(self):
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            shard = root / "parts" / "a"
            shard.mkdir(parents=True)
            (shard / "chapters").mkdir()
            records = []
            for n in (1, 2):
                body = "漢字" * 210
                title = f"第{n}章 測試"
                (shard / "chapters" / f"{n:03d}.txt").write_text(title + "\n\n" + body + "\n", encoding="utf-8")
                records.append({
                    "position":n, "printed_number":n, "title":title,
                    "url":f"https://www.twbook.cc/1105578080/{n}.html",
                    "status":"ok", "characters":len(body),
                    "chinese_characters":len(body),
                    "sha256":sha256(body.encode()).hexdigest(),
                    "filename":f"chapters/{n:03d}.txt"
                })
            (shard / "manifest.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8"
            )
            report = MOD.assemble(root/"parts", root/"final", expected=2)
            self.assertEqual(report["ready_for_translation"],2)
            self.assertEqual(report["pending_positions"],[])
            self.assertEqual(len(list((root/"final"/"chapters").glob("*.txt"))), 2)
            self.assertIn("第1章", (root/"final"/"all_chinese_for_translation.txt").read_text())

    def test_missing_not_marked_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            shard = root/"shard"
            shard.mkdir()
            (shard/"manifest.jsonl").write_text("", encoding="utf-8")
            report=MOD.assemble(shard, root/"final", expected=2)
            self.assertEqual(report["ready_for_translation"],0)
            self.assertEqual(report["pending_positions"],[1,2])

    def test_rejects_tampering(self):
        body="中文"*210
        import hashlib
        row={"title":"第1章", "characters":len(body),"chinese_characters":len(body),
             "sha256":hashlib.sha256(body.encode()).hexdigest()}
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            MOD.validate_raw("第1章\n\n" + "漢文"*210 + "\n",row)

if __name__=="__main__":
    unittest.main()
