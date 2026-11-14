# My Scoop Bucket

GitHub Releases の Windows 向け配布ファイルから、個人用 Scoop マニフェストを自動生成します。Python の外部パッケージや常駐アプリは不要です。

## 設定

1. このリポジトリを fork します。
2. `Settings → Secrets and variables → Actions → Variables` に `MANAGED_REPOS` を追加します。値は GitHub リポジトリの URL を1行ずつ並べたものです。

   ```text
   https://github.com/owner/project-a
   https://github.com/owner/project-b
   ```

3. `Actions → Sync Manifests → Run workflow` で初回実行します。以後は毎日 9:00 JST 頃に更新を確認します。GitHub Actions のスケジュール実行時刻は前後する場合があります。

## インストールと更新

```powershell
scoop bucket add mybucket https://github.com/YOUR_ACCOUNT/my-scoop-repository
scoop install mybucket/project-a
```

**UniGetUI を利用する場合:** UniGetUI 側で Scoop を有効にし、上記 bucket を Scoop に登録してください。UniGetUI は Scoop が管理するパッケージを扱えます。別途 UniGetUI のインストール一覧を同期する機能はありません。更新は Scoop / UniGetUI に統一することを推奨します。

**注意:** マニフェストの更新は PC 上のアプリを直接更新しません。アプリ本体の更新には `scoop update *` または UniGetUI を使用します。

## 仕様

- Windows または x64 と明記された `.zip` / `.7z` と、ポータブル版と明記された `.exe` を選択します。Windows x64 の配布ファイルを優先します。
- インストーラーと `.msi` / `.msixbundle`、プラットフォームや用途の不明なファイルは、誤登録防止のため対象外です。対応するアプリが減る場合もあります。
- GitHub Releases に SHA-256 が公開されていればそれを利用します。なければダウンロードして計算します。同一バージョン・同一 URL は既存のハッシュを再利用します。
- 管理対象から外したリポジトリの自動生成マニフェストだけを削除します。手動作成したファイルは削除しません。
- Releases がない場合や適合する配布ファイルがない場合、既存マニフェストを保持します。通信・設定エラー時は更新せず終了します。
- 管理対象リポジトリ同士の名前が重複するとエラーになります。
- ZIP / 7z の内部を解析しないため、実行ファイルの PATH 登録やスタートメニューのショートカットは自動生成されません。Scoop でインストールできても、アプリの起動にはマニフェストの手動調整が必要な場合があります。
- この仕組みはリリースごとの Scoop マニフェストを更新するものであり、アプリそのものの互換性・正常動作を保証するものではありません。

## テスト

CI が停止している場合は、GitHub の Actions 設定と実行履歴を確認してください。公開リポジトリの定期実行は、60日間更新がないと自動停止することがあります。

```sh
python -m unittest discover -s tests -v
```
