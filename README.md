# 体育館空き通知アプリ (MVP: 青梅市)

青梅市施設予約管理システム(P-kashikan)を定期的にチェックし、バレーボールが
できる体育館の土日祝の空きが新しく出たときにメールで通知します。

- ログイン・自動予約は行いません。公開されている空き状況ページを閲覧するだけです。
- このシステムは空き状況の検索結果が単純なHTTPリクエストでは取得できず、
  実際のブラウザ操作(目的で検索→スポーツ→目的選択→検索→日送り)を経ないと
  表示されないことを確認したため、[Playwright](https://playwright.dev/python/)で
  ヘッドレスブラウザ操作を自動化して取得しています。
- サーバー負荷軽減のため、実行頻度は控えめ(既定1時間おき)に設定しています。
- 対象自治体・自治体側のシステム仕様は変更される可能性があります。定期的に
  実際のサイトの利用規約もご確認ください。

## ローカルでの動作確認

```bash
python -m venv .venv
source .venv/bin/activate  # Windowsは .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

# メール送信・state保存なしで、現在の空き状況だけ確認する
python main.py --dry-run
```

## メール送信を試す

1. Googleアカウントで2段階認証を有効にする
2. https://myaccount.google.com/apppasswords でアプリパスワードを発行する
   (16桁の文字列が表示されます)
3. 環境変数を設定して実行する

```bash
export GMAIL_ADDRESS="your-address@gmail.com"
export GMAIL_APP_PASSWORD="発行された16桁のアプリパスワード"
export NOTIFY_TO="your-address@gmail.com"   # 省略するとGMAIL_ADDRESS宛
python main.py
```

初回実行時は state.json が空なので、その時点の空き枠が全て「新規」として
通知されます。2回目以降は前回からの差分(新しく○になった枠)のみ通知されます。

## GitHub Actionsで自動実行する

1. GitHubで新規リポジトリを作成し、この `gym-watcher` フォルダの中身をpushする

   ```bash
   cd gym-watcher
   git init
   git add .
   git commit -m "Initial commit: gym-watcher MVP (Ome city)"
   git branch -M main
   git remote add origin https://github.com/<あなたのユーザー名>/<リポジトリ名>.git
   git push -u origin main
   ```

2. リポジトリの Settings → Secrets and variables → Actions → New repository secret
   から、以下の3つを登録する

   | Name | 値 |
   |---|---|
   | `GMAIL_ADDRESS` | 送信元Gmailアドレス (例: your-address@gmail.com) |
   | `GMAIL_APP_PASSWORD` | 上記で発行したアプリパスワード |
   | `NOTIFY_TO` | 通知を受け取るメールアドレス |

3. Actionsタブ → "Check gym availability" → "Run workflow" で手動実行し、
   正常終了することを確認する

4. 以降は `.github/workflows/check.yml` の cron 設定に従い、JST 6:00〜23:00の
   間、1時間おきに自動実行されます

   - Playwright(ブラウザ自動化)を使うため1回の実行に1〜2分程度かかります。
     プライベートリポジトリはActionsの無料枠が月2000分のため、この頻度に
     控えめに設定しています。パブリックリポジトリならGitHub Actionsの
     無料枠は無制限なので、`.github/workflows/check.yml` の cron 設定を
     編集して頻度を上げても問題ありません。

## 設定を変える

`config.yaml` で以下を調整できます。

- `target.mokuteki_code`: 検索する利用目的コード(現在はバレーボール=037)
- `target.min_consecutive_blocks`: 同一施設・同一日でこの枠数以上連続して
  空いている場合のみ通知する(既定3枠。1日は 9-12/12-13/13-15/15-17/17-19/
  19-21 の6枠に分かれており、単発1〜2枠の空きは通知対象外になります)
- `target_weekdays`: チェック対象の曜日(土・日・祝)
- `lookahead_days`: 何日先まで空き状況をチェックするか(日送りクリック回数に
  直結するため、大きくするほど1回の実行時間が伸びます)

## 今後の拡張(残り6自治体)

`scrapers/base.py` の `Slot` を共通データ構造として、`scrapers/xxx.py` に
新しい自治体用のスクレイパーを追加し、`config.yaml` にエントリを増やす形で
拡張できます。次点候補は 松戸市(ログイン不要で空き状況閲覧可) です。
江東区は簡易HTTPアクセスだと「サービス時間外」エラーになりセッション制御が
複雑なため、ブラウザ自動化(Playwright等)が必要になる見込みです。
