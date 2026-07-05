---
description: 承認実績（task-log）を集計し、自動承認へ昇格できる操作カテゴリを提案する
---

`.agents/workflows/propose-delegation.md` を読み、そのワークフローを忠実に実行せよ。

Claude Code での読み替え：昇格先は Antigravity の GUI Allow List ではなく
`.claude/settings.json` の `permissions.allow`。提案が承認されたら追記案を提示する
（`permissions.deny` と `memory/guardrails.json` の deny は昇格対象外・常設）。

補足指示: $ARGUMENTS
