# ai-agency 本体メンテナンス（CLAUDE.local.md ── 本体フォルダ専用・配布されない）

このフォルダは AI秘書チーム **ai-agency の本体（テンプレート）**。ここでは秘書ではなく
**製品のメンテナ**として振る舞う。秘書の常時ルール（`CLAUDE.md`）・スキル・フック・MCP は
`.claude/settings.local.json` で無効化されている（`tools/dev/README.md`）。製品は **Claude Code 専用**
（他ツール対応は凍結。`PORTABILITY.md`）。

## 役割

- 配布物（`CLAUDE.md`・`.claude/agents|skills|commands|settings.json`・`tools/`・`memory/` の雛形）を設計・改善する
- インスタンス利用者の体験（オンボーディング → 記憶が育つ → 委任が広がる）を良くすることが目的

## 絶対にしないこと

- `memory/` に**実データを書かない**。ここにあるのは雛形。編集は「雛形の構造・説明文の改善」に限る
- `/setup`・`/intake`・commitment-track・person-model-update を**ここで実行しない**。動作確認は
  テストインスタンスで行う（`maint-test-instance` スキル）
- 秘書の口調・フォーマット（「▶ 実行してよいですか？」等）で振る舞わない。普通のエンジニアリング支援でよい

## 編集の規律（OPERATIONS.md §1・§5）

- **正典**：常時ルール＝`CLAUDE.md`、人格＝`.claude/agents/{handle}.md`、手順＝`.claude/skills/{name}/SKILL.md`、
  コマンド＝`.claude/commands/{name}.md`。ポインタ層は無い（2026-11-04 に廃止）
- 追加・削除・改名・節番号変更のあとは `maint-check-structure` スキルで整合を検査する
- **コードや雛形を変えたら自動テストを回す**：`python -m unittest discover -s tests -t tests`（標準ライブラリのみ・約15秒）。
  push すると GitHub Actions（`.github/workflows/test.yml`）が Windows / Linux × Python 3.11 / 3.12 で回す。
  `memory/` の雛形を変えたら `tests/fixtures/memory/` にも写す（ずれは test_structure が検出する）
- `memory/` 雛形の構造を変えるときは、既存インスタンス向けの**移行手順**をコミットメッセージに書く
- `CLAUDE.md` は 200 行前後を目安に保つ。長くなる手順は skills へ移す
- 説明は Claude Code 前提で書いてよい。ただし正典（手順・人格・記憶）とツール固有の配線（frontmatter・
  settings・hooks）は分けて書く（将来の他ツール再対応を安くするため）

## 動作確認

- 構文面（コマンドが `/` に出る、フックが JSON を返す）はここで確認できる
  （フックは `echo '{...}' | python tools/hooks/xxx.py` で単体実行）
- 記憶に書く動作（/setup・/intake・人物ファイル）は**テストインスタンス**で行う

## このフォルダの制約

- `permissions.deny` は本体でも効く（局所設定では外せない）。ディレクトリ削除は `git rm -r` か Python
- `git push --force` / `git reset --hard` は使えない。履歴を直すときは新しいコミットで
- コミットメッセージは日本語・1行目に要約・本文に「なぜ」。末尾に Co-Authored-By

## メンテ用メモリ

- `tools/dev/memory/roadmap.md` ── 改善の段階計画と優先順位（着手前に読む）
- `tools/dev/memory/design-decisions.md` ── 設計判断の記録（なぜこうなっているか）
