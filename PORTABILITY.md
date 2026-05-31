# 移植性メモ ── Antigravity ⇄ Claude Code

将来 Claude Code へ移行する可能性を見込み、**移植可能コア**と**薄いプラットフォーム適応層**を分離してある。
移行時は適応層だけを差し替えればよい。

## 層の分離

| 層 | 中身 | 役割 |
|---|---|---|
| **移植可能コア（共通）** | `AGENTS.md`、`.agents/skills/*/SKILL.md`、`memory/*.md`、`memory/guardrails.json`、`tools/`（MCP・フックスクリプト） | 行動規範・知識・スキル・記憶・ロジック。ツール非依存。 |
| **適応層（差し替え対象）** | `.agents/agents.md`、`.agents/workflows/`、`.agents/config.json`、`/schedule` 登録、`GEMINI.md` | オーケストレーションとツール固有の起動方法。 |

## 対応表（移行時の読み替え）

| 機能 | Antigravity 2.0（現在） | Claude Code（移行先） |
|---|---|---|
| 常時ルール | `AGENTS.md`（＋`GEMINI.md`） | `AGENTS.md` / `CLAUDE.md`（どちらも読む） |
| ペルソナ／専門家 | `.agents/agents.md`（1ファイルに全員） | `.claude/agents/{handle}.md`（サブエージェントごとに1ファイル） |
| スキル | `.agents/skills/{name}/SKILL.md` | `.claude/skills/{name}/SKILL.md`（同じSKILL.md標準） |
| スラッシュコマンド | `.agents/workflows/{name}.md` | `.claude/commands/{name}.md` |
| 物理ガードレール（主） | **IDE GUI の Deny List**（Ctrl+, → Agent → Terminal Execution policy）。`guardrails.json` の `gui_deny_tokens` を転記 | `.claude/settings.json` の `permissions.deny` |
| フック（スクリプト・副） | **IDE は非対応**。CLI は `.gemini/settings.json` の `hooks`（`BeforeTool`/`SessionEnd`） | `.claude/settings.json` の `hooks`（`PreToolUse`/`Stop`） |
| 活動ログ | IDE: エージェントが会話中に task-log へ追記（memory-update）。CLI: `SessionEnd` フックで task-log-writer.py | `Stop` フックで task-log-writer.py |
| サブエージェント召集 | 動的サブエージェント（自動）を `/consult` が指示 | `Task`/サブエージェント機構を `/consult` が指示 |
| 定期実行 | `/schedule`・`agy` cron | cron／`/loop`／scheduled routines |
| MCP | IDE MCPストア／`~/.gemini/config/mcp_config.json` | `.mcp.json`／`~/.claude.json` |

> **重要（実機検証で判明）**：Antigravity **IDE(GUI)** はプロジェクト内のスクリプトフック（`.agents/config.json` 等）を
> 読まない。IDE の安全は**ネイティブ Deny List（GUI）**で担保する。`tools/hooks/*.py` と `guardrails.json` は
> CLI／Claude Code 用の移植資産として保持する。

## CLI / Claude Code でフックを使う場合の設定例（参考・現在は未使用）

Gemini/Antigravity **CLI** ── `.gemini/settings.json`：
```json
{ "hooks": { "BeforeTool": [ { "matcher": ".*", "hooks": [
  { "type": "command", "command": "python tools/hooks/guardrail.py", "timeout": 15000 } ] } ],
  "SessionEnd": [ { "hooks": [
  { "type": "command", "command": "python tools/hooks/task-log-writer.py", "timeout": 15000 } ] } ] } }
```
**Claude Code** ── `.claude/settings.json`：同様に `hooks.PreToolUse` / `hooks.Stop` へ。

## 移行時の作業（概要）
1. `.agents/agents.md` の各 `## The X (@handle)` を `.claude/agents/{handle}.md`（frontmatter付き）へ分割。
2. `.agents/workflows/*.md` を `.claude/commands/*.md` へコピー（中身はほぼそのまま）。
3. 上記のフック設定例を `.claude/settings.json` に置く。フックスクリプトは入力キーに頑健なのでそのまま流用可。
4. ガードレールは GUI Deny List → `permissions.deny` へ（`gui_deny_tokens` を流用）。
5. MCP 設定を `.mcp.json` へ移す。
6. `AGENTS.md`・`memory/`・`.agents/skills/` は無変更で動く。
