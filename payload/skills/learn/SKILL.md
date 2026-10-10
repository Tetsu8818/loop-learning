---
name: learn
description: Manually extract learnings from the current session and promote them into project memory. Use when the user runs /learn, or asks to "save what we learned", "覚えておいて" for the whole session (not a single fact), or before ending a session where SessionEnd hooks might not fire (app force-quit, crash risk). This is the manual fallback for the automatic SessionEnd hook — see ~/.claude/rules/self-improve.md for the promotion criteria this skill follows.
user_invocable: true
triggers:
  - /learn
  - 今回学んだことを保存して
  - セッションの知見を記録して
argument-hint: (no arguments)
---

# /learn — 手動での知見抽出・昇格

**前提として読むもの:** [~/.claude/rules/self-improve.md](../../rules/self-improve.md) —
昇格するかどうかの判断基準はここに定義されている。このスキルはその基準を
「今の会話全体」に対してその場で適用する。

自動の SessionEnd フック（`~/.claude/hooks/session_end_learn.py`）は
Haiku にログを渡して抽出させるが、このコマンドは違う。**あなた自身が
この会話全体を実際に見ているので、Haiku を経由せず、あなた自身が
抽出から判断・書き込みまで一貫して行う。**

## 手順

1. **この会話を振り返る。** ユーザーからの訂正、確認済みの方針、判明した
   環境制約、繰り返し使えるパターンを洗い出す。コードや git 履歴から
   読み取れることは対象外。

2. **`~/.claude/rules/self-improve.md` の基準で判断する。** 一般的で
   次回以降も成立するものだけを残す。そのセッション限りの一時的な話は
   捨てる。

3. **このプロジェクトの INBOX も一緒に処理する。** 存在すれば
   `~/.claude/learnings/<このプロジェクトのディレクトリ名>/INBOX.md`
   を読み、同じ基準で判断する。判断済みの行は削除する。

4. **昇格するものを書く。** 既存の `memory/` の作法に従う:
   - 1事実1ファイル、frontmatter (`name`, `description`, `metadata.type`)
   - `MEMORY.md` に1行ポインタを追記
   - 関連する既存メモリがあれば `[[name]]` でリンク
   - 既に近い内容のファイルがあれば新規作成せず更新する

5. **何を昇格し、何を捨てたかを短く報告する。** 「◯件保存、△件は
   一時的な内容のため見送り」程度でよい。捨てたものを列挙する必要はない。

## やらないこと

- Haiku や `claude` CLI を呼ばない（あなた自身が会話を見ているので不要）
- 確信の持てない内容を推測で `memory/` に書かない。迷ったら user に確認する
