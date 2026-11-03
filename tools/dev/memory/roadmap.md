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
| 3 | 専門家の周辺スキル：anthropics/knowledge-work-plugins の legal / finance / product-management / human-resources を @legal / @accountant / @pm / @hr に紐付け。リポジトリ内の research-report・deliverable-create はホストに同等スキル（deep-research・docs・pptx 等）があれば委譲する形に | 🔲 未着手 |
| 4 | `/morning-brief` を定期実行に乗せ、Claude Duet 的な「本人がいない間も動く」体験に。承認ゲートは delegation.md のまま | 🔲 未着手 |

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
