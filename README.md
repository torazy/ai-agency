# AI秘書チーム ai-agency（Claude Code）

自律成長型のAI秘書「チーム」。第一秘書（@chief）が真意を読み、専門家集団を独立サブエージェントとして
召集し、統合回答を返す。情報をプロジェクト別に蓄積し、定期実行で先回りし、信頼の蓄積に応じて
自律範囲を広げていく。最終ゴールは「ユーザーは承認するだけ」。

> **Claude Code 専用**（デスクトップアプリ / CLI / IDE 拡張）。他ツールへの対応は 2026-11-04 に凍結
> （復元の手がかりは `PORTABILITY.md`）。記憶の正典は `memory/` の markdown で、ツールに依存しない。

> ⚠️ **このリポジトリは本体（テンプレート）。ここに個人の記憶を蓄積しない。**
> 使う人ごとに自分のプライベートリポジトリ（インスタンス）を作って運用する。
> セットアップ・本体アップデートの取り込み・改善の還流は **`OPERATIONS.md`** 参照。

## セットアップ（インスタンス側）

### 1. 環境変数
```powershell
Copy-Item .env.template .env
# .env を開いて各値を設定する（Backlog / GitHub / Brave）
```

### 2. Backlog MCP の依存ライブラリ
```powershell
cd tools/backlog-mcp
pip install -r requirements.txt
```

### 3. Claude Code でフォルダを開く
`.mcp.json`（github / backlog / brave-search）、`.claude/settings.json`（ガードレール・フック）、
`.claude/agents|skills|commands` が自動で認識される。Slack・Google Workspace などは claude.ai のコネクタを
接続しておくと MCP として使える（無ければ手動アシストに落ちる）。

### 4. `/setup` を実行する
対話でプロフィール・案件・人物・優先事項を登録し、使えるツールを棚卸しする（約15分・再実行可）。
これをしないと `/simulate`・`/morning-brief`・`/propose-delegation` は素材が無く動かない。

---

## ファイル構造

```
ai-agency/
├── CLAUDE.md                 # 常時ルール（唯一の正典。@chief の人格・記憶モデル・安全規則）
├── USAGE.md                  # 場面別の使い方ガイド（人間向け）
├── OPERATIONS.md             # 本体とインスタンスの分離・メンテナンス層・2台運用
├── PORTABILITY.md            # 他ツール対応の凍結メモ（復元の手がかり）
├── .mcp.json                 # MCP 設定（github / backlog / brave-search）
├── .claude/
│   ├── agents/               # 専門家×7＋汎用秘書（人格本体。/consult が Agent ツールで並列召集）
│   ├── commands/             # /setup /intake /update /consult /discuss /simulate /incident /morning-brief /review-decisions /propose-delegation
│   ├── skills/               # 32スキル（intake-and-file, commitment-track, person-model-update, *-review 等）
│   └── settings.json         # permissions.deny＋SessionStart/PreToolUse/PostToolUse フック
├── memory/
│   ├── dashboard.md          # 稼働登録簿（CLAUDE.md がインポート・100行上限）
│   ├── situation.md          # 優先事項ダッシュボード
│   ├── inbox.md              # 「覚えておいて」の受け皿（分類前に即時保存。mem.py inbox）
│   ├── commitments.md        # 約束台帳（ID 付き。mem.py commitment で追記・状態変更）
│   ├── delegation.md         # 委任レベル（人間可読）
│   ├── guardrails.json       # 物理ガードレール（機械可読・フックが参照）
│   ├── service-capabilities.md, user-profile.md, insights.md
│   ├── team/roster.md        # チーム名簿
│   ├── projects/{name}/      # プロジェクト別ナレッジ
│   ├── people/               # 人物情報＋人物モデル（判断事例ログ・反応予測の検証）
│   └── task-log/             # 操作実績（gitignore・フックが自動記録）
├── tests/                    # 自動テスト（unittest。記録・検索・点検・フック・ボード API・構造。fixtures/memory は雛形の写し）
├── .github/workflows/test.yml # push / PR で Windows・Linux の両方でテスト
└── tools/
    ├── mem.py                    # memory/ への決定的な追記 CLI（inbox / commitment / person / decision / check / migrate）
    ├── recall.py                 # 探す・読む・点検する（読み取り専用。find / who / map / show / review。段階的開示の道具）
    ├── import_legacy.py          # 旧版のインスタンスから記憶だけを移す（既定は計画表示。--apply で実行。OPERATIONS.md §2b）
    ├── board/                    # 秘書ボード：memory/ を見る・触るローカル画面（server.py＋index.html。書き込みは mem.py 経由）
    ├── backlog-mcp/server.py
    ├── dev/                      # 本体メンテナンス層（雛形。本体フォルダでだけ有効化する）
    ├── sync/                     # 2台運用の片方向同期（export / import。OPERATIONS.md §6）
    └── hooks/
        ├── guardrail.py          # PreToolUse：deny 操作を物理ブロック
        ├── session-start.py      # SessionStart：記憶の育成状態（未初期化・期限超過）を会話冒頭に注入
        ├── operation-logger.py   # PostToolUse：外部書き込み・送信を task-log に自動記録（/propose-delegation の素材）
        └── operation-log.json    # ↑の分類設定（書き込み動詞・Bash パターン・コネクタ ID の別名）
```

---

## 使い方

> 📖 **場面別の詳しい使い方は [USAGE.md](USAGE.md)**（人物モデルの育て方・定期実行の推奨セット含む）。

| やりたいこと | コマンド／言い方 |
|---|---|
| **最初に記憶を初期化する（必須）** | `/setup`（プロフィール・案件・人物・優先事項を対話で登録、使えるツールを棚卸し。約15分） |
| 複合・専門的な判断を仰ぐ | `/consult このツール導入をコスト・法務・セキュリティで評価して` |
| テーマを与えて議論させる | `/discuss 勤怠SaaSはクラウド型でよいか 参加: @accountant @it-architect 田中部長`（専門家＋人物モデルの混成パネル。ラウンド制で統合） |
| 報告・指示のリハーサル | `/simulate 田中部長 来期予算の増額案を報告したい` |
| 障害対応 | `/incident 本番APIがエラーを返している` |
| 情報をファイリング | `/intake`（または会議メモ等を貼って「ファイリングして」） |
| 忘れずに覚えさせる | 「覚えておいて」＋内容（分類前に inbox へ即時保存され、分類後に記録先が報告される。迷えば1行で確認される） |
| タスク・指示を記録 | 「〇〇やっておく」「部長に△△を頼まれた」「鈴木さんに□□を依頼した」と言うだけ |
| 今日の状況を把握 | `/morning-brief`（平日の朝に無人で回しておくと、`memory/briefs/` に用意され、拾った約束は台帳に追記済み・要確認だけ inbox に残る） |
| つながったサービスから取り込む | `/update`（カレンダー・メール・チャット等から前回以降の約束・依頼・人物の反応を拾い、秘書が記録して事後報告。確認が要るものだけ1問） |
| 専門家を強くする | 「@legal を強くしたい」→ knowledge-work-plugins（legal / finance / human-resources / product-management）を承認制で導入。入れなくても動く |
| 記憶を探す・見直す | 「〇〇について何て言ってた？」→ 秘書が `tools/recall.py` で段階的に探す（一覧→該当節だけ）。毎朝の点検で古さ・食い違い・重複・取り出し漏れを拾い、秘書が直す |
| 画面で見る・触る | 「ボードを開いて」（または `python tools/board/server.py` → http://127.0.0.1:8765/）。約束・inbox・案件・人物・決定を一覧し、状態変更や分類も画面から |
| 過去の決定を検証 | `/review-decisions` |
| 自律範囲の昇格提案 | `/propose-delegation` |
| プロジェクト登録 | 「〇〇を登録したい。Backlogキー=XXX、GitHub=owner/repo、Drive=URL、Slack=#ch」 |

---

## 定期実行の登録例

毎朝のブリーフは**このフォルダで動かす**（memory/ がここにあるため。クラウドの `/schedule` は memory/ を書き戻せない）。
`/setup` の Step 6 で秘書が案内する。手で登録する場合は次のどちらか：

- **デスクトップアプリの定期タスク**（アプリが開いている間に動く）：会話で「平日8:30に朝のブリーフを無人で回して」と頼む
- **OS のスケジューラ**（アプリを閉じていても動く）：
  - Windows：`tools/schedule/morning-brief.ps1` をタスク スケジューラに登録（コマンド例はスクリプト冒頭）
  - macOS / Linux：`tools/schedule/morning-brief.sh` を cron に登録（`30 8 * * 1-5 /path/to/tools/schedule/morning-brief.sh`）

無人実行は `/morning-brief --unattended`：コネクタから前回以降の約束・依頼を拾い、確信のあるものは台帳等に**追記**、要確認だけ inbox に残す。
外部への送信や既存行の書き換えはしない。結果は `memory/briefs/{日付}.md` に残り、次に会話を始めると件数が知らされ、ボードの「今日」にも出る。
週次の `/review-decisions`・`/propose-delegation`、月次の `memory-maintenance` も同じ仕組みで登録できる。

---

## 自律と安全の仕組み

Claude Code の権限機構と `memory/delegation.md` を組み合わせる。「育てると承認だけ」の昇格モデルにそのまま対応する：

| 段階 | Claude Code 設定 | 意味 |
|---|---|---|
| Lv1（出発点・現在） | 既定の確認モード | 外部書き込み・シェル実行は毎回許可を求める。最も確実 |
| 昇格 | `permissions.allow` | 実績を積んだ安全な操作を自動承認に移す。`/propose-delegation` が候補を提案する |
| 常設ガード | `permissions.deny` ＋ PreToolUse フック | `memory/guardrails.json` の deny パターン（`rm -rf`・`terraform apply`・`DROP TABLE` 等）を機械的にブロック。allow より常に優先 |

- **承認実績の自動記録**：PostToolUse フック（`tools/hooks/operation-logger.py`）が外部書き込み・送信を
  `memory/task-log/` に記録し、`/propose-delegation` が集計する。秘書が手で書く必要はない。
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

1. 新しいサービスが必要 → `capability-expansion` が Level 1（ブラウザ）/2（MCP・コネクタ）/3（カスタム）を判定。
2. 専門分野が不足 → `recruit-expert` が `.claude/agents/` に新しい専門家を追加提案。
3. 定型タスク反復 → `skill-improvement` がスキル化・改善を提案。
4. 人物モデルの精度不足 → 観察のファイリングを促し、成熟度Lvの昇格で使える場面を広げる。
