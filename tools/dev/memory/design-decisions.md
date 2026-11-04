# 設計判断の記録（本体メンテ用メモリ）

> 「なぜこうなっているか」を残す。変えるときはここを読んでから。

## 2026-11-04: Claude Code 専用化（ポインタ層の廃止）

- **判断**：ユーザー決定「Claude Code 専用にしてよい。他は必要になったら対応」を受け、正典を `CLAUDE.md`・
  `.claude/agents`・`.claude/skills`・`.claude/commands` に一本化。AGENTS.md・GEMINI.md・`.agents/`・`.github/`・
  `tools/hermes`・`tools/mcp_config.sample.json`・`task-log-writer.py` は削除（復元経路は PORTABILITY.md と git 履歴）
- **理由**：ポインタ方式は正典→4層の同期コストが常にかかり、frontmatter 変更のたびに再生成が必要だった。
  Claude Code 専用なら、人格はエージェント本体に書けて `skills:` で担当スキルを事前読込でき、二重読みも消える
- **分割の指針**：人格（誰か）＝エージェント、手順（どうやるか）＝スキル、常時ルール＝CLAUDE.md。
  エージェントは `memory:`（持続メモリ）や `tools` 制限の口を持つので残す。`memory: project` は今は付けない
  （業務記憶の正典は memory/ という原則とぶつかるため。使うなら専門家の作業上の癖に限定）
- **/consult のブリーフ**：情報は渡す・思考は渡さない。事実と座標（memory/ のパス）だけを全員に同じ内容で渡し、
  専門家は memory/ を自分で読む。fork（会話全体の引き継ぎ）はアンカリングを招くので使わない
- **残る制約**：CLAUDE.md が約230行（目安200行超）。手順的な節は後で skills や `.claude/rules/` へ移す候補

## 2026-11-04: 本体とインスタンスを「層」で分ける（フォルダではなく）

- **判断**：本体フォルダでのメンテ時に製品を止める手段として、別フォルダから編集する方式ではなく、
  gitignore されるローカル上書き層（`CLAUDE.local.md`・`.claude/settings.local.json`・`.claude/skills/maint-*`）を採る。
  雛形は `tools/dev/` にコミットし、有効化スクリプトでローカルファイルを生成する
- **理由**：Claude Code は配下フォルダの CLAUDE.md/AGENTS.md とスキルを「ファイルを触った時点で」遅延ロードするため、
  親フォルダから開いても人格と手順書は入り込み、外れるのはフックと deny だけ（最悪の組み合わせ）。
  ローカル層なら `claudeMdExcludes` / `disableAllHooks` / `skillOverrides` / `disabledMcpjsonServers` で機械的に止まる
- **限界**：`permissions.deny` は層をまたいで合算されるため本体でも残る（`git rm -r` で代替）。
  Antigravity・Copilot にはローカル上書き層が無い
- **検証**：有効化後の新規セッションで「あなたは誰？」→ 第一秘書と名乗らないこと

## 2026-11-04: 無人実行は「inbox まで」、定期実行はローカル

- **判断**：`/morning-brief --unattended` と `/update --capture` は、拾ったものを **inbox にだけ**入れ、台帳・人物・決定・
  situation は書き換えない。外部への送信は delegation で許可済みのものでもしない（下書きをブリーフに書く）。
  出力は `memory/briefs/{日付}.md`（gitignore）。起動フックとボードが次の対話でそれを示す
- **理由**：CLAUDE.md §5 の「迷ったら確認」は人がいないと守れない。inbox は「分類前の受け皿」として既にあり、
  無人で拾った候補の置き場に合う。capture-first のまま人が分類するので、誤記録が正典に入らない
- **定期実行の仕組み**：クラウドの `/schedule` は memory/ を書き戻せない（clone 上で動く）ので使わない。
  デスクトップの定期タスク（アプリ起動中）か、OS スケジューラ＋`claude -p`（`tools/schedule/`）。後者は
  `--allowedTools` を読み取り・mem.py・`memory/briefs/` 書き込みに絞り、それ以外は settings の allow にあるものだけ通る
- **ログの区別**：無人実行は環境変数 `AI_AGENCY_UNATTENDED` を立て、operation-logger が「自動実行[unattended]」と書く。
  `/propose-delegation` が人の承認実績と混ぜないため
- **/update の範囲**：`memory/sync-state.json`（gitignore）にサービスごとの取り込み済み時刻。初回は直近7日、1サービス30件まで

## 2026-11-04: 外部プラグインは取り込まず「入っていれば使う」

- **判断**：knowledge-work-plugins（legal / finance / human-resources / product-management）は本体に同梱・複製しない。
  インスタンスごとに承認制で `--scope local` 導入し、専門家は共通規則「スキル一覧にあれば Skill ツールで使う」で拾う。
  エージェント frontmatter の `skills:` には書かない（プラグイン名前空間のスキルを preload できるかは文書化されておらず、
  未導入の環境で壊れるため）
- **理由**：中身は米国実務前提（US GAAP・SOX・米国の雇用慣行・米国の標準契約ポジション）で、取り込むと日本向けの修正と
  上流追従の保守を背負う。人格と判断の型（日本前提）は本体、深掘りの手順は上流、という分担なら両方の更新を活かせる
- **local スコープ**：インスタンスの `.claude/settings.json` を本体からの upstream merge と衝突させないため
- **ホスト委譲**：research-report は検索手段を固定せず（組み込み Web 検索 → brave → 手動）、本格調査はホストの調査スキルへ。
  deliverable-create はファイル生成を docx/pptx/xlsx/pdf 等のホストスキルへ。どちらも「範囲・宛先の定義」と
  「memory への保存」だけを本体の価値として残す

## 2026-11-04: ボードのアイコンは「正典に書く」、見た目は外部依存ゼロ

- **判断**：専門家のアイコンは `.claude/agents/{handle}.md` の frontmatter `icon:`、人物のアイコンは人物ファイルの
  基本情報 `アイコン:` に置く。ボードは読むだけ（人物は mem.py 経由で書ける）。アイコンは index.html 内の
  SVG スプライト（lucide 風・約50個）＋絵文字。Web フォント・CDN は使わない
- **理由**：ボード専用の設定ファイルを増やすと二重管理になる。正典（agents / people）に置けば、会話の見出しでも
  同じアイコンを使えるし、インスタンスの upstream merge でも衝突しにくい。外部依存ゼロはオフライン・社内 PC での
  動作を守るため
- **@chief** だけは `.claude/agents/` に定義が無い（CLAUDE.md §1）ので、ボード側で crown 固定

## 2026-11-04: 操作ログは秘書が書かず、フックが書く

- **判断**：`/propose-delegation` の素材となる `**操作**:` 行は PostToolUse フックが自動記録する。Stop フックは廃止
- **理由**：Stop は毎ターン発火し空エントリを量産していた。秘書に手で書かせる設計は実績ゼロだった
- **分類**：MCP は書き込み動詞で判定、シェルは git push / gh pr 等のパターン、Write/Edit は memory/ 配下のみ。
  claude.ai コネクタのアカウント固有 ID は `operation-log.json` の `server_aliases` で別名化
- **フックの無いホスト**（Antigravity IDE・Copilot）は従来通り秘書が追記する（CLAUDE.md §7）

## 2026-11-04: 能力地図は「記録より現物が正」

- **判断**：`service-capabilities.md` の静的な対応表を廃し、解決順に「0. いま使えるツールを確認」を置く。
  表はホスト別の観測記録（`/setup` が更新）
- **理由**：同じリポジトリを複数ホストで開くため、静的表は必ずどこかのホストで嘘になる

## 2026-07-05 以前の判断（要約）

- 正典は `.agents/`・`AGENTS.md`・`memory/`。各ツールの層はポインタ（ドリフト対策）
- 本体はテンプレート、利用者は履歴保持 clone でインスタンスを作り upstream merge で追従（OPERATIONS.md）
- モデル階層の分担軸は「難易度」ではなく「記憶への書き込み有無」（軽量モデルの記録ミスは全ホストに波及）
