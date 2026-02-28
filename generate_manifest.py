#!/usr/bin/env python3
"""
GitHub URL → Scoop manifest 自動生成スクリプト
MANAGED_REPOS 変数に書かれたURLを元に bucket/*.json を生成・更新・削除する
"""

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

BUCKET_DIR = Path(__file__).parent.parent / "bucket"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Windows向けアセットの優先度（高い順）
ASSET_PRIORITY = [".zip", ".exe", ".msi", ".7z"]
ASSET_EXCLUDE = [
    ".tar.gz", ".tar.bz2", ".tar.xz", ".AppImage",
    ".deb", ".rpm", ".dmg", ".pkg", ".apk",
    "-linux-", "-darwin-", "-macos-", "-mac-",
    ".sha256", ".sha512", ".md5", ".sig", ".asc",
    "symbols", "debug", "source", "src",
]

# Windowsアセット判定キーワード（名前に含まれているとWindows向け寄り）
WIN_KEYWORDS = ["win", "windows", "x64", "x86_64", "amd64", "portable"]


def api_request(url: str) -> dict | list | None:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [ERROR] API request failed: {url} → {e}", file=sys.stderr)
        return None


def sha256_of_url(url: str) -> str | None:
    req = urllib.request.Request(url)
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    try:
        h = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=60) as resp:
            while chunk := resp.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        print(f"  [ERROR] Download failed: {url} → {e}", file=sys.stderr)
        return None


def score_asset(name: str) -> int:
    """アセットのスコアを計算。高いほどWindows向け優先度が高い"""
    name_lower = name.lower()

    # 除外対象は問答無用で最低点
    for excl in ASSET_EXCLUDE:
        if excl.lower() in name_lower:
            return -1

    score = 0

    # 拡張子スコア
    for i, ext in enumerate(ASSET_PRIORITY):
        if name_lower.endswith(ext):
            score += (len(ASSET_PRIORITY) - i) * 10
            break
    else:
        return -1  # 対応拡張子なし

    # Windowsキーワードボーナス
    for kw in WIN_KEYWORDS:
        if kw in name_lower:
            score += 5

    return score


def pick_best_asset(assets: list) -> dict | None:
    candidates = [(score_asset(a["name"]), a) for a in assets]
    candidates = [(s, a) for s, a in candidates if s >= 0]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def parse_version(tag: str) -> str:
    """v1.2.3 → 1.2.3"""
    return re.sub(r"^[vV]", "", tag)


def build_manifest(repo_url: str) -> dict | None:
    m = re.match(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url.strip())
    if not m:
        print(f"  [SKIP] URLの形式が不正: {repo_url}", file=sys.stderr)
        return None

    repo_path = m.group(1)
    print(f"  処理中: {repo_path}")

    # リリース取得
    release = api_request(f"https://api.github.com/repos/{repo_path}/releases/latest")
    if not release or "tag_name" not in release:
        print(f"  [SKIP] リリースが見つからない: {repo_path}", file=sys.stderr)
        return None

    tag = release["tag_name"]
    version = parse_version(tag)
    assets = release.get("assets", [])

    asset = pick_best_asset(assets)
    if not asset:
        print(f"  [SKIP] Windows向けアセットが見つからない: {repo_path}", file=sys.stderr)
        return None

    asset_url = asset["browser_download_url"]
    asset_name = asset["name"]
    print(f"  アセット: {asset_name}")

    # ハッシュ計算
    print(f"  ハッシュ計算中...")
    digest = sha256_of_url(asset_url)
    if not digest:
        return None

    # URLテンプレート生成（バージョン部分を$versionに置換）
    # タグそのまま版とvプレフィックス剥がし版の両方を試す
    url_template = asset_url.replace(tag, "$version").replace(version, "$version")

    # bin 推定
    ext = Path(asset_name).suffix.lower()
    bin_name = None
    if ext == ".exe":
        bin_name = asset_name

    manifest: dict = {
        "version": version,
        "description": f"Auto-managed: {repo_path}",
        "homepage": f"https://github.com/{repo_path}",
        "license": "Unknown",
        "url": asset_url,
        "hash": f"sha256:{digest}",
        "checkver": {
            "github": f"https://github.com/{repo_path}"
        },
        "autoupdate": {
            "url": url_template
        }
    }

    if bin_name:
        manifest["bin"] = bin_name

    # zipなら extract_dir 推定
    if ext == ".zip":
        manifest["extract_dir"] = ""

    return manifest


def repo_to_name(repo_url: str) -> str:
    """URL → manifest ファイル名（リポジトリ名部分）"""
    m = re.match(r"https://github\.com/[^/]+/([^/]+?)(?:\.git)?/?$", repo_url.strip())
    return m.group(1).lower() if m else ""


def main():
    managed_repos_raw = os.environ.get("MANAGED_REPOS", "")
    repos = [line.strip() for line in managed_repos_raw.splitlines() if line.strip()]

    if not repos:
        print("MANAGED_REPOS が空。何もしない。")
        return

    BUCKET_DIR.mkdir(exist_ok=True)

    # 管理対象のファイル名セット
    managed_names = set()
    for url in repos:
        name = repo_to_name(url)
        if name:
            managed_names.add(f"{name}.json")

    # 既存manifestで管理対象外になったものを削除
    for existing in BUCKET_DIR.glob("*.json"):
        if existing.name not in managed_names:
            print(f"削除: {existing.name}（MANAGED_REPOSから除去済み）")
            existing.unlink()

    # 各リポジトリのmanifest生成・更新
    for url in repos:
        name = repo_to_name(url)
        if not name:
            continue

        manifest_path = BUCKET_DIR / f"{name}.json"

        # 既存バージョン確認
        existing_version = None
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text())
                existing_version = existing.get("version")
            except Exception:
                pass

        manifest = build_manifest(url)
        if not manifest:
            continue

        if existing_version == manifest["version"]:
            print(f"  スキップ（最新: {manifest['version']}）")
            continue

        manifest_path.write_text(
            json.dumps(manifest, indent=4, ensure_ascii=False) + "\n"
        )
        print(f"  → {manifest_path.name} を{'更新' if existing_version else '新規作成'} ({existing_version} → {manifest['version']})")


if __name__ == "__main__":
    main()
