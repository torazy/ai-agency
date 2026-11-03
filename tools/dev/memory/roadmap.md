# 改善ロードマップ（本体メンテ用メモリ）

> 本体の改善は段階的に行う。着手前にここを読み、終わったら状態を更新する。
> 利用者の実データは書かない（それは各インスタンスの `memory/`）。

## 診断（2026-11-04）

- 仕組みの大半（/simulate・/morning-brief・/propose-delegation・/review-decisions）は**記憶が育ってから効く**のに、
  育てる入口（オンボーディング・自動ログ・コネクタ駆動の取り込み）が無かった
- 操作ログは「秘書が手で書く」設計で誰も書かず、委任昇格エンジンが空回りしていた
- 能力地図（service-capabilities）が静的で、実際に接続されているコネクタと乖離していた
- スキルに特定ツール前提（Chrome・/browser）が残っていた
- 本体フォルダで製品そのものが起動し、メンテ中に制限・記録が発生していた

## 段階

| 段階 | 内容 | 状態 |
|---|---|---|
| 1 | `/setup` オンボーディング、PostToolUse 自動記録（Stop 廃止）、SessionStart で育成状態注入、能力地図を実行時発見に、4スキルの手段非依存化 | ✅ 2026-11-04 完了（9b46869・ca98ad2） |
| 1.5 | 本体メンテナンス層（`tools/dev/`：CLAUDE.local・settings.local・maint-* スキル・メンテ用メモリ）。製品を本体フォルダで止める | ✅ 2026-11-04 |
| 2 | `/update` 相当：接続済みコネクタ（メール・カレンダー・チャット）から約束・人物観察を拾う（貼り付け依存を脱する）。約束台帳を表の直接編集から小さな CLI（`tools/mem.py` 等）経由の追記に変え、壊れ方を減らす。操作ログ分類の精度向上（heredoc 本文のマッチ、`git -c … commit` の見逃し） | 🔲 未着手 |
| 3 | 専門家の周辺スキル：anthropics/knowledge-work-plugins の legal / finance / product-management / human-resources を @legal / @accountant / @pm / @hr に紐付け。リポジトリ内の research-report・deliverable-create はホストに同等スキル（deep-research・docs・pptx 等）があれば委譲する形に | ✅ 2026-11-04（expert-plugins スキル・専門家の共通規則と周辺スキル行・/setup Step 6b・roster の導入表・research-report と deliverable-create をホスト委譲に） |
| 4 | `/morning-brief` を定期実行に乗せ、Claude Duet 的な「本人がいない間も動く」体験に。承認ゲートは delegation.md のまま | 🔲 未着手 |

## 方針転換（2026-11-04）：Claude Code 専用でよい

ユーザー決定：「Claude Code 専用にしてよい。他のエージェントは必要になったら対応させる」。
→ 4ツール分の適応層（ポインタ方式）の維持は必須でなくなった。正典（手順・人格・記憶）とツール固有の配線は
分けたまま、Claude Code の機能（サブエージェント・agent teams・hooks・skills frontmatter・preview・
scheduled task）を前提に設計する。他ツール向けファイルは削除せず「凍結」と明記する。

## 新規要件（2026-11-04・ユーザーから）

| 要件 | 内容 | 設計メモ |
|---|---|---|
| **記録の確実性** | 人物・案件の記録を文脈から正しく判断できるか。曖昧なら確認する。「覚えておいて」は確実に覚え、分類して記録する | capture-first（`memory/inbox.md` に即時保存＝取りこぼしゼロ）→ classify-second（確信あり＝記録して事後報告／迷い＝記録先を1行提示して事前確認）。確認が要る典型：新規人物の作成、案件が複数あり本文に名前が無い、決定か案か不明、発言者不明。表の直接編集をやめ `tools/mem.py` 経由で追記（モデルの表崩しを防ぐ・ボードと共用） |
| **ボード** | タスク・案件・人物を画面で確認し、入力・操作もできる | `tools/board/`：Python のローカルサーバー＋単一ページ。memory/ の markdown を読み、書き込みは `tools/mem.py` と同じ経路（markdown が正典のまま。二重ストアにしない）。Claude Code の preview で開く。inbox の仕分け・約束の状態変更・人物の判断事例追加を最初の操作対象に。段階：読み取り専用 → inbox 仕分け → 編集 |
| **ディスカッション** | 人（人物モデル）や既定エージェント（専門家）をアサインし、テーマを与えて議論させる | `/discuss` ワークフロー：参加者＝専門家ペルソナ＋人物モデル（Lv1 以上）の混成可。@chief が司会。ラウンド制（①各自の意見を並列 → ②他者の意見を見て反論・修正 → ③収束と残論点）。実装は Agent ツール＋SendMessage で同じサブエージェントを継続（1セッション内）。ユーザーが途中で口を挟みたい用途は agent teams（複数セッション）を検討。専門家は「正しい答え」、人物モデルは「予測される反応」とラベルを分けて混ぜない。記録：decisions（予測・検証日）／人物の検証テーブル／insights |

**専門家の実行形態の指針**：結論を1つに統合して返す用途（/consult・/discuss の通常形）→ **サブエージェント**
（同期・並列・安価・文脈は chief が注入）。長時間の自律作業（@generalist のリサーチ・一括 intake）や
ユーザーが途中参加する議論 → **セッション／agent teams**。memory/ への同時書き込みは1人（chief）に限定する。

## 改訂した段階（2026-11-04）

| 段階 | 内容 | 状態 |
|---|---|---|
| 2a | **Claude Code 専用化**：`.agents/skills` を `.claude/skills` に正典として移す（ポインタ廃止）、ワークフローを `.claude/commands` に統合、専門家の人格を `.claude/agents/*.md` 本体に（`skills:` で担当スキルを事前読込）、CLAUDE.md を正典に。/consult のブリーフを「事実と座標のみ・思考は渡さない」に明文化。他ツール層（AGENTS.md・GEMINI.md・.agents/・.github/・tools/hermes）は削除し、復元経路を PORTABILITY.md に記載 | ✅ 2026-11-04 |
| 2b | **記録の確実性**：`memory/inbox.md`（capture-first）、確認の二段階＋必ず確認する4場面（intake-and-file・CLAUDE.md §5）、`tools/mem.py`（inbox / commitment（ID 付き）/ person / decision / check）、decisions の 提案（未承認）→決定、専門家出力は本文30行＋付録、観測記録表の縦持ち、user-profile と dashboard の二重管理解消、status.md に期限付きタスクを書かない規則、operation-logger が permission_mode を見て「承認済」と「自動実行」を区別 | ✅ 2026-11-04 |
| 2c-test | テストインスタンスで `/discuss`（@accountant・@it-architect・架空部長モデル×2ラウンド）を完走。R1 並列・R2 継続・統合・記録すべて OK、R3 不要。実測：3名合計 約27万トークン・約8分。指摘7点のうち6点を手順書とフックに反映（表示名の定義、R2 は R1 本文をそのまま貼る、人物モデルの出力形式の接合、コスト目安の明記、verify-date は約束台帳から、付録15行、`--help` をログ除外） | ✅ 2026-11-04 |
| 2c | **/discuss**：混成パネル（専門家＋Lv1 以上の人物モデル）、R1 意見表明（Agent 並列）→ R2 反論・修正（SendMessage で同じ agent を継続、他者の結論だけを見せる）→ R3 収束（対立時のみ）→ @chief 統合（合意／対立／裁定／ラウンドで変わったこと）。記録は meetings/ の議事録＋decision（提案）＋person predict。着手前にコスト確認 | ✅ 2026-11-04（実行テストは別途） |
| 2d | **ボード**：`tools/board/server.py`（標準ライブラリの HTTP サーバー・127.0.0.1 専用・JSON API）＋ `index.html`（7タブ：今日/約束/inbox/案件/人物/決定/操作ログ）。書き込みは mem.py の関数を直接呼ぶ（二重ストア無し）。`board` スキルと `.claude/launch.json` で「ボードを開いて」→ preview。mem.py は MemError 例外化してライブラリ利用可に | ✅ 2026-11-04 |
| 2d-2 | **ボードの見た目**（ユーザー要望 2026-11-04）：おしゃれでかっこよく。①エージェント（`.claude/agents/*.md` の frontmatter に `icon:`／絵文字 or SVG）と人物（人物ファイルの基本情報に `アイコン:`）にアイコンを設定でき、ボードと会話の見出しで使う ②操作ボタン・状態・種別にアイコン（状態変更・期限・分類・承認、受けた/出した/自分、Lv バッジ）③案件カードに進捗や期限の図（期限までのバー、約束の件数リング）④タイポグラフィ・余白・カードの階層を整える。外部依存なしのまま（インライン SVG アイコンセット） | ✅ 2026-11-04（agents `icon:`・人物 `アイコン`＋`person set-icon`・`/api/agents`・チームタブ・期限バー・完了リング・Lv メーター・SVG スプライト 50 個・dialog 化・ライト/ダーク切替・モバイル対応。テストインスタンスでブラウザ確認済） |
| 3 | 専門家の周辺スキル（knowledge-work-plugins）、重複スキルのホスト委譲 | ✅ 2026-11-04（expert-plugins スキル・専門家の共通規則と周辺スキル行・/setup Step 6b・roster の導入表・research-report と deliverable-create をホスト委譲に） |
| 4 | /morning-brief の定期実行、`/update` 相当のコネクタ駆動取り込み | 🔲 |

## テストインスタンスの観察（2026-11-04・2a 直後・全項目 OK）

起動フック注入・人格・/setup（ダミー8ファイル・dashboard 48行）・自動記録（memory-write 12行）・
/consult（@accountant＋@it-architect → 1推奨 → decisions.md）はすべて動作。NG なし。指摘された改善点（2b の入力）：

1. **自動記録が Write/Edit 限定**：Bash のヒアドキュメントや sed での memory/ 書き込みは拾わない → `tools/mem.py` 経由に
   統一すれば解決（CLI 呼び出しは Bash パターンで記録）
2. **「承認済」が無条件**：auto モードや allow で通った書き込みも承認済と記録される → PermissionRequest 等で
   「ユーザーが実際に承認した」と「自動で通った」を区別するか、集計時に permission_mode を併記
3. **専門家の出力が長い**（各150行超・約9万トークン）→ consult のブリーフ雛形に「結論30行＋付録」の2層指示
4. **同じ事実を3か所に書く**（situation / commitments / status）→ status.md のマイルストーン欄は台帳参照のみ、等の規則
5. **ユーザー情報の二重管理**（dashboard と user-profile）と「呼び方」の置き場が無い → 雛形の整理
6. **service-capabilities の観測記録表が横に長い**（10列）→ 縦持ち（サービス×手段）に
7. **decisions.md に書くタイミング**：ユーザー承認前に書くか不明 → 「提案（未承認）」と「決定」を区別する規則

## テストインスタンスの観察（2026-11-04・2b 直後・全項目 OK）

移行シナリオ（upstream merge で memory/ 4ファイル衝突 → 解消 → migrate → check）、覚えておいて（inbox）、約束の CLI 記録、
曖昧時の事前確認（新規人物＋案件不明で停止）、/morning-brief（inbox・期限・未承認提案）まで動作。指摘と対処：

| 指摘 | 対処 |
|---|---|
| OPERATIONS §3「ヘッダは本体側」を文字通りやると migrate が効かない | §3 を「表は ours をそのまま残して migrate に任せる」に書き換え |
| migrate が未承認の項目も「決定」に補完 | 見出し近傍に 未承認/提案/検討中 があれば 提案（未承認）に |
| 期限変更の CLI が無い | `commitment set-due` / `set-follow` を追加 |
| 1つの Bash に mem.py 2回で1行しか記録されない | logger が && ; 改行で分割して各コマンドを記録 |
| python スクリプト経由の memory/ 書き込みが無記録 | `memory-write(sh)` カテゴリ（リダイレクト・tee・sed -i・write_text・cp/mv）＋ヒアドキュメント本文も走査 |
| 関連欄の表記混在（バッククォート） | add で除去、migrate で既存行を正規化 |
| 人物ファイル新規作成が手作業・命名に迷う | `person new {識別子} --name --org --email …`（命名規則を検証。役職不明なら部署略称） |
| service-capabilities 横→縦は手作業 | 一度きりなので据え置き |

未対処：人物の思考特性（箇条書き）更新は手編集のまま（本文はそれでよいと判断）。

## 参照（外部）

- Claude Duet（2026-10-21 研究プレビュー）：朝ブリーフ＋接続アプリの常時監視＋送信前承認。記憶は不透明。
  本リポジトリの差別化＝人物・案件・決定・約束を構造化 markdown で持ち git で監査できること
- anthropics/knowledge-work-plugins `productivity`：CLAUDE.md＋memory/ の2層記憶、TASKS.md、`/start`、
  `/update --comprehensive`、dashboard.html。第2段の設計の参考
- Claude Code 公式：hooks（PostToolUse の matcher は正規表現、`mcp__server__tool` 形式）、memory
  （AGENTS.md ネイティブ読込 v2.1.277+、`claudeMdExcludes`）、settings（`disableAllHooks`、`skillOverrides`、
  `settings.local.json` は git 外・リストは層をまたいで合算＝deny は局所で外せない）

## 持ち越しの疑問

- インスタンス利用者が実際にどこで詰まるか（本体では観測できない。テストインスタンスと利用者の報告で集める）
- Antigravity・Copilot 側でのメンテ時の製品抑止手段（ローカル上書き層が無い → 親フォルダから開く運用で足りるか）
