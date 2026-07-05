---
description: 複合・専門的な相談。@chief が専門家を並列サブエージェントとして召集し、統合回答を返す
---

`.agents/workflows/consult.md` を読み、そのワークフローを忠実に実行せよ。

相談内容: $ARGUMENTS

Claude Code での読み替え：Step 4 の「動的サブエージェント召集」は **Task ツール**で行う。
`.claude/agents/` に定義済みの専門家（accountant / hr / dx / pm / qa / it-architect / legal / generalist）を
使い、分析が独立なら**1メッセージで複数 Task を並列起動**する。1つの文脈で全役を演じることは禁止。
