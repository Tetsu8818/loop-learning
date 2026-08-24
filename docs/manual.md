# 運用マニュアル

導入手順・設計判断は [README.md](../README.md) / [design.md](design.md) 参照。
本書は「導入後、日常どう付き合うか」だけを扱う。

## 1. 何もしなくていい部分（自動）

- **セッションを開くたび**（`SessionStart`）、前回までの未処理トランスクリプトを
  1本だけ拾い直し、知見候補が Haiku で抽出されて
  `~/.claude/learnings/<proj>/INBOX.md` に積まれる。非同期実行なので、
  この処理待ちで操作がブロックされることはない。**実務上、抽出が成立して
  いるのはこの経路である**（理由は4節）。
- **セッションを終えるたび**（`SessionEnd`）にも同じ抽出が走る設計だが、
  デスクトップアプリの長寿命セッションではほぼ発火しない（4節）。
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
3. `~/.claude/rules/self-improve.md`
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
抽出・判断・保存まで行う（実体は `~/.claude/skills/learn/SKILL.md`）。
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

`SKIP` は異常ではない。特に `digest too short` と
`no transcript on disk` は、中身のない短命セッションで毎回出るので
気にしなくてよい（後者の事情は4節b）。

`ERROR` が出たら4節を先に見る。既知の2パターンに当てはまらない
`ERROR` は新しい不具合なので、その日のログごと記録に残すこと。

## 4. 既知の問題（2026-08-24 時点）

### 未解決

**(a) `read_hook_input` の JSON 破損 — 原因未特定**

- 症状: `[ERROR] read_hook_input failed: Expecting ',' delimiter: line 1 column N`
- 影響: **そのフック呼び出しが丸ごと無効になる。**`session_id` も
  `transcript_path` も取れないため、処理を始める前に return する。
  サイレントに落ちるので、ログを見ない限り気づけない。
- 実測: 2026-08-24 に4回（15:48 / 16:35 / 16:48 に2回）。
- 否定済みの仮説2本: cp932 の1文字混入 / 2文字ぶんのアラインメントずれ。
  どちらも再現実験で否定した（design.md §15）。
- 打った手: stdin をバイト列で読んで UTF-8 で明示デコードするよう変更した
  （これは別の実バグで、修正の正しさは実測で確認済み）。加えて、失敗時に
  生の入力を `~/.claude/hooks/logs/badinput-<時刻>-<スクリプト名>.bin` に
  保存するようにした。
- 状態: キャプチャを入れた 17:38 以降、実発生はゼロ。ただし発生頻度が
  1日数回の散発なので、**短時間の無発生は直った証拠にならない。**
  `badinput-*.bin` が1件出るまで判断を保留する。

**(b) `SessionEnd` 経由の抽出が実質機能しない**

- 症状: `[SKIP] session_end_learn: no transcript on disk`
- 実測: 導入から5日間、この経路での抽出は0件。同メッセージは1日十数回出る。
- 推論（**未確定**）: デスクトップアプリは短命なセッションを多数作っては
  捨てており、`SessionEnd` が発火しているのはそちら。中身が無いので
  トランスクリプトファイル自体が生成されない。一方、実作業をしている
  長寿命セッション（実測で23時間超のものがある）は、アプリを閉じるまで
  `SessionEnd` を出さない。
- 回避: `SessionStart` の拾い直し（`session_start_catchup.py`）が前回までの
  トランスクリプトを見に行くため、実務上の抽出はそちらで成立している。
  この経路を止めない限り、(b) は実害として顕在化しない。

### 解決済み（同じ症状を見たときのために残す）

| 症状 | 原因 | 対処 |
|---|---|---|
| 黒い `claude` コンソール窓が定期的に一瞬開く | `call_claude_cli` に `creationflags` が無く、コンソールを持たない親から起動された子がコンソールを確保していた | `CREATE_NO_WINDOW` を付与。A/B実測でウィンドウ0件を確認 |
| `exit 1` / `exit 129` で原因が出ない | CLI が `Error: Exceeded USD budget` を **stdout** に書くのに、stderr しか読んでいなかった | 両ストリームを読んでログに出す。同時にダイジェスト上限を 20,000 文字、予算上限を $0.15 に調整 |
| 抽出が最大15分間まったく動かない | worker が異常終了してロックを解放せず、残ったロックが後続を止めていた | ロック保持者の PID の生存を確認し、死んでいれば回収する |
| 要約が会話の続きを書き出す | プロンプトが「指示→データ」の並びで、末尾のログに引きずられていた | データを `<transcript>` で囲み、指示を後ろに置く形へ統一 |
| 日本語を含む入力で `UnicodeDecodeError` | Windows の既定 cp932 で stdin を読んでいた | バイト列で読んで UTF-8 明示デコード |

経緯と実測値は [design.md](design.md) の §11〜§15 に残してある。

## 5. ファイル配置早見表

| 用途 | パス |
|---|---|
| フック実行ログ | `~/.claude/hooks/logs/YYYY-MM-DD.log` |
| 知見INBOX（プロジェクト別） | `~/.claude/learnings/<proj>/INBOX.md` |
| 知見詳細（frontmatter付き） | `~/.claude/learnings/<proj>/YYYY-MM-DD-<sid8>.md` |
| 編集ファイルログ | `~/.claude/learnings/<proj>/edited-files.jsonl` |
| 圧縮前スナップショット | `~/.claude/snapshots/session-<id>-<timestamp>.md` |
| 拾い直しの処理済み記録 | `~/.claude/learnings/<proj>/processed.json` |
| 抽出の排他ロック | `~/.claude/learnings/<proj>/extract.lock` |
| 壊れた入力の生バイト列（4節a） | `~/.claude/hooks/logs/badinput-<時刻>-<スクリプト名>.bin` |
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
| `SessionStart` | `startup\|resume\|clear` | `session_start_catchup.py` | 180s | ○ |
| `PostToolUse` | `Edit\|Write\|NotebookEdit` | `post_edit_log.py` | 15s | ○ |
| `PreCompact` | — | `pre_compact_snapshot.py` | 120s | — |

すべて Python 実行ファイル
（`C:\Users\yanagawa\AppData\Local\Programs\Python\Python312\python.exe`）
を直接呼ぶ exec 形式。シェル経由のクォート展開を通らない。

## 7. コスト

`call_claude_cli()` の呼び出し1回あたり `--max-budget-usd 0.15` で
歯止め（`lib_transcript.DEFAULT_MAX_BUDGET_USD`）。Haiku を呼ぶのは
`SessionEnd` / `SessionStart` の拾い直し / `PreCompact` の3か所。
`session_start_inbox` と `PostToolUse` は LLM を使わないのでゼロ。

**1回あたりの実費は $0.04〜$0.10 です**（2026-08-24 実測）。以前ここに
書いてあった「$0.01未満」は誤りでした。内訳と測り方は
[design.md §6](design.md) 参照。固定費が約 $0.023 あるため、会話が短くても
$0.04 は下回りません。

同じ入力でも実費は揺れます（出力トークン数が実行ごとに2.5倍変わる）。
上限 0.15 はその上振れを見込んだ値です（[design.md §13](design.md)）。

## 8. テストとアンインストール

```bash
python tests/run_offline.py    # claude CLI を呼ばないオフラインテスト
```

```powershell
powershell -File uninstall.ps1              # hooks/rules/skill を削除、learnings/snapshots は残す
powershell -File uninstall.ps1 -RemoveData  # learnings/snapshots も削除
```
