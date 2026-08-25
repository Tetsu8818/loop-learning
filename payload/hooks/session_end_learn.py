"""SessionEnd フック。

セッション終了時に1回だけ発火する。トランスクリプトを圧縮し、Haiku で
「次回以降に役立つ知見」を3件以内に抽出し、learnings/<proj>/ 配下に積む。

設計上の注意:
  - ここでは ~/.claude/projects/<proj>/memory/ には一切書き込まない。
    Haiku の生出力を直接 memory/ に混ぜると、frontmatter 付きの既存メモリ
    運用（MEMORY.md は1行インデックス、本文は別ファイル）を壊すため。
    昇格判断は次回セッションで Claude 本体が行う（SessionStart で通知）。
  - 失敗しても例外で落とさない。SessionEnd は結果を誰も見ないので、
    ログにだけ残して静かに exit する。
  - このフックだけでは取りこぼす。デスクトップアプリの長寿命セッションから
    SessionEnd が届いた実績がないため、session_start_catchup.py が
    次回セッション開始時に未処理分を拾い直す。抽出の実体は lib_extract.py に
    あり、両者で共有している。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_extract import acquire_lock, extract_and_store, mark_processed, release_lock  # noqa: E402
from lib_transcript import (  # noqa: E402
    LEARNINGS_DIR,
    is_child_invocation,
    log,
    project_slug_from_transcript,
    read_hook_input,
)


def main() -> int:
    if is_child_invocation():
        log("SKIP", "session_end_learn: child invocation guard triggered")
        return 0

    hook_input = read_hook_input()
    transcript_path = hook_input.get("transcript_path", "")
    session_id = hook_input.get("session_id", "unknown")
    reason = hook_input.get("reason", "unknown")

    if not transcript_path:
        log("WARN", "session_end_learn: no transcript_path in hook input, skip")
        return 0

    if not Path(transcript_path).exists():
        # トランスクリプトを残さない短命セッション。アプリが日常的に発火させるので
        # WARN では騒がしすぎる。拾い直しの対象にもならないため、静かに落とす。
        log("SKIP", f"session_end_learn: no transcript on disk. session={session_id}")
        return 0

    # 拾い直しの worker と同時に走ると、双方が claude.exe を起動して
    # ~/.claude.json を取り合う。取れなければ諦める（次回開始時に拾い直される）。
    if not acquire_lock():
        log("SKIP", f"session_end_learn: 抽出が既に走っているため見送る。session={session_id}")
        return 0

    try:
        proj_dir = LEARNINGS_DIR / project_slug_from_transcript(transcript_path)
        extract_and_store(transcript_path, session_id, reason, "session_end")
        mark_processed(proj_dir, session_id)
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
