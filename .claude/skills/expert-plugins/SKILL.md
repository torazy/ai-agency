---
name: expert-plugins
description: 専門家（@legal・@accountant・@hr・@pm など）に周辺スキルを足すため、Anthropic 公式の knowledge-work-plugins（legal / finance / human-resources / product-management 等）を提案・導入・確認するとき。「専門家を強くしたい」「プラグインを入れたい」「契約審査をもっと深く」と言われたとき、/setup の任意ステップ、recruit-expert で新しい専門家を足したときに使う。
---

# 専門家の周辺プラグイン（expert-plugins）

専門家の**人格と判断の型は本リポジトリが正典**（`.claude/agents/{handle}.md`＋`{domain}-review` スキル）。
プラグインは「深掘りの手順書」を足す供給元で、**入っていれば使い、無くても動く**。リポジトリに中身は取り込まない
（Apache-2.0 だが米国実務前提で、取り込むと追従と日本向け修正の保守を背負うため）。

## 対応表

| 専門家 | プラグイン | 主に使うスキル（`{plugin}:{skill}`） | 日本で使うときの読み替え |
|---|---|---|---|
| @legal | `legal` | review-contract（契約審査・修正案）、triage-nda（NDA の一次仕分け）、compliance-check、legal-risk-assessment、vendor-check | プレイブック `.claude/legal.local.md` が無いと**米国の標準ポジション**になる → 下の雛形で日本法を初期値に |
| @accountant | `finance` | variance-analysis（予実差異の分解）、financial-statements（P/L・B/S・CF とフラックス分析）、close-management（月次決算の段取り）、audit-support（内部統制テスト）、reconciliation | US GAAP・SOX 前提 → J-GAAP・J-SOX に読み替え。税務は税理士 |
| @hr | `human-resources` | org-planning（人員・組織設計）、interview-prep（面接設計・評価表）、performance-review、comp-analysis、onboarding、policy-lookup | 米国の雇用慣行前提 → 労基法・均等法・育介法で読み替え |
| @pm | `product-management` | write-spec（PRD・受入基準）、roadmap-update、stakeholder-update、sprint-planning、metrics-review、competitive-brief、synthesize-research | ほぼそのまま使える |

他の専門家の候補（必要になったら）：@it-architect・@qa → `engineering`、データ分析 → `data`、業務運用 → `operations`、
マーケ → `marketing`。新しい専門家を足すとき（`recruit-expert`）は、まずこの一覧と
`claude plugin list --available --json` で対応プラグインがあるか確認する。

## 導入（ユーザーの承認を取ってから）

1. 入れる前に提示する：どの専門家のために・どのプラグインか・**常駐コスト**（実測：legal は9スキルで常駐 約1千トークン／
   1回の呼び出しで 約1〜6千トークン。`claude plugin details {plugin}@knowledge-work-plugins` で確認できる）。
   **必要な専門家の分だけ**入れる
2. 承認されたら実行する（マーケットプレイス登録はユーザー設定、プラグインは端末ローカル）：
   ```bash
   claude plugin marketplace add anthropics/knowledge-work-plugins
   claude plugin install legal@knowledge-work-plugins --scope local
   ```
   - `--scope local` は `.claude/settings.local.json`（git 管理外）に書く。本体から upstream merge するときに
     `.claude/settings.json` が衝突しないようにするため。チーム全員で同じ構成にしたいインスタンスだけ `--scope project`
   - プラグインは MCP 接続を同梱している（legal は slack・box・egnyte・atlassian・docusign・google calendar・gmail の7つ）。
     使う時に個別承認が出るので、普段使っていないサービスは拒否してよい。接続しなくてもスキルは動く
3. 有効になるのは**次のセッションから**。「新しいセッションで @legal に契約を見せてください」と案内する
4. `memory/team/roster.md` の「周辺プラグイン」表に1行追記する（導入日・スコープ・プレイブックの有無）。
   古いインスタンスで表が無ければ「専門領域クロスマトリクス」の前に作る（列：専門家｜プラグイン｜スコープ｜導入日｜備考）
5. 外し方も伝える：`claude plugin uninstall legal@knowledge-work-plugins --scope local`

## 導入状態の確認

```bash
claude plugin list --json
```
`@knowledge-work-plugins` の行の有効／無効を見る。roster の表と食い違っていたら roster を直す。

## 法務プレイブックの雛形（`.claude/legal.local.md`）

legal プラグインはこのファイルを「自社の標準ポジション」として読む。以下は**日本法前提の初期値の例**。
ユーザーと一緒に自社方針へ置き換え、インスタンスにコミットしてよい（業務ルールとして蓄積する）。
本体（テンプレート）には置かない。

```markdown
# 法務プレイブック（日本法前提・自社方針に置き換えること）

## 準拠法・管轄
- 準拠法：日本法
- 管轄：{本店所在地}を管轄する地方裁判所を第一審の専属的合意管轄裁判所とする

## 標準ポジション
- 損害賠償：直接かつ通常の損害に限る。上限は直近12か月の支払総額（故意・重過失は除く）
- 秘密保持：期間は契約終了後3年。営業秘密は期間の定めなし
- 再委託：事前の書面承諾を要する
- 知的財産：成果物の著作権は対価の完済時に移転（著作権法27条・28条の権利を含むと明記）。著作者人格権は行使しない
- 個人情報：個人情報保護法に基づく安全管理措置と、委託先の監督を定める
- 取引法令：下請法・フリーランス法（特定受託事業者取引適正化法）の適用有無を必ず確認する

## エスカレーション（弁護士へ）
- 賠償上限なし、または一方的な解除権
- 競業避止・引き抜き禁止の条項
- 準拠法が外国法、または管轄が海外
```

## 秘書側の規律

- プラグインのスキルは説明文で自動起動しうる。**複数観点が絡む判断は従来どおり /consult・/discuss**で専門家に回す。
  @chief がプラグインのスキルを直接使うのは、単一領域の定型作業（NDA の一次仕分け、差異分析の表づくり等）に限る
- 専門家の出力規則（本文30行・付録・免責）はプラグインの出力形式より優先する
- プラグインの結果も「判断の根拠」にすぎない。記録（decisions・insights）は @chief が従来どおり行う
