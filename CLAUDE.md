# loop-learning — 作業前に読むこと

Claude Code の自己改善システム（セッションの知見を Haiku で抽出し、次回セッションで
memory/ へ昇格させるフック群）の設計・テスト・配布物。この PC の Claude Code 自身が
毎セッション使っている。

## 環境の制約

| 制約 | 意味 |
|---|---|
| **正本は `~/.claude/` 側**（hooks/*.py、rules/self-improve.md、skills/learn・memory-review） | このリポジトリの `payload/` は配布用コピー。**直すのは正本**。直したら `sync_payload.ps1` で payload を最新化してからコミットする（一方向。逆方向は install.ps1） |
| 正本を直すと、**この PC の全プロジェクトのフックが即座に変わる** | 壊すと全セッションの SessionStart / PreCompact が落ちる。直したら `python tests/run_offline.py` を必ず通す |
| Python は `C:\Users\yanagawa\AppData\Local\Programs\Python\Python312\python.exe` | settings.json のフックもこのフルパスで呼んでいる。PATH に無いことがある |
| `claude` CLI を呼ぶ検証は1回 $0.04〜$0.10 かかる | オフラインテストで足りる検証に使わない。使うときはテスト用のスラッグ（例 `zz-selftest`）で行い、終わったら `learnings/` から消す |

## 正本の場所

- 設計判断と実測の記録: `docs/design.md`（§番号で参照される。**節を足すときは末尾に追番**）
- 日常運用・既知の問題: `docs/manual.md`
- 導入・構成・コスト: `README.md`

## 実行・テスト

```bash
python tests/run_offline.py        # claude CLI を呼ばない。10 件
```

```powershell
powershell -File sync_payload.ps1  # 正本 → payload/
powershell -File install.ps1       # payload/ → 別 PC の ~/.claude/
```

ログ: `~/.claude/hooks/logs/YYYY-MM-DD.log`。抽出の成否は `wrote` / `CLI call failed` で引く。

## VCS — SVN と git の二重管理

両方を同じ内容に保つ（どちらかが正ではない）。手順と理由は memory `[[repo-dual-svn-git]]`。

1. `git add` → `git commit` → `git push origin master`
2. `svn update`（飛ばすと `E155011: resource out of date`）→ `svn commit`
3. 新規ファイルは `git add` と `svn add` の**両方**。削除は `svn delete` と `git rm` の両方

## 落とし穴（実測したものだけ）

1. **Python の stdin はこの PC では cp932。**フック入力は `sys.stdin.buffer.read().decode("utf-8")` で読む（design.md §15・§16）
2. **標準出力も cp932。**日本語や `—` を print するスクリプトは `main()` で stdout を UTF-8 に固定する（design.md §17）
3. **`svn log`/`status` の生出力は日本語が崩れる。**`--xml` でファイルへ落として読む
4. **Git Bash に `C:\...` のバックスラッシュパスを渡すと区切りが消える。**`/c/...` で書く
5. **テストで環境変数（`PYTHONIOENCODING` 等）を付けると、それが要ることに気づけない。**
   文字コードの回帰テストは環境変数を外した別プロセスで行う
6. **`rules/` の合計は目安 15,000B。**超えたら新規追加より先に既存を圧縮する
   （`wc -c ~/.claude/CLAUDE.md ~/.claude/rules/*.md`）

## 規約

- コメント・ログ・ドキュメント・コミットメッセージは日本語
- 失敗を記録するときは握っている情報（stderr と stdout の両方、生の入力）を捨てない（design.md §12）
- 数値の根拠（コスト・閾値）はコードのコメントと design.md の両方に同じ値で書く
