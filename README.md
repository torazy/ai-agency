# AI秘書エージェント（Antigravity 2.0）

自律成長型のAI秘書「チーム」。第一秘書（@chief）が真意を読み、専門家集団を独立サブエージェントとして
召集し、統合回答を返す。情報をプロジェクト別に蓄積し、定期実行で先回りし、信頼の蓄積に応じて
自律範囲を広げていく。最終ゴールは「ユーザーは承認するだけ」。

> 前提：**Antigravity 2.0（IDE/GUI）**。動的サブエージェント・Scheduled Tasks・Hooks を使う。
> 将来の Claude Code 移行も考慮した構成（`PORTABILITY.md`）。

## セットアップ

### 1. 環境変数
```powershell
Copy-Item .env.template .env
# .env を開いて各値を設定する
```

### 2. Backlog MCP の依存ライブラリ
```powershell
cd tools/backlog-mcp
pip install -r requirements.txt
```

### 3. MCP サーバーを登録する
IDE の MCP ストア、または `~/.gemini/config/mcp_config.json` に設定する。
構成の控えは `tools/mcp_config.sample.json`（github / backlog / brave-search）。

### 4. Antigravity IDE でワークスペースを開く
`ai-manager` を開くと、`AGENTS.md`（常時ルール）・`.agents/agents.md`（ペルソナ）・
`.agents/skills/`（スキル）・`.agents/workflows/`（スラッシュコマンド）が認識される。
安全のため、続けて GUI の Deny List を設定する（下記「自律と安全の仕組み」参照）。

### 5. Chrome を Slack・Google Workspace にログインさせておく
ブラウザ操作スキル（slack-via-bot, calendar-read, drive-search）の前提。

---

## ファイル構造

```
ai-manager/
├── AGENTS.md                 # 常時ルール（移植可能コア・行動規範/記憶モデル/安全規則）
├── GEMINI.md                 # Antigravity固有の上書き＋稼働中サマリー（薄い）
├── PORTABILITY.md            # Claude Code 移行マップ
├── .agents/
│   ├── agents.md             # 全ペルソナ（@chief + @generalist + 8専門家）
│   ├── workflows/            # スラッシュコマンド
│   │   ├── consult.md            # /consult  専門家を並列召集して統合
│   │   ├── intake.md             # /intake   情報ファイリング
│   │   ├── morning-brief.md      # /morning-brief 朝の横断ブリーフィング
│   │   ├── review-decisions.md   # /review-decisions 予測vs実際の検証
│   │   └── propose-delegation.md # /propose-delegation 委任昇格の提案
│   └── skills/               # 24スキル（intake-and-file, capability-expansion, 各専門家review 等）
├── memory/
│   ├── situation.md          # 優先事項ダッシュボード
│   ├── delegation.md         # 委任レベル（人間可読）
│   ├── guardrails.json       # 物理ガードレール（機械可読・フックが参照）
│   ├── service-capabilities.md, user-profile.md, insights.md
│   ├── team/roster.md        # チーム名簿
│   ├── projects/{name}/      # プロジェクト別ナレッジ
│   ├── people/               # 人物情報
│   └── task-log/             # 活動ログ（gitignore・個人情報を含みうる）
└── tools/
    ├── backlog-mcp/server.py
    ├── mcp_config.sample.json
    └── hooks/
        ├── guardrail.py          # CLI/Claude Code用：deny操作を物理ブロック（IDEはGUI Deny Listを使用）
        └── task-log-writer.py    # CLI/Claude Code用：セッション終了時に活動マーカー記録
```

---

## 使い方

| やりたいこと | コマンド／言い方 |
|---|---|
| 複合・専門的な判断を仰ぐ | `/consult このツール導入をコスト・法務・セキュリティで評価して` |
| 情報をファイリング | `/intake`（または会議メモ等を貼って「ファイリングして」） |
| 今日の状況を把握 | `/morning-brief` |
| 過去の決定を検証 | `/review-decisions` |
| 自律範囲の昇格提案 | `/propose-delegation` |
| プロジェクト登録 | 「〇〇を登録したい。Backlogキー=XXX、GitHub=owner/repo、Drive=URL、Slack=#ch」 |

---

## 定期実行（Scheduled Tasks）の登録例

IDE のチャットで `/schedule` に続けて自然言語で指示する：

```
/schedule 毎朝8:30に /morning-brief を実行して結果を報告して
/schedule 毎週金曜17:00に /review-decisions を実行して
/schedule 毎週月曜9:00に /propose-delegation を実行して
```

エージェントはデスクトップを閉じていてもバックグラウンドで実行し、結果を後で確認できる。

---

## 自律と安全の仕組み

IDE の権限機構（Settings → **Permissions** → **Advanced**）と `delegation.md` を組み合わせて運用する。
これは「育てると承認だけ」の昇格モデルにそのまま対応する：

| 段階 | IDE設定 | 意味 |
|---|---|---|
| Lv1（出発点・現在） | **Strict Mode 推奨** | 全ターミナルコマンドで確認を求める。最も確実（リストのバグに依存しない） |
| 昇格 | **Allow List Terminal Commands** | 実績を積んだ安全なコマンド（`ls`/`git status`/`npm test` 等）を自動承認に移す |
| 常設ガード | **Deny List Terminal Commands** | `gui_deny_tokens`（`rm -rf`・`terraform apply`・`DROP TABLE` 等）。一致すると**実行前に必ず確認**を求める（Allowより優先） |

- 設定変更後は **Antigravity を再起動**する（反映に必要）。Deny/Allow が効かない既知バグ報告があるため、
  まず Strict Mode で確実に確認フローを確保し、信頼に応じて Allow List へ移すのが安全。
- `/propose-delegation` が承認実績を見て「Allow List へ移す候補」を提案する。実際の追加はGUIで行う。
- **物理ガードレール（CLI/Claude Code）**：`tools/hooks/guardrail.py` ＋ `memory/guardrails.json`（IDEは未使用）。
  （`tools/hooks/guardrail.py` は CLI／Claude Code 用の移植資産。IDEはスクリプトフックを読まないため未使用。）
- **フィードバックループ**（`/review-decisions`）：決定の予測と実際を突き合わせ `insights.md` に学習。

---

## 自己成長

1. 新しいサービスが必要 → `capability-expansion` が Level 1（ブラウザ）/2（MCP）/3（カスタム）を判定。
2. 専門分野が不足 → `recruit-expert` が `.agents/agents.md` に新ペルソナを追加提案。
3. 定型タスク反復 → `skill-improvement` がスキル化・改善を提案。
