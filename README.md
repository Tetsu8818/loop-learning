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
│   ├── lib_transcript.py            ← 共有ライブラリ
│   ├── session_end_learn.py         ← SessionEnd: 知見抽出 → INBOX
│   ├── session_start_inbox.py       ← SessionStart: INBOX 未処理を通知
│   ├── post_edit_log.py             ← PostToolUse: 編集ファイルを記録（LLM 不使用）
│   ├── pre_compact_snapshot.py      ← PreCompact: 作業状態のスナップショット
│   └── logs/YYYY-MM-DD.log          ← 全フックの実行ログ
├── rules/self-improve.md            ← INBOX → memory/ 昇格の判断基準
├── skills/learn/SKILL.md            ← /learn コマンド（手動フォールバック）
└── learnings/<project-slug>/
    ├── INBOX.md                     ← 未処理の知見（1行1件）
    ├── YYYY-MM-DD-<sid8>.md         ← 抽出詳細（frontmatter 付き）
    └── edited-files.jsonl           ← 編集ファイルのログ
```

## 動作の流れ

1. **セッション終了時** — `SessionEnd` フックが発火。トランスクリプトを
   ダイジェスト化し、Haiku（`--safe-mode` で子プロセス側のフックを無効化
   した状態で起動）に知見抽出させ、`learnings/<proj>/INBOX.md` に積む
2. **次回セッション開始時** — `SessionStart` フックが INBOX の未処理件数を
   通知（LLM は使わない）
3. **Claude 本体が判断** — `~/.claude/rules/self-improve.md` の基準で
   INBOX を精査し、価値あるものだけを既存の `memory/` の作法
   （frontmatter 付きファイル + `MEMORY.md` への1行ポインタ）で保存
4. **保険** — `/learn` で手動実行も可能（SessionEnd が発火しない場合用）

## 導入手順

1. CLI をインストール: `irm https://claude.ai/install.ps1 | iex`
2. ログイン: `claude /login`（ブラウザで Pro/Max アカウントを承認）
3. 疎通確認: `claude --version` / `claude -p "ping" --model claude-haiku-4-5-20251001`
4. `~/.claude/hooks/` に5本のスクリプトを配置（このリポジトリの設計に基づく）
5. `~/.claude/settings.json` をバックアップしてから `hooks` ブロックを追記
6. 新規セッションを開いて動作確認

## コスト

SessionEnd 1回あたり Haiku 4.5 換算で概算 $0.01 未満（`--max-budget-usd 0.05`
で歯止め）。PostToolUse は LLM を使わないのでゼロ。サブスク認証時に
これが従量課金か契約枠消費かは環境依存のため個別に確認すること。

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
