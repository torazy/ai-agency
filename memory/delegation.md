# 委任レベル設定（delegation）

> 秘書がどこまで自律実行してよいかを定義するファイル。
> ユーザーが明示的に許可した操作のみ自律実行できる。
> 「このくらいはいちいち聞かなくていい」と思ったら秘書に指示して追加する。

---

## 現在のレベル：Level 1（全確認）

新しい秘書なので、すべての実行前にユーザーへ確認を取る。
信頼が積み重なったら Level 2、Level 3 へ移行する。

### IDE での強制（Antigravity Permissions と対応）

この委任レベルは Antigravity の権限機構（Settings → Permissions → Advanced）で物理的に裏付ける：
- **Level 1 = Strict Mode**：全ターミナルコマンドで確認を求める（最も確実）。
- **昇格 = Allow List Terminal Commands**：実績を積んだ安全なコマンドを自動承認へ移す（下の承認済みテーブルと同期）。
- **常設ガード = Deny List Terminal Commands**：`memory/guardrails.json` の `gui_deny_tokens` を登録。一致時は必ず確認。
- 変更後は Antigravity を再起動。Deny/Allow が効かない既知バグ時は Strict Mode を保険にする。

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
