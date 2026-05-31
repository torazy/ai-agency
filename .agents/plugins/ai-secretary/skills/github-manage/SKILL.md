---
name: github-manage
description: GitHubでIssue作成・PRコメント追加・ラベル設定などの書き込み操作をするとき。「GitHubにIssueを作って」「このPRにレビューコメントを投稿して」などの依頼に使用する。実行前に必ず内容を提示して確認を取る。
---

# GitHub 管理スキル

GitHub MCPツールを使ってIssue・PRへの書き込み操作を行う。
**すべての操作は実行前にユーザーへ内容を提示して確認を取ること。**

## 使用するMCPツール

- `github_create_issue`: Issue作成
- `github_update_issue`: Issue更新
- `github_add_issue_comment`: Issueコメント追加
- `github_create_pull_request_review`: PRレビューコメント投稿
- `github_create_pull_request`: PR作成

## 共通ルール：実行前確認

```
## GitHub 操作確認

**操作**: {Issue作成 / コメント追加 / PRレビュー / PR作成}
**リポジトリ**: {owner/repo}
**対象**: {Issue#番号 / PR#番号 / 新規}
**内容**:
（送信される内容を全文表示）

▶ この内容で実行してよいですか？
```

## Issue 作成の手順

### Step 1: 必要情報を収集する

- **リポジトリ**: `memory/projects/{name}/overview.md` から取得
- **タイトル**: 問題を明確に表す（例：「〇〇でNullPointerExceptionが発生する」）
- **本文**: 再現手順・期待する動作・実際の動作・環境情報
- **ラベル**: bug / enhancement / question など
- **担当者**: 指定する場合のみ

### Step 2: 確認 → 実行

確認フォームを提示 → 承認後 → `github_create_issue` 実行

### Step 3: 完了報告

```
✅ GitHub Issue を作成しました。

**Issue**: #{番号} {タイトル}
**URL**: {URL}
```

## PRレビュー投稿の手順

`github-review` スキルで作成したレビューコメントを確認した後：

1. コメントを全文表示して確認を取る
2. 承認後に `github_create_pull_request_review` で投稿する
3. APPROVE / REQUEST_CHANGES / COMMENT のどれかをユーザーに選択させる

## PR作成の手順

### 必要情報

- **ベースブランチ**: main / develop など
- **ヘッドブランチ**: 作業ブランチ名
- **タイトル**: 変更内容の要約
- **本文**: 変更の目的・内容・テスト方法・Backlogチケット番号

```markdown
## 変更内容

{変更の目的と概要}

## 関連チケット

- Backlog: [{PROJECT}-{番号}]({URL})

## テスト

- [ ] {テスト項目1}
- [ ] {テスト項目2}
```

## 注意事項

- コメント・レビューは一度投稿すると通知が発火するため、特に慎重に確認する
- PRのマージ操作はこのスキルの対象外（別途確認が必要）
- フォースプッシュ・ブランチ削除は禁止（`delegation.md` 参照）
