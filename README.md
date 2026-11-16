# ネットワーク機器・サーバ 参照系ログ自動収集マクロ (Tera Term 4.x互換)

踏み台サーバ経由で複数のネットワーク機器（Cisco / Juniper / Yamaha / Huawei / Linux 等）へ順次SSH/Telnet接続し、安全に参照系コマンド（showコマンド等）の実行とログ収集を自動化するTera Termマクロ（TTL）です。

## 主な特徴

- **ログイン前 機器種別不明への完全自動適応**:
  - ログイン前の時点で機器の種類が分かっていなくても、認証ループによりユーザ名要求（`login:`, `Username:`, `User:` 等）、パスワード要求（`Password:`, `パスワード:`）、SSH初回鍵確認（`yes/no`）、バナー（Enter/any key）を自動検知してログインを突破します。
  - コマンドファイル列が空欄の場合は、全ベンダーの基本情報取得コマンドを安全に網羅した `commands_universal.txt` を自動適用します。
- **特権昇格 (enable) の自動判別 & Yamahaフォールバック**:
  - ログイン時のプロンプト記号（`#`, `$`, `%`, `]`）から特権モードやLinux/Juniperを自動判別し、不要な `enable` 試行を回避します。
  - 一般モード（`>`）の場合のみ `enable` を試行し、`enable` が無効（Yamahaルータ等）の場合は `administrator` コマンドへ自動フォールバックします。
  - それでも昇格できない場合でも、エラー中断せず通常モードのままコマンドを実行します。
- **ページ送り抑止の総当たり & 自動ページング解消 (`--More--` 対策)**:
  - 主要ベンダー向けページング解除コマンド（`terminal length 0`, `set cli screen-length 0`, `console lines infinity` 等）を自動送信します。
  - 万が一途中で `--More--` や `-- 続く --` 等が発生した場合も、マクロが自動検知してスペースキーを送信し、自動で最後までページ送りします。
- **欠損値・省略値への柔軟なフォールバック**:
  - `device.csv` 内のプロトコル（未指定時は `ssh`）、ユーザー名・パスワード（未指定時は踏み台情報を流用）、管理者パスワード（未指定時は通常パスワードを流用）の欠損に自動対応します。
- **実行結果サマリCSV（`summary_YYYYMMDD_HHMMSS.csv`）自動出力**:
  - 機器ごとの個別ログ（`ホスト名_日時.log`）に加えて、全機器の最終プロンプト、実行成否ステータス（`OK`/`NG`）、エラー詳細をまとめたCSVを自動生成します。

---

## ディレクトリ構成

```text
.
├── device_runner.ttl        # メインマクロ（マルチベンダー完全自動適応）
├── device.csv               # 接続対象機器リスト
├── commands_universal.txt   # 機種不明時用 汎用コマンド一覧
├── commands_cisco.txt       # Cisco向けコマンド一覧
├── commands_juniper.txt     # Juniper向けコマンド一覧
├── commands_linux.txt       # Linux向けコマンド一覧
├── Makefile                 # 自動テスト・構文チェック用
├── tests/
│   └── test_device_runner.py# 自動テストスイート
└── README.md                # 本ドキュメント
```

---

## 各設定ファイルの書き方

### `device.csv`（機器リスト）

カンマ区切りで各機器の情報を記載します。先頭が `#` の行やヘッダ行は自動スキップされます。

```csv
# ホスト名,IPアドレス,ユーザー名,パスワード,管理者パスワード,プロトコル,コマンドファイル
# 1. ログイン前に機種が完全に不明な場合（全て未指定で全自動適応）
UNKNOWN-NODE-01,192.168.10.99,,,,,
# 2. Cisco等 (管理者PW指定、専用コマンドファイル指定)
RT-CISCO-01,192.168.10.1,admin,Pass1234,EnablePass123,ssh,commands_cisco.txt
# 3. Cisco等 (管理者PW未記載 -> 通常PW流用)
SW-CISCO-02,192.168.10.2,admin,Pass1234,,ssh,commands_cisco.txt
# 4. Linux等 (enable不要機器)
SRV-LINUX-01,192.168.10.3,user1,Pass1234,,,commands_linux.txt
# 5. Juniper等 (踏み台クレデンシャル流用)
RT-JUNIPER-01,192.168.10.4,,,,ssh,commands_juniper.txt
# 6. レガシーTelnet機器
SW-LEGACY-01,192.168.10.5,admin,Pass1234,,telnet,commands_cisco.txt
```

#### 省略時の自動補完動作
- **コマンドファイル**: 空欄の場合、自動的に `commands_universal.txt` を適用。
- **プロトコル**: 空欄の場合、自動的に `ssh` を適用。
- **管理者パスワード**: 空欄の場合、ログインパスワードを流用。
- **ユーザー名・パスワード**: 空欄の場合、踏み台サーバのユーザー名・パスワードを流用。
- **ホスト名**: 空欄の場合、IPアドレスをログファイル名等に利用。

---

## 実行手順

1. `device_runner.ttl` の冒頭にある踏み台サーバ情報（`BASTION_HOST`、`BASTION_USER` 等）を確認・編集します。
2. Tera Term を起動し、メニューの **[コントロール] > [マクロ]** から `device_runner.ttl` を選択します。
3. 踏み台サーバのパスワード入力ダイアログが表示されるので入力して実行します。
4. 処理完了後、機器ごとの生ログファイル（`ホスト名_YYYYMMDD_HHMMSS.log`）およびサマリ一覧（`summary_YYYYMMDD_HHMMSS.csv`）が出力されます。

---

## テストの実行

プロジェクト内の整合性チェックおよび全自動適応ロジックの単体テストを実行できます：

```bash
make test
# または
make check
```
