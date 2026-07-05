# Hermes Agent で使う（セットアップ手順）

> Hermes Agent（Nous Research, OSS）v0.17 以降を想定。常駐・内蔵cron・メッセージング
> ゲートウェイ（Slack/Telegram 等22チャネル）を持つため、**常時稼働とモバイルからの利用**という
> 他ツールにない価値を足せる。正典（AGENTS.md・.agents/・memory/）は一切変更不要。

## 1. 前提

- **作業ディレクトリ＝自分のインスタンスリポジトリのルート**にする（`hermes` の terminal cwd 設定）。
  Hermes はプロジェクトの `AGENTS.md` をネイティブに読むため、これだけでコア規範・記憶モデル・
  安全規則が有効になる
- 動作確認：「あなたは誰？」→ 第一秘書と名乗れば OK（全ツール共通の確認方法）

## 2. スキルのインストール

```bash
bash tools/hermes/install-skills.sh
```

- `.agents/skills/`（正典）から Hermes 標準（agentskills.io / SKILL.md）のポインタを
  `~/.hermes/skills/` に生成する（`HERMES_SKILLS_DIR` で変更可）
- **本体アップデート後は再実行**する（冪等・上書き安全）
- ポインタは ai-manager ワークスペース外では自己無効化する（誤発動防止の注記入り）

## 3. ワークフロー（/consult 等）

Hermes のスラッシュコマンドは独自体系のため、`/consult` は自動認識されない。
チャットで普通に「**consult: この件を専門家で評価して**」「**simulate: 田中部長にこの報告**」の
ように書けば、AGENTS.md の誘導により該当の `.agents/workflows/*.md` を読んで実行する。
サブエージェント並列召集は Hermes の `delegate_task`（background subagents）で行われる。

## 4. MCP（Backlog / GitHub / Web検索）

リポジトリ直下の `.mcp.json` と同じ内容（`tools/mcp_config.sample.json` が正典）を
Hermes の MCP 設定に転記する。APIキーは `.env` と同じ環境変数を使う。

## 5. メモリの二重管理を防ぐ（最重要の注意点）

Hermes は独自の常設メモリ（`~/.hermes/memories/MEMORY.md`・`USER.md`）を毎セッション注入する。
**業務記憶の正典はワークスペースの `memory/`**（AGENTS.md §6）なので、二重管理（split-brain）を
防ぐため、Hermes の memory ツールに以下を1度だけ登録しておく：

```
業務・プロジェクト・人物に関する記憶の正典は、ai-manager ワークスペースの memory/ 配下の
markdown である。この MEMORY.md には業務記憶を蓄積せず、必要なら memory/ にファイリングする。
```

（SOUL.md を使っている場合は同趣旨を SOUL.md に書いてもよい。Hermes 側メモリに残してよいのは
「ユーザーの呼び方・応答言語」程度の対話設定のみ）

## 6. 定期実行（Hermes 内蔵 cron）

```
毎朝8:30に morning-brief ワークフロー（.agents/workflows/morning-brief.md）を実行して、
結果を {Slack/Telegram 等の接続チャネル} に送って
```
週次の review-decisions / propose-delegation、月次の memory-maintenance も同様に登録できる。
**外部チャネルへの送信を伴うため、初回は出力内容を確認してから定常化すること。**

## 7. 安全上の注意

- **`--yolo`（承認バイパス）は使わない**。Hermes の危険コマンド承認プロンプトが
  委任レベル Lv1（全確認）に相当する
- `tools/hooks/guardrail.py` 相当の外部スクリプトフックは Hermes に無い（2026-06 時点）。
  `memory/guardrails.json` の deny は AGENTS.md 遵守＋承認プロンプトで担保される
- メッセージングゲートウェイを開く場合、ペアリングコードで接続元を必ず制限する
  （秘書は memory/ の業務情報にアクセスできるため、チャネル＝入口の管理は重要）
