# 設計メモ — 元記事との差分

元記事: https://zenn.dev/miyan/articles/claude-code-self-improving-system
（2026-08-20 時点の内容を WebFetch で取得して要約したものを基にした）

この文書は「記事のとおりに作らなかった箇所」と、その理由を実測ベースで
記録する。記事自体を否定するものではなく、この PC の実環境（Windows /
CLI 未導入 / 既存の Auto Memory 運用あり）に合わせた適応の記録。

## 1. Stop フック → SessionEnd フック

記事は Stop フックを「セッション終了時」の処理として使っている。

**実測（公式ドキュメント `code.claude.com/docs/en/hooks`）:** `Stop` は
「Claude finishes responding」、つまり **1ターンの応答が終わるたびに**
発火する。マルチターンの会話では1セッションで数十回発火しうる。

記事のスクリプトは Stop 発火のたびに `claude -p --model haiku` を呼ぶ。
そのまま採用すると、1セッションでの API 呼び出し回数が「セッション数」
ではなく「ターン数」に比例し、コストとレイテンシが記事の想定より
一桁以上増える。

→ 本実装は `SessionEnd` を使う。これはセッションが終了するときに一度だけ
発火する（`SessionEnd` イベントの `reason` フィールドで `clear` /
`resume` / `logout` / `prompt_input_exit` / `other` を区別できる）。

## 2. 抽出結果の書き込み先: MEMORY.md 直接追記 → INBOX 経由の二段階

記事: `>> "$MEMORY_DIR/MEMORY.md"` で Haiku の出力をそのまま追記する。

**実測:** この PC の既存 `MEMORY.md`（例: bihinyoyaku, ankenkanri など
8プロジェクト分を確認）は、1行1件の Markdown リンク形式のインデックス
であり、本文は `frontmatter` 付きの個別ファイルに分けて置く運用が
すでに定着している（`~/.claude/CLAUDE.md` の「メモリ」節に規定あり）。

Haiku の生出力（箇条書き3行程度、frontmatter なし）をそのまま
`MEMORY.md` に追記すると、この構造と混在し、インデックスとしての
一覧性が壊れる。またフォーマット規約（`type: user|feedback|project|
reference` 等）を満たさないファイルが増える。

→ 本実装は Haiku の出力を `~/.claude/learnings/<proj>/INBOX.md` という
**別の場所**に積む。`memory/` への昇格（正規フォーマットでの保存）は、
次回セッションで Claude 本体が INBOX を読んで判断する
（`~/.claude/rules/self-improve.md` に基準を明文化）。

## 3. bash + jq → Python 3.12

記事のスクリプトは bash + jq 前提。

**実測:** `jq` は未インストール（`which jq` で確認）。Python 3.12.10 は
インストール済み。Windows のシェル引用符・パス区切りの問題を避けるため、
フック本体は Python で書き、`settings.json` 側は `command` + `args`
配列（exec 形式）で呼ぶ。shell 経由のクォート展開を経由しないので、
パス中の空白やバックスラッシュで壊れない。

## 4. `claude -p` の呼び出し方: 引数直渡し → stdin 経由

記事はプロンプトをコマンドライン引数として渡す例になっている
（長いプロンプトを想定していない）。

**実測（`claude --help`）:** `-p, --print` は「prompt」を位置引数として
受け取れるが、標準入力からも読める。トランスクリプトのダイジェストは
最大 40,000 文字になりうり、Windows のコマンドライン長制限
（`CreateProcess` で概ね 32,767 文字）に抵触するおそれがある。

→ 本実装は `subprocess.run(..., input=prompt, ...)` で **stdin 経由**に
統一した。

## 5. 再帰防止: 環境変数ガードのみ → `--safe-mode` + 環境変数ガードの二重化

記事には子プロセスでのフック再帰に関する記述がない。

**実測（`claude --help`）:** `--safe-mode` フラグは「CLAUDE.md, skills,
plugins, hooks, MCP servers, custom commands and agents, output styles,
workflows, custom themes, keybindings」を含む全カスタマイズを無効化する
公式フラグで、`CLAUDE_CODE_SAFE_MODE=1` が設定される。フックから呼ぶ
子プロセスの `claude` にこれを付ければ、子プロセスでフックが評価されず
再帰が起きない。これを一次防御とし、`CLAUDE_SELFIMPROVE_CHILD=1` の
環境変数ガード（`os.environ.get(...) == "1"` で即 exit）を二次防御として
併用した。理由: `--safe-mode` は仕様変更・バグで無効化される可能性が
ゼロではないため、独立した二つの機構で守る。

さらに `--no-session-persistence`（呼び出しをディスクに残さない）と
`--max-budget-usd`（1回あたりの上限、暴走時の歯止め）を付けた。

## 6. コスト上限

記事にコスト上限の指定はない。本実装は CLI 呼び出しごとに
`--max-budget-usd 0.05` を付けている。1セッションの学習抽出コストは
実測ベースで Haiku 4.5 換算 $0.01 未満（入力40k文字≒12kトークン、
出力300トークン程度）を想定しており、0.05 は十分な余裕を持たせた
歯止め。

## 7. `everything-claude-code` (ECC) は導入しない

記事後半で紹介されている `/instinct-status` 等のコマンド群は、
user の判断で今回は導入しない。実測（WebFetch）では 68 agents /
286 skills / 94 commands という大規模なバンドルで、既存の
`fable-kata` 出力スタイル・`fable-partner` ルール・自作スキル5本との
競合リスクが高い。導入する場合は自前システムが安定してから改めて
評価する。
