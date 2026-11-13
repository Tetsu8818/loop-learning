"""SessionStart フック。会話冒頭に注入する通知を出す。LLM は呼ばない。

3系統ある。どれも独立に判定し、該当するものだけ出す。

1. 失敗の通知 — 抽出の CLI 呼び出しが続けて失敗しているか
2. INBOX 通知 — learnings/<proj>/INBOX.md に未処理の知見があるか
3. 棚卸し通知 — memory/ が溜まったか、前回の棚卸しから日が経ったか

SessionStart の stdout はそのまま文脈として Claude に渡る。
出すものが無ければ、何も出さずに静かに exit する。
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_transcript import (  # noqa: E402
    CLAUDE_HOME,
    LEARNINGS_DIR,
    LOG_DIR,
    is_auth_failure,
    log,
    project_slug_from_transcript,
    read_hook_input,
)

# 抽出の連続失敗を知らせる条件。
#
# 2026-09-19 から 8 週間、CLI の認証切れで抽出が 153 回続けて失敗していたが、どこにも
# 通知が出ず、ログを集計するまで気づけなかった（2026-11-13 実測）。
# 3 回にしたのは、単発のタイムアウトや予算超過（8〜9 月に計 9 件）では出さないため。
FAILURE_STREAK_THRESHOLD = 3
# さかのぼるログの本数（1 日 1 本）。成功がこの範囲に無ければ、範囲内の失敗をすべて数える。
FAILURE_SCAN_FILES = 30
CLI_FAILED_MARK = "CLI call failed: "
# CLI が応答を返したことを示すログ。書き込み・知見なし・体裁不正のどれも「CLI は動いた」。
CLI_ALIVE_MARKS = (": wrote ", "no learnings extracted", "output is not a bullet list")
MAX_REASON_CHARS = 120

MAX_ENTRIES_SHOWN = 5
INBOX_DATE_RE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\b")
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
    # 行は "- [YYYY-MM-DD sid8](file)" 形式（lib_extract.extract_and_store が書く）。
    # 滞留が見えるよう最古の日付を出す。形式外の行は無視する。
    dates = sorted(m.group(1) for l in lines if (m := INBOX_DATE_RE.match(l)))
    oldest = f"、最古 {dates[0]}" if dates else ""
    print("【自己改善システム】前回までのセッションで抽出された未確認の知見があります。")
    print(f"件数: {len(lines)}{oldest}（うち直近{len(shown)}件を表示）")
    print(f"ファイル: {inbox_path}")
    for l in shown:
        print(l)
    print(
        "処理するなら /learn。"
        "user から依頼が無い限り、勝手に処理しないでください。"
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


def failure_streak(log_dir: Path = LOG_DIR) -> tuple[int, str, str]:
    """直近の成功より後に続いている CLI 失敗を数える。

    返すのは (回数, 最初の失敗の日付, 直近の失敗理由)。失敗が無ければ (0, "", "")。
    ログを新しい順にたどり、CLI が応答した行に当たったところで止める。
    """
    count, first_date, last_reason = 0, "", ""
    files = sorted(log_dir.glob("????-??-??.log"), reverse=True)[:FAILURE_SCAN_FILES]
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if any(mark in line for mark in CLI_ALIVE_MARKS):
                return count, first_date, last_reason
            if CLI_FAILED_MARK in line:
                count += 1
                first_date = line[:10]
                if not last_reason:
                    reason = line.split(CLI_FAILED_MARK, 1)[1].split(" session=")[0]
                    last_reason = reason[:MAX_REASON_CHARS]
    return count, first_date, last_reason


def report_failures(log_dir: Path = LOG_DIR) -> bool:
    count, first_date, last_reason = failure_streak(log_dir)
    if count < FAILURE_STREAK_THRESHOLD:
        return False
    print("【自己改善システム】知見の抽出が止まっています。user に伝えてください。")
    print(f"連続失敗: {count} 回（{first_date} から。直近のログ {FAILURE_SCAN_FILES} 本で集計）")
    print(f"直近の理由: {last_reason}")
    if is_auth_failure(last_reason):
        print("対処: user 本人がターミナルで `claude /login` を実行する。")
    else:
        print(f"対処: {log_dir} の当日のログで `CLI call failed` の行を確認する。")
    return True


def main() -> int:
    # この PC の標準出力は既定が cp932 で、Claude Code は hook の stdout を UTF-8 として
    # 読むため、通知が文字化けして文脈に入っていた（2026-10-10 実測）。
    # memory_scan.py と同じく、自分の出力は自分で UTF-8 に固定する。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass  # reconfigure できないストリームではそのまま続ける

    hook_input = read_hook_input()
    # SessionStart には transcript_path が入っているので、そこから同じ規則で
    # プロジェクトディレクトリを特定する。
    transcript_path = hook_input.get("transcript_path", "")
    if not transcript_path:
        return 0

    proj = project_slug_from_transcript(transcript_path)
    # 3系統は独立。どれかが出なくても、残りは判定する。
    if report_failures():
        print()
    shown = report_inbox(proj)
    if shown:
        print()
    report_review(proj)
    return 0


if __name__ == "__main__":
    sys.exit(main())
