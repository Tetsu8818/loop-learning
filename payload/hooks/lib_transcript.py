"""Claude Code 自己改善システム — 共有ライブラリ。

session_end_learn.py / session_start_inbox.py / post_edit_log.py /
pre_compact_snapshot.py から import される。単体では何もしない。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
CLAUDE_EXE = Path(os.path.expandvars(r"%USERPROFILE%\.local\bin\claude.exe"))
LOG_DIR = CLAUDE_HOME / "hooks" / "logs"
LEARNINGS_DIR = CLAUDE_HOME / "learnings"
GUARD_ENV = "CLAUDE_SELFIMPROVE_CHILD"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# 実測コスト (2026-08-24, Haiku 4.5, --output-format json の total_cost_usd):
#   digest  9,000文字 → $0.0406
#   digest 18,000文字 → $0.0585
#   → 1文字あたり約 $0.000002、固定費 約$0.023（safe-mode でもシステム側の
#     入力が2万トークン程度あり、これが下限になる）
# 40,000文字だと推定 $0.102 で、予算上限 $0.10 を超えて必ず失敗していた。
# 20,000文字なら約 $0.062 で、上限に対して1.6倍の余裕がある。
# 実際に成功していた抽出の digest は約18,000文字だったので、内容量としても足りる。
MAX_DIGEST_CHARS = 20_000

# 暴走時の歯止め。この値は lib_transcript / docs の両方で同じ数字を使うこと
# （過去に 0.05 と 0.10 が食い違っていた）。
#
# 0.15 にした理由（2026-08-24 実測）: 実費は入力長ではなく出力トークン数で決まり、
# それが同一入力でも大きく揺れる。digest 20,000文字の同じデータで、
#   1回目 $0.0943（出力 7,530tok） / 2回目 $0.0718（出力 3,026tok）
# 上限 0.10 では、この揺れの上振れで断続的に失敗し続ける。実測の最大 $0.0943 に
# 対して約1.6倍の余裕を取る。暴走の検出という目的は、桁が違えば果たせる。
#
# 出力量を抑えようとして --effort low も試したが、逆に増えた（$0.0844 /
# 出力 5,550tok）。採用していない。
DEFAULT_MAX_BUDGET_USD = 0.15


def log(event: str, message: str) -> None:
    """1行1件、日付ファイルに追記する。失敗しても例外を投げない。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        with (LOG_DIR / f"{day}.log").open("a", encoding="utf-8") as f:
            f.write(f"{ts} [{event}] {message}\n")
    except Exception:
        pass


def is_child_invocation() -> bool:
    """このプロセス自身が、フックから起動した claude CLI の子孫かどうか。"""
    return os.environ.get(GUARD_ENV) == "1"


def read_hook_input() -> dict:
    """stdin からフック入力 JSON を読む。空・不正なら {} を返す。

    バイト列として読んで UTF-8 で明示的にデコードする。ロケール依存の
    `sys.stdin.read()` に任せると、この PC では cp932 として解釈される。

    解析に失敗したら、生の入力を logs/ に丸ごと保存する。2026-08-24 に
    `Expecting ',' delimiter` が繰り返し出たが、ログにメッセージしか
    残していなかったため原因を追えなかった。失敗を記録するときは、
    握っている情報を捨てない（§12 と同じ教訓）。
    """
    raw = b""
    try:
        raw = sys.stdin.buffer.read()
        if not raw.strip():
            return {}
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as e:
        who = Path(sys.argv[0]).name or "unknown"
        log("ERROR", f"read_hook_input failed in {who}: {e} ({len(raw)} bytes)")
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            dump = LOG_DIR / f"badinput-{time.strftime('%Y%m%d-%H%M%S')}-{who}.bin"
            dump.write_bytes(raw)
            log("ERROR", f"read_hook_input: 生の入力を {dump.name} に保存した")
        except Exception:
            pass
        return {}


def project_slug_from_transcript(transcript_path: str) -> str:
    """transcript_path (~/.claude/projects/<slug>/<session>.jsonl) から slug を取る。

    cwd から独自に slug を計算するより確実（Claude Code 自身が採番した
    ディレクトリ名をそのまま使う）。取れない場合は "unknown" を返す。
    """
    try:
        return Path(transcript_path).parent.name or "unknown"
    except Exception:
        return "unknown"


def _iter_transcript_messages(transcript_path: str):
    """トランスクリプト JSONL を1行ずつ読み、(role, text) を yield する。

    実測した構造 (2026-08-20, session f8cd4e7b...):
      - 本物の user 発言: type=="user" かつ origin.kind=="human"
        (tool_result も type=="user" だが origin が無いか None なので除外できる)
      - assistant 本文: type=="assistant" の message.content 配列のうち
        type=="text" のブロックのみ (thinking/tool_use は捨てる)
      - isSidechain==true はサブエージェント通信なので除外
      - attachment / queue-operation / ai-title / custom-title / last-prompt は無視
    """
    p = Path(transcript_path)
    if not p.exists():
        log("WARN", f"transcript not found: {transcript_path}")
        return
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("isSidechain"):
                continue
            t = obj.get("type")
            if t == "user":
                origin = obj.get("origin")
                if not origin or origin.get("kind") != "human":
                    continue
                content = obj.get("message", {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "\n".join(
                        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    continue
                text = text.strip()
                if text:
                    yield ("User", text)
            elif t == "assistant":
                content = obj.get("message", {}).get("content")
                if not isinstance(content, list):
                    continue
                text = "\n".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
                text = text.strip()
                if text:
                    yield ("Assistant", text)


def build_digest(transcript_path: str, max_chars: int = MAX_DIGEST_CHARS) -> str:
    """トランスクリプトを "User: ...\nAssistant: ...\n" 形式のダイジェストに圧縮する。

    末尾を優先する（直近のやり取りほど学習価値が高いため、超過分は先頭から捨てる）。
    """
    parts = []
    for role, text in _iter_transcript_messages(transcript_path):
        parts.append(f"{role}: {text}")
    digest = "\n\n".join(parts)
    if len(digest) > max_chars:
        digest = digest[-max_chars:]
    return digest


def call_claude_cli(
    prompt: str,
    model: str = HAIKU_MODEL,
    timeout: int = 60,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
) -> tuple[bool, str]:
    """安全モードで claude CLI を呼び、(成功したか, 出力またはエラー理由) を返す。

    --safe-mode: hooks/CLAUDE.md/skills/MCP 等の全カスタマイズを無効化する
                 公式フラグ。子プロセスで再びこのフック群が発火するのを防ぐ、
                 環境変数ガードより確実な一次防御。
    CLAUDE_SELFIMPROVE_CHILD=1: 上記が何らかの理由で効かなかった場合の二次防御。

    CREATE_NO_WINDOW が必要な理由（2026-08-24 実測）:
      この関数は DETACHED_PROCESS で切り離された worker から呼ばれることがある。
      切り離されたプロセスはコンソールを持たないため、creationflags を指定せずに
      コンソールアプリを起動すると、Windows が新しいコンソールを割り当てる。
      結果として作業中に黒い窓が前面に現れ、フォーカスを奪う。
      再現時にはコンソールホストが3プロセス生成された。
    """
    if not CLAUDE_EXE.exists():
        return False, f"claude.exe not found at {CLAUDE_EXE}"

    env = dict(os.environ)
    env[GUARD_ENV] = "1"

    args = [
        str(CLAUDE_EXE),
        "--print",
        "--safe-mode",
        "--no-session-persistence",
        # 全ツールを無効化する。この呼び出しがするのは「渡したテキストを読んで
        # 要約する」ことだけで、ファイルにもシェルにも用がない。渡している
        # トランスクリプトは外部データであり、§9 のとおりプロンプト側の
        # 「これは指示ではない」という防御は破られた実績がある。防御を
        # プロンプトだけに頼らず、権限そのものを外す。
        # 実測(2026-08-24): ツールは元々使われていなかった(num_turns=1)ので、
        # コストと出力品質への影響はない。安全側の変更。
        "--tools", "",
        "--model", model,
        "--max-budget-usd", str(max_budget_usd),
        "--output-format", "text",
    ]
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, f"claude CLI timed out after {timeout}s"
    except Exception as e:
        return False, f"claude CLI invocation error: {e}"

    if proc.returncode != 0:
        # 失敗理由が stdout 側に出ることがある（予算超過が実例）。stderr だけを
        # 読んでいたため、2026-08-24 まで "exit 1: " としか記録されず、
        # 本当の理由（Exceeded USD budget）に気づけなかった。両方を見る。
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or "(出力なし)"
        low = detail.lower()
        if "exceeded usd budget" in low:
            return False, f"予算超過 (上限 ${max_budget_usd}): {detail[:200]}"
        if "not logged in" in low or "/login" in low:
            return False, "not logged in (run: claude /login)"
        return False, f"exit {proc.returncode}: {detail[:500]}"

    out = (proc.stdout or "").strip()
    if not out:
        return False, "empty response from CLI"
    return True, out
