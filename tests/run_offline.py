"""claude CLI を叩かずに、パーサと再帰ガードだけを検証するオフラインテスト。

実行方法:
    python tests/run_offline.py

完了条件との対応:
  - 完了条件2/3 の前段として、build_digest() が実データから妥当な出力を
    作れているか、is_child_invocation() のガードが機能するかを確認する。
"""
import os
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / ".claude" / "hooks"
# 上の相対パスは環境依存になるため、まず明示パスを試し、無ければ環境変数で解決する。
if not HOOKS_DIR.exists():
    HOOKS_DIR = Path(os.path.expandvars(r"%USERPROFILE%\.claude\hooks"))

sys.path.insert(0, str(HOOKS_DIR))

FIXTURE = Path(__file__).parent / "fixtures" / "session-selfimprove-planning.jsonl"


def test_build_digest():
    from lib_transcript import build_digest

    digest = build_digest(str(FIXTURE))
    assert len(digest) > 200, f"digest too short: {len(digest)} chars"
    assert "User:" in digest, "digest missing User: turns"
    assert "Assistant:" in digest, "digest missing Assistant: turns"
    # 生の JSON 構造が漏れていないこと（パースに失敗して丸ごと文字列化していないか）
    assert '"type":"assistant"' not in digest, "raw JSON leaked into digest"
    print(f"[OK] build_digest: {len(digest)} chars, sample:\n---\n{digest[:300]}\n---")


def test_project_slug():
    from lib_transcript import project_slug_from_transcript

    slug = project_slug_from_transcript(str(FIXTURE))
    assert slug == "session-selfimprove-planning" or slug == "fixtures", f"unexpected slug: {slug}"
    # fixtures ディレクトリ配下に置いてあるので、親ディレクトリ名は "fixtures" になるはず
    print(f"[OK] project_slug_from_transcript: {slug}")


def test_guard():
    from lib_transcript import is_child_invocation

    assert is_child_invocation() is False, "guard should be False without env var"
    os.environ["CLAUDE_SELFIMPROVE_CHILD"] = "1"
    assert is_child_invocation() is True, "guard should be True with env var set"
    del os.environ["CLAUDE_SELFIMPROVE_CHILD"]
    print("[OK] is_child_invocation guard toggles correctly")


def _write_memory(root, proj, name, desc, body, index=True):
    d = root / proj / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        "metadata:\n  type: project\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    if index:
        with (d / "MEMORY.md").open("a", encoding="utf-8") as f:
            f.write(f"- [{name}]({name}.md) - {desc}\n")


def test_memory_scan():
    """メモリ棚卸しの走査。実データには触れず、一時ディレクトリで検証する。"""
    import tempfile

    from memory_scan import collect

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 同じ内容を2プロジェクトに置く → 完全重複として出るはず
        dup = "PnP PowerShell はこのテナントで使えない。Entra ID アプリ登録が未完了。"
        _write_memory(root, "projA", "shared-fact", "共通の制約", dup)
        _write_memory(root, "projB", "shared-fact", "共通の制約", dup)
        # インデックスに載せないファイル → orphan
        _write_memory(root, "projA", "lonely", "索引に無い", "本文", index=False)
        # 実体の無いファイルへのポインタ → broken_link
        with (root / "projA" / "memory" / "MEMORY.md").open("a", encoding="utf-8") as f:
            f.write("- [gone](gone.md) - 消えたメモリ\n")
        # rules/ を指す [[name]] は不整合ではない（実在するルール名を使う）
        _write_memory(root, "projB", "links-to-rule", "ルール参照",
                      "詳細は [[self-improve]] にある。")

        r = collect(root)

    assert r["total"] == 4, f"expected 4 memories, got {r['total']}"

    ident = r["identical_groups"]
    assert len(ident) == 1, f"expected 1 identical group, got {len(ident)}"
    assert sorted(ident[0]["members"]) == ["projA/shared-fact.md", "projB/shared-fact.md"]

    # `\b` がバックスペース文字として焼き込まれた実例があった（2026-08-25）
    assert r["corrupted"] == [], f"健全なファイルを破損と誤検出: {r['corrupted']}"

    kinds = {(i["kind"], i["target"]) for i in r["index_issues"]}
    assert ("orphan", "lonely.md") in kinds, f"orphan not detected: {kinds}"
    assert ("broken_link", "gone.md") in kinds, f"broken_link not detected: {kinds}"
    assert ("dangling_wikilink", "self-improve") not in kinds, \
        "rules/ を指す [[name]] を不整合として誤検出している"

    print(f"[OK] memory_scan: {r['total']}件、完全重複1組、orphan/broken_link を検出")


def test_memory_scan_control_chars():
    """制御文字の混入を拾えること。改行・タブは誤検出しないこと。"""
    import tempfile

    from memory_scan import collect

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 実例と同じ形: パス中の \b がバックスペース文字になっている
        _write_memory(root, "p1", "broken", "壊れたパス",
                      "C:\\Claude\\Project\\yanagawa\x08ihinyoyaku を参照する。")
        _write_memory(root, "p1", "clean", "健全", "改行\nとタブ\tは含んでよい。")
        r = collect(root)

    paths = {c["path"] for c in r["corrupted"]}
    assert paths == {"p1/broken.md"}, f"検出結果が想定と違う: {r['corrupted']}"
    found = r["corrupted"][0]["found"]
    assert found[0]["char"] == "0x8", f"文字の記録が誤っている: {found}"

    print("[OK] memory_scan 制御文字: 混入1件を検出、改行/タブは誤検出しない")


def test_memory_scan_similarity():
    """希少語の共有で近いペアを拾えること。頻出語だけの一致では拾わないこと。"""
    import tempfile

    from memory_scan import collect

    # 全文書に共通する頻出語。これだけ一致しても「近い」と判定してはいけない。
    common = "作業 手順 確認 実測 設定 記録 対応 内容 結果 状態"
    # 2件だけが共有する希少語。これが検出の根拠になる。
    rare = "mousedown pointerdown csp dom fetch クリップボード"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_memory(root, "p1", "browser-a", "ブラウザの制約", f"{common} {rare}")
        _write_memory(root, "p2", "browser-b", "ブラウザの操作", f"{common} {rare}")
        for i in range(6):
            _write_memory(root, "p3", f"noise{i}", f"無関係{i}",
                          f"{common} 固有語{i} 別語{i} 独自{i}")
        r = collect(root)

    pairs = {tuple(sorted((p["a"], p["b"]))) for p in r["similar_pairs"]}
    target = tuple(sorted(("p1/browser-a.md", "p2/browser-b.md")))
    assert target in pairs, f"希少語を共有するペアを検出できていない: {r['similar_pairs']}"
    assert len(pairs) == 1, f"頻出語だけのペアまで拾っている: {pairs}"

    hit = next(p for p in r["similar_pairs"] if tuple(sorted((p["a"], p["b"]))) == target)
    assert hit["cross_project"] is True, "プロジェクト横断の判定が誤っている"
    assert "csp" in hit["terms"], f"根拠の語が出力されていない: {hit['terms']}"

    print(f"[OK] memory_scan 類似判定: 1組を検出、根拠語 {' '.join(hit['terms'][:4])}")


def test_memory_scan_project_filter():
    """--project で絞っても「希少語」の意味が変わらないこと。

    絞り込み時に母集団まで絞ると、2件しかないプロジェクトでは frontmatter の
    定型語（how / why / user）が「希少語」になり誤検出する（2026-08-25 実測）。
    """
    import tempfile

    from memory_scan import collect

    common = "作業 手順 確認 実測 設定 記録 対応 内容 結果 状態"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 対象プロジェクトの2件は、頻出語しか共有していない
        _write_memory(root, "target", "a", "ひとつめ", f"{common} 固有語A")
        _write_memory(root, "target", "b", "ふたつめ", f"{common} 固有語B")
        # 母集団を作る他プロジェクト。common を全員が持つので頻出語になる
        for i in range(8):
            _write_memory(root, "other", f"n{i}", f"その他{i}", f"{common} 別語{i}")

        scoped = collect(root, only="target")
        whole = collect(root)

    assert scoped["total"] == 2, f"絞り込みが効いていない: {scoped['total']}"
    assert scoped["similar_pairs"] == [], \
        f"頻出語だけの2件を「近い」と誤検出した: {scoped['similar_pairs']}"
    assert {m["project"] for m in scoped["memories"]} == {"target"}
    assert whole["total"] == 10, f"全体走査が壊れている: {whole['total']}"

    print("[OK] memory_scan 絞り込み: 母集団を保ったまま報告だけを絞れている")


def test_memory_scan_stdout_encoding():
    """PYTHONIOENCODING が無くても標準出力で落ちないこと。

    この PC の既定は cp932 で、メモリの description に含まれる `—`（em dash）を
    書けずに UnicodeEncodeError で落ちていた（2026-08-25 実測）。テストのとき
    だけ環境変数を付けていたため、長く見えていなかった。実プロセスで確かめる。
    """
    import subprocess

    script = HOOKS_DIR / "memory_scan.py"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}

    for args in ([], ["--json"]):
        p = subprocess.run([sys.executable, str(script), *args],
                           capture_output=True, env=env)
        assert p.returncode == 0, (
            f"{args or ['(引数なし)']} で失敗した: "
            f"{p.stderr.decode('utf-8', errors='replace')[-400:]}"
        )
        assert p.stdout, f"{args} の出力が空"
        # 出力そのものが UTF-8 として読めること
        p.stdout.decode("utf-8")

    print("[OK] memory_scan 出力encoding: 環境変数なしで2形式とも正常終了")


def test_session_start_inbox_stdout_encoding():
    """SessionStart の通知が UTF-8 で出ること。

    Claude Code は hook の stdout を UTF-8 として読む。この PC の既定は cp932 のため、
    通知が文字化けしたまま文脈に入っていた（2026-10-10 実測）。実プロセスで、
    環境変数を付けずに確かめる。実データに依存しないよう CLAUDE_CONFIG_DIR を一時ディレクトリにする。
    """
    import json
    import subprocess
    import tempfile

    proj = "C--test-proj"
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        inbox = home / "learnings" / proj / "INBOX.md"
        inbox.parent.mkdir(parents=True)
        inbox.write_text("- [2026-10-01 abcd1234](2026-10-01-abcd1234.md) — 日本語の知見\n",
                         encoding="utf-8")
        hook_input = json.dumps({
            "session_id": "probe",
            "transcript_path": str(home / "projects" / proj / "probe.jsonl"),
            "source": "startup",
        }).encode("utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        env["CLAUDE_CONFIG_DIR"] = str(home)
        p = subprocess.run([sys.executable, str(HOOKS_DIR / "session_start_inbox.py")],
                           input=hook_input, capture_output=True, env=env)

    assert p.returncode == 0, f"失敗した: {p.stderr.decode('utf-8', errors='replace')[-400:]}"
    try:
        text = p.stdout.decode("utf-8")
    except UnicodeDecodeError as e:
        raise AssertionError(f"通知が UTF-8 で出ていない（{e.reason} at {e.start}）") from e
    assert "自己改善システム" in text and "/learn" in text, f"通知の文面が想定と違う: {text[:200]!r}"
    print("[OK] session_start_inbox 出力encoding: 環境変数なしで UTF-8 の通知を出す")


def test_candidate_floor():
    """拾い直しの対象選定。導入日より古いもの・期間外・処理済みを外すこと。"""
    import tempfile
    import time

    from session_start_catchup import MAX_AGE_DAYS, find_candidate

    day = 86400
    now = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        learn = Path(tmp) / "learnings"
        proj = home / "projects" / "p"
        proj.mkdir(parents=True)
        learn.mkdir()

        def put(name, age_days):
            f = proj / f"{name}.jsonl"
            f.write_text("{}\n", encoding="utf-8")
            t = now - age_days * day
            os.utime(f, (t, t))

        put("fresh", 0.001)             # 更新中（30分未満）→ 対象外
        put("recent", 2)                # 対象
        put("older", 20)                # 30日以内だが導入日より前 → フロア有りで対象外
        put("ancient", MAX_AGE_DAYS + 1)  # 期間外

        # フロア無し: 最新の "recent" が選ばれる
        got = find_candidate("p", "", claude_home=home, learnings_dir=learn)
        assert got is not None and got.stem == "recent", f"フロア無しで誤選定: {got}"

        # recent を処理済みにすると、フロア無しでは older が次の候補
        (learn / "p").mkdir()
        (learn / "p" / "processed.json").write_text('["recent"]', encoding="utf-8")
        got = find_candidate("p", "", claude_home=home, learnings_dir=learn)
        assert got is not None and got.stem == "older", f"処理済みの除外が効いていない: {got}"

        # 導入日を10日前にすると older（20日前）は対象外になり、候補なし
        from datetime import datetime
        floor_dt = datetime.fromtimestamp(now - 10 * day).astimezone()
        (learn / "installed-at").write_text(floor_dt.isoformat() + "\n", encoding="utf-8")
        got = find_candidate("p", "", claude_home=home, learnings_dir=learn)
        assert got is None, f"導入日フロアが効いていない: {got} (floor={floor_dt.isoformat()})"

    print(f"[OK] find_candidate: 更新中/期間外({MAX_AGE_DAYS}日超)/処理済み/導入日前を除外")


def test_resolve_claude_exe():
    """既定パスに CLI が無ければ PATH から探し、どちらにも無ければ None。"""
    import tempfile

    from lib_transcript import resolve_claude_exe

    exe_name = "claude.exe" if os.name == "nt" else "claude"
    saved_path = os.environ.get("PATH", "")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nowhere" / exe_name
            os.environ["PATH"] = tmp
            assert resolve_claude_exe(missing) is None, "どこにも無いのに見つかったことになっている"

            fake = Path(tmp) / exe_name
            fake.write_bytes(b"")
            if os.name != "nt":
                fake.chmod(0o755)
            got = resolve_claude_exe(missing)
            assert got is not None and got.name.lower() == exe_name, f"PATH 上の CLI を拾えない: {got}"

            assert resolve_claude_exe(fake) == fake, "既定パスが優先されていない"
    finally:
        os.environ["PATH"] = saved_path

    print("[OK] resolve_claude_exe: 既定パス優先 → PATH → None の順に解決")


def test_failure_streak():
    """抽出の連続失敗の通知（tests.json #3）。失敗が続くと出て、成功が挟まると出ない。"""
    import contextlib
    import io
    import tempfile

    from session_start_inbox import FAILURE_STREAK_THRESHOLD, failure_streak, report_failures

    auth = ("exit 1: Failed to authenticate: OAuth session expired and could not be refreshed.")
    fail = "{d}T10:00:0{i} [ERROR] catchup: CLI call failed: {r} session=s{i}"
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp)

        def write(day, lines):
            (logs / f"{day}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 成功のあとに失敗が 2 日で 4 回続く
        write("2026-09-18", ["2026-09-18T09:00:00 [OK] catchup: wrote a.md, appended INBOX. session=a",
                             fail.format(d="2026-09-18", i=1, r=auth)])
        write("2026-09-19", [fail.format(d="2026-09-19", i=i, r=auth) for i in (1, 2, 3)])
        count, first, reason = failure_streak(logs)
        assert (count, first) == (4, "2026-09-18"), f"連続失敗の数え方が違う: {count} {first}"
        assert reason.startswith("exit 1: Failed to authenticate"), f"理由の切り出しが違う: {reason!r}"

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            shown = report_failures(logs)
        assert shown and "4 回" in buf.getvalue() and "claude /login" in buf.getvalue(), \
            f"通知文が想定と違う: {buf.getvalue()!r}"

        # そのあとに成功が挟まると、以前の失敗は数えない
        write("2026-09-20", ["2026-09-20T09:00:00 [INFO] catchup: no learnings extracted. session=b",
                             fail.format(d="2026-09-20", i=1, r="claude CLI timed out after 120s.")])
        count, _, _ = failure_streak(logs)
        assert count == 1, f"成功より前の失敗まで数えている: {count}"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            shown = report_failures(logs)
        assert not shown and buf.getvalue() == "", "閾値未満なのに通知が出た"

    print(f"[OK] failure_streak: {FAILURE_STREAK_THRESHOLD}回以上の連続失敗で通知、成功が挟まると出ない")


def test_retry_after_cli_failure():
    """CLI が失敗した対象のやり直し（tests.json #4）。

    失敗した対象は次回も選ばれ、上限回数で打ち切る。認証切れは回数に数えない。
    """
    import json
    import tempfile
    import time

    import lib_extract
    from session_start_catchup import find_candidate

    saved = (lib_extract.LEARNINGS_DIR, lib_extract.call_claude_cli, lib_extract.log)
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        learn = Path(tmp) / "learnings"
        proj = home / "projects" / "p"
        proj.mkdir(parents=True)
        learn.mkdir()
        target = proj / "sess.jsonl"
        row = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "あ" * 300}}
        target.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        two_days_ago = time.time() - 2 * 86400
        os.utime(target, (two_days_ago, two_days_ago))

        def attempt(reason):
            """フック本体と同じ順序: 選ぶ → claim → worker。選ばれなければ False。"""
            got = find_candidate("p", "", claude_home=home, learnings_dir=learn)
            if got is None:
                return False
            lib_extract.mark_processed(learn / "p", got.stem)
            lib_extract.call_claude_cli = lambda *a, **k: (False, reason)
            lib_extract.run_worker(got, "catchup", "catchup")
            return True

        try:
            lib_extract.LEARNINGS_DIR = learn
            lib_extract.log = lambda *a, **k: None  # 本物のログに失敗行を書かない

            # 認証切れは何度失敗しても対象に残り、回数も増えない
            auth = "exit 1: Failed to authenticate: OAuth session expired and could not be refreshed."
            for _ in range(lib_extract.MAX_RETRIES + 2):
                assert attempt(auth), "認証切れで失敗した対象が次回の候補から外れた"
            assert lib_extract.load_retries(learn / "p") == {}, "認証切れを回数に数えている"

            # それ以外の失敗は上限回数まで選ばれ、その次は選ばれない
            for n in range(1, lib_extract.MAX_RETRIES + 1):
                assert attempt("claude CLI timed out after 120s"), f"{n} 回目の前に候補から外れた"
            assert lib_extract.load_retries(learn / "p") == {"sess": lib_extract.MAX_RETRIES}
            assert not attempt("claude CLI timed out after 120s"), "上限を超えても候補に残っている"
        finally:
            lib_extract.LEARNINGS_DIR, lib_extract.call_claude_cli, lib_extract.log = saved

    print(f"[OK] 再試行: 失敗した対象を次回も選び、{lib_extract.MAX_RETRIES}回で打ち切る。認証切れは数えない")


def test_cli_runs_outside_project():
    """CLI をプロジェクトの外で起動すること（tests.json #5）。

    --safe-mode でも権限設定は読まれる。プロジェクトの .claude/settings.local.json に
    不正な権限ルールがあると CLI が exit 1 で落ちるので、作業フォルダを learnings/ に固定する。
    """
    import tempfile
    import types

    import lib_transcript as lt

    saved = (lt.subprocess.run, lt.resolve_claude_exe, lt.LEARNINGS_DIR)
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(returncode=0, stdout="- ok\n", stderr="")

    with tempfile.TemporaryDirectory() as tmp:
        learn = Path(tmp) / "learnings"
        try:
            lt.subprocess.run = fake_run
            lt.resolve_claude_exe = lambda *a, **k: Path("claude.exe")
            lt.LEARNINGS_DIR = learn
            ok, out = lt.call_claude_cli("prompt")
        finally:
            lt.subprocess.run, lt.resolve_claude_exe, lt.LEARNINGS_DIR = saved

        assert ok and out == "- ok", f"呼び出しが失敗した: {out}"
        assert seen.get("cwd") == str(learn), f"作業フォルダが learnings/ でない: {seen.get('cwd')!r}"
        assert learn.is_dir(), "作業フォルダが作られていない"
        assert Path(seen["cwd"]).resolve() != Path.cwd().resolve(), "作業フォルダが呼び出し元と同じ"

    print("[OK] call_claude_cli: 作業フォルダを learnings/ に固定して起動する")


def test_digest_keeps_user_messages():
    """上限を超える記録でも、user の発言がすべてダイジェストに残ること（tests.json #18）。"""
    from lib_transcript import (
        DIGEST_OMISSION_MARK,
        DIGEST_SEPARATOR,
        MAX_USER_MESSAGE_CHARS,
        select_for_digest,
    )

    limit = 20_000
    # 30 往復。user は短い訂正、Assistant は長い説明（合計 約 93,000 文字）
    messages = []
    for n in range(30):
        messages.append(("User", f"訂正{n:02d}: それは違う。こうしてほしい"))
        messages.append(("Assistant", f"応答{n:02d}: " + "説明" * 1500))
    digest = DIGEST_SEPARATOR.join(select_for_digest(messages, limit))

    assert len(digest) <= limit, f"上限を超えた: {len(digest)}"
    missing = [n for n in range(30) if f"訂正{n:02d}:" not in digest]
    assert not missing, f"user の発言が落ちた: {missing}"
    assert "応答29:" in digest, "最新の Assistant の発言が残っていない"
    assert "応答00:" not in digest, "古い Assistant の発言が残っている（枠を超えるはず）"
    assert DIGEST_OMISSION_MARK in digest, "落とした箇所の目印が無い"
    assert digest.index("訂正00:") < digest.index("訂正29:"), "元の順序が崩れた"

    # 収まるときは何も落とさず、目印も入れない
    small = [("User", "質問"), ("Assistant", "回答")]
    assert select_for_digest(small, limit) == ["User: 質問", "Assistant: 回答"]

    # 貼り付けで長くなった user の発言は 1 件の上限で切る
    pasted = select_for_digest([("User", "あ" * (MAX_USER_MESSAGE_CHARS + 500))], limit)
    assert len(pasted[0]) == len("User: ") + MAX_USER_MESSAGE_CHARS, "user の発言の上限が効いていない"

    print(f"[OK] digest: 上限({limit:,}文字)を超えても user の発言 30 件をすべて残し、Assistant は新しい順")


def test_ephemeral_slug_skipped():
    """一時フォルダ由来のセッションを対象にしないこと（tests.json #23）。"""
    import tempfile
    import time

    from lib_transcript import is_ephemeral_slug
    from session_start_catchup import find_candidate

    slugs = (
        "C--Users-yanagawa-AppData-Local-Temp-claude-C--Claude-Project-x-scratchpad-live",
        "C--Users-yanagawa-AppData-Roaming-Claude-scratch-workspaces-4777f50c-scratch-2026-10-01",
    )
    assert all(is_ephemeral_slug(s) for s in slugs), "一時フォルダ由来を見分けられていない"
    assert not is_ephemeral_slug("C--Claude-Project-yanagawa-loop-learning"), "通常のプロジェクトを除外した"

    two_days_ago = time.time() - 2 * 86400
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        learn = Path(tmp) / "learnings"
        learn.mkdir()
        for slug in slugs + ("C--normal",):
            f = home / "projects" / slug / "sess.jsonl"
            f.parent.mkdir(parents=True)
            f.write_text("{}\n", encoding="utf-8")
            os.utime(f, (two_days_ago, two_days_ago))

        for slug in slugs:
            got = find_candidate(slug, "", claude_home=home, learnings_dir=learn)
            assert got is None, f"一時フォルダ由来が候補になった: {slug}"
            assert not (learn / slug).exists(), f"learnings/ にフォルダが作られた: {slug}"
        assert find_candidate("C--normal", "", claude_home=home, learnings_dir=learn) is not None, \
            "通常のプロジェクトまで候補から外れた"

    print("[OK] 一時フォルダ由来: 拾い直しの候補にせず、learnings/ にフォルダを作らない")


if __name__ == "__main__":
    # この PC の標準出力は cp932。テスト名と失敗時のメッセージの日本語が文字化けするので
    # UTF-8 に固定する（CLAUDE.md 落とし穴 2。2026-11-13 実測）。
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    test_build_digest()
    test_project_slug()
    test_guard()
    test_memory_scan()
    test_memory_scan_control_chars()
    test_memory_scan_similarity()
    test_memory_scan_project_filter()
    test_memory_scan_stdout_encoding()
    test_session_start_inbox_stdout_encoding()
    test_candidate_floor()
    test_resolve_claude_exe()
    test_failure_streak()
    test_retry_after_cli_failure()
    test_cli_runs_outside_project()
    test_ephemeral_slug_skipped()
    test_digest_keeps_user_messages()
    print("\nALL OFFLINE TESTS PASSED")
