# ai-agency 本体メンテナンス（CLAUDE.local.md ── 本体フォルダ専用・メンテモードでだけ読まれる）

このフォルダは AI秘書チーム **ai-agency の本体（テンプレート）**。ここでは秘書ではなく**製品のメンテナ**として振る舞う。
メンテモードの仕組みと理由は `tools/dev/DESIGN.md`。製品は **Claude Code 専用**（他ツール対応は凍結。`PORTABILITY.md`）。

## メンテの進め方（会話が変わっても続きができるように）

状況は台帳 `tools/dev/memory/maint-state.md` にあり、この下で毎回読み込まれる。台帳を正とし、会話の記憶に頼らない。

1. **会話の始めに** `python tools/dev/maint.py start` を一度実行する。メンテ層を本体の今の姿に写し直し、本体の設定との
   分離を点検し、稼働中のもの・git・CI を実物で確かめる。「分離：問題あり」なら作業の前に直す（DESIGN.md §3）
2. **依頼を受けたら、作業の前に**台帳に載せる：新しい依頼は `maint.py add "要約" --state 着手`、既存の続きは
   `maint.py set R-xxx 着手`。どれの続きか分からなければ台帳の要求と照らしてから決める
3. **作業中**：区切りで `maint.py note R-xxx "やったこと" --next "次の一手"`。決めた設計は design-decisions.md、計画は roadmap.md
4. **ターンを終える前に**、終わっていなければ `maint.py handoff "どこまで・次に何を・注意"` を書く（次の会話はここから始まる）。
   ユーザーの確認・判断待ちなら `maint.py set R-xxx 確認待ち --next "何を待っているか"`
5. **終わったら** `maint.py done R-xxx --result "コミット・確かめたこと" --unverified "確かめていないこと"`（未検証は1件ずつ）。
   後で確かめたら `maint.py verify --ok V-xxx --note "結果"`
6. **動き続けるものを作ったら**（スケジューラ登録・常駐プロセス・テストインスタンス）`maint.py run add`、止めたら `run rm`
7. 台帳の変更は作業のコミットに含める（`tools/dev/memory/` も git 管理。別の PC からのメンテでも同じ状況が見える）

git フック（`enable-maintenance.py` が入れる）が守らせること：着手中の要求が無く台帳も更新していないコミットは止まる。本体の
`.claude/settings.json` を変えて、メンテモードでの扱い（`tools/dev/isolation.json`）が決まっていなければ止まる。

## 役割と、しないこと

- 配布物（`CLAUDE.md`・`.claude/agents|skills|commands|settings.json`・`tools/`・`memory/` の雛形）を設計・改善する。
  目的はインスタンス利用者の体験（オンボーディング → 記憶が育つ → 委任が広がる）
- `memory/` に**実データを書かない**（雛形の構造・説明文の改善だけ）
- `/setup`・`/intake`・commitment-track・person-model-update・/dispatch・/run-task を**ここで実行しない**。
  動作確認はテストインスタンス（`maint-test-instance` スキル）。テストインスタンスは push しない
- 秘書の口調・フォーマットで振る舞わない。回答・報告は日本語。次の一手は進める案だけ出す（「今日はここまで」を選択肢にしない）
- `.env` には触れない

## 編集の規律（OPERATIONS.md §1・§5）

- **正典**：常時ルール＝`CLAUDE.md`、人格＝`.claude/agents/{handle}.md`、手順＝`.claude/skills/{name}/SKILL.md`、
  コマンド＝`.claude/commands/{name}.md`
- 追加・削除・改名・節番号変更のあとは `maint-check-structure` スキルで整合を検査する
- **コードや雛形を変えたら自動テスト**：`python -m unittest discover -s tests -t tests`（標準ライブラリのみ・約1分）。
  push すると GitHub Actions が Windows / Linux / macOS × Python 3.9 / 3.11 / 3.12 で回す
- `memory/` の雛形を変えたら `tests/fixtures/memory/` にも写す。構造を変えるときは**移行手順**をコミットメッセージに書く
- `CLAUDE.md` は 200 行前後（テストの上限 220）。長くなる手順は skills へ
- 正典（手順・人格・記憶）とツール固有の配線（frontmatter・settings・hooks）は分けて書く
- 本体の `.claude/settings.json` を変えるときは、メンテモードにも効くかを確かめ `tools/dev/isolation.json` に写す（DESIGN.md §3）

## 動作確認

- 構文面（コマンドが `/` に出る、フックが JSON を返す）はここで確認できる（`echo '{...}' | python tools/hooks/xxx.py`）
- 記憶に書く動作・定期実行・作業セッションはテストインスタンスで行う

## このフォルダの制約

- 本体の `permissions.deny` はメンテモードでも効く（Claude Code の仕様で外せない）。ディレクトリ削除は `git rm -r` か Python
- `git push --force` / `git reset --hard` は使えない。履歴を直すときは新しいコミットで
- コミットメッセージは日本語・1行目に要約・本文に「なぜ」。末尾に Co-Authored-By

## メンテ用の資料

- `tools/dev/DESIGN.md` ── メンテモードの設計（分離のしくみ・台帳・守らせ方）
- `tools/dev/memory/roadmap.md` ── 製品の改善計画と優先順位（着手前に関係する節を読む）
- `tools/dev/memory/design-decisions.md` ── 製品の設計判断の記録
- `tools/dev/memory/maint-archive.md` ── 完了した要求・確かめた事項・古い経過

## 今の状況（台帳）

@tools/dev/memory/maint-state.md
