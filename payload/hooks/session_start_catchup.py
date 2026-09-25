"""SessionStart フック（拾い直し）。

SessionEnd が取りこぼした過去セッションのトランスクリプトから知見を抽出する。

なぜ必要か:
  2026-08-24 の実測で、SessionEnd フック自体は正常に動くこと、しかし
  デスクトップアプリの長寿命セッションからは5日間で一度も届かなかったことが
  判明した（90回の発火はすべてトランスクリプトを持たない短命セッション由来、
  抽出成功は0件）。「終了の瞬間を捕まえる」設計をやめ、次にセッションを
  開いたときに未処理分を拾う。SessionStart が実運用で発火することは
  session_start_inbox.py の稼働ログで確認済み。

安全弁:
  - 1回の起動で処理するのは最大1本。取りこぼしが溜まっていても一気に走らない
  - IDLE_SECONDS 以内に更新のあるファイルは「まだ書き込み中」とみなし対象外
  - MAX_AGE_DAYS より古いものは対象外
  - learnings/installed-at（導入日時）より古いものは対象外。導入時に過去の全セッションを
    さかのぼって課金することを防ぐ。ファイルが無ければこの条件は掛けない
  - processed.json に記録済みのものは対象外（SessionEnd と二重処理しない）
  - 抽出できてもできなくても processed に記録する。失敗を毎回リトライしない

このフックは stdout に何も出さない（文脈を汚さない）。抽出結果の通知は
次回起動時に session_start_inbox.py が行う。

プロセスを切り離す理由（2026-08-24 実測）:
  async フックとして直接 Haiku を呼ぶと、セッションが短い場合に親プロセスの
  終了で道連れになる。実測では `--print` の1往復セッションで、対象の選定
  （INFO ログ）まで進んだあと完走せずに殺された。そのためフック本体は
  「対象を選んで、切り離した worker を起動して、即座に終わる」だけにし、
  実際の抽出は DETACHED_PROCESS で起動した別プロセスが行う。

  worker を起動する前に processed へ記録する（claim）。二重起動と二重課金を
  防ぐためで、代償として worker が異常終了するとその1本は再試行されない。
  その場合は ERROR としてログに残るので、握りつぶしにはならない。
"""
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_extract import (  # noqa: E402
    acquire_lock,
    load_processed,
    mark_processed,
    release_lock,
    run_worker,
    spawn_detached_worker,
)
from lib_transcript import (  # noqa: E402
    CLAUDE_HOME,
    LEARNINGS_DIR,
    is_child_invocation,
    log,
    project_slug_from_transcript,
    read_hook_input,
)

IDLE_SECONDS = 1800  # 30分。これ未満の更新があるものは進行中とみなす
# 7日だと、1週間以上開かなかったプロジェクトの知見が永久に拾われなかった。
# 課金の遡りは INSTALLED_AT_FILE で止めるので、ここは取りこぼし防止を優先する。
MAX_AGE_DAYS = 30
INSTALLED_AT_FILE = "installed-at"


def installed_at(learnings_dir: Path = LEARNINGS_DIR) -> float | None:
    """導入日時（epoch 秒）。ファイルが無い・読めなければ None（フロアを掛けない）。"""
    try:
        text = (learnings_dir / INSTALLED_AT_FILE).read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(text).timestamp()
    except (OSError, ValueError) as e:
        if not isinstance(e, FileNotFoundError):
            log("WARN", f"catchup: {INSTALLED_AT_FILE} を読めない ({e})、導入日の制限なしで続行")
        return None


def find_candidate(
    proj_slug: str,
    current_session_id: str,
    claude_home: Path = CLAUDE_HOME,
    learnings_dir: Path = LEARNINGS_DIR,
) -> Path | None:
    """このプロジェクトで抽出対象にすべきトランスクリプトを1本選ぶ。

    条件を満たすもののうち、最後に更新されたもの（＝直近に終わったもの）を返す。
    """
    proj_transcripts = claude_home / "projects" / proj_slug
    if not proj_transcripts.is_dir():
        return None

    processed = load_processed(learnings_dir / proj_slug)
    floor = installed_at(learnings_dir)
    now = time.time()
    candidates = []
    for f in proj_transcripts.glob("*.jsonl"):
        sid = f.stem
        if sid == current_session_id or sid in processed:
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        age = now - mtime
        if age < IDLE_SECONDS or age > MAX_AGE_DAYS * 86400:
            continue
        if floor is not None and mtime < floor:
            continue
        candidates.append((mtime, f))

    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def main() -> int:
    if is_child_invocation():
        return 0

    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        return run_worker(Path(sys.argv[2]), "catchup", "catchup")

    hook_input = read_hook_input()
    transcript_path = hook_input.get("transcript_path", "")
    current_session_id = hook_input.get("session_id", "")
    if not transcript_path:
        return 0

    # 先にロックを取る。取れなければ他のセッションが抽出中なので、今回は何もしない
    # （次にセッションを開いたときに拾い直せばよい）。
    if not acquire_lock():
        return 0

    try:
        proj_slug = project_slug_from_transcript(transcript_path)
        target = find_candidate(proj_slug, current_session_id)
        if target is None:
            release_lock()
            return 0

        # claim してから起動する。順序を逆にすると、起動が速い場合に worker 側の
        # find_candidate と競合して同じ対象を二重に処理しうる。
        mark_processed(LEARNINGS_DIR / proj_slug, target.stem)
        log("INFO", f"catchup: dispatched {target.name} in {proj_slug}")
        spawn_detached_worker(Path(__file__), target)
    except Exception:
        # worker を起動できなかった場合、解放しないと LOCK_STALE_SECONDS の間
        # 抽出が止まる。
        release_lock()
        raise
    # 正常に起動できたら、解放は worker の責任（run_worker の finally）。
    return 0


if __name__ == "__main__":
    sys.exit(main())
