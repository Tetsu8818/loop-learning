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


if __name__ == "__main__":
    test_build_digest()
    test_project_slug()
    test_guard()
    print("\nALL OFFLINE TESTS PASSED")
