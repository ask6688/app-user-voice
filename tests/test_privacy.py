import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("publication_check", Path(__file__).resolve().parents[1] / "scripts" / "check_public_candidate.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class PublicationPrivacyTests(unittest.TestCase):
    def test_terms_and_environment_references_are_safe(self):
        text = '手机号、邮箱、token 字段必须保护。\napi_key = os.getenv("DEMO_API_KEY")\nwebhook: ${DEMO_WEBHOOK}\nhttps://example.invalid/reviews/1'
        self.assertEqual(CHECK.scan_text("example.md", text), [])
        self.assertEqual(CHECK.scan_text(".gitignore", ".browser" + "/"), [])
        self.assertEqual(CHECK.scan_text("tests/mock.py", "http://127.0.0.1:1234/\npassword: 'fixture-password'"), [])

    def test_values_are_detected_without_echoing_values(self):
        synthetic_value = "A" * 32
        text = "api_" + "key = '" + synthetic_value + "'\n" + "person" + "@" + "example.com\n" + "138" + "00000000\n" + "/" + "Users" + "/demo/private\n" + "https://" + "jira.internal" + "/ticket/1"
        findings = CHECK.scan_text("config.txt", text)
        self.assertEqual({row.category for row in findings}, {"credential-literal", "email-value", "phone-value", "absolute-local-path", "private-url"})
        self.assertNotIn(synthetic_value, repr(findings))
        self.assertNotIn("person" + "@", repr(findings))

    def test_prefixed_unquoted_environment_values_are_detected(self):
        name = "DEMO" + "_" + "ACCESS" + "_" + "TOKEN"
        text = name + "=" + ("Z" * 32)
        findings = CHECK.scan_text(".env.example", text)
        self.assertEqual({row.category for row in findings}, {"credential-literal", "credential-value"})

    def test_explicit_manifest_and_runtime_exclusion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\nREADME.md\n", encoding="utf-8")
            (root / "README.md").write_text("Synthetic public example.", encoding="utf-8")
            (root / "outputs").mkdir()
            (root / "outputs" / "local.json").write_text("Local run output.", encoding="utf-8")
            self.assertEqual(CHECK.scan_candidate(root), [])
            (root / "unlisted.csv").write_text("text\nexample\n", encoding="utf-8")
            self.assertIn("unlisted-file", {row.category for row in CHECK.scan_candidate(root)})

    def test_private_and_traversal_manifest_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\n../outside.txt\n.env\n" + ".browser" + "/state.json\n", encoding="utf-8")
            categories = {row.category for row in CHECK.scan_candidate(root)}
            self.assertIn("invalid-manifest-path", categories)
            self.assertIn("private-manifest-entry", categories)

    def test_symlinks_are_rejected_without_reading_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\nlink.txt\n", encoding="utf-8")
            (root / "link.txt").symlink_to(root / "missing-secret.txt")
            self.assertIn("publication-symlink", {row.category for row in CHECK.scan_candidate(root)})

    def test_local_settings_are_excluded_but_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\nLICENSE\n", encoding="utf-8")
            (root / "LICENSE").write_text("License choice pending.", encoding="utf-8")
            for name in [".env.local", "config.local.yaml", "rules.local.yml", "collector.local.json"]:
                (root / name).write_text("Private local settings.", encoding="utf-8")
            self.assertEqual(CHECK.scan_candidate(root), [])
            (root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\n.env.local\nconfig.local.yaml\n", encoding="utf-8")
            self.assertIn("private-manifest-entry", {row.category for row in CHECK.scan_candidate(root)})

    def test_only_reviewed_image_paths_without_metadata_are_allowed(self):
        import struct
        import zlib

        def chunk(kind, data=b""):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "docs/assets/report-overview.png"
            target.parent.mkdir(parents=True)
            (root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\ndocs/assets/report-overview.png\n", encoding="utf-8")
            target.write_bytes(png)
            self.assertEqual(CHECK.scan_candidate(root), [])
            target.write_bytes(png[:-12] + chunk(b"tEXt", b"Comment\x00private metadata") + chunk(b"IEND"))
            self.assertIn("invalid-or-metadata-public-image", {row.category for row in CHECK.scan_candidate(root)})
            target.write_bytes(png + b"private trailing text")
            self.assertIn("invalid-or-metadata-public-image", {row.category for row in CHECK.scan_candidate(root)})
            target.unlink()
            other = root / "unreviewed.png"
            other.write_bytes(png)
            (root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\nunreviewed.png\n", encoding="utf-8")
            self.assertIn("unsupported-public-file", {row.category for row in CHECK.scan_candidate(root)})


if __name__ == "__main__":
    unittest.main()
