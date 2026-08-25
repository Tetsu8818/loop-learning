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
  - MAX_AGE_DAYS より古いものは対象外。導入時に過去の全セッションを
    さかのぼって課金することを防ぐ
  - processed.json に記録済みのものは対象外（SessionEnd と二重処理しない）
  - 抽出できてもできても processed に記録する。失敗を毎回リトライしない

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
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_extract import (  # noqa: E402
    acquire_lock,
    extract_and_store,
    load_processed,
    mark_processed,
    release_lock,
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
MAX_AGE_DAYS = 7


def find_candidate(proj_slug: str, current_session_id: str) -> Path | None:
    """このプロジェクトで抽出対象にすべきトランスクリプトを1本選ぶ。

    条件を満たすもののうち、最後に更新されたもの（＝直近に終わったもの）を返す。
    """
    proj_transcripts = CLAUDE_HOME / "projects" / proj_slug
    if not proj_transcripts.is_dir():
        return None

    processed = load_processed(LEARNINGS_DIR / proj_slug)
    now = time.time()
    candidates = []
    for f in proj_transcripts.glob("*.jsonl"):
        sid = f.stem
        if sid == current_session_id or sid in processed:
            continue
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        if age < IDLE_SECONDS or age > MAX_AGE_DAYS * 86400:
            continue
        candidates.append((f.stat().st_mtime, f))

    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def spawn_worker(target: Path) -> None:
    """抽出を行う worker を、このプロセスから切り離して起動する。

    親（Claude Code のセッション）が終了しても生き残らせる必要があるため、
    Windows の DETACHED_PROCESS を使う。標準入出力は捨てる。
    """
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, name, 0)
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(target)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def run_worker(target: Path) -> int:
    """切り離されたプロセスとして実行される側。実際の抽出を行う。

    ロックは呼び出し元のフックが取得済み。ここで必ず解放する。
    """
    try:
        if not target.exists():
            log("ERROR", f"catchup worker: target vanished: {target.name}")
            return 0
        ok = extract_and_store(str(target), target.stem, "catchup", "catchup")
        if not ok:
            log("INFO", f"catchup worker: nothing stored for {target.name}")
        return 0
    finally:
        release_lock()


def main() -> int:
    if is_child_invocation():
        return 0

    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        return run_worker(Path(sys.argv[2]))

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
        spawn_worker(target)
    except Exception:
        # worker を起動できなかった場合、解放しないと LOCK_STALE_SECONDS の間
        # 抽出が止まる。
        release_lock()
        raise
    # 正常に起動できたら、解放は worker の責任（run_worker の finally）。
    return 0


if __name__ == "__main__":
    sys.exit(main())
