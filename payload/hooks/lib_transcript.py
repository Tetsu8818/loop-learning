"""Claude Code 自己改善システム — 共有ライブラリ。

lib_extract.py と、各フック（session_end_learn / session_start_catchup /
session_start_inbox）から import される。
単体では何もしない。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
# 公式ネイティブインストーラ（install.ps1）の配置先。npm で入れた PC などでは
# ここに無いので、resolve_claude_exe() が PATH から探す。
CLAUDE_EXE = Path(os.path.expandvars(r"%USERPROFILE%\.local\bin\claude.exe"))
LOG_DIR = CLAUDE_HOME / "hooks" / "logs"
LEARNINGS_DIR = CLAUDE_HOME / "learnings"
GUARD_ENV = "CLAUDE_SELFIMPROVE_CHILD"
# 系列名のエイリアスで指定し、Haiku の最新版に自動で追従させる（2026-10-02 変更）。
# 別 PC では 2026-10-10 に固定 ID（claude-haiku-5-5）にしていた。理由は「hook が呼ぶ
# ~/.local/bin/claude.exe がデスクトップ版より古く、エイリアスの解決先が版で変わり得る」。
# 2026-11-13 にエイリアスへ統一した（この PC の CLI は 2.1.319）。CLI が古い PC では、
# エイリアスが旧版に解決されていないかを確かめること。
HAIKU_MODEL = "haiku"

# 実測コスト（2026-11-13、Haiku 5.5、--output-format json の total_cost_usd、8 回）:
#   digest  5,712文字 → $0.0013 / $0.0014
#   digest 14,669文字 → $0.0008〜$0.0039（同じ digest で 4 回。プロンプトと effort は回ごとに違う。出力 338〜2,342tok）
#   digest 19,401文字 → $0.0034 / $0.0035
#   所要時間は 11〜24 秒。
# Haiku 4.5 のとき（2026-08-24）は 1 回 $0.04〜$0.10 で、固定費だけで約 $0.023 あった。
# 現在はおよそ 1/20。当時の数値と経緯は design.md §6・§13 に残してある。
#
# 上限の 20,000 文字は、Haiku 4.5 で予算内に収めるために決めた値のまま。費用の面では
# 広げる余地があるが、抽出の質への影響を測っていないので変えていない（tests.json #26）。
MAX_DIGEST_CHARS = 20_000

# 暴走時の歯止め。この値は lib_transcript / docs の両方で同じ数字を使うこと
# （過去に 0.05 と 0.10 が食い違っていた）。
#
# 0.03 にした理由（2026-11-13 実測）: 実費の最大は $0.0039 で、その約 8 倍。実費は入力長より
# 出力トークン数（thinking を含む）で決まり、同じ digest でも 5 倍ほど開いたので、余裕を大きく取る。
# 以前の 0.15 は Haiku 4.5 の実測最大 $0.0943 の約 1.6 倍として決めた値で、現在の実費に
# 対しては約 40 倍あり、暴走を検出する役に立たない。
#
# --effort low は 1 回だけ測った（$0.0008、出力 1,151tok）。回数が足りず、採否を決めていない。
# Haiku 4.5 では --effort low で出力が逆に増えた（2026-08-24）。
DEFAULT_MAX_BUDGET_USD = 0.03


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


# スラッグにこれらを含むセッションは、アプリが一時フォルダで開いたもの。プロジェクトの
# 知見にならないので抽出の対象にしない（2026-11-13 実測: learnings/ に該当フォルダ 2 件）。
EPHEMERAL_SLUG_MARKERS = ("appdata-local-temp", "scratch-workspaces")

# CLI の失敗理由のうち、ログインし直せば解消するもの。費用が掛からずに失敗するので、
# 再試行の回数には数えない（lib_extract.run_worker）。
# 実測（2026-09-19〜11-13、153 件）: "Failed to authenticate: OAuth session expired and
# could not be refreshed."
AUTH_FAILURE_MARKERS = ("not logged in", "failed to authenticate", "oauth session expired")


def is_ephemeral_slug(slug: str) -> bool:
    """一時フォルダ由来のプロジェクトスラッグかどうか。"""
    low = slug.lower()
    return any(m in low for m in EPHEMERAL_SLUG_MARKERS)


def is_auth_failure(reason: str) -> bool:
    """call_claude_cli の失敗理由が、認証切れによるものかどうか。"""
    low = reason.lower()
    return any(m in low for m in AUTH_FAILURE_MARKERS)


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


# user の発言 1 件の上限。ログや資料を貼り付けた長い発言で枠を使い切らないための歯止め。
# 実測（2026-11-13、直近 38 本）: user の発言 1 件の最大は 2,936 文字、1 本あたりの合計は
# 中央値 1,333 文字・最大 6,454 文字。通常の発言はこの上限に掛からない。
MAX_USER_MESSAGE_CHARS = 3_000
DIGEST_SEPARATOR = "\n\n"
# 落とした発言があった位置に置く目印。Haiku が前後を連続した会話と読み違えないようにする。
DIGEST_OMISSION_MARK = "[…中略…]"


def select_for_digest(
    messages: list[tuple[str, str]], max_chars: int = MAX_DIGEST_CHARS
) -> list[str]:
    """(role, text) の列から、ダイジェストに載せる行を選ぶ。副作用なし。

    上限に収まればすべて載せる。超えるときは user の発言を優先する:
      1. user の発言は（1 件 MAX_USER_MESSAGE_CHARS まで）すべて残す。user の発言だけで
         上限を超えるときは、新しいものから収まる分を残す
      2. 残りの枠を、新しい順の Assistant の発言で埋める
      3. 元の順序で並べ、落とした箇所に DIGEST_OMISSION_MARK を置く

    以前は末尾 max_chars 文字だけを残していた。長いセッションでは前半が丸ごと落ち、
    そこにあった user の訂正・決定（抽出したい知見の中心）が Haiku に渡らなかった。
    実測（2026-11-13、直近 38 本）: 21 本が 20,000 文字を超え、最大は 57,109 文字。
    """
    def cost(part: str) -> int:
        return len(part) + len(DIGEST_SEPARATOR)

    parts = [
        f"{role}: {text[:MAX_USER_MESSAGE_CHARS] if role == 'User' else text}"
        for role, text in messages
    ]
    if sum(cost(p) for p in parts) <= max_chars:
        return parts

    # 1 件ごとに目印 1 つ分を足して数える（目印は落とした箇所ごとに入り、最大で残した件数 + 1）。
    # 多めに見積もることで、目印を足しても上限を超えないことを保証する。
    keep: set[int] = set()
    budget = max_chars - cost(DIGEST_OMISSION_MARK)
    newest_first = list(range(len(parts) - 1, -1, -1))
    for wanted_role in ("User", "Assistant"):
        for i in newest_first:
            if messages[i][0] != wanted_role:
                continue
            need = cost(parts[i]) + cost(DIGEST_OMISSION_MARK)
            if need > budget:
                continue  # 枠に入らない長い発言は飛ばし、入るものを古いほうへ探す
            keep.add(i)
            budget -= need

    selected: list[str] = []
    previous = -1
    for i in sorted(keep):
        if i != previous + 1:
            selected.append(DIGEST_OMISSION_MARK)
        selected.append(parts[i])
        previous = i
    if previous != len(parts) - 1:
        selected.append(DIGEST_OMISSION_MARK)
    return selected


def build_digest(transcript_path: str, max_chars: int = MAX_DIGEST_CHARS) -> str:
    """トランスクリプトを "User: ...\\n\\nAssistant: ..." 形式のダイジェストに圧縮する。

    上限を超えるときの選び方は select_for_digest()。
    """
    messages = list(_iter_transcript_messages(transcript_path))
    return DIGEST_SEPARATOR.join(select_for_digest(messages, max_chars))


def resolve_claude_exe(default: Path = CLAUDE_EXE) -> Path | None:
    """claude CLI の実体。既定の配置先に無ければ PATH から探す。見つからなければ None。"""
    if default.exists():
        return default
    found = shutil.which("claude")
    return Path(found) if found else None


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
    exe = resolve_claude_exe()
    if exe is None:
        return False, f"claude CLI not found ({CLAUDE_EXE} にも PATH にも無い)"

    env = dict(os.environ)
    env[GUARD_ENV] = "1"

    # 作業フォルダをプロジェクトの外に固定する。--safe-mode でも権限設定は読まれるため、
    # フックを起動したプロジェクトの .claude/settings.local.json に不正な権限ルールが
    # あると、CLI が起動時に exit 1 で落ちる（2026-10-26〜11-09 に 13 件。
    # "Permission allow rule (.claude\settings.local.json): Write(./_out/**) is not matched..."）。
    # この呼び出しはプロジェクトのファイルに用がないので、設定の無い場所で起動する。
    try:
        LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"作業フォルダを作れない: {LEARNINGS_DIR} ({e})"

    args = [
        str(exe),
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
            cwd=str(LEARNINGS_DIR),
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
