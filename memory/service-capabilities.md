# サービス能力マップ

> 秘書がアクセスできるサービスの一覧。
> 新しいサービスが必要になったとき、秘書はここを確認して対応策を判断する。
> 追加・更新は `capability-expansion` スキルが行う。

---

## 稼働中

| サービス | 手段 | スキル/MCP | 備考 |
|---|---|---|---|
| GitHub | MCP | github-mcp（公式） | PAT認証 |
| Backlog | MCP | backlog-mcp（カスタム） | APIキー認証 |
| Web検索 | MCP | brave-search-mcp | APIキー認証 |
| Memory | MCP | memory-mcp（公式） | ローカル |
| Filesystem | MCP | filesystem-mcp（公式） | ローカル |
| Slack | ブラウザ（Slackbot経由） | slack-via-bot | Chromeログイン済み前提 |
| Google Calendar | ブラウザ | calendar-read | 読み取り専用・Chromeログイン済み |
| Google Drive / Docs / Sheets | ブラウザ | drive-search | 読み取り専用・Chromeログイン済み |

---

## 未対応（拡張候補）

| サービス | 難易度 | 対応策 | ステータス |
|---|---|---|---|
| Dropbox | Level 1 | ブラウザスキル作成で対応可 | 未着手 |
| Notion | Level 2 | コミュニティMCPあり | 未着手 |
| Confluence | Level 2 | コミュニティMCPあり | 未着手 |

---

## 対応レベルの定義

- **Level 1**: ブラウザ操作スキルの作成で対応可能。軽い確認で秘書が自律作成。
- **Level 2**: コミュニティMCPの設定変更で対応可能。内容を提示して承認を得る。
- **Level 3**: カスタム実装（MCP開発）が必要。要件・工数・選択肢を整理して判断を仰ぐ。

---

## 対応失敗・学習記録

| 日付 | サービス | 試みた内容 | 失敗理由 | 次の対策 |
|---|---|---|---|---|
| （記録なし） | | | | |
