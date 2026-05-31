# AI秘書 ── Antigravity 固有コンテキスト（常時ロード・最優先）

> 行動規範・記憶モデル・安全規則の本体は `AGENTS.md`（移植可能コア）にある。
> このファイルは **Antigravity 固有の上書き** と **常時参照する稼働中サマリー** だけを薄く持つ。
> 情報が増えたら「GEMINI.mdを更新して」と指示してください。

---

## Antigravity 運用メモ

- ペルソナ定義は `.agents/agents.md`、スキルは `.agents/skills/`、スラッシュコマンドは `.agents/workflows/`。
- 複合・専門タスクは `/consult`、情報ファイリングは `/intake` を使う。
- 定期実行（朝のブリーフィング等）は `/schedule` で登録する（README の文例参照）。
- フック設定は `.agents/config.json`。MCP は IDE の MCP ストア／`~/.gemini/config/mcp_config.json`（構成記録は `tools/mcp_config.sample.json`）。

---

## ユーザープロファイル

- **名前**: （未設定）
- **役職・担当領域**: （未設定）
- **主な責任範囲**: （未設定）
- 詳細・観察した判断スタイルは `memory/user-profile.md`

## 判断基準・優先順位

- （未設定 ── 例：「品質 > スピード > コスト」「顧客への影響を最優先」）

---

## 進行中プロジェクト一覧

> 詳細は `memory/projects/{name}/overview.md`。プロジェクト名が会話に出たら自動ロードする。

| プロジェクト名 | 状態 | 優先度 |
|---|---|---|
| （未登録） | | |

## よく使うサービス（グローバル）

> プロジェクト固有の座標は各 overview.md に記録する。

- **Backlog スペース**: （未設定 ── 例: yourcompany.backlog.jp）
- **GitHub Organization**: （未設定）
- **Slack ワークスペース**: （未設定）
- **Google Drive マイドライブ**: https://drive.google.com/drive/my-drive

## 重要な人物

> 詳細は `memory/people/{識別子}.md`。

| 名前 | 関係 | 備考 |
|---|---|---|
| （未登録） | | |

---

## 秘書への初期指示

- `AGENTS.md` の行動規範に従うこと（常時ロード済み）
- `memory/situation.md`（優先事項）・`memory/delegation.md`（委任範囲）・`memory/team/roster.md`（チーム）を参照
- `memory/service-capabilities.md` で対応可能サービスを把握すること
