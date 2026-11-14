"""Offline tests for the manifest generator."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_manifest.py"
spec = importlib.util.spec_from_file_location("generate_manifest", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def release(tag="v1.2.3", name="tool-win-x64.zip", digest=None):
    asset = {"name": name, "browser_download_url": f"https://example.com/{name}"}
    if digest:
        asset["digest"] = f"sha256:{digest}"
    return {"tag_name": tag, "assets": [asset]}


class ManifestTests(unittest.TestCase):
    def test_asset_selection_prefers_windows_over_generic_and_ignores_other_platforms(self):
        assets = [
            {"name": "tool-linux-x64.zip"},
            {"name": "tool.zip"},
            {"name": "tool-win-x64.zip"},
            {"name": "tool-win-x64-setup.exe"},
            {"name": "tool-macos.zip"},
        ]
        self.assertEqual(module.pick_best_asset(assets)["name"], "tool-win-x64.zip")
        self.assertEqual(module.score_asset("tool-setup.exe"), -1)
        self.assertEqual(module.score_asset("tool-arm64.zip"), -1)

    def test_github_url_and_name_collision(self):
        self.assertEqual(module.parse_repo("https://github.com/Some/Tool.git"), ("Some/Tool", "tool"))
        with self.assertRaises(ValueError):
            module.parse_repo("https://evil.example.com/Some/Tool")

    def test_digest_from_github_metadata_avoids_download(self):
        with patch.object(module, "api_request", return_value=release(digest="a" * 64)), patch.object(
            module, "sha256_of_url", side_effect=AssertionError("should not download")
        ):
            manifest = module.build_manifest("test/tool")
        self.assertEqual(manifest["hash"], "sha256:" + "a" * 64)
        self.assertNotIn("autoupdate", manifest)
        self.assertNotIn("extract_dir", manifest)

    def test_same_version_and_url_uses_existing_hash(self):
        previous = {
            "version": "1.2.3",
            "url": "https://example.com/tool-win-x64.zip",
            "hash": "sha256:" + "c" * 64,
        }
        with patch.object(module, "api_request", return_value=release()), patch.object(
            module, "sha256_of_url", side_effect=AssertionError("should not download")
        ):
            manifest = module.build_manifest("test/tool", previous)
        self.assertEqual(manifest["hash"], previous["hash"])

    def test_new_version_without_digest_downloads_once(self):
        with patch.object(module, "api_request", return_value=release(tag="v2.0")), patch.object(
            module, "sha256_of_url", return_value="b" * 64
        ) as download:
            manifest = module.build_manifest("test/tool")
        self.assertEqual(manifest["version"], "2.0")
        download.assert_called_once()

    def test_failed_request_does_not_delete_existing_manifest(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "BUCKET_DIR", Path(directory)):
            old = Path(directory) / "obsolete.json"
            old.write_text(json.dumps({"description": "Auto-managed: old/repo"}))
            with patch.dict(os.environ, {"MANAGED_REPOS": "https://github.com/test/tool"}), patch.object(
                module, "api_request", side_effect=RuntimeError("rate limited")
            ):
                self.assertEqual(module.main(), 1)
            self.assertTrue(old.exists())

    def test_empty_configuration_preserves_manifests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "BUCKET_DIR", Path(directory)):
            existing = Path(directory) / "app.json"
            existing.write_text("{}")
            with patch.dict(os.environ, {"MANAGED_REPOS": ""}):
                self.assertEqual(module.main(), 0)
            self.assertTrue(existing.exists())

    def test_cleanup_only_removes_generated_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "BUCKET_DIR", Path(directory)):
            generated = Path(directory) / "obsolete.json"
            manual = Path(directory) / "manual.json"
            generated.write_text(json.dumps({"description": "Auto-managed: old/repo"}))
            manual.write_text(json.dumps({"description": "Hand maintained"}))
            with patch.dict(os.environ, {"MANAGED_REPOS": "https://github.com/test/tool"}), patch.object(
                module, "api_request", return_value=release(digest="d" * 64)
            ):
                self.assertEqual(module.main(), 0)
            self.assertFalse(generated.exists())
            self.assertTrue(manual.exists())
            self.assertTrue((Path(directory) / "tool.json").exists())

    def test_manual_empty_manifest_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "BUCKET_DIR", Path(directory)):
            manual = Path(directory) / "tool.json"
            manual.write_text("{}")
            with patch.dict(os.environ, {"MANAGED_REPOS": "https://github.com/test/tool"}):
                self.assertEqual(module.main(), 1)
            self.assertEqual(manual.read_text(), "{}")

    def test_duplicate_repository_names_abort_without_changes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "BUCKET_DIR", Path(directory)):
            with patch.dict(os.environ, {
                "MANAGED_REPOS": "https://github.com/one/tool\nhttps://github.com/two/tool"
            }):
                self.assertEqual(module.main(), 1)
            self.assertEqual(list(Path(directory).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
