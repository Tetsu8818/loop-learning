# 運用マニュアル

導入手順・設計判断は [README.md](../README.md) / [design.md](design.md) 参照。
本書は「導入後、日常どう付き合うか」だけを扱う。

## 1. 何もしなくていい部分（自動）

- **セッションを終えるたび**（`SessionEnd`）、そのプロジェクトのやり取りから
  知見候補が Haiku で抽出され、`~/.claude/learnings/<proj>/INBOX.md` に積まれる。
  非同期実行なので、この処理待ちで次の操作がブロックされることはない。
- **編集のたび**（`PostToolUse`, Edit/Write/NotebookEdit）、触ったファイルパスが
  `~/.claude/learnings/<proj>/edited-files.jsonl` に記録される。現状これを
  読むコードはなく、監査ログとして溜まるだけ。
- **コンテキスト圧縮の直前**（`PreCompact`）、直近のやり取りが「実装済み /
  未完了 / 直面している問題」の3点に要約され、
  `~/.claude/snapshots/session-<id>-<timestamp>.md` に保存される。

## 2. 対応が要る部分

### セッション開始時に「未確認の知見があります」と出たら

`SessionStart` フックが、そのプロジェクトの `INBOX.md` の未処理件数を
会話冒頭に注入する（LLM は使わない、件数の機械通知）。これが出たら:

1. 通知に書かれた `INBOX.md` のパスを読む
2. 各行が指す詳細ファイル（`YYYY-MM-DD-<sid8>.md`）を読む
3. [~/.claude/rules/self-improve.md](../../../.claude/rules/self-improve.md)
   の基準で「昇格する／しない」を判断する
4. 昇格するものは `memory/` の作法（frontmatter 付きファイル +
   `MEMORY.md` への1行ポインタ）で保存する
5. 判断済みの行を `INBOX.md` から削除する（詳細ファイルは監査用に残してよい）

この手順は Claude 本体が会話の中で自律的に行う想定。人間が直接
`INBOX.md` を編集する必要はない。

### `/learn` を手動で使う場面

- アプリの強制終了などで `SessionEnd` が発火しなかったとき
- セッション全体の知見をその場で確実に確定させたいとき

`/learn` は Haiku を経由せず、今の会話全体を Claude 本体が直接見て
抽出・判断・保存まで行う（[SKILL.md](../../../.claude/skills/learn/SKILL.md)）。
自動抽出より判断の精度が高い分、明示的に呼ぶ必要がある。

## 3. ログの読み方

場所: `~/.claude/hooks/logs/YYYY-MM-DD.log`（1日1ファイル、全フック共通）

形式: `<timestamp> [<LEVEL>] <message>`

| LEVEL | 意味 |
|---|---|
| `OK` | 正常完了（詳細ファイル書き込み・INBOX 通知など） |
| `SKIP` | 意図的にスキップ（子プロセスガード発動／ダイジェストが短すぎる／抽出結果が `NONE`） |
| `WARN` | 続行はするが異常（transcript ファイルが見つからない等） |
| `ERROR` | 処理失敗（CLI 呼び出し失敗など） |

`SKIP` は異常ではない。特に `digest too short` は「学習するほどの
やり取りがなかった」セッション（起動直後に閉じた等）で毎回出るので、
気にしなくてよい。

## 4. 既知の問題（実測、未解決）

**2026-08-20 のログで確認**: 他プロジェクトのセッション終了時、
`session_end_learn.py` が次のように `transcript not found` →
`digest too short (0 chars)` で処理をスキップするケースが複数回発生している。

```
14:16:38 [WARN] transcript not found: C:\Users\yanagawa\.claude\projects\C--Users-yanagawa\572561ed-....jsonl
14:16:38 [SKIP] session_end_learn: digest too short (0 chars), skip. session=572561ed-...
```

- 発生対象: `loop-learning` 以外の複数プロジェクト（`RevSTiKA`,
  `ping-all-machine` 等）を含む
- 影響: 該当セッションでは知見抽出が一切行われない（サイレントに
  スキップされるため、ログを見ない限り気づけない）
- 原因: **未確認**。`SessionEnd` は `async: true` で実行されるため、
  トランスクリプトファイルの書き込み完了前にフックが走っている
  可能性が高いと推測しているが、実装コードを見て確認したわけではない。
- 対処: 現時点では未対応。ログにこのパターンが頻発する場合は、
  `async` を外す／リトライを挟む等の修正が要るかもしれない。

このマニュアル作成の作業では原因調査までは行っていない。原因特定と
修正が必要か、user の判断を仰ぐこと。

## 5. ファイル配置早見表

| 用途 | パス |
|---|---|
| フック実行ログ | `~/.claude/hooks/logs/YYYY-MM-DD.log` |
| 知見INBOX（プロジェクト別） | `~/.claude/learnings/<proj>/INBOX.md` |
| 知見詳細（frontmatter付き） | `~/.claude/learnings/<proj>/YYYY-MM-DD-<sid8>.md` |
| 編集ファイルログ | `~/.claude/learnings/<proj>/edited-files.jsonl` |
| 圧縮前スナップショット | `~/.claude/snapshots/session-<id>-<timestamp>.md` |
| 昇格判断の基準 | `~/.claude/rules/self-improve.md` |
| 手動昇格コマンド | `~/.claude/skills/learn/SKILL.md`（`/learn`） |

`<proj>` はトランスクリプトパスの親ディレクトリ名（Claude Code が
採番するプロジェクトスラッグ）。このプロジェクトの場合は
`C--Claude-Project-yanagawa-loop-learning`。

## 6. 設定内容（settings.json の hooks ブロック）

| イベント | matcher | 実行スクリプト | timeout | async |
|---|---|---|---|---|
| `SessionEnd` | — | `session_end_learn.py` | 180s | ○ |
| `SessionStart` | `startup\|resume\|clear` | `session_start_inbox.py` | 15s | — |
| `PostToolUse` | `Edit\|Write\|NotebookEdit` | `post_edit_log.py` | 15s | ○ |
| `PreCompact` | — | `pre_compact_snapshot.py` | 120s | — |

すべて Python 実行ファイル
（`C:\Users\yanagawa\AppData\Local\Programs\Python\Python312\python.exe`）
を直接呼ぶ exec 形式。シェル経由のクォート展開を通らない。

## 7. コスト

`call_claude_cli()` の呼び出し1回あたり `--max-budget-usd 0.05` で
歯止め（`lib_transcript.py`）。`SessionEnd` と `PreCompact` のみ Haiku を
呼ぶ。`SessionStart` と `PostToolUse` は LLM を使わないのでゼロ。

## 8. テストとアンインストール

```bash
python tests/run_offline.py    # claude CLI を呼ばないオフラインテスト
```

```powershell
powershell -File uninstall.ps1              # hooks/rules/skill を削除、learnings/snapshots は残す
powershell -File uninstall.ps1 -RemoveData  # learnings/snapshots も削除
```
