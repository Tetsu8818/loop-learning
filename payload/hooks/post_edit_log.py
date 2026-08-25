"""PostToolUse フック (matcher: Edit|Write|NotebookEdit)。

LLM は呼ばない。触ったファイルパスを JSONL に1行追記するだけ。
セッション終了時の知見抽出で「何を変更したセッションか」の補助情報として
将来使う可能性があるための記録用途。現時点では session_end_learn.py は
これを読まない（トランスクリプト側に同じ情報が残っているため）。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_transcript import LEARNINGS_DIR, is_child_invocation, log, project_slug_from_transcript, read_hook_input  # noqa: E402


def main() -> int:
    if is_child_invocation():
        return 0

    hook_input = read_hook_input()
    transcript_path = hook_input.get("transcript_path", "")
    if not transcript_path:
        return 0

    tool_name = hook_input.get("tool_name", "")
    file_path = hook_input.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return 0

    proj = project_slug_from_transcript(transcript_path)
    proj_dir = LEARNINGS_DIR / proj
    try:
        proj_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tool": tool_name,
            "file": file_path,
        }
        with (proj_dir / "edited-files.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log("ERROR", f"post_edit_log: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
