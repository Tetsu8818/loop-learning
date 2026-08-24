# Claude Code 自己改善システム

元記事: https://zenn.dev/miyan/articles/claude-code-self-improving-system

この記事のアイデア（セッションの知見を自動抽出し、次回セッションに
引き継ぐループ）を、この PC（Windows / claude CLI 未導入 / 既存の
Auto Memory 運用あり）向けに実装したもの。記事どおりには動かない箇所が
あったため変更している。差分と理由は [docs/design.md](docs/design.md) 参照。

**実体は `~/.claude/` 配下に置いてある。**このプロジェクトディレクトリ
（`C:\Claude\Project\yanagawa\loop-learning\`）には設計ドキュメントと
オフラインテストだけを置く。フックのパスがプロジェクトの移動・削除で
壊れないようにするための意図的な分離。

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
└── learnings/<project-slug>/
    ├── INBOX.md                     ← 未処理の知見（1行1件）
    ├── YYYY-MM-DD-<sid8>.md         ← 抽出詳細（frontmatter 付き）
    ├── processed.json               ← 抽出済みセッション ID（二重処理の防止）
    ├── extract.lock                  ← 抽出の排他ロック（同時実行の防止）
    └── edited-files.jsonl           ← 編集ファイルのログ
```

## 動作の流れ

抽出の入口は2つある。**どちらか一方でも通れば知見は積まれる。**

1a. **セッション終了時** — `SessionEnd` フックが発火。トランスクリプトを
   ダイジェスト化し、Haiku（`--safe-mode` で子プロセス側のフックを無効化
   した状態で起動）に知見抽出させ、`learnings/<proj>/INBOX.md` に積む

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

## 検証状況（2026-08-24 時点）

| 経路 | 状態 |
|---|---|
| 実トランスクリプトからの抽出（プロンプト含む） | ✅ 実測。会話途中で終わる記録でも箇条書き3件を正しく抽出 |
| SessionEnd → 抽出（実セッション、CLI） | ✅ 実測。有効な `transcript_path` が渡る |
| SessionStart → 拾い直し → INBOX（実セッション、CLI） | ✅ 実測。親 CLI 終了の25秒後に worker が完走 |
| 二重処理の防止 | ✅ 実測。`processed.json` により次の対象へ進む |
| 拾い直し（デスクトップアプリ） | ✅ 実測。2026-08-24 のログで INBOX への書き込みを複数回確認 |
| **SessionEnd → 抽出（デスクトップアプリの長寿命セッション）** | ❌ **5日間で0件。**短命セッションにだけ発火しているという推論はあるが未確定。拾い直し経路で回避している |

既知の問題（未解決1件・解決済み6件）は
[docs/manual.md §4](docs/manual.md) にまとめてある。

## 導入手順

1. CLI をインストール: `irm https://claude.ai/install.ps1 | iex`
2. ログイン: `claude /login`（ブラウザで Pro/Max アカウントを承認）
3. 疎通確認: `claude --version` / `claude -p "ping" --model claude-haiku-4-5-20251001`
4. `~/.claude/hooks/` に7本のスクリプトを配置（このリポジトリの設計に基づく）
5. `~/.claude/settings.json` をバックアップしてから `hooks` ブロックを追記
6. 新規セッションを開いて動作確認

## コスト

Haiku 呼び出しは**実測 $0.04〜$0.10/回**（`--max-budget-usd 0.15` で歯止め）。
同じ入力でも実費が揺れる（出力トークン数が実行ごとに2.5倍変わる）ため、
上限は揺れの上振れを見込んだ値にしてある。
固定費が約 $0.023 あるため、会話が短くても $0.04 は下回らない。測り方と
内訳は [docs/design.md §6](docs/design.md)。
呼び出しが起きるのは **SessionEnd で1回、SessionStart の拾い直しで最大1回**。
拾い直しは1起動あたり1本までで、更新から30分未満のものと7日より古いものは
対象外にしてある（導入時に過去の全セッションをさかのぼらないため）。
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

`claude` CLI を呼ばずに、トランスクリプトのパーサと再帰防止ガードだけを
検証する。fixture は `tests/fixtures/` に実セッションのトランスクリプトを
1件置いてある。
