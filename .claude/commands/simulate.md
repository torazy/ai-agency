---
description: 実在の上司・メンバー・顧客の反応を事前予測するリハーサル。人物モデル（memory/people/）を独立サブエージェントとして召集する
---

`.agents/workflows/simulate.md` を読み、そのワークフローを忠実に実行せよ。

対象と案件: $ARGUMENTS

Claude Code での読み替え：Step 3 の「人物モデル召集」は **Task ツール**（subagent_type: 汎用）で行い、
指示書（人物ファイルからの観察記録抜粋）をプロンプトとして渡す。複数人なら並列起動。
安全規則（予測を本人発言として扱わない・なりすまし禁止）は Antigravity 版と同一に適用する。
