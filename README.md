# AI秘書エージェント

自律成長型AI秘書エージェント。複雑なタスクを自律実行し、情報をプロジェクト別に蓄積し、新しいサービスへの対応を自力で広げていく。

## セットアップ

### 1. 環境変数を設定する

```powershell
Copy-Item .env.template .env
# .env を開いて各値を設定する
```

### 2. Backlog MCPサーバーの依存ライブラリをインストールする

```powershell
cd tools/backlog-mcp
pip install -r requirements.txt
```

### 3. GitHub MCP と Brave Search MCP をインストールする

```powershell
# Node.js が必要（https://nodejs.org）
npx -y @modelcontextprotocol/server-github --version
npx -y @modelcontextprotocol/server-brave-search --version
```

### 4. Antigravity IDE でワークスペースを開く

`ai-manager` フォルダをAntigravity IDEで開くと、`.agents/plugins/ai-secretary/` が自動認識され、16のスキルとルールが有効になる。

### 5. ChromeをSlack・Google Workspace にログインさせておく

ブラウザ操作スキル（slack-via-bot, calendar-read, drive-search）の前提条件。

---

## ファイル構造

```
ai-manager/
├── GEMINI.md                          # 毎会話自動ロードされる永続コンテキスト
├── .env.template                      # 環境変数テンプレート
├── memory/
│   ├── situation.md                   # 現在の優先事項ダッシュボード
│   ├── delegation.md                  # 自律実行レベル設定
│   ├── service-capabilities.md        # サービス能力マップ（自己成長の基盤）
│   ├── user-profile.md                # ユーザープロファイル詳細
│   ├── insights.md                    # 学習記録・ドメイン知識
│   ├── projects/                      # プロジェクト別ナレッジベース
│   │   └── {project-name}/
│   │       ├── overview.md            # 概要 + サービス座標（最重要）
│   │       ├── status.md              # 現在の状態
│   │       ├── decisions.md           # 決定事項履歴
│   │       ├── issues.md              # 課題・懸念事項
│   │       ├── meetings/              # 会議メモ（YYYY-MM-DD.md）
│   │       ├── context/               # 背景知識・仕様
│   │       └── memos/                 # 雑多なメモ
│   ├── people/                        # 人物情報
│   └── task-log/                      # タスクログ（YYYY-MM.md）
├── .agents/
│   └── plugins/
│       └── ai-secretary/
│           ├── plugin.json            # プラグイン定義
│           ├── mcp_config.json        # MCPサーバー設定
│           ├── hooks.json             # Stopフック設定
│           ├── rules/
│           │   └── secretary-persona.md  # 秘書ペルソナ（Always On）
│           └── skills/
│               ├── intake-and-file/   # ★最重要：情報ファイリングエンジン
│               ├── capability-expansion/  # ★自己成長エンジン
│               ├── synthesize-situation/  # 状況統合
│               ├── judgment-support/  # 判断支援
│               ├── memory-update/     # 知識記録
│               ├── skill-improvement/ # スキル自己改善
│               ├── backlog-search/    # Backlog検索
│               ├── backlog-manage/    # Backlog操作
│               ├── github-review/     # GitHubレビュー
│               ├── github-manage/     # GitHub操作
│               ├── research-report/   # Webリサーチ
│               ├── slack-via-bot/     # Slack（ブラウザ）
│               ├── calendar-read/     # Googleカレンダー（ブラウザ）
│               ├── drive-search/      # Google Drive（ブラウザ）
│               ├── project-planning/  # プロジェクト計画
│               └── document-review/   # ドキュメントレビュー
└── tools/
    ├── backlog-mcp/
    │   ├── server.py                  # カスタムBacklog MCPサーバー
    │   └── requirements.txt
    └── hooks/
        └── task-log-writer.py         # タスクログ自動記録スクリプト
```

---

## 使い方

### プロジェクトを登録する

秘書に教える：
```
「〇〇プロジェクト」を登録したい。
BacklogプロジェクトキーはXXX、GitHubリポジトリはowner/repo、
Driveフォルダは https://drive.google.com/... 、Slackチャンネルは #channel-name
```

### 情報をファイリングする

会議メモ・Slack会話・仕様書のテキストを貼り付けて：
```
これをプロジェクトAにファイリングして
```

### 状況を把握する

```
プロジェクトAの現状を教えて
今週の優先事項は？
```

### タスクを実行する

```
プロジェクトAのBacklogで期限が近いチケットを確認して
このPRをレビューして
〇〇について調べてまとめて
```

---

## 自己成長の仕組み

1. **Slackbot・Dropboxなど新しいサービス**が必要になったら秘書に伝える
2. `capability-expansion` スキルが対応レベルを判定して提案する
3. Level 1: ブラウザスキルを自律作成
4. Level 2: MCP設定変更を提示
5. Level 3: カスタム実装の要件を整理
