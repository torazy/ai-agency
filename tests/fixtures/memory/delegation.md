# 委任レベル設定（delegation）

> 秘書がどこまで自律実行してよいかを定義するファイル。
> ユーザーが明示的に許可した操作のみ自律実行できる。
> 「このくらいはいちいち聞かなくていい」と思ったら秘書に指示して追加する。

---

## 現在のレベル：Level 1（全確認）

新しい秘書なので、すべての実行前にユーザーへ確認を取る。
信頼が積み重なったら Level 2、Level 3 へ移行する。

### Claude Code での強制（権限機構と対応）

この委任レベルは Claude Code の権限機構で物理的に裏付ける：
- **Level 1 = 既定の確認モード**：外部書き込み・シェル実行は毎回許可を求める（最も確実）。
- **昇格 = `permissions.allow`**：実績を積んだ安全な操作を `.claude/settings.local.json`（個人）または
  `.claude/settings.json`（共有）の allow に追加し、自動承認へ移す（下の承認済みテーブルと同期）。
- **常設ガード = `permissions.deny` ＋ PreToolUse フック**：`memory/guardrails.json` の `deny.patterns` を
  `tools/hooks/guardrail.py` が照合してブロックする。deny は allow より常に優先される。
- 承認実績は PostToolUse フック（`tools/hooks/operation-logger.py`）が `memory/task-log/` に自動記録し、
  `/propose-delegation` がそれを集計して昇格を提案する。

---

## Level 定義

| Level | 方針 |
|---|---|
| 1 | すべての実行・作成・更新・送信前に確認する |
| 2 | 承認済みリストの操作は確認なしで実行。新しい操作は確認する |
| 3 | 定型操作は完全自律。完了を事後報告するだけでよい |

---

## 自律実行OK（承認済み操作）

> ユーザーが明示的に「確認不要」と指示した操作をここに追加する。

| 操作 | 対象範囲 | 承認日 | 条件 |
|---|---|---|---|
| `memory/` 配下のファイル読み取り | すべて | 初期設定 | 常に許可 |
| `memory/` 配下へのメモ書き込み（新規ファイル） | memos/, task-log/ | 初期設定 | 常に許可 |

---

## 要確認（実行前に必ず提示）

- Backlog チケット作成・更新・コメント追加
- GitHub PR作成・コメント・レビュー
- Google Drive ファイル作成・編集
- Slack メッセージ送信（Slackbot問い合わせ含む）
- `memory/projects/` `memory/delegation.md` `memory/dashboard.md` の更新
- 外部サービスへのいかなる書き込み操作

---

## 禁止（明示的許可なしに絶対行わない）

- 本番環境へのデプロイ
- データ削除（Backlogチケットclose含む）
- 認証情報の変更
- 外部への情報共有（社外へのファイル送付等）
