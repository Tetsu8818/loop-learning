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


if __name__ == "__main__":
    test_build_digest()
    test_project_slug()
    test_guard()
    test_memory_scan()
    test_memory_scan_control_chars()
    test_memory_scan_similarity()
    test_memory_scan_project_filter()
    print("\nALL OFFLINE TESTS PASSED")
