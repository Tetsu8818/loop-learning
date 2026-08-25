"""SessionStart フック。会話冒頭に注入する通知を出す。LLM は呼ばない。

2系統ある。どちらも独立に判定し、該当するほうだけ出す。

1. INBOX 通知 — learnings/<proj>/INBOX.md に未処理の知見があるか
2. 棚卸し通知 — memory/ が溜まったか、前回の棚卸しから日が経ったか

SessionStart の stdout はそのまま文脈として Claude に渡る。
出すものが無ければ、何も出さずに静かに exit する。
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_transcript import (  # noqa: E402
    CLAUDE_HOME,
    LEARNINGS_DIR,
    log,
    project_slug_from_transcript,
    read_hook_input,
)

MAX_ENTRIES_SHOWN = 5
STATE_FILE = LEARNINGS_DIR / "memory-review-state.json"

# 棚卸しを促す条件。いずれかを満たせば通知する。
#
# 件数は「絶対値」ではなく「前回の棚卸しからの増加」で見る。絶対値にすると、
# 棚卸しを終えた直後でも件数が閾値を超えたまま毎回通知が出続ける
# （2026-08-25 実測: 1件を4件に分割した結果 11件になり、直後の起動でも発火した）。
# 通知が毎回出るなら、それは読まれなくなる。
REVIEW_GROWTH_THRESHOLD = 5
REVIEW_DAYS_THRESHOLD = 30


def _memory_counts(proj: str) -> tuple[int, int]:
    """(このプロジェクトの件数, 全プロジェクトの合計) を返す。"""
    projects = CLAUDE_HOME / "projects"
    if not projects.is_dir():
        return 0, 0

    def count(mem_dir: Path) -> int:
        return sum(1 for p in mem_dir.glob("*.md") if p.name != "MEMORY.md")

    total = 0
    mine = 0
    for mem_dir in projects.glob("*/memory"):
        n = count(mem_dir)
        total += n
        if mem_dir.parent.name == proj:
            mine = n
    return mine, total


def _review_state() -> dict:
    """前回の棚卸しの記録。無ければ空の辞書。"""
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def report_inbox(proj: str) -> bool:
    inbox_path = LEARNINGS_DIR / proj / "INBOX.md"
    if not inbox_path.exists():
        return False
    lines = [l for l in inbox_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return False

    shown = lines[-MAX_ENTRIES_SHOWN:]
    print("【自己改善システム】前回までのセッションで抽出された未確認の知見があります。")
    print(f"件数: {len(lines)}（うち直近{len(shown)}件を表示）")
    print(f"ファイル: {inbox_path}")
    for l in shown:
        print(l)
    print(
        "内容を確認し、次回以降も使える一般的な知見だけを、既存の memory/ の作法"
        "（frontmatter付きファイル + MEMORY.md への1行ポインタ）で保存してください。"
        "保存し終えたら INBOX.md から該当行を削除してください。"
    )
    log("OK", f"session_start_inbox: reported {len(lines)} pending entries. proj={proj}")
    return True


def report_review(proj: str) -> bool:
    mine, total = _memory_counts(proj)
    if mine == 0:
        return False

    state = _review_state()
    reviewed = (state.get("counts") or {}).get(proj)

    reasons = []
    if reviewed is None:
        # このプロジェクトはまだ一度も棚卸ししていない。
        reasons.append(f"棚卸しの実行記録がない（{mine}件）")
    else:
        grown = mine - reviewed
        if grown >= REVIEW_GROWTH_THRESHOLD:
            reasons.append(f"前回の棚卸し後に{grown}件増えた")
        try:
            days = (date.today() - date.fromisoformat(state["last_review"])).days
            if days >= REVIEW_DAYS_THRESHOLD:
                reasons.append(f"前回の棚卸しから{days}日")
        except Exception:
            pass
    if not reasons:
        return False

    print("【自己改善システム】メモリの棚卸しどきです。")
    print(f"理由: {' / '.join(reasons)}")
    print(f"件数: このプロジェクト {mine} 件 / 全プロジェクト合計 {total} 件")
    print(
        "整理するなら /memory-review（このプロジェクトのみ）または "
        "/memory-review all（全プロジェクト横断で、昇格候補も出す）。"
        "user から依頼が無い限り、勝手に実行しないでください。"
    )
    log("OK", f"session_start_inbox: review notice. proj={proj} mine={mine} "
              f"total={total} reviewed={reviewed} reasons={len(reasons)}")
    return True


def main() -> int:
    hook_input = read_hook_input()
    # SessionStart には transcript_path が入っているので、そこから同じ規則で
    # プロジェクトディレクトリを特定する。
    transcript_path = hook_input.get("transcript_path", "")
    if not transcript_path:
        return 0

    proj = project_slug_from_transcript(transcript_path)
    # 2系統は独立。片方が出なくても、もう片方は判定する。
    shown = report_inbox(proj)
    if shown:
        print()
    report_review(proj)
    return 0


if __name__ == "__main__":
    sys.exit(main())
