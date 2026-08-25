"""メモリ棚卸しの下ごしらえ。LLM は呼ばない。

`~/.claude/projects/*/memory/*.md` を走査し、機械的に決まることだけを出す。
重複の確定検出・インデックスの整合性・肥大・陳腐化の候補。
判断（RETIRE / MERGE / PROMOTE）はここではしない。/memory-review が行う。

単体で実行できる:
    python memory_scan.py            人間可読
    python memory_scan.py --json     /memory-review が読む形式
    python memory_scan.py --project C--Claude-Project-yanagawa-loop-learning
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_transcript import CLAUDE_HOME  # noqa: E402

PROJECTS_DIR = CLAUDE_HOME / "projects"
INDEX_NAME = "MEMORY.md"

# 4KB を超えるメモリは、1ファイル1事実の原則から外れている疑いが濃い。
# 実測: 32件中の中央値は約1,100B、最大は 11,122B（進捗ダッシュボード）。
LARGE_FILE_BYTES = 4096

# 類似の測り方（2026-08-25 実測で決めた）。
#
# 最初は語彙全体の Jaccard で測ったが、この母集団では機能しなかった:
#   本当に関連するペア 0.085〜0.201 / 無関係なペアの中央値 0.133・95%点 0.337
#   → 閾値をどこに置いても区別できない。
# overlap 係数（min で割る）も試したが、巨大ファイル1件が全ペアの上位を占めた。
#
# 採用したのは「希少語の共有数」。全体の 25% 以下にしか出ない語だけを見る。
# SharePoint・フロー・実測のような頻出語は捨て、pnp / csp / mousedown のような
# 主題を決める語だけが残る。共有した語そのものを出力に載せるので、人が理由を
# その場で確かめられる。
SHARED_RARE_MIN = 5          # これ以上の希少語を共有していれば提示する
RARE_DF_RATIO = 0.25         # 全体のこの割合以下にしか出ない語を「希少」とする
OUTLIER_MULTIPLIER = 5       # 希少語数が中央値のこの倍数を超える文書は比較から外す

# 日本語のひらがな連続はほぼ助詞・活用語尾（される・しても・という）で、
# 主題を持たない。内容語は漢字・カタカナ・英数に集中するので、それだけを拾う。
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[ァ-ヴ][ァ-ヴー]{1,}|[一-龠]{2,}")

# 改行・タブ以外の制御文字。エスケープを解釈する書き手が「バックスラッシュ + b」の
# ような並びをバックスペース文字として焼き込むことがある（2026-08-25 に実測で1件）。
CONTROL_CHAR_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")

INDEX_LINK_RE = re.compile(r"^\s*-\s*\[[^\]]*\]\(([^)]+)\)")
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _frontmatter(text: str) -> dict[str, str]:
    """先頭の YAML frontmatter から、必要な数個のキーだけ拾う。

    PyYAML は入っていない環境なので、正規表現で必要な分だけ取る。
    ネストした metadata の中身も、インデントを無視して同じ辞書に入れる。
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if m and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip()
    return out


def _body(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4:] if end >= 0 else text


def _tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def _rare_terms(memories: list[dict]) -> None:
    """各メモリに「希少語の集合」を持たせる。母集団全体の出現数から決まる。"""
    df: collections.Counter[str] = collections.Counter()
    for m in memories:
        df.update(m["_tokens"])
    cutoff = max(2, int(len(memories) * RARE_DF_RATIO))
    for m in memories:
        # df が 1（そのファイルにしか出ない語）は共有し得ないので除く。
        m["_rare"] = {t for t in m["_tokens"] if 2 <= df[t] <= cutoff}


def _age_days(fm: dict[str, str], path: Path) -> int:
    """frontmatter の modified を優先し、無ければファイルの mtime を使う。"""
    raw = fm.get("modified", "")
    dt = None
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None:
        dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def collect(projects_dir: Path, only: str | None = None) -> dict:
    """走査して素材を返す。判断はしない。"""
    memories: list[dict] = []
    index_issues: list[dict] = []

    # only が指定されていても、走査自体は全プロジェクトを対象にする。
    # 「希少語」は母集団全体での出現数で決まる概念なので、母集団を絞ると意味が
    # 変わる（2026-08-25 実測: 2件に絞ると how / why / user のような frontmatter の
    # 定型語が「希少語」になり誤検出した）。絞り込みは母集団を作ってから行う。
    for mem_dir in sorted(projects_dir.glob("*/memory")):
        proj = mem_dir.parent.name

        files = sorted(p for p in mem_dir.glob("*.md") if p.name != INDEX_NAME)
        index_path = mem_dir / INDEX_NAME
        index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        linked = {m.group(1).split("#")[0] for m in
                  (INDEX_LINK_RE.match(l) for l in index_text.splitlines()) if m}

        present = {p.name for p in files}
        for target in sorted(linked - present):
            index_issues.append({"project": proj, "kind": "broken_link", "target": target})
        for orphan in sorted(present - linked):
            index_issues.append({"project": proj, "kind": "orphan", "target": orphan})

        for p in files:
            text = p.read_text(encoding="utf-8")
            fm = _frontmatter(text)
            body = _body(text)
            memories.append({
                "project": proj,
                "file": p.name,
                "path": str(p),
                "name": fm.get("name", p.stem),
                "type": fm.get("type", "?"),
                "description": fm.get("description", ""),
                "bytes": len(text.encode("utf-8")),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "age_days": _age_days(fm, p),
                "wiki_links": sorted(set(WIKI_LINK_RE.findall(body))),
                "control_chars": [
                    {"pos": m.start(), "char": hex(ord(m.group())),
                     "context": text[max(0, m.start() - 20):m.start() + 10]}
                    for m in CONTROL_CHAR_RE.finditer(text)
                ],
                "_tokens": _tokens(fm.get("description", "") + " " + body),
            })

    # [[name]] は memory 同士に限らず rules/・skills/・output-styles/ も指す
    # （実測: svn-default-repo は rules、fable-kata は output-styles）。
    # 解決先にそれらを含めないと、正しいリンクを不整合として誤検出する。
    known_names = {m["name"] for m in memories}
    known_names |= {p.stem for p in (CLAUDE_HOME / "rules").glob("*.md")}
    known_names |= {p.stem for p in (CLAUDE_HOME / "output-styles").glob("*.md")}
    known_names |= {p.name for p in (CLAUDE_HOME / "skills").glob("*/")}
    for m in memories:
        for link in m["wiki_links"]:
            if link not in known_names:
                index_issues.append({
                    "project": m["project"], "kind": "dangling_wikilink",
                    "target": link, "from": m["file"],
                })

    # 希少語は母集団全体（絞り込み前）で決める。ここまでが母集団の仕事。
    _rare_terms(memories)

    # ここから先が報告の仕事。only が指定されていれば、この時点で絞る。
    if only:
        memories = [m for m in memories if m["project"] == only]
        index_issues = [i for i in index_issues if i["project"] == only]

    # 完全重複: 内容のハッシュが一致するもの。判断の余地がない確定検出。
    by_hash: dict[str, list[dict]] = {}
    for m in memories:
        by_hash.setdefault(m["sha256"], []).append(m)
    identical = [
        {"sha256": h, "bytes": g[0]["bytes"],
         "members": [f'{x["project"]}/{x["file"]}' for x in g]}
        for h, g in by_hash.items() if len(g) > 1
    ]

    # 近いペア: 希少語の共有。共有した語も返すので、人が理由を確かめられる。
    counts = sorted(len(m["_rare"]) for m in memories)
    median = counts[len(counts) // 2] if counts else 0
    cap = median * OUTLIER_MULTIPLIER
    # 語彙が極端に多い文書は、あらゆる相手と語を共有してしまい比較が成立しない。
    # 指標をひねって合わせるのではなく、比較から外して「分割が先」と報告する。
    excluded = [f'{m["project"]}/{m["file"]}' for m in memories
                if cap and len(m["_rare"]) > cap]

    similar = []
    for i in range(len(memories)):
        for j in range(i + 1, len(memories)):
            a, b = memories[i], memories[j]
            if a["sha256"] == b["sha256"]:
                continue
            if cap and (len(a["_rare"]) > cap or len(b["_rare"]) > cap):
                continue
            shared = a["_rare"] & b["_rare"]
            if len(shared) >= SHARED_RARE_MIN:
                similar.append({
                    "shared": len(shared),
                    "terms": sorted(shared)[:12],
                    "a": f'{a["project"]}/{a["file"]}',
                    "b": f'{b["project"]}/{b["file"]}',
                    "cross_project": a["project"] != b["project"],
                })
    similar.sort(key=lambda x: -x["shared"])

    for m in memories:
        del m["_tokens"]
        del m["_rare"]

    return {
        "scanned_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "projects": sorted({m["project"] for m in memories}),
        "total": len(memories),
        "memories": memories,
        "identical_groups": identical,
        "similar_pairs": similar,
        "similarity_excluded": excluded,
        "index_issues": index_issues,
        "corrupted": [
            {"path": f'{m["project"]}/{m["file"]}', "found": m["control_chars"]}
            for m in memories if m["control_chars"]
        ],
        "large_files": [
            {"path": f'{m["project"]}/{m["file"]}', "bytes": m["bytes"]}
            for m in memories if m["bytes"] > LARGE_FILE_BYTES
        ],
    }


def render(r: dict) -> str:
    out = [
        f'走査: {r["scanned_at"]}',
        f'対象: {len(r["projects"])} プロジェクト / {r["total"]} 件',
        "",
        "== 完全重複（内容がバイト単位で同一） ==",
    ]
    if r["identical_groups"]:
        for g in r["identical_groups"]:
            out.append(f'  {g["bytes"]}B  ' + "  ==  ".join(g["members"]))
    else:
        out.append("  なし")

    out += ["", f'== 近いペア（希少語を {SHARED_RARE_MIN} 語以上共有） ==']
    if r["similar_pairs"]:
        for s in r["similar_pairs"][:20]:
            mark = "横断" if s["cross_project"] else "同一"
            out.append(f'  {s["shared"]:3}語 [{mark}] {s["a"]}  <->  {s["b"]}')
            out.append(f'        {" ".join(s["terms"][:8])}')
        if len(r["similar_pairs"]) > 20:
            out.append(f'  ... 他 {len(r["similar_pairs"]) - 20} 件')
    else:
        out.append("  なし")
    if r["similarity_excluded"]:
        out.append(f'  ※ 語彙が多すぎて比較できないため除外（分割が先）: '
                   f'{", ".join(r["similarity_excluded"])}')

    out += ["", "== インデックスの不整合（自動修復の対象） =="]
    if r["index_issues"]:
        for i in r["index_issues"]:
            extra = f' (from {i["from"]})' if "from" in i else ""
            out.append(f'  [{i["kind"]}] {i["project"]}: {i["target"]}{extra}')
    else:
        out.append("  なし")

    out += ["", "== 制御文字の混入（要修正） =="]
    if r["corrupted"]:
        for c in r["corrupted"]:
            for f in c["found"]:
                out.append(f'  {c["path"]}  {f["char"]} at {f["pos"]}  {f["context"]!r}')
    else:
        out.append("  なし")

    out += ["", f'== 肥大（{LARGE_FILE_BYTES}B 超） ==']
    out += [f'  {x["bytes"]:6}B  {x["path"]}' for x in r["large_files"]] or ["  なし"]

    out += ["", "== 一覧 =="]
    for m in sorted(r["memories"], key=lambda x: (x["project"], x["file"])):
        out.append(
            f'  {m["project"][:26]:26} {m["file"][:32]:32} '
            f'{m["type"]:9} {m["bytes"]:5}B {m["age_days"]:4}日'
        )
    return "\n".join(out)


def main() -> int:
    # この PC の標準出力は既定が cp932 で、メモリの description に含まれる
    # `—`（em dash）などを書けずに UnicodeEncodeError で落ちる
    # （2026-08-25 実測。呼び出し側が PYTHONIOENCODING を付けていれば起きないため、
    # 環境変数を付けてテストしていた間はずっと見えていなかった）。
    # 呼び出し側の環境に依存させず、自分の出力は自分で UTF-8 に固定する。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass  # パイプ等で reconfigure できない場合はそのまま続ける

    ap = argparse.ArgumentParser(description="メモリの棚卸し材料を出す（LLM 不使用）")
    ap.add_argument("--json", action="store_true", help="JSON で出す")
    ap.add_argument("--project", help="このプロジェクトスラッグだけを見る")
    args = ap.parse_args()

    if not PROJECTS_DIR.is_dir():
        print(f"projects ディレクトリが無い: {PROJECTS_DIR}", file=sys.stderr)
        return 1

    result = collect(PROJECTS_DIR, args.project)
    if args.json:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
