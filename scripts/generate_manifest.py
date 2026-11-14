#!/usr/bin/env python3
"""Generate Scoop manifests from GitHub Releases without extra dependencies."""

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BUCKET_DIR = Path(__file__).resolve().parent.parent / "bucket"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_URL = re.compile(r"^https://github\.com/([a-z0-9_.-]+)/([a-z0-9_.-]+?)(?:\.git)?/?$", re.I)
REJECTED_PLATFORM = re.compile(
    r"(?:^|[._-])(?:linux|macos|darwin|osx|mac|android|ios|arm64|aarch64)(?:[._-]|$)", re.I
)
INSTALLER = re.compile(r"(?:^|[._\s-])(?:setup|install(?:er|ation|ing)?|update(?:r)?)(?:[._\s-]|$)", re.I)
SUPPORTED = {".zip": 30, ".7z": 25, ".exe": 15}
RELEASE_NOISE = re.compile(r"(?:^|[._\s-])(?:group.?policy|sdk|symbols?|debug|source|sources|checksums?|sbom|binaries)(?:[._\s-]|$)", re.I)
WINDOWS_HINT = re.compile(r"(?:^|[._\s-])(?:windows|win|win32|win64)(?:[._\s-]|$)", re.I)
X64_HINT = re.compile(r"(?:^|[._\s-])(?:x64|x86_64|amd64)(?:[._\s-]|$)", re.I)
PORTABLE_HINT = re.compile(r"(?:^|[._\s-])portable(?:[._\s-]|$)", re.I)
HASH_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$", re.I)


def parse_repo(url: str) -> tuple[str, str]:
    match = REPO_URL.fullmatch(url.strip())
    if not match:
        raise ValueError(f"Invalid GitHub repository URL: {url}")
    owner, name = match.groups()
    if owner in {".", ".."} or name in {".", ".."} or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", name, re.I):
        raise ValueError(f"Unsupported repository name for Windows: {name}")
    return f"{owner}/{name}", name.lower()


def api_request(url: str) -> dict | None:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"GitHub API failed ({exc.code}): {url}") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"GitHub API failed: {url}: {exc}") from exc


def sha256_of_url(url: str) -> str:
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=90) as response:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeError(f"Asset download failed: {url}: {exc}") from exc
    return digest.hexdigest()


def score_asset(name: str) -> int:
    if not isinstance(name, str):
        return -1
    lower = name.lower()
    extension = next((ext for ext in SUPPORTED if lower.endswith(ext)), None)
    if not extension or REJECTED_PLATFORM.search(lower) or INSTALLER.search(lower) or RELEASE_NOISE.search(lower):
        return -1
    win, x64, portable = bool(WINDOWS_HINT.search(lower)), bool(X64_HINT.search(lower)), bool(PORTABLE_HINT.search(lower))
    # Unmarked archives are often source or ancillary files. A generic EXE is
    # frequently a self-extracting installer, not a portable application.
    if extension == ".exe" and not portable:
        return -1
    if not (win or x64 or portable):
        return -1
    score = SUPPORTED[extension]
    if win:
        score += 40
    if x64:
        score += 10
    if portable:
        score += 5
    return score


def pick_best_asset(assets: list[dict]) -> dict | None:
    candidates = [asset for asset in assets if isinstance(asset, dict)
                  and isinstance(asset.get("browser_download_url"), str)
                  and asset["browser_download_url"].startswith("https://")
                  and score_asset(asset.get("name", "")) >= 0]
    return max(candidates, key=lambda asset: score_asset(asset["name"]), default=None)


def sha256_from_metadata(asset: dict) -> str | None:
    value = asset.get("digest") or ""
    match = HASH_RE.fullmatch(value)
    return match.group(1).lower() if match else None


def build_manifest(repo_path: str, previous: dict | None = None) -> dict | None:
    release = api_request(f"https://api.github.com/repos/{repo_path}/releases/latest")
    if release is not None and not isinstance(release, dict):
        raise RuntimeError(f"Unexpected GitHub release response: {repo_path}")
    if not release or not release.get("tag_name"):
        print(f"[SKIP] No latest release: {repo_path}")
        return None
    assets = release.get("assets") or []
    if not isinstance(assets, list):
        raise RuntimeError(f"Invalid release assets: {repo_path}")
    asset = pick_best_asset(assets)
    if not asset or not asset.get("browser_download_url"):
        print(f"[SKIP] No supported Windows asset: {repo_path}")
        return None

    tag = release["tag_name"]
    if not isinstance(tag, str):
        raise ValueError(f"Invalid release tag: {repo_path}")
    version = re.sub(r"^[vV](?=\d)", "", tag)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
        raise ValueError(f"Unsupported release version: {repo_path}: {version!r}")
    url = asset["browser_download_url"]
    digest = sha256_from_metadata(asset)
    unchanged = previous and previous.get("version") == version and previous.get("url") == url
    if digest is None and unchanged:
        prior_hash = HASH_RE.fullmatch(previous.get("hash", ""))
        if prior_hash:
            digest = prior_hash.group(1).lower()
    if digest is None:
        print(f"[HASH] {repo_path}: downloading {asset['name']}")
        digest = sha256_of_url(url)

    manifest = {
        "version": version,
        "description": f"Auto-managed: {repo_path}",
        "homepage": f"https://github.com/{repo_path}",
        "license": "Unknown",
        "url": url,
        "hash": f"sha256:{digest}",
    }
    if asset["name"].lower().endswith(".exe"):
        # Only non-installer executables are eligible for automatic bin shims.
        manifest["bin"] = asset["name"]
    return manifest


def main() -> int:
    lines = [line.strip() for line in os.environ.get("MANAGED_REPOS", "").splitlines()]
    urls = list(dict.fromkeys(line for line in lines if line and not line.startswith("#")))
    if not urls:
        print("MANAGED_REPOS is empty; no changes.")
        return 0

    try:
        repositories = [parse_repo(url) for url in urls]
        names = [name for _, name in repositories]
        if len(names) != len(set(names)):
            raise ValueError("Repository names collide; use repos with distinct names")

        updates: dict[Path, dict] = {}
        keep: set[str] = set()
        for repo_path, name in repositories:
            path = BUCKET_DIR / f"{name}.json"
            keep.add(path.name)
            previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if previous is not None:
                if not isinstance(previous, dict):
                    raise ValueError(f"Invalid manifest object: {path.name}")
                source = previous.get("description", "")
                if not isinstance(source, str) or source.casefold() != f"Auto-managed: {repo_path}".casefold():
                    raise ValueError(f"Refusing to overwrite a manifest from another source: {path.name}")
            manifest = build_manifest(repo_path, previous)
            if manifest is None:
                continue  # Do not delete an existing manifest when a release is missing.
            if manifest != previous:
                updates[path] = manifest
            else:
                print(f"[OK] {repo_path}: {manifest['version']}")

        deletions = []
        if BUCKET_DIR.exists():
            for path in BUCKET_DIR.glob("*.json"):
                if path.name in keep:
                    continue
                current = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(current, dict) and isinstance(current.get("description"), str) and current["description"].startswith("Auto-managed: "):
                    deletions.append(path)

        BUCKET_DIR.mkdir(parents=True, exist_ok=True)
        for path, manifest in updates.items():
            path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[WRITE] {path.name}: {manifest['version']}")
        for path in deletions:
            path.unlink()
            print(f"[DELETE] {path.name}")
        print(f"Done: {len(updates)} updated, {len(deletions)} removed")
        return 0
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
