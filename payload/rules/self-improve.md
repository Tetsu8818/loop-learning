# 自己改善プロトコル — INBOX からの学習昇格

SessionEnd フック（`~/.claude/hooks/session_end_learn.py`）が Haiku でセッションの知見候補を
抽出し `~/.claude/learnings/<proj>/INBOX.md` に積む。セッション開始時に `session_start_inbox.py`
が未処理件数を通知する（LLM なし）。通知が出ていれば着手前に目を通す。

## 位置づけ

INBOX は Haiku が会話ログだけから機械抽出した**未検証**の候補。直接 `memory/` に転記せず、
必ず以下の基準で精査する。処理の手順は `/learn`（[[learn]] skill）が正本。

## 昇格する / しない

**昇格する**（`memory/` に frontmatter 付き 1 事実 1 ファイル + `MEMORY.md` に 1 行）:
- 次回以降のセッションでも成立する一般的な事実・好み・環境の制約
- このセッション中に user から得た訂正・確認済みの方針
- 既存の `[[name]]` メモリと矛盾せず、重複しないもの

**昇格しない**（INBOX から削除してよい）:
- そのセッション限りの一時的な文脈
- リポジトリのコードや git 履歴を読めば分かること
- Haiku の誤読・過度な一般化に見えるもの
- 既存メモリと矛盾するが、どちらが正しいか未確認のもの → user に確認してから

判断済みの行は昇格の有無を問わず INBOX.md から削除する。詳細ファイルは監査用に残す。

## 横断昇格 — memory から rules / skill へ

memory から rules / skill への昇格先の判断基準は `/memory-review`（[[memory-review]] skill）が正本。
