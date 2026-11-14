"""Regression tests for real-world release names and edge cases."""

import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_manifest.py"
spec = importlib.util.spec_from_file_location("generator_audit", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def asset(name, url=True):
    item = {"name": name}
    if url:
        item["browser_download_url"] = "https://github.com/sample/tool/releases/download/v1/" + name
    return item


class AuditTests(unittest.TestCase):
    def test_real_release_names(self):
        # Names sampled from real GitHub Releases; no package downloads required.
        fixtures = [
            ("bat", [
                "bat-v0.26.1-aarch64-pc-windows-msvc.zip",
                "bat-v0.26.1-i686-pc-windows-msvc.zip",
                "bat-v0.26.1-x86_64-pc-windows-msvc.zip",
                "bat-v0.26.1-x86_64-unknown-linux-gnu.tar.gz",
            ], "bat-v0.26.1-x86_64-pc-windows-msvc.zip"),
            ("fzf", [
                "fzf-0.75.0-windows_amd64.msi",
                "fzf-0.75.0-windows_amd64.zip",
                "fzf-0.75.0-windows_arm64.zip",
            ], "fzf-0.75.0-windows_amd64.zip"),
            ("UniGetUI", [
                "UniGetUI.arm64.zip", "UniGetUI.Installer.arm64.exe",
                "UniGetUI.Installer.x64.exe", "UniGetUI.x64.zip",
            ], "UniGetUI.x64.zip"),
            ("PowerToys", [
                "GroupPolicyObjectFiles-0.102.3101.0.zip",
                "PowerToysSetup-0.102.3101.0-x64.exe", "symbols-x64.zip",
            ], None),
            ("Obsidian", [
                "Obsidian-1.15.1.exe", "Obsidian-1.15.1.dmg",
                "Obsidian-1.15.1.AppImage",
            ], None),
            ("NanaZip", [
                "NanaZip_7.0.1882.0.msixbundle",
                "NanaZip_7.0.1882.0_Binaries.zip",
                "NanaZip_7.0.1882.0_DebugSymbols.zip",
            ], None),
        ]
        for name, names, expected in fixtures:
            with self.subTest(repository=name):
                selected = app.pick_best_asset([asset(n) for n in names])
                self.assertEqual(selected["name"] if selected else None, expected)

    def test_ambiguous_installers_rejected(self):
        for name in [
            "Example Setup.exe", "Example Installer x64.exe",
            "Example Setup 1.5.0.exe", "Obsidian-1.15.1.exe",
            "app-installation-x64.exe", "app-win-x64-setup.exe",
            "app.exe", "app-x64.exe",
        ]:
            with self.subTest(name=name):
                self.assertEqual(app.score_asset(name), -1)

    def test_explicit_portable_executable_is_allowed(self):
        self.assertGreaterEqual(app.score_asset("example-windows-x64-portable.exe"), 0)

    def test_asset_requires_valid_download_url(self):
        options = [
            asset("app-windows-x64.zip", False),
            {"name": "app-win-x64.zip", "browser_download_url": "http://example.com/bad.zip"},
            asset("app-windows.zip"),
        ]
        self.assertEqual(app.pick_best_asset(options)["name"], "app-windows.zip")

    def test_missing_and_malformed_assets_ignored(self):
        options = [None, 123, {}, {"name": None, "browser_download_url": "https://example.com/x"},
                   asset("app-win-x64.zip")]
        self.assertEqual(app.pick_best_asset(options)["name"], "app-win-x64.zip")

    def test_unexpected_release_response_is_error(self):
        with patch.object(app, "api_request", return_value=["bad"]):
            with self.assertRaises(RuntimeError):
                app.build_manifest("test/tool")
        with patch.object(app, "api_request", return_value={"tag_name": "v1", "assets": {}}):
            # Empty dict is tolerated as no release assets.
            self.assertIsNone(app.build_manifest("test/tool"))
        with patch.object(app, "api_request", return_value={"tag_name": "v1", "assets": {"a": 1}}):
            with self.assertRaises(RuntimeError):
                app.build_manifest("test/tool")

    def test_invalid_manifest_description_aborts_safely(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, "BUCKET_DIR", Path(temp)):
            path = Path(temp) / "tool.json"
            path.write_text(json.dumps({"description": None}))
            with patch.dict(os.environ, {"MANAGED_REPOS": "https://github.com/test/tool"}):
                self.assertEqual(app.main(), 1)
            self.assertEqual(json.loads(path.read_text()), {"description": None})

    def test_windows_reserved_repository_names(self):
        for name in ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9", ".", ".."]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    app.parse_repo("https://github.com/user/" + name)

    def test_repo_url_validation(self):
        for url in ["http://github.com/a/b", "https://evil.example/a/b",
                    "https://github.com/a/b/tree/main", "https://github.com/a/b?x=1"]:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    app.parse_repo(url)
        self.assertEqual(app.parse_repo("https://github.com/Owner/MyApp.git"), ("Owner/MyApp", "myapp"))

    def test_api_auth_and_rate_limit_fail_loudly(self):
        for status in [401, 403, 429, 500]:
            with self.subTest(status=status):
                error = urllib.error.HTTPError("https://api.github.com", status, "", {}, None)
                with patch.object(app.urllib.request, "urlopen", side_effect=error):
                    with self.assertRaises(RuntimeError):
                        app.api_request("https://api.github.com/repos/test/tool/releases/latest")

    def test_damaged_existing_json_does_not_get_replaced(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, "BUCKET_DIR", Path(temp)):
            file = Path(temp) / "tool.json"
            file.write_text("{not json")
            with patch.dict(os.environ, {"MANAGED_REPOS": "https://github.com/test/tool"}):
                self.assertEqual(app.main(), 1)
            self.assertEqual(file.read_text(), "{not json")

    def test_partial_api_failure_produces_no_changes(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, "BUCKET_DIR", Path(temp)):
            prior = Path(temp) / "obsolete.json"
            prior.write_text(json.dumps({"description": "Auto-managed: old/obsolete"}))
            def load(url):
                if "two" in url:
                    raise RuntimeError("API temporarily unavailable")
                return {"tag_name": "v1.0", "assets": [dict(asset("one-win-x64.zip"), digest="sha256:" + "a" * 64)]}
            with patch.dict(os.environ, {
                "MANAGED_REPOS": "https://github.com/test/one\nhttps://github.com/test/two",
            }), patch.object(app, "api_request", side_effect=load):
                self.assertEqual(app.main(), 1)
            self.assertEqual([p.name for p in Path(temp).iterdir()], ["obsolete.json"])

    def test_100_unchanged_packages_require_no_downloads(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, "BUCKET_DIR", Path(temp)):
            names = [f"app{i}" for i in range(100)]
            for name in names:
                manifest = {
                    "description": "Auto-managed: test/" + name,
                    "version": "1.0",
                    "url": "https://example.com/" + name + "-win-x64.zip",
                    "hash": "sha256:" + "a" * 64,
                }
                (Path(temp) / f"{name}.json").write_text(json.dumps(manifest))
            def load(url):
                name = url.split("/")[-3]
                return {"tag_name": "v1.0", "assets": [{
                    "name": name + "-win-x64.zip",
                    "browser_download_url": "https://example.com/" + name + "-win-x64.zip",
                }]}
            with patch.dict(os.environ, {"MANAGED_REPOS": "\n".join(
                "https://github.com/test/" + name for name in names,
            )}), patch.object(app, "api_request", side_effect=load), patch.object(
                app, "sha256_of_url", side_effect=AssertionError("redundant download"),
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(), 0)

    def test_generated_hash_is_valid_scoop_sha256(self):
        with patch.object(app, "api_request", return_value={
            "tag_name": "v1.0",
            "assets": [dict(asset("tool-win-x64.zip"), digest="sha256:" + "f" * 64)],
        }):
            manifest = app.build_manifest("test/tool")
        self.assertRegex(manifest["hash"], r"^sha256:[a-f0-9]{64}$")
        self.assertTrue(manifest["url"].startswith("https://"))
        self.assertNotIn("autoupdate", manifest)
        self.assertNotIn("extract_dir", manifest)
