---
name: maint-check-structure
description: 本体メンテ用。正典（CLAUDE.md・.claude/agents・.claude/skills・.claude/commands・memory/ 雛形・tools/hooks）の整合を検査する。スキル・コマンド・エージェントを追加・削除・改名したとき、CLAUDE.md の節番号や参照パスを変えたとき、コミット前の点検に使う。
---

# 構造の整合検査（本体メンテ用）

ポインタ層は廃止済み（2026-11-04）。検査対象は正典そのものの整合。

## 手順

1. **エージェント**：`.claude/agents/*.md` の各ファイルに frontmatter（`name`・`description`）と本文があるか。
   `skills:` に書かれたスキル名が `.claude/skills/{name}/SKILL.md` として存在するか。
   `memory/team/roster.md` の名簿と handle が一致するか（`CLAUDE.md` §1 の専門家一覧とも）。
2. **スキル**：`.claude/skills/*/SKILL.md`（`maint-*` を除く）に frontmatter（`name`＝フォルダ名・`description`）があるか。
   本文中の参照パス（`.claude/skills/…`・`memory/…`・`CLAUDE.md §n`）が実在するか。
3. **コマンド**：`.claude/commands/*.md` に `description` があり、本文に `$ARGUMENTS` があるか。
   `CLAUDE.md` §10 と `README.md` の一覧に全コマンドが載っているか。
4. **CLAUDE.md**：節番号（§1〜§10）が本文と参照側（スキル・コマンド・USAGE）で一致するか。200行を大きく超えて
   いないか（超えるなら手順を skills へ移す）。
5. **フック**：`.claude/settings.json` の各 hook が指す `tools/hooks/*.py` が存在し、`python -m py_compile` が通るか。
   `tools/hooks/operation-log.json` と `memory/guardrails.json` が JSON として正しいか。
6. **残存参照**：`grep -rn -e "\.agents/" -e "AGENTS\.md" -e "GEMINI" -e "Antigravity" -e "Copilot" -e "Hermes"`
   が `PORTABILITY.md`・`tools/dev/memory/`・履歴記述以外に無いか。
7. **メンテ層の漏れ**：`git status` に `CLAUDE.local.md`・`.claude/settings.local.json`・`.claude/skills/maint-*`・
   `.ai-agency-template` が現れていないか（現れたら gitignore の崩れ）。
8. 結果を「項目／OK・NG／修正したこと」の表で報告する。

## 注意

- 検査で直すのは参照・一覧・frontmatter の整合まで。手順や人格の内容変更は別作業として提案する
- `memory/` の雛形構造を変えたときは、既存インスタンス向けの移行手順をコミットメッセージに書く（OPERATIONS.md §5）
