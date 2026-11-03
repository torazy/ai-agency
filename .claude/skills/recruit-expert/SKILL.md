---
name: recruit-expert
description: 必要な専門家が秘書チームに不在のとき、新しい専門家エージェントを追加する。capability-expansionの人材版。
---

# 専門家補充スキル

チームに不在の専門家が必要になったとき、新しい専門家エージェントを追加するプロセス。

## 実行手順

### Step 1: 不足を確認する

`memory/team/roster.md` を参照して：
- 必要な専門分野がチームにあるか
- 在籍しているが能力が不足していないか

### Step 2: 必要な専門家の要件を定義する

```markdown
## 補充要件

**専門分野**: {何の専門家が必要か}
**必要な背景知識**: {業界知識・資格・経験}
**担当するタスクの例**:
- {タスク例1}
- {タスク例2}
**呼び出し頻度の予測**: 高/中/低
```

### Step 3: ユーザーに提案する

```
## 専門家の補充提案

現在の秘書チームに **{専門分野}の専門家** がいません。

**必要な背景**:
{なぜ必要か}

**新しい専門家の概要**:
- 名前: {agent-name}（例: legal-agent, finance-agent）
- 専門知識: {知識・フレームワーク・資格}
- 担当タスク: {主に担当するタスク}

▶ この専門家をチームに追加してよいですか？
```

### Step 4: 承認後に専門家を追加する

> 対応する knowledge-work-plugins（marketing・data・operations・engineering 等）があれば、提案に「周辺プラグイン」として
> 添える（`expert-plugins` スキル）。人格はこのリポジトリに書き、深掘りの手順はプラグインに任せる分担にする。

承認が得られたら：

1. `.claude/agents/{handle}.md` を新規作成する（下のテンプレート参照。frontmatter の `skills:` に担当スキルを書く）
2. 専門分野のレビュースキルが必要なら `.claude/skills/{domain}-review/SKILL.md` を作成する
   （`.claude/skills/` に置けば自動認識される。追加登録は不要）

> 専門家は Claude Code のサブエージェント定義。`/consult` が Agent ツール（`subagent_type: {handle}`）で
> 独立コンテキストに召集する。既存の `.claude/agents/legal.md` を雛形にすると「共通規則」の節を揃えやすい。

### Step 5: ロスターを更新する

`memory/team/roster.md` に新メンバー（@handle・専門領域・トリガー条件）を追加する。

## 専門家エージェントのテンプレート（`.claude/agents/{handle}.md`）

```markdown
---
name: {handle}
description: {役割}。{どんな判断が必要なとき}、/consult のチーム編成で{観点}が要るときに使用。
icon: {ボードのアイコン名（calculator / users / bolt / flag / shield / server / scale / briefcase / chart / globe / pen / heart …）または絵文字1つ}
skills:
  - {domain}-review
---

あなたはAI秘書チームの{役割}（@{handle}）。{資格・経験の設定}を持ち、{視点の特徴}から物事を見る。

**専門**: {知識領域1}、{知識領域2}、…
**思考**: ①{観点1} ②{観点2} ③{観点3}
**Traits**: {アウトプットの特徴・切り口}
**口癖**: 「{この専門家らしい問い}」
**隣接**: {答えられないこと→誰に委ねるか}。

## 共通規則（全専門家）
{既存エージェント（例: legal.md）の「共通規則」節をそのままコピーする}
```
