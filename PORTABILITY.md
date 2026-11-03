# 移植性メモ ── マルチエージェント対応（Antigravity / Claude Code / GitHub Copilot）

**移植可能コア**と**薄いプラットフォーム適応層**を分離し、適応層は**3ツール分を並置済み**（2026-07-05 実装）。
どのツールで開いても同じ秘書・同じ記憶で動く。

## 層の分離

| 層 | 中身 | 役割 |
|---|---|---|
| **移植可能コア（唯一の正典）** | `AGENTS.md`、`.agents/agents.md`（ペルソナ）、`.agents/workflows/`（手順）、`.agents/skills/*/SKILL.md`、`memory/`、`tools/`（MCP・フックスクリプト） | 行動規範・ペルソナ・手順・知識・記憶・ロジック。ツール非依存。 |
| **Antigravity 適応層** | `GEMINI.md`、`.agents/`（ネイティブ認識）、GUI Deny List、`/schedule` | 現行のメイン環境 |
| **Claude Code 適応層** | `CLAUDE.md`（@import）、`.claude/agents/`・`.claude/commands/`・`.claude/skills/`（すべて正典へのポインタ）、`.claude/settings.json`（deny＋フック） | ほぼ完全対応（並列サブエージェント・フック・スキル自動起動が全部動く） |
| **Copilot 適応層** | `.github/copilot-instructions.md`、`.github/prompts/*.prompt.md`（正典へのポインタ） | 部分対応（並列サブエージェント・フック・定期実行なし → 劣化モード明記） |
| **Hermes Agent 適応層** | `tools/hermes/`（スキルインストーラ＋`SETUP.md`）。`AGENTS.md` はネイティブ認識 | ほぼフル対応（サブエージェント・MCP・内蔵cron可。**常駐＋メッセージング**で常時稼働とモバイルを補完。ガードレールは承認プロンプト依存） |

> **ドリフト対策の原則**：手順・ペルソナの本文は必ず正典（`.agents/`・`AGENTS.md`）側を編集する。
> `.claude/`・`.github/` 配下は「正典を読んで実行せよ」というポインタなので、正典を直せば全ツールに反映される。
> 例外は各ポインタの frontmatter（name/description＝起動トリガー）で、正典の description を変えたら転写する。

## 対応表（実装済み）

| 機能 | Antigravity 2.0 | Claude Code | GitHub Copilot (VS Code) |
|---|---|---|---|
| 常時ルール | `AGENTS.md`＋`GEMINI.md` | `CLAUDE.md`（`@AGENTS.md`・`@memory/dashboard.md` をインポート） | `AGENTS.md`（ネイティブ）＋`.github/copilot-instructions.md` |
| 稼働サマリー | `memory/dashboard.md`（ツール中立・GEMINI.md が参照指示） | 同左（CLAUDE.md がインポート） | 同左（instructions が参照指示） |
| ペルソナ／専門家 | `.agents/agents.md`（正典） | `.claude/agents/{handle}.md` ×8（正典へのポインタ） | ✕ 単一コンテキストの節分割で代替（劣化モード） |
| スキル | `.agents/skills/{name}/SKILL.md`（正典） | `.claude/skills/{name}/SKILL.md`（ポインタ・自動起動有効） | 手動参照（instructions で誘導） |
| スラッシュコマンド | `.agents/workflows/{name}.md`（正典） | `.claude/commands/{name}.md`（ポインタ） | `.github/prompts/{name}.prompt.md`（ポインタ） |
| 物理ガードレール | **GUI Deny List**（`gui_deny_tokens` を転記） | `.claude/settings.json` の `permissions.deny` ＋ PreToolUse フック（guardrail.py） | ✕ 機械的ブロックなし（instructions の自制のみ） |
| 活動ログ（操作実績） | 会話中に秘書が追記 | `PostToolUse` フックで operation-logger.py が外部書き込み・送信を自動記録（秘書は書かない） | 会話中に追記（instructions で指示） |
| 記憶の育成状態の注入 | 秘書が自分で診断 | `SessionStart` フックで session-start.py が「未初期化／案件n件／期限超過」を注入 → `/setup` 案内 | 秘書が自分で診断 |
| オンボーディング | `/setup` | `/setup` | `/setup`（prompts） |
| サブエージェント召集 | 動的サブエージェント | `Task` ツール（本物の並列・独立） | ✕ |
| 定期実行 | `/schedule` | cron／`/loop`／scheduled routines | ✕（GitHub Actions で代替可） |
| MCP | IDE MCPストア／`~/.gemini/config/mcp_config.json` | `.mcp.json`（**設置済み**・github/backlog/brave-search） | `.vscode/mcp.json`（キー名 `servers`・未設置） |

**対応度の目安**：Antigravity＝フル ／ Claude Code＝フル同等（ガードレールはむしろ強い） ／
Copilot＝閲覧・ファイリング・簡易相談まで（重要判断は他ツールへ誘導） ／
Hermes Agent＝ほぼフル＋常時稼働・モバイル（詳細は `tools/hermes/SETUP.md`。
スキル＝agentskills.io 標準の SKILL.md 互換、ワークフローはチャットで「consult: …」と依頼、
**メモリ二重管理の禁止ルール必須**＝Hermes の MEMORY.md には正典へのポインタのみ置く）。

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
**Claude Code** ── `.claude/settings.json`（設置済み）：`SessionStart`=session-start.py／`PreToolUse`=guardrail.py／
`PostToolUse`・`PostToolUseFailure`（matcher `mcp__.*|Bash|PowerShell|Write|Edit|…`）=operation-logger.py。
記録対象の分類（書き込み動詞・Bash パターン・コネクタ ID の別名）は `tools/hooks/operation-log.json` で調整する。
`task-log-writer.py`（SessionEnd 用の活動マーカー）は Gemini CLI 向けの任意資産で、Claude Code では使わない
（毎ターン発火する Stop に付けると空エントリが量産されるため）。

## 適応層のメンテナンス

- **正典を変更したら**：`.claude/`・`.github/` のポインタは本文を読むだけなので基本追従する。
  スキル・ワークフローを**追加/削除/改名**したときだけ、対応するポインタファイルを追加/削除する。
  スキルのポインタは以下で一括再生成できる（bash）：
  ```bash
  for dir in .agents/skills/*/; do name=$(basename "$dir"); mkdir -p ".claude/skills/$name";
    awk 'NR==1 && /^---/{fm=1; print; next} fm==1{print; if(/^---/){exit}}' "$dir/SKILL.md" > ".claude/skills/$name/SKILL.md";
    printf '\n# %s（ポインタ）\n\nこのスキルの手順の正典は `.agents/skills/%s/SKILL.md` にある。\nそれを読み、記載された手順を忠実に実行すること。手順を編集する場合も正典側を直す。\n' "$name" "$name" >> ".claude/skills/$name/SKILL.md"; done
  ```
- **ガードレールを変更したら**：`memory/guardrails.json`（正典）→ ①GUI Deny List ②`.claude/settings.json` の
  `permissions.deny` の両方に転記する。フックスクリプト（guardrail.py）は正典を直接読むため転記不要。
- **Hermes 利用者**：本体アップデート後に `bash tools/hermes/install-skills.sh` を再実行する
  （`~/.hermes/skills/` のポインタを最新化）。
- 他ツール（Cursor / Codex 等）への追加対応：`AGENTS.md`＋`memory/` はそのまま効くため、
  必要ならスラッシュコマンド相当のポインタ層だけ足せばよい。
