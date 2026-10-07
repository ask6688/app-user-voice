"""Snapshot synchronization uses local files and never runs either classifier."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("semantic_sync", ROOT / "scripts/sync_semantics.py")
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class SemanticSyncTests(unittest.TestCase):
    def make_sources(self, root):
        (root / "feedback_pipeline").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "feedback_pipeline/semantic_definitions.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
        (root / "tests/semantic_cases.json").write_text('[{"text":"视频播放很流畅"}]\n', encoding="utf-8")

    def test_check_does_not_write_and_write_syncs_both_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_sources(root)
            target, cases = root / "target.json", root / "other/cases.json"
            target.write_text('{"version":"old"}', encoding="utf-8")
            before = target.read_bytes(), target.stat().st_mtime_ns
            args = ["--target", str(target), "--cases-target", str(cases)]
            with patch.object(SYNC, "ROOT", root), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(SYNC.main(args), 1)
                self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
                self.assertFalse(cases.parent.exists())
                self.assertEqual(SYNC.main(args + ["--write"]), 0)
                self.assertEqual(SYNC.main(args), 0)
            self.assertEqual(target.read_bytes(), (root / "feedback_pipeline/semantic_definitions.json").read_bytes())
            self.assertEqual(cases.read_bytes(), (root / "tests/semantic_cases.json").read_bytes())
            self.assertIn("version=1.0.0 sha256=", output.getvalue())

    def test_invalid_or_missing_source_prevents_all_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_sources(root)
            target = root / "target.json"
            target.write_text('{"version":"old"}', encoding="utf-8")
            before = target.read_bytes()
            source = root / "tests/semantic_cases.json"
            for invalid in ("not-json", None):
                if invalid is None:
                    source.unlink()
                else:
                    source.write_text(invalid, encoding="utf-8")
                with patch.object(SYNC, "ROOT", root), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        SYNC.main(["--target", str(target), "--cases-target", str(root / "cases.json"), "--write"])
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(target.read_bytes(), before)
                self.assertFalse((root / "cases.json").exists())

    def test_isolated_checkout_runs_without_other_repository_or_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_sources(root)
            (root / "scripts").mkdir()
            script = root / "scripts/sync_semantics.py"
            shutil.copyfile(ROOT / "scripts/sync_semantics.py", script)
            target = root / "snapshot.json"
            command = [sys.executable, "-B", str(script), "--target", str(target)]
            written = subprocess.run(command + ["--write"], cwd=root.parent, capture_output=True, text=True)
            self.assertEqual(written.returncode, 0, written.stderr)
            checked = subprocess.run(command, cwd=root.parent, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(target.read_bytes(), (root / "feedback_pipeline/semantic_definitions.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
