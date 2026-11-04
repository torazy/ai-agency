---
name: board
description: 「ボードを開いて」「画面で見たい」「一覧で確認したい」「今のタスク/案件/人物をまとめて見せて」と言われたとき。memory/ を見る・触るローカルボード（tools/board）を起動して案内する。ボードからの操作は mem.py と同じ経路で markdown に書き戻される。
---

# 秘書ボード（tools/board）

memory/ の markdown を**正典のまま**、見る・触るための画面。標準ライブラリだけのローカルサーバー
（`tools/board/server.py`）が 127.0.0.1 に立ち、画面の操作は `tools/mem.py` と同じ関数で markdown に書き戻す。
二重ストアは無い。

## 起動

- Claude Code デスクトップ：`preview_start` で `.claude/launch.json` の **board** を起動し、ブラウザペインで
  `http://127.0.0.1:8765/` を開く
- それ以外：ユーザーに次を案内する
  ```bash
  python tools/board/server.py
  ```
  ブラウザで http://127.0.0.1:8765/ を開く（Ctrl+C で停止）

## 画面でできること

| タブ | 見る | 触る |
|---|---|---|
| 今日 | 期限3日以内・フォロー到来の約束、inbox 未分類、未承認の提案、situation.md | 状態変更・期限変更・inbox の分類・提案の承認 |
| 約束 | 約束台帳（ID・期限・関連・状態） | 追加／状態変更／期限変更 |
| inbox | 未分類（＝秘書の確認待ち）・分類済 | 追加／記録先を付けて分類済に（通常は秘書が仕分ける。画面は手で直したいとき用） |
| 案件 | overview の目的・状態・期限・座標、未完了の約束数、決定・課題の件数、status.md | ─（編集は会話か /intake で） |
| 人物 | アイコン・成熟度（Lv メーター）・判断事例数・最終観察・効果的なアプローチ | 判断事例の追加／アイコンの設定 |
| 決定 | 全案件の決定（状態・予測・検証日）を 提案／決定／検証済 で分けて表示 | 提案→決定（承認）、決定→提案に戻す |
| チーム | @chief と専門家（`.claude/agents/`）のアイコン・役割・担当スキル | ─（アイコンは frontmatter の `icon:` を編集） |
| 操作ログ | task-log の直近の `**操作**` 行（カテゴリ・対象・承認/自動の別） | ─ |

案件カードには期限までの進捗バーと、その案件に紐づく約束の完了リング（完了数／総数）が出る。

## アイコン（エージェント・人物）

- **専門家**：`.claude/agents/{handle}.md` の frontmatter `icon:` にボードのアイコン名（calculator / users / bolt / flag / shield /
  server / scale / briefcase / chart / globe / lightbulb / palette / target / pen / message / layers / star / heart / building / crown / user）
  または絵文字1つ。@chief は crown 固定
- **人物**：人物ファイルの基本情報 `- **アイコン**: 🌸` 。ボードの人物カード「アイコン」ボタン、または
  `python tools/mem.py person set-icon {識別子} {絵文字|アイコン名}`（`person new --icon` でも指定可）。未設定なら氏名の頭文字
- 会話の見出し（/consult・/discuss の参加者表）でも、専門家名の前にこのアイコンを添えてよい

## 秘書側の規律

- ボードでユーザーが変えた内容は markdown に即時反映される。**会話で動く前に該当ファイルを読み直す**
  （特に commitments.md・inbox.md・decisions.md）
- ボードからの書き込みは**ユーザー自身の操作**なので、フックの操作ログには残らない（承認実績の対象外）。
  「ボードで直した」と言われたら、そのまま信じて読み直せばよい
- 本体（テンプレート）フォルダでボードを開くと上部に警告が出る。そこでは記録しない
