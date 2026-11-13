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
  導入から1か月の実績は0件（4節）。2026-09-18 から切り離し worker 方式に変え、
  以後は実セッションから届いている（09-19〜11-13 に worker の起動 43 回）。
- 拾い直しの対象は「更新から30分以上・30日以内・導入日時（`learnings/installed-at`）以降」の
  未処理トランスクリプト。1週間以上開かなかったプロジェクトも、30日以内なら拾われる。
- 3経路（SessionEnd・拾い直し・PreCompact）は `extract.lock` で直列化される。
  抽出中にコンテキスト圧縮が起きると、その回のスナップショットは見送られる（`[SKIP]`）。
- **編集ファイルの記録**（`PostToolUse` の `post_edit_log.py`）は、2026-11-13 に
  この PC の正本と settings.json から外してある（`hooks/_bak/` に退避）。
  `edited-files.jsonl` を読むコードは無い。配布物（payload・install.ps1）にはまだ
  残っており、扱いは未決（`tests.json` #8）。
- **状態ファイルの通知**（`SessionStart` の `state_report.py`）は、作業フォルダ直下に
  `tests.json` があるときだけ、未完了の一覧と直近の記録を出す。`state-files` スキルの
  一部で、このリポジトリの配布物には入っていない（扱いは `tests.json` #9）。
- **コンテキスト圧縮の直前**（`PreCompact`）、直近のやり取りが「実装済み /
  未完了 / 直面している問題」の3点に要約され、
  `~/.claude/snapshots/session-<id>-<timestamp>.md` に保存される。

## 2. 対応が要る部分

### セッション開始時に「未確認の知見があります」と出たら

`SessionStart` フックが、そのプロジェクトの `INBOX.md` の未処理件数と最古の日付を
会話冒頭に注入する（LLM は使わない、件数の機械通知）。最古の日付が古いほど、
昇格が追いついていない。これが出たら:

1. 通知に書かれた `INBOX.md` のパスを読む
2. 各行が指す詳細ファイル（`YYYY-MM-DD-<sid8>.md`）を読む
3. `~/.claude/rules/self-improve.md`
   の基準で「昇格する／しない」を判断する
4. 昇格するものは `memory/` の作法（frontmatter 付きファイル +
   `MEMORY.md` への1行ポインタ）で保存する
5. 判断済みの行を `INBOX.md` から削除する（詳細ファイルは監査用に残してよい）

この手順は Claude 本体が会話の中で自律的に行う想定。人間が直接
`INBOX.md` を編集する必要はない。

### セッション開始時に「知見の抽出が止まっています」と出たら

抽出の CLI 呼び出しが 3 回以上続けて失敗している。通知に連続回数・始まった日・直近の理由が出る。

- 理由が認証切れ（`Failed to authenticate` / `OAuth session expired`）なら、ターミナルで
  `claude /login` を実行する。フックが呼ぶ CLI はデスクトップアプリとは別に認証を持っている
- それ以外なら、当日のログで `CLI call failed` の行を読む（3節・4節）

成功が 1 回出れば通知は止まる。失敗したセッションは次回の起動でやり直される。

### セッション開始時に「メモリの棚卸しどきです」と出たら

INBOX の通知とは別系統。**すでに `memory/` に入ったものを見直す**合図。
そのプロジェクトを一度も棚卸ししていないか、前回の棚卸し後に5件以上増えたか、
前回から30日以上経つと出る。

**件数は絶対値ではなく「前回からの増加」で見る。**絶対値にすると、棚卸しを
終えた直後でも閾値を超えたまま毎回通知が出続け、読まれなくなるため。

| コマンド | 範囲 |
|---|---|
| `/memory-review` | 今のプロジェクトだけ。重複マージと陳腐化の除去 |
| `/memory-review all` | 全プロジェクト横断。**昇格候補も出す** |

横断のほうは、複数プロジェクトで成立する知識を `~/.claude/rules/` か
スキルへ引き上げる。memory はプロジェクトごとに隔離されているため、
**昇格しない限り他プロジェクトでは効かない。**

インデックスの不整合（`MEMORY.md` のリンク切れ・孤児）だけは確認なしで
直る。**それ以外はすべて提案止まりで、承認したものだけが適用される。**
他プロジェクトの文脈を持たないまま削除・マージすると、知識が消えるため。

判断基準は `~/.claude/skills/memory-review/SKILL.md` の「昇格先の決め方」にある。

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

`ERROR` が出たら4節を先に見る。既知のパターンに当てはまらない
`ERROR` は新しい不具合なので、その日のログごと記録に残すこと。

## 4. 既知の問題（2026-11-13 時点）

未解決の項目と対応の予定は、リポジトリ直下の `tests.json`（台帳）が正本。

### 未解決

**`claude` CLI が未ログインで、抽出が全件失敗している（2026-09-19〜）**

- 症状: `[ERROR] ...: CLI call failed: exit 1: Failed to authenticate: OAuth session expired and could not be refreshed.`
- 実測（2026-11-13）: 09-19 以降の `wrote` は 0 件、この認証エラーは 153 件。
  `claude auth status` は `"loggedIn": false`。最後の成功は 09-18。
- 対処: user 本人がターミナルで `claude /login` を実行する（`tests.json` #1）。
- 2026-11-13 の修正より前に失敗した 77 本は「処理済み」に記録されたままで、再ログイン
  しても自動では再試行されない。記録は残っている（`tests.json` #2）。
- 2026-11-13 以降は、CLI が失敗したセッションは次回の起動でやり直す（認証切れは回数無制限、
  それ以外は 3 回まで）。失敗が 3 回続くと、セッション開始時に「知見の抽出が止まっています」
  と通知が出る（[design.md §19](design.md)）。

**`SessionEnd` 経由の抽出は判定待ち**

- 症状（09-18 まで）: `[SKIP] session_end_learn: no transcript on disk`
- 実測: 導入から1か月（〜2026-09-18）、この経路での抽出は0件。同メッセージは350回、
  `digest too short (0 chars)` が89回。
- 2026-09-18 から抽出を切り離し worker で行う形に変えた。09-19〜11-13 の実測では
  worker の起動が 43 回あり、実セッションから届いている。ただし同じ期間は CLI が
  未ログインで `wrote` が 0 件のため、**残す・廃止の判定は再ログイン後に行う**
  （`tests.json` #6。[design.md §18](design.md)）。
- 推論（**未確定**）: デスクトップアプリは短命なセッションを多数作っては
  捨てており、`SessionEnd` が発火しているのはそちら。中身が無いので
  トランスクリプトファイル自体が生成されない。一方、実作業をしている
  長寿命セッション（実測で23時間超のものがある）は、アプリを閉じるまで
  `SessionEnd` を出さない。
- 回避: `SessionStart` の拾い直し（`session_start_catchup.py`）が前回までの
  トランスクリプトを見に行くため、実務上の抽出はそちらで成立している。
  この経路を止めない限り、実害としては顕在化しない。

### 解決済み（同じ症状を見たときのために残す）

| 症状 | 原因 | 対処 |
|---|---|---|
| 黒い `claude` コンソール窓が定期的に一瞬開く | `call_claude_cli` に `creationflags` が無く、コンソールを持たない親から起動された子がコンソールを確保していた | `CREATE_NO_WINDOW` を付与。A/B実測でウィンドウ0件を確認 |
| `exit 1` / `exit 129` で原因が出ない | CLI が `Error: Exceeded USD budget` を **stdout** に書くのに、stderr しか読んでいなかった | 両ストリームを読んでログに出す。同時にダイジェスト上限を 20,000 文字、予算上限を $0.15 に調整 |
| 抽出が最大15分間まったく動かない | worker が異常終了してロックを解放せず、残ったロックが後続を止めていた | ロック保持者の PID の生存を確認し、死んでいれば回収する |
| 要約が会話の続きを書き出す | プロンプトが「指示→データ」の並びで、末尾のログに引きずられていた | データを `<transcript>` で囲み、指示を後ろに置く形へ統一 |
| 日本語を含む入力で `UnicodeDecodeError` | Windows の既定 cp932 で stdin を読んでいた | バイト列で読んで UTF-8 明示デコード |
| フック入力が `Expecting ',' delimiter` で壊れる | 同上。cp932 は UTF-8 を「エラーにせず違う文字として」読むことがあり、長い入力ではアラインメントがずれて `,` や `"` が食われる | 同上。2026-08-25 に同一バイト列で再現し、読み方だけの違いで再現/解消することを実測（[design.md §16](design.md)） |
| `uninstall.ps1` 後に一部のフックと `/memory-review` が残る | 削除対象が初期の5本のまま、後から足した3本と `skills/memory-review` が漏れていた | 削除対象を `sync_payload.ps1` と同じ8本＋2スキルに揃えた（2026-09-18） |
| 1週間以上開かなかったプロジェクトの知見が拾われない | 拾い直しの対象が「7日以内」だった | 30日に延ばし、課金の遡りは導入日時で止める（2026-09-18、[design.md §18](design.md)） |

経緯と実測値は [design.md](design.md) の §11〜§19 に残してある。

## 5. ファイル配置早見表

| 用途 | パス |
|---|---|
| フック実行ログ | `~/.claude/hooks/logs/YYYY-MM-DD.log` |
| 知見INBOX（プロジェクト別） | `~/.claude/learnings/<proj>/INBOX.md` |
| 知見詳細（frontmatter付き） | `~/.claude/learnings/<proj>/YYYY-MM-DD-<sid8>.md` |
| 編集ファイルログ（2026-11-13 から記録停止） | `~/.claude/learnings/<proj>/edited-files.jsonl` |
| 圧縮前スナップショット | `~/.claude/snapshots/session-<id>-<timestamp>.md` |
| 拾い直しの処理済み記録 | `~/.claude/learnings/<proj>/processed.json` |
| CLI が失敗した回数（3 回で打ち切り。認証切れは数えない） | `~/.claude/learnings/<proj>/retries.json` |
| 抽出の排他ロック（**全体で1つ**） | `~/.claude/learnings/extract.lock` |
| 導入日時（拾い直しの下限） | `~/.claude/learnings/installed-at`（install.ps1 が作る。ISO 日時1行） |
| 壊れた入力の生バイト列（保険。現在は既知の原因なし） | `~/.claude/hooks/logs/badinput-<時刻>-<スクリプト名>.bin` |
| メモリ棚卸しの実行記録 | `~/.claude/learnings/memory-review-state.json` |
| 昇格判断の基準 | `~/.claude/rules/self-improve.md` |
| 手動昇格コマンド | `~/.claude/skills/learn/SKILL.md`（`/learn`） |
| メモリ棚卸しコマンド | `~/.claude/skills/memory-review/SKILL.md`（`/memory-review`） |
| メモリ走査スクリプト | `~/.claude/hooks/memory_scan.py`（LLM 不使用） |

ロックがプロジェクト別でないのは、競合する相手が `~/.claude.json` という
グローバルな資源だから。中身は「pid 取得時刻」の1行で、保持者が死んでいれば
次の取得時に回収される（4節の解決済み表）。

`<proj>` はトランスクリプトパスの親ディレクトリ名（Claude Code が
採番するプロジェクトスラッグ）。このプロジェクトの場合は
`C--Claude-Project-yanagawa-loop-learning`。

## 6. 設定内容（settings.json の hooks ブロック）

| イベント | matcher | 実行スクリプト | timeout | async |
|---|---|---|---|---|
| `SessionEnd` | — | `session_end_learn.py` | 180s | ○ |
| `SessionStart` | `startup\|resume\|clear\|compact` | `state_report.py` | 15s | — |
| `SessionStart` | `startup\|resume\|clear` | `session_start_inbox.py` | 15s | — |
| `SessionStart` | `startup\|resume\|clear` | `session_start_catchup.py` | 180s | ○ |
| `PreCompact` | — | `pre_compact_snapshot.py` | 120s | — |

この PC の `settings.json` の実際の内容（2026-11-13 実測）。`install.ps1` が別 PC に
追記する内容とは 2 点で違う: `state_report.py` は入らず、`PostToolUse` の
`post_edit_log.py` が入る（`tests.json` #8・#9 で揃える）。

`memory_scan.py` はフックではない。`/memory-review` から呼ばれる走査
スクリプトで、LLM を使わないため単体でも実行できる
（`python ~/.claude/hooks/memory_scan.py`）。

すべて Python 実行ファイル
（`C:\Users\yanagawa\AppData\Local\Programs\Python\Python312\python.exe`）
を直接呼ぶ exec 形式。シェル経由のクォート展開を通らない。

## 7. コスト

`call_claude_cli()` の呼び出し1回あたり `--max-budget-usd 0.03` で
歯止め（`lib_transcript.DEFAULT_MAX_BUDGET_USD`）。Haiku を呼ぶのは
`SessionEnd` / `SessionStart` の拾い直し / `PreCompact` の3か所。
`session_start_inbox` と `state_report` は LLM を使わないのでゼロ。

**1回あたりの実費は $0.0008〜$0.0039 です**（2026-11-13 実測、Haiku 5.5、8 回）。
所要時間は 11〜24 秒。内訳と測り方は [design.md §21](design.md) 参照。
Haiku 4.5 のとき（2026-08-24 実測）は $0.04〜$0.10 でした（[design.md §6](design.md)）。

実費は出力トークン数で決まり、同じ入力でも 5 倍ほど開きます。
上限 0.03 は実測最大の約 8 倍です。

## 8. テストとアンインストール

```bash
python tests/run_offline.py    # claude CLI を呼ばないオフラインテスト
```

```powershell
powershell -File uninstall.ps1              # hooks 8本/rules/skill 2本を削除、learnings/snapshots は残す
powershell -File uninstall.ps1 -RemoveData  # learnings/snapshots も削除
```
