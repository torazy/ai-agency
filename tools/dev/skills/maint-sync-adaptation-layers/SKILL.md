---
name: maint-sync-adaptation-layers
description: 本体メンテ用。正典（.agents/skills・.agents/workflows・AGENTS.md）を編集した後に、Claude Code / Copilot / Hermes の適応層（.claude/skills ポインタ・.claude/commands・.github/prompts・PORTABILITY 対応表）を同期・検査する。スキルやワークフローを追加・削除・改名したとき、frontmatter を変えたときに使う。
---

# 適応層の同期（本体メンテ用）

正典は `.agents/`。ポインタ層がずれていないかを検査し、ずれていれば直す。

## 手順

1. **スキルポインタを再生成する**（frontmatter を正典から転写）：
   ```bash
   for dir in .agents/skills/*/; do name=$(basename "$dir"); mkdir -p ".claude/skills/$name";
     awk 'NR==1 && /^---/{fm=1; print; next} fm==1{print; if(/^---/){exit}}' "$dir/SKILL.md" > ".claude/skills/$name/SKILL.md";
     printf '\n# %s（ポインタ）\n\nこのスキルの手順の正典は `.agents/skills/%s/SKILL.md` にある。\nそれを読み、記載された手順を忠実に実行すること。手順を編集する場合も正典側を直す。\n' "$name" "$name" >> ".claude/skills/$name/SKILL.md"; done
   ```
   `.claude/skills/maint-*` はメンテ用（gitignore）なので触らない。
2. **孤児ポインタを検出する**：`.claude/skills/{name}` があって `.agents/skills/{name}` が無いもの
   （maint-* を除く）を列挙し、`git rm -r` で削除する。
3. **ワークフロー ↔ コマンド/プロンプトの対応を検査する**：`.agents/workflows/*.md` の各名前について
   `.claude/commands/{name}.md` と `.github/prompts/{name}.prompt.md` が存在するか。欠けていれば既存の
   ポインタ（例：`intake.md`）を雛形に作る。
4. **人間向けドキュメントの件数・一覧を更新する**：README のファイル構造（スキル数・コマンド数）、
   USAGE の早見表、PORTABILITY の対応表。
5. **Hermes**：スキルを追加・削除した場合は `tools/hermes/install-skills.sh` の再実行が必要な旨を
   コミットメッセージに書く。
6. 結果を報告する：再生成 n 件／孤児 n 件／欠落 n 件／更新したドキュメント。

## 注意

- ポインタ本文は正典を読むだけなので、正典の手順変更ではポインタの再生成は不要。**frontmatter の変更時だけ**必要
- `.claude/skills/maint-*` と `CLAUDE.local.md` と `.claude/settings.local.json` はメンテ用（gitignore）。
  コミットに含まれていたら誤り
