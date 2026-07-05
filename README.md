# AI秘書エージェント（Antigravity 2.0）

自律成長型のAI秘書「チーム」。第一秘書（@chief）が真意を読み、専門家集団を独立サブエージェントとして
召集し、統合回答を返す。情報をプロジェクト別に蓄積し、定期実行で先回りし、信頼の蓄積に応じて
自律範囲を広げていく。最終ゴールは「ユーザーは承認するだけ」。

> **ツール非依存**。Antigravity 2.0・Claude Code（どちらもフル機能）・Hermes Agent（ほぼフル＋
> 常時稼働・モバイル）・GitHub Copilot（部分対応）の適応層を並置済みで、どのツールで開いても
> 同じ秘書・同じ記憶で動く（対応表は `PORTABILITY.md`、Hermes は `tools/hermes/SETUP.md`）。

> ⚠️ **このリポジトリは本体（テンプレート）。ここに個人の記憶を蓄積しない。**
> 使う人ごとに GitHub Template から自分のプライベートリポジトリ（インスタンス）を作って運用する。
> セットアップ・本体アップデートの取り込み・改善の還流は **`OPERATIONS.md`** 参照。

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
├── AGENTS.md                 # 常時ルール（唯一の正典・全ツールが読む）
├── USAGE.md                  # 場面別の使い方ガイド（人間向け）
├── GEMINI.md                 # Antigravity適応層＋稼働中サマリー（薄い）
├── CLAUDE.md                 # Claude Code適応層（@AGENTS.md をインポート・薄い）
├── .mcp.json                 # Claude Code用MCP設定（tools/mcp_config.sample.json と同期）
├── PORTABILITY.md            # Claude Code 移行マップ
├── .agents/
│   ├── agents.md             # 全ペルソナ（@chief + @generalist + 8専門家）
│   ├── workflows/            # スラッシュコマンド
│   │   ├── consult.md            # /consult  専門家を並列召集して統合
│   │   ├── intake.md             # /intake   情報ファイリング
│   │   ├── simulate.md           # /simulate 実在人物の反応を事前予測（リハーサル）
│   │   ├── incident.md           # /incident 障害対応（時系列ログ・連絡下書き・振り返り）
│   │   ├── morning-brief.md      # /morning-brief 朝の横断ブリーフィング（約束台帳もスキャン）
│   │   ├── review-decisions.md   # /review-decisions 予測vs実際の検証（人物予測も対象）
│   │   └── propose-delegation.md # /propose-delegation 委任昇格の提案
│   └── skills/               # 28スキル（intake-and-file, commitment-track, deliverable-create 等）
├── .claude/                  # Claude Code 適応層（すべて .agents/ へのポインタ）
│   ├── agents/               # 専門家サブエージェント×8（Taskツールで並列召集）
│   ├── commands/             # /consult /simulate 等×6
│   ├── skills/               # スキルポインタ×25（自動起動用）
│   └── settings.json         # permissions.deny＋PreToolUse/Stopフック（ガードレール）
├── .github/                  # GitHub Copilot 適応層（部分対応・劣化モード明記）
│   ├── copilot-instructions.md
│   └── prompts/              # /consult 等×6（prompt.md）
├── memory/
│   ├── dashboard.md          # 稼働登録簿（全ツールが会話開始時にロード・100行上限）
│   ├── situation.md          # 優先事項ダッシュボード
│   ├── commitments.md        # 約束台帳（細かなタスク・受けた指示・出した指示のフォロー）
│   ├── delegation.md         # 委任レベル（人間可読）
│   ├── guardrails.json       # 物理ガードレール（機械可読・フックが参照）
│   ├── service-capabilities.md, user-profile.md, insights.md
│   ├── team/roster.md        # チーム名簿
│   ├── projects/{name}/      # プロジェクト別ナレッジ
│   ├── people/               # 人物情報＋人物モデル（判断事例ログ・反応予測の検証）
│   └── task-log/             # 活動ログ（gitignore・個人情報を含みうる）
└── tools/
    ├── backlog-mcp/server.py
    ├── mcp_config.sample.json
    ├── hermes/                   # Hermes Agent 適応層（SETUP.md＋スキルインストーラ）
    ├── sync/                     # 2台運用の片方向同期（export.sh / import.sh。OPERATIONS.md §6）
    └── hooks/
        ├── guardrail.py          # CLI/Claude Code用：deny操作を物理ブロック（IDEはGUI Deny Listを使用）
        └── task-log-writer.py    # CLI/Claude Code用：セッション終了時に活動マーカー記録
```

---

## 使い方

> 📖 **場面別の詳しい使い方は [USAGE.md](USAGE.md)**（人物モデルの育て方・定期実行の推奨セット含む）。

| やりたいこと | コマンド／言い方 |
|---|---|
| 複合・専門的な判断を仰ぐ | `/consult このツール導入をコスト・法務・セキュリティで評価して` |
| 報告・指示のリハーサル | `/simulate 田中部長 来期予算の増額案を報告したい` |
| 障害対応 | `/incident 本番APIがエラーを返している` |
| 情報をファイリング | `/intake`（または会議メモ等を貼って「ファイリングして」） |
| タスク・指示を記録 | 「〇〇やっておく」「部長に△△を頼まれた」「鈴木さんに□□を依頼した」と言うだけ |
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
/schedule 毎月1日9:00に memory-maintenance スキルでメモリを整理して
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

## 実在人物モデル（AI支援チームとは別枠）

実際の上司・メンバー・顧客の**考え方・反応を観察から学習**し、報告・指示の前にリハーサルできる。
AI専門家チーム（正しい答えを出す）とは独立した仕組み（実際のあの人の反応を予測する）。

1. **蓄積**：会議メモ・Slack・会話に人物の発言・判断があれば `person-model-update` が
   `memory/people/{識別子}.md` の判断事例ログに記録。蓄積量でモデル成熟度が上がる
   （Lv0: 情報のみ → Lv1: 仮説 → Lv2: 事例5件以上でシミュレーション実用 → Lv3: 予測的中実績あり）。
2. **リハーサル**：`/simulate 田中部長 この提案` で人物モデルを独立サブエージェントとして召集し、
   第一反応・想定質問・懸念・OK条件を予測。伝え方の推奨まで出す。複数人指定で会議リハーサルも可。
3. **検証**：予測は人物ファイルに記録され、実際の反応との答え合わせを `/review-decisions` が走査。
   外れたパターンがモデルの補正材料になる（decisions.md と同じフィードバックループ）。

> 安全規則：予測を本人の発言として扱わない・共有しない。**なりすまし文面の作成・送信は禁止**。
> 人物ファイルはリポジトリ外に持ち出さない。

## 自己成長

1. 新しいサービスが必要 → `capability-expansion` が Level 1（ブラウザ）/2（MCP）/3（カスタム）を判定。
2. 専門分野が不足 → `recruit-expert` が `.agents/agents.md` に新ペルソナを追加提案。
3. 定型タスク反復 → `skill-improvement` がスキル化・改善を提案。
4. 人物モデルの精度不足 → 観察のファイリングを促し、成熟度Lvの昇格で使える場面を広げる。
