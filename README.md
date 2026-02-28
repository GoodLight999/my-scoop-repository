# My Scoop Bucket

URL を貼るだけ。あとは全自動。

## セットアップ

### 1. このリポジトリをfork or テンプレートとして作成

### 2. Repository variable を設定

`Settings → Secrets and variables → Actions → Variables → New repository variable`

| Name | Value |
|------|-------|
| `MANAGED_REPOS` | GitHubリポジトリURLを改行区切りで列挙 |

```
https://github.com/foo/bar
https://github.com/baz/qux
```

### 3. Actions を有効化

初回は `Actions → Sync Manifests → Run workflow` で手動実行。
以降は毎日 JST 9:00 に自動実行される。

---

## 運用

| やること | 操作 |
|----------|------|
| ソフト追加 | `MANAGED_REPOS` にURLを1行追加 |
| ソフト削除 | `MANAGED_REPOS` からURLを削除 |
| 今すぐ反映 | `Actions → Sync Manifests → Run workflow` |
| バージョン更新 | 毎日自動 |

---

## このbucketを使う

```powershell
scoop bucket add mybucket https://github.com/あなたのユーザー名/あなたのリポジトリ名
scoop install mybucket/<パッケージ名>
```

---

## 注意事項

- GitHub Releases を持たないリポジトリはスキップされる
- Windows向けアセットが判定できない場合もスキップされる
- `license` フィールドは `Unknown` で生成されるので、気になるなら手動で直す
