# メンテモードの設計（tools/dev/DESIGN.md）

ai-agency の本体（テンプレート）を、本体フォルダの中で製品とは切り離して保守するための仕組み。
使い方は `tools/dev/README.md`、毎回の手順は `tools/dev/claude-local.md`（有効化すると `CLAUDE.local.md` になる）。

## 1. 目的と前提

- **本体の中でモードを切り替える**。別リポジトリから保守しない（保守する対象と同じ場所で、同じ git 履歴で作業したい）
- メンテモードでは**製品（秘書）が動かない**：秘書の常時ルール・フック・スキル・コマンド・MCP は見えず、効かない
- **本体の設定を変えても、メンテモードの動作が変わらない**。変わってしまうもの（後述の合算されるルール）は、
  黙って効かせず、変更のたびに人が影響を確かめて承認する
- **会話が変わっても続きができる**：依頼・進み具合・動いているもの・未検証の事項を、会話ではなくファイル（台帳）に置く
- 動作の約束は文章（常時ルール）だけに頼らず、守れなかったときに止まる仕組み（git フック・CI）を併せて置く
- インスタンスには何も起きない：`tools/dev/` はインスタンスにも届くが、有効化しなければ何もしない
  （`maint.py` は本体マーカーが無いと何もしない。git フックは各 clone の `.git/` の中にしか入らない）

## 2. 構成

| 部品 | 置き場所（git 管理・正本） | 有効化後の実体（本体フォルダだけ・配布されない） | 役目 |
|---|---|---|---|
| 常時ルール | `tools/dev/claude-local.md` | `CLAUDE.local.md`（gitignore） | メンテナとしての振る舞いと、毎回の手順。台帳を `@` で読み込む |
| 製品を止める設定 | `enable-maintenance.py` が生成 | `.claude/settings.local.json`（gitignore） | 本体の CLAUDE.md・フック・スキル・MCP を止める（§3） |
| 状況台帳 | `tools/dev/memory/maint-state.md` | ─（そのまま読む） | 依頼・状態・次の一手・未検証・稼働中のもの・経過（§4） |
| 記録庫 | `tools/dev/memory/maint-archive.md` | ─ | 台帳から移した完了・確認済み・古い経過 |
| 台帳と点検の道具 | `tools/dev/maint.py` | ─ | 台帳の読み書き、`start`（写し直し・点検・実物の確認）、git フックの中身 |
| 本体設定の扱いの台帳 | `tools/dev/isolation.json` | ─ | 本体 `settings.json` の各項目がメンテモードでどう効くか（§3） |
| メンテ用スキル | `tools/dev/skills/maint-*` | `.claude/skills/maint-*`（gitignore） | 構造検査・テストインスタンス |
| git フック | `enable-maintenance.py` が生成 | `.git/hooks/{pre-commit,post-commit,post-merge,post-checkout}` | 写し直しの自動化と、手順を守らせる止め（§5） |
| 本体マーカー | ─ | `.ai-agency-template`（gitignore） | 製品のツールが本体で動かない／`maint.py` がメンテモードと判断する |
| 計画と判断 | `tools/dev/memory/roadmap.md`・`design-decisions.md` | ─ | 製品の改善計画と、製品の設計判断（台帳とは別。台帳は作業の状況） |

常時ルールの正本を `tools/dev/CLAUDE.local.md` ではなく `claude-local.md` という名前にしているのは、Claude Code が
`tools/dev/` のファイルを読んだときに、入れ子の `CLAUDE.local.md` として二重に読み込み、`@` の相対パスが壊れるのを避けるため。

## 3. 本体の設定との分離

Claude Code の設定は、管理者・ユーザー・プロジェクト（`.claude/settings.json`＝製品）・ローカル（`.claude/settings.local.json`
＝メンテ層）の層を重ねて決まる。**単一の値はローカルが勝つが、リスト（permissions の allow / deny / ask）は層をまたいで合算される**。
フックは `disableAllHooks` で層に関係なく全部止まる。この性質の上で、製品の要素ごとに次のように扱う：

| 製品の要素 | メンテモードでの扱い | 本体に足したとき | 守り |
|---|---|---|---|
| `CLAUDE.md` → `memory/dashboard.md` | `claudeMdExcludes` で読まない | ─ | ─ |
| フック（settings.json の hooks） | `disableAllHooks: true` で全部止まる | **自動で止まる** | ─ |
| スキル・コマンド | `skillOverrides` で名前ごとに off | 写し直すまで**見えてしまう** | `maint.py start`・git フック（commit / merge / checkout）が自動で写し直す。`check` が漏れを検出 |
| `.mcp.json` の MCP サーバー | `disabledMcpjsonServers` | 写し直すまで起動する | 同上 |
| `permissions.deny` / `ask` / `allow` | **合算されてメンテモードにも効く**（外せない） | 効いてしまう | `isolation.json` の承認済み一覧と違えば、pre-commit・CI・`check` が止める。人が影響を確かめて写す |
| 上記以外の settings.json の項目（env・model・statusLine など） | ローカルで同じ項目を書けば打ち消せる | 効いてしまう | 扱いが `isolation.json` に無ければ、pre-commit・CI・`check` が止める。扱いを決めて（必要ならローカルで打ち消して）登録する |
| `.claude/agents/`（専門家） | 止める手段が無い。サブエージェントの候補に出るだけで、勝手には動かない | 出る | 残る差（害なし） |

要点：**「本体の設定を変えるとメンテモードにも効く」状態を、黙って起こさない**。完全に切り離せないもの（合算されるルール）は、
変更のたびに承認を求める形で管理する。`isolation.json` の `acknowledged` は「メンテモードでも効くと分かったうえで受け入れた一覧」。

たとえば本体に `Bash(git push*)` の deny を足すと、メンテモードでも push できなくなる。そうしたい変更なら承認として写し、
困るなら本体側の書き方を変える（例：無人実行だけを止める仕組みにする）。

CLI から起動するときに限れば `claude --setting-sources user,local` でプロジェクトの設定そのものを読ませない方法もあるが、
デスクトップアプリでは指定できないので、この仕組みは「どこから開いても同じに効く」ほうを採っている。

## 4. 状況台帳（会話をまたぐ状態）

`tools/dev/memory/maint-state.md` は、メンテモードの常時ルールから `@` で**毎回の会話に読み込まれる**。だから短く保つ
（完了・取り下げは新しい6件だけ、経過は直近30行だけを残し、古いものは `maint-archive.md` へ自動で移す）。

| 節 | 何を置くか | 書くとき |
|---|---|---|
| 引き継ぎ | 途中で会話が終わるときの「どこまで・次に何を・注意」 | ターンの終わりに作業が残るとき（`handoff`） |
| 要求 | 依頼1件＝1行。受付・要求の要約・状態・現在地と次の一手・結果（コミット）・更新日 | 依頼を受けたら作業の前に（`add` / `set`）、区切りで（`note`）、完了で（`done`） |
| 未検証 | 完了したが実物で確かめていないこと | 完了時（`done --unverified`）。確かめたら `verify --ok` で記録庫へ |
| 稼働中のもの | スケジューラ登録・常駐・テストインスタンスなど、会話が終わっても動き続けるもの。止め方つき | 作ったとき・止めたとき（`run add` / `run rm`）。`start` が実物を確かめる（別ホストのものは確かめない） |
| 経過 | 日付つきの1行ログ | 各操作が自動で書く |

要求の状態：受付 → 着手 → 確認待ち（ユーザーの確認・判断を待つ）→ 完了。ほかに保留・取り下げ。

台帳を git 管理にしているのは、別の PC（Mac など）でメンテするときも同じ状況が見えるようにするため。
秘密や個人の情報は書かない（公開リポジトリ）。

製品の計画（roadmap.md）・製品の設計判断（design-decisions.md）とは役目を分ける：台帳は「いま何をしていて、どこまでか」。

## 5. 振る舞いの制御

| 何を | どう守らせるか |
|---|---|
| 会話の始めに状況を把握する | 台帳が常時ルールから自動で読み込まれる。加えて手順で `maint.py start`（実物の確認と写し直し） |
| 依頼を台帳に載せてから作業する | 手順＋**pre-commit**：作業ファイルを含むコミットで、着手中の要求が1件も無く、台帳も同じコミットで更新していなければ止まる（`MAINT_ALLOW_UNTRACKED=1` で例外） |
| 本体設定の変更をメンテモードに黙って効かせない | **pre-commit**（settings.json か isolation.json を含むコミット）・**CI**（tests/test_maint.py）・`check` |
| 製品の新しいスキル・MCP を見せない | **post-commit / post-merge / post-checkout** と `start` が写し直す |
| 製品の実行系（/setup・/dispatch など）を本体で動かさない | スキルを止めている＋本体マーカーで製品のツールが本体では何もしない |

Claude Code のフックをメンテ用に使わない理由：メンテ用のフックを動かすには `disableAllHooks` を外す必要があり、
外すと製品のフックも動き、本体に足したフックがメンテモードに効く（§3 の一番強い分離が崩れる）。だから
「会話の始め」は常時ルールの読み込み（`@` の取り込み）で、「守れなかったら止める」は git フックで行う。

## 6. 運用

- 有効化（初回・別の PC で clone したとき）：`python tools/dev/enable-maintenance.py` → 新しいセッションで `maint.py start`
- フォルダを改名したら同じコマンドを再実行（`claudeMdExcludes` はフォルダ名の glob）
- 無効化：`python tools/dev/disable-maintenance.py`（git フックも外す）
- 本体の settings.json を変えたら：`python tools/dev/maint.py check` → 指摘に従い影響を確かめ `isolation.json` を更新
- スキルの見え方の変化は、写し直したあとの**新しいセッション**から確実に反映される

## 7. 変更履歴

- 2026-11-04：メンテ層を導入（CLAUDE.local・settings.local・maint スキル）
- 2026-11-05：状況台帳・maint.py・isolation.json・git フック。写し直しを自動化（それまでは手動で、本体に後から足した
  dispatch / run-task / scout / independent-review がメンテモードに見えていた）。常時ルールの正本を claude-local.md に改名
