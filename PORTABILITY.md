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
| フック | `.agents/config.json`（`BeforeTool`/`SessionEnd`） | `.claude/settings.json`（`PreToolUse`/`Stop`） |
| 物理ガードレール | フック＋`guardrails.json` | `settings.json` の `permissions.deny` ＋同フック |
| サブエージェント召集 | 動的サブエージェント（自動）を `/consult` が指示 | `Task`/サブエージェント機構を `/consult` が指示 |
| 定期実行 | `/schedule`・`agy` cron | cron／`/loop`／scheduled routines |
| MCP | IDE MCPストア／`~/.gemini/config/mcp_config.json` | `.mcp.json`／`~/.claude.json` |

## 移行時の作業（概要）
1. `.agents/agents.md` の各 `## The X (@handle)` を `.claude/agents/{handle}.md`（frontmatter付き）へ分割。
2. `.agents/workflows/*.md` を `.claude/commands/*.md` へコピー（中身はほぼそのまま）。
3. `.agents/config.json` のフックを `.claude/settings.json` の `hooks` へ移植（イベント名を読み替え）。
4. フックスクリプト（`tools/hooks/*.py`）は入力キーに頑健なのでそのまま流用可。
5. MCP 設定を `.mcp.json` へ移す。
6. `AGENTS.md`・`memory/`・`.agents/skills/` は無変更で動く。
