# 体育館空き通知アプリ

各自治体の施設予約システムを定期的にチェックし、バレーボールができる体育館の
土日祝の空きが新しく出たときにメールで通知します。

## 対応自治体と通知条件

| 自治体 | システム | 1日の枠 | 通知条件 |
|---|---|---|---|
| 青梅市 | P-kashikan | 6枠 (9-12/12-13/13-15/15-17/17-19/19-21) | 3枠以上連続 |
| 江東区 | スポーツネット | 3枠 (9-12/13-17/18-21:30) | 2枠以上連続、ただし13:00-17:00は1枠でも通知 |
| 荒川区 | 施設予約システム | 4枠 (9-12/12:30-15/15:30-18/18:30-21:30) | 2枠以上連続、荒川総合スポーツセンターの大体育室全面・小体育室のみ |
| 市川市 | 公共施設予約システム | 6枠 (2時間単位 9-11〜19-21) | 2枠以上連続、国府台・信篤市民体育館の「全面」のみ |

江東区と荒川区は同一ベンダーのシステムのため、`scrapers/stagia.py` を共通エンジン
として使い、サイトごとの差分(入口・分類の階層・時間枠)だけを
`scrapers/koto.py` / `scrapers/arakawa.py` で定義しています。

市川市は別系統(ASP.NETのウィザード形式)で、日単位の一覧で空きがある日だけを
選んで時間帯別画面に進む必要があるため、`scrapers/ichikawa.py` に独自実装して
います。対象の館・面は同ファイル上部の `TARGET_BUILDINGS` / `SUBROOM_KEYWORD`
で指定します。なお市川市は「△(用途によっては使用可能)」を空きとは扱わず、
確実に空いている「○」のみを通知対象にしています。

- ログイン・自動予約は行いません。公開されている空き状況ページを閲覧するだけです
  (どちらの自治体も空き状況の閲覧自体はログイン不要です)。
- どちらのシステムも単純なHTTPリクエストでは空き状況を取得できないため、
  [Playwright](https://playwright.dev/python/)でヘッドレスブラウザ操作を自動化
  しています。
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

### メールが送れないとき

`check_smtp.py` でGmailの認証情報だけを単体で確認できます(入力値はどこにも
保存・送信されません)。

```bash
python check_smtp.py
```

「失敗」になる場合は、2段階認証が有効なアカウントで発行したアプリパスワード
(16桁)を使っているか確認してください。通常のGoogleログインパスワードでは
SMTPは認証できません。

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

`config.yaml` で以下を調整できます。`targets` は自治体ごとの設定リストです。

- `sport_code`: 検索する種目コード(青梅市は目的コード、江東区は種目コード。
  どちらも現在はバレーボール)
- `min_consecutive_blocks`: この枠数以上連続して空いている場合に通知する
- `always_notify_block_indexes`: 1枠だけでも通知したい枠を0始まりの番号で指定
  (例: 江東区の `[1]` は 13:00-17:00 の枠)
- `facility_filters`: 施設名にこの文字列を含む施設だけを対象にする(省略時は全施設)
- `target_weekdays`: チェック対象の曜日(土・日・祝)
- `lookahead_days`: 何日先まで空き状況をチェックするか(日送りクリック回数に
  直結するため、大きくするほど1回の実行時間が伸びます)

## 今後の拡張(残り3自治体)

葛飾区・江戸川区・松戸市が未対応です。拡張手順:

1. `scrapers/xxx.py` を追加し、`fetch_all_slots(target_dates, sport_code,
   min_consecutive, always_notify_indexes)` を実装する
2. 連続枠の判定とラベル生成は `scrapers/base.py` の `find_qualifying_runs()` /
   `build_time_label()` をそのまま使える
3. `main.py` の `SCRAPERS` に登録し、`config.yaml` の `targets` に追加する

次点候補は松戸市(ログイン不要で空き状況閲覧可)です。
