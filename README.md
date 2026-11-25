# ネットワーク機器・サーバ 参照系ログ自動収集マクロ (Tera Term 4.x互換)

踏み台サーバ経由で複数のネットワーク機器（Cisco / Juniper / Yamaha / Huawei / Linux 等）へ順次 SSH / Telnet 接続し、参照系コマンド（`show` コマンド等）の実行とログ収集を自動化する Tera Term マクロ（TTL）です。

---

## 主な特徴

- **Tera Term 4.0x / 4.x 実機完全互換**:
  - Ver 4.53+ の新設コマンド（`strsplit`, `break`, `continue`, `sprintf2`, `tolower` 等）を排除。
  - `wait` / `waitln` の引数上限（10個）、ダイアログ文字列 255 バイト上限、`CRLF = #13#10`、実機特有の `and` / `or` 演算子優先順位（全条件括弧囲み）、`filereadln` の最終行（末尾改行なし）EOF 仕様まで完全対応。
- **外部パスワード一覧（`credentials.csv`）による自動総当たり & リアルタイム最適化**:
  - `device.csv` のユーザー名・パスワード列が空欄の機器に対して、`credentials.csv` の候補を上から順に自動試行します。
  - **リアルタイム先頭移動**（`REORDER_CRED_CSV = 1`、`runner.ttl`）：ログインに成功した行を即座に `credentials.csv` の先頭（コメント／ヘッダ直後）へ移動し、次機器からの試行回数を最小化します。
  - 試行中のユーザー名・パスワード・プロトコルは画面タイトルと `statusbox` にリアルタイム表示されます。
  - `FALLBACK_TO_BASTION_CRED = 0`（デフォルト）により、意図せず踏み台のユーザー名が対象機器へ流用されるのを防止します。
- **SSH / Telnet の自動判別・高速フォールバック**:
  - `device.csv` のプロトコル列が空欄の場合は **SSH → 失敗時は自動で Telnet** を試行します。
  - SSH には `-o PubkeyAuthentication=no -o PreferredAuthentications=keyboard-interactive,password` を自動付与し、秘密鍵・GSSAPI 遅延によるタイムアウトや切断を防止します。
  - 古い機器で SSH の鍵交換・暗号アルゴリズム不一致（`no matching` / `no mutual`）が発生した場合は、自動で旧アルゴリズム互換オプションを付けて即リトライします。
  - Telnet がポート拒否・ログイン画面前切断・タイムアウトで受け付けていない場合は、**1回目で即打ち切り**して無駄な待機を省きます。
- **マルチベンダー自動適応（特権昇格・ページング解除）**:
  - 一般モード（`>`）のみ `enable` を試行し、非対応機器（Yamaha 等）では `administrator` へ自動フォールバックします。
  - ベンダー別ページング無効化コマンドを順次送信し、途中の `--More--` / `続く` 等も自動でスペース送りします。
- **充実したログ・診断出力**:
  - `logs/devices/<ホスト名>_YYYYMMDD_HHMMSS.log`: 機器ごとのコマンド実行生ログ（`DEF_DEV_LOG_SUBDIR = 'devices'` でサマリCSVとフォルダ分離）
  - `logs/summary_YYYYMMDD_HHMMSS.csv`: 全機器の成否（`OK`/`NG`）・プロンプト・詳細・ログインユーザー・**成功したパスワードパターン名**一覧
  - `logs/resolved_accounts_YYYYMMDD_HHMMSS.csv`: ログイン成功した**パスワードパターン名**・ユーザー名・プロトコルの一覧
  - `logs/attempts_YYYYMMDD_HHMMSS.csv`: 各試行の**パスワードパターン名**・ユーザー名・プロトコル・`SKIP_PROTO` / `FAIL (原因)` 履歴

---

## ディレクトリ構成

```text
.
├── runner.ttl               # ★分割版メインマクロ（Tera Term 5.x / 5.4.1 向け・おすすめ）
├── lib/                     # runner.ttl から include される機能別モジュール群
│   ├── init.ttl             #   起動設定・パス解決・ログCSV/devicesフォルダ準備・踏み台接続
│   ├── auth.ttl             #   credentials.csv 選択・リアルタイム先頭移動・SSH/Telnet 2段階ログイン
│   ├── exec_device.ttl      #   特権昇格・ページング解除・コマンド実行・切断
│   └── abort_bastion.ttl    #   認証失敗/タイムアウト時の安全な踏み台復帰
├── device_runner.ttl        # 互換用レガシー1ファイル版（Tera Term 4.02〜4.x 互換・2,866行）
├── device.csv               # 接続対象機器リスト
├── credentials.csv          # 共通パスワード一覧（自動試行用）
├── commands_universal.txt   # 機種不明時用 汎用コマンド一覧
├── commands_cisco.txt       # Cisco 向けコマンド一覧
├── commands_juniper.txt     # Juniper 向けコマンド一覧
├── commands_linux.txt       # Linux 向けコマンド一覧
├── Makefile                 # 自動テスト・構文チェック・掃除コマンド
├── README.md                # 本ドキュメント
├── tests/                   # 静的解析・実機互換エミュレータ・実Tera Term CIサーバ・E2Eテスト一式
│   ├── test_device_runner.py
│   ├── test_end_to_end.py
│   ├── test_modular_runner.py
│   ├── real_ssh_ci_server.py
│   ├── mock_lab.py
│   └── ttl_engine.py
└── archive/                 # 旧プロトタイプ・参考用ファイル保管場所
```

### マクロの使い分け
- **`runner.ttl` ＋ `lib/`（おすすめ / Tera Term 5.x・5.4.1 対応）**: `include` による役割別ファイル分割とモダン命令を採用し、`credentials.csv` のリアルタイム先頭移動・結果CSVへのパスワード出力・`logs\devices\` 分離・無人実行（`SILENT_MODE`）を備えた最新版です。GitHub Actions 上で本物の Tera Term 5.4.1（`ttpmacro.exe`）＋実 SSH2/Telnet サーバによる E2E 検証も行っています。
- **`device_runner.ttl`（互換用レガシー版 / Tera Term 4.02〜4.x）**: 1ファイル完結・インライン複数候補（`user1|user2`）を含む旧バージョンです（※新機能は `runner.ttl` のみに実装されています）。

---

## 設定ファイルの書き方（推奨パターン）

### 1. `credentials.csv`（共通パスワード一覧）
1列目に **パスワードパターン名** を記載し、先頭行には `#` を付けます。**5列目（プロトコル）は空欄にしておくと SSH / Telnet 両方に自動マッチする**ため最も確実です。

```csv
# パターン名,ユーザー名,パスワード,管理者パスワード,プロトコル
Cisco標準,admin,Pass1234,EnablePass123,
Cisco初期,cisco,cisco,cisco,
Linux標準,user1,Pass1234,,
運用Telnet専用,operator,Pass1234,,telnet
```

### 2. `device.csv`（機器リスト）
基本は **7列（カンマ6個）** で記載します。`credentials.csv` を使う場合は 3〜5 列目を空欄（`,,,,`）にします。

```csv
# ホスト名,IPアドレス,ユーザー名,パスワード,管理者パスワード,プロトコル,コマンドファイル
# 1. credentials.csv 自動ログイン（プロトコル空欄 = SSH → Telnet 自動試行）
AUTO-NODE-01,192.168.10.1,,,,,commands_cisco.txt
# 2. credentials.csv 自動ログイン（SSH 指定）
RT-JUNIPER-01,192.168.10.4,,,,ssh,commands_juniper.txt
# 3. 機器固有の認証情報を直接指定
RT-CISCO-01,192.168.10.1,admin,Pass1234,EnablePass123,ssh,commands_cisco.txt
# 4. Telnet 機器
SW-LEGACY-01,192.168.10.5,admin,Pass1234,,telnet,commands_cisco.txt
```

※ `ホスト名,IPアドレス,ssh,commands_cisco.txt` のような 4 列短縮形式も自動補正して動作します。
※ 文字コードは **UTF-8（BOMなし）または ASCII**、先頭ヘッダには `#` を付けて保存してください。

---

## 実行手順

1. `device.csv` と `credentials.csv`（必要に応じて `commands_*.txt`）を編集します。
2. Tera Term を起動し、**[コントロール] > [マクロ]** から `device_runner.ttl` を選択します（踏み台に接続済みの状態で起動するとそのまま踏み台セッションを流用します）。
3. ポップアップ画面で対象 CSV・パスワード CSV・ログ保存先フォルダを確認し、踏み台サーバの接続情報（未接続時）を入力します。
4. 完了後、指定フォルダ（デフォルト `logs/`）に機器別ログと各種サマリ CSV が出力されます。

---

## テスト・メンテナンスコマンド

```bash
make check   # Tera Term 4.x 互換性・構文・演算子優先順位チェック
make test    # 全単体テスト & E2E 模擬環境テスト実行
make clean   # 生成された logs/ や __pycache__/ を削除
```
