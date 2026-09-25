# Claude Code 自己改善システム

元記事: https://zenn.dev/miyan/articles/claude-code-self-improving-system

この記事のアイデア（セッションの知見を自動抽出し、次回セッションに
引き継ぐループ）を、この PC（Windows / claude CLI 未導入 / 既存の
Auto Memory 運用あり）向けに実装したもの。記事どおりには動かない箇所が
あったため変更している。差分と理由は [docs/design.md](docs/design.md) 参照。

**正本は `~/.claude/` 配下に置いてある。**フックのパスがプロジェクトの
移動・削除で壊れないようにするための意図的な分離で、設定ファイルは
常に絶対パスで `~/.claude/hooks/...` を指す。

このプロジェクトディレクトリ（`C:\Claude\Project\yanagawa\loop-learning\`）
には設計ドキュメント・オフラインテストに加えて、**他PCへ移植するための
配布用コピー**を `payload/` として置く。`payload/` は正本の
スナップショットであり、hooks 等を改修したら
`powershell -File sync_payload.ps1` で最新化してからコミットすること
（正本 → payload の一方向コピー）。

## 構成

```
~/.claude/
├── settings.json                    ← hooks ブロックを追加（既存設定は保持）
├── hooks/
│   ├── lib_transcript.py            ← 共有ライブラリ（読み取り・CLI 呼び出し）
│   ├── lib_extract.py               ← 共有ライブラリ（抽出プロンプトと書き込み）
│   ├── session_end_learn.py         ← SessionEnd: 知見抽出 → INBOX
│   ├── session_start_catchup.py     ← SessionStart: 取りこぼしの拾い直し（別プロセス）
│   ├── session_start_inbox.py       ← SessionStart: INBOX 未処理を通知
│   ├── post_edit_log.py             ← PostToolUse: 編集ファイルを記録（LLM 不使用）
│   ├── pre_compact_snapshot.py      ← PreCompact: 作業状態のスナップショット
│   ├── memory_scan.py               ← メモリ棚卸しの走査（LLM 不使用）
│   ├── logs/YYYY-MM-DD.log          ← 全フックの実行ログ
│   └── logs/badinput-*.bin          ← 壊れた入力の生バイト列（未解決問題の調査用）
├── rules/self-improve.md            ← INBOX → memory/ 昇格の判断基準
├── skills/learn/SKILL.md            ← /learn コマンド（手動フォールバック）
├── skills/memory-review/SKILL.md    ← /memory-review コマンド（棚卸しと昇格）
├── learnings/extract.lock           ← 抽出の排他ロック（プロジェクト別ではなく全体で1つ）
├── learnings/installed-at           ← 導入日時。拾い直しはこれより古いセッションを対象にしない
└── learnings/<project-slug>/
    ├── INBOX.md                     ← 未処理の知見（1行1件）
    ├── YYYY-MM-DD-<sid8>.md         ← 抽出詳細（frontmatter 付き）
    ├── processed.json               ← 抽出済みセッション ID（二重処理の防止）
    └── edited-files.jsonl           ← 編集ファイルのログ

C:\Claude\Project\yanagawa\loop-learning\    （このリポジトリ。SVN と git の二重管理 — CLAUDE.md 参照）
├── CLAUDE.md                        ← このリポジトリで作業する Claude 向けの前提
├── payload/                         ← 移植用の配布コピー（正本は ~/.claude/ 側）
│   ├── hooks/*.py                   ← 上記 hooks/ と同一内容（sync_payload.ps1 で同期）
│   ├── rules/self-improve.md
│   └── skills/{learn,memory-review}/SKILL.md
├── sync_payload.ps1                 ← 正本 → payload/ の同期（改修後、コミット前に実行）
├── install.ps1                      ← payload/ → 他PCの ~/.claude/ への配置
└── uninstall.ps1                    ← このPC上の自己改善システムを削除
```

## 動作の流れ

抽出の入口は2つある。**どちらか一方でも通れば知見は積まれる。**

1a. **セッション終了時** — `SessionEnd` フックが発火し、**切り離した別プロセス**に
   抽出させる。worker はトランスクリプトをダイジェスト化し、Haiku（`--safe-mode` で
   子プロセス側のフックを無効化した状態で起動）に知見抽出させ、`learnings/<proj>/INBOX.md` に積む

1b. **次回セッション開始時（拾い直し）** — `SessionStart` で
   `session_start_catchup.py` が、このプロジェクトの未処理トランスクリプトを
   1本選び、**切り離した別プロセス**に抽出させる。SessionEnd が届かなくても
   ここで拾える。`processed.json` により 1a と二重処理しない

2. **次回セッション開始時** — `session_start_inbox.py` が INBOX の未処理件数を
   通知（LLM は使わない）
3. **Claude 本体が判断** — `~/.claude/rules/self-improve.md` の基準で
   INBOX を精査し、価値あるものだけを既存の `memory/` の作法
   （frontmatter 付きファイル + `MEMORY.md` への1行ポインタ）で保存
4. **保険** — `/learn` で手動実行も可能

**なぜ入口が2つあるか。**1a だけでは、導入から5日間で実運用の抽出が0件だった。
詳細は [docs/design.md](docs/design.md) の8節。

## 運用実績と検証状況（2026-09-18 時点）

導入（2026-08-20）から約1か月のログの集計:

| 経路 | 実績 |
|---|---|
| 拾い直し（catchup）による抽出 | **79件**書き込み / 91回起動（残りは NONE・短すぎ等） |
| SessionEnd による抽出 | **0件**（テスト投入を除く）。9/18 に worker 方式へ変更し、実験中（[design.md §18](docs/design.md)） |
| PreCompact スナップショット | 25件 / 失敗3件（8月の予算超過。修正済み） |

| 検証項目 | 状態 |
|---|---|
| オフラインテスト（`tests/run_offline.py`） | ✅ 10件通過（2026-09-18） |
| SessionEnd の worker 方式（手動発火） | ✅ フックは即終了、worker が32秒で書き込み・ロック解放、再送は処理済みとして SKIP（2026-09-18） |
| PreCompact のロック待ち | ✅ ロック保持中は CLI を呼ばずに SKIP（2026-09-18） |
| SessionEnd の worker 方式（デスクトップアプリの実セッション） | ⏳ **未確認。**10/02 頃にログで判定する |

既知の問題は [docs/manual.md §4](docs/manual.md) にまとめてある。

## 導入手順

### 他PC（このシステムを既に使っている環境から移植する場合）

1. CLI をインストール: `irm https://claude.ai/install.ps1 | iex`
2. ログイン: `claude /login`（ブラウザで Pro/Max アカウントを承認）
3. 疎通確認: `claude --version` / `claude -p "ping" --model claude-haiku-4-5-20251001`
4. Python 3.12 系を導入し、PATH に通す（`python --version` で確認）
5. このリポジトリを移植先 PC に用意する（SVN checkout など）
6. `powershell -File install.ps1` を実行 — `payload/` の内容を
   `~/.claude/` に配置し、`settings.json` の `hooks` ブロックへ
   5エントリを追記する（バックアップは自動で
   `settings.json.bak-<timestamp>` に取る。再実行しても二重追加されない）
7. 新規セッションを開いて動作確認（`~/.claude/hooks/logs/` にログが出るか）

### 一から作る場合（元記事どおりに自分で組む場合）

1〜3 は上と同じ。
4. `~/.claude/hooks/` に8本のスクリプトを配置（このリポジトリの設計に基づく）
5. `~/.claude/settings.json` をバックアップしてから `hooks` ブロックを追記
6. 新規セッションを開いて動作確認

## コスト

Haiku 呼び出しは**実測 $0.04〜$0.10/回**（`--max-budget-usd 0.15` で歯止め）。
同じ入力でも実費が揺れる（出力トークン数が実行ごとに2.5倍変わる）ため、
上限は揺れの上振れを見込んだ値にしてある。
固定費が約 $0.023 あるため、会話が短くても $0.04 は下回らない。測り方と
内訳は [docs/design.md §6](docs/design.md)。
呼び出しが起きるのは **SessionEnd で1回、SessionStart の拾い直しで最大1回**。
拾い直しは1起動あたり1本までで、更新から30分未満のもの・30日より古いもの・
導入日時（`learnings/installed-at`）より前のものは対象外にしてある
（導入時に過去の全セッションをさかのぼって課金しないため）。
PostToolUse は LLM を使わないのでゼロ。サブスク認証時にこれが従量課金か
契約枠消費かは環境依存のため個別に確認すること。

## アンインストール

```powershell
powershell -File uninstall.ps1
```

`-RemoveData` を付けると `learnings/` と `snapshots/` も削除する。既定では
監査用に残す。

## テスト

```bash
python tests/run_offline.py
```

`claude` CLI を呼ばずに、トランスクリプトのパーサ・再帰防止ガード・
メモリ走査（`memory_scan.py`）・拾い直しの対象選定・CLI の所在解決を検証する。
fixture は `tests/fixtures/` に実セッションのトランスクリプトを1件置いてある。
