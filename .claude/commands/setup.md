---
description: 初回オンボーディング。ユーザー・案件・人物・優先事項を対話で登録し、使えるツールを棚卸しして memory/ を稼働状態にする（再実行可）
---

`.agents/workflows/setup.md` を読み、そのワークフローを忠実に実行せよ。

補足入力: $ARGUMENTS

Claude Code での読み替え：Step 1 の「使えるツールの棚卸し」は、このセッションに読み込まれている
ツール一覧（`mcp__{server}__*` の接頭辞でサーバーを識別、deferred も含む）と、接続失敗として
報告されている MCP サーバーから判断する。Step 6 の定期実行は `/schedule` スキル（scheduled task）で
登録できるが、登録はユーザー承認後に行う。
