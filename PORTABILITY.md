# 移植性メモ ── 他ツール対応は凍結中（2026-11-04）

このリポジトリは **Claude Code 専用**として運用する。2026-07 時点では Antigravity / GitHub Copilot / Hermes Agent
向けの適応層（ポインタ方式）を並置していたが、維持コストと「本体フォルダで製品が起動する」問題を踏まえ、
**他ツール対応は凍結**した。必要になったら以下を手がかりに再構築する。

## 正典とツール固有の配線は分けてある（再対応を安くするため）

| 層 | 場所 | 性質 |
|---|---|---|
| 常時ルール | `CLAUDE.md` | 正典。他ツールで読ませるには、このファイルを `AGENTS.md` 等にコピー／インポートする |
| 人格 | `.claude/agents/*.md`（frontmatter＋本文） | 本文はツール非依存。frontmatter（`skills:` 等）は Claude Code 固有 |
| 手順 | `.claude/skills/*/SKILL.md` | agentskills.io 標準の SKILL.md 形式。他ツールへは**ポインタ**または**コピー**で配布できる |
| コマンド | `.claude/commands/*.md` | 本文はワークフロー手順そのもの。他ツールのプロンプト形式へ転写可 |
| ガードレール | `memory/guardrails.json`（正典）→ `.claude/settings.json` の deny ＋ `tools/hooks/guardrail.py` | 他ツールでは GUI の Deny List 等に `deny.patterns` を転記する |
| 記憶 | `memory/` | ツール非依存（markdown） |

## 凍結前の実装の所在（git 履歴）

- 最終コミット：`987b143`（2026-11-04）。ここまでに `AGENTS.md`、`GEMINI.md`、`.agents/`（skills / workflows / agents.md）、
  `.github/copilot-instructions.md`・`.github/prompts/`、`tools/hermes/`、`tools/mcp_config.sample.json`、
  `tools/hooks/task-log-writer.py` が存在する
- 対応表・ポインタ再生成スクリプト・Hermes の SETUP.md も同コミットの `PORTABILITY.md` と `tools/hermes/` にある
- 復元するときは古いファイルをそのまま戻さず、**現在の正典（`.claude/`）からポインタを再生成**する

## 当時の対応度の要約（参考）

- Antigravity 2.0：`AGENTS.md`＋`.agents/` をネイティブ認識。スクリプトフックは IDE が読まないため、
  ガードレールは GUI Deny List（Settings → Permissions → Advanced）に転記する必要があった
- GitHub Copilot：並列サブエージェント・フック・定期実行なし。単一コンテキストの「劣化モード」で /consult を模倣
- Hermes Agent：`AGENTS.md` ネイティブ認識、スキルは `~/.hermes/skills/` にポインタ設置。常駐・cron・メッセージングで
  常時稼働とモバイルを補完できる位置づけだった
