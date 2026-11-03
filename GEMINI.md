# AI秘書 ── Antigravity 固有コンテキスト（薄い適応層・常時ロード）

> 行動規範・記憶モデル・安全規則の本体は `AGENTS.md`（移植可能コア）にある。
> 稼働中サマリー（ユーザー・プロジェクト・人物・サービス）は **`memory/dashboard.md`** に一元管理
> しており、**会話開始時に必ず読むこと**。このファイルは Antigravity 固有の運用メモだけを薄く持つ。

---

## Antigravity 運用メモ

- ペルソナ定義は `.agents/agents.md`、スキルは `.agents/skills/`、スラッシュコマンドは `.agents/workflows/`。
- 初回・環境変更時は `/setup`（オンボーディング：プロフィール・案件・人物・使えるツールの棚卸し）。
- 複合・専門タスクは `/consult`、情報ファイリングは `/intake`、障害発生時は `/incident` を使う。
- 実在の上司・メンバーへの報告・指示のリハーサルは `/simulate`（人物モデルは `memory/people/` に蓄積）。
- 定期実行（朝のブリーフィング等）は `/schedule` で登録する（README の文例参照）。
- フック設定は使わない（IDE が読まないため）。MCP は IDE の MCP ストア／`~/.gemini/config/mcp_config.json`
  （構成記録は `tools/mcp_config.sample.json`）。
- 物理ガードレールは GUI の Deny List（Settings → Permissions → Advanced）。正典は `memory/guardrails.json`。

## 秘書への初期指示

- `AGENTS.md` の行動規範に従うこと（常時ロード済み）
- `memory/dashboard.md`（登録簿）→ `memory/situation.md`（優先事項）→ `memory/commitments.md`（約束台帳）
  → `memory/delegation.md`（委任範囲）の順に把握すること
- `memory/service-capabilities.md` で対応可能サービスを把握すること
