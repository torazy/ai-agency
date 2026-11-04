# 運用設計（OPERATIONS.md）── 本体とインスタンスの分離

このリポジトリは **本体（テンプレート）** である。**ここに個人の記憶を蓄積しない。**
使う人はそれぞれ自分の **インスタンス（プライベートリポジトリ）** を作って運用する。

```
ai-agency（本体・テンプレート）          ← ツールの進化はここで起きる
   │  GitHub Template として複製
   ├── ai-agency-yamada（山田さんのインスタンス）← 山田さんの記憶が育つ
   ├── ai-agency-suzuki（鈴木さんのインスタンス）
   └── ...
```

- **本体の更新** → 各インスタンスが upstream として取り込む（下記手順）
- **現場の改善** → インスタンスで直接コアを書き換えず、本体への PR / Issue で還流する

---

## 1. 領域の境界（これが運用の核）

| 領域 | パス | 所有者 | アップデート時 |
|---|---|---|---|
| **コア（本体領域）** | `CLAUDE.md` `README.md` `USAGE.md` `PORTABILITY.md` `OPERATIONS.md` `.claude/`（settings.local.json・skills/maint-* 除く） `.mcp.json` `tools/` | 本体 | 本体の変更で上書きされる。**インスタンスで直接編集しない** |
| **データ（インスタンス領域）** | `memory/`（全部） `deliverables/` `.env` `.claude/settings.local.json` | 各ユーザー | 本体は触らない。マージで衝突しない |

例外：緊急でインスタンス側のコアを直した場合は、**必ず本体へ PR で還流**する（しないと次の
アップデートで消えるか、永久に分岐する）。

---

## 2. 各ユーザーのセットアップ手順（1回だけ・約15分）

> 前提：本体リポジトリは Private のため、利用者に Read 権限を付与しておく（人数が増えたら
> Organization への移管を検討）。

1. **本体を clone して自分のインスタンスリポジトリを作る**（履歴を保持 ── 後の upstream merge が
   きれいに通る。この手順を推奨）：
   ```bash
   git clone https://github.com/torazy/ai-agency.git ai-agency-{name}
   cd ai-agency-{name}
   git remote rename origin upstream
   git remote set-url --push upstream DISABLED   # インスタンスの記憶を誤って本体へ push しないための安全策
   gh repo create ai-agency-{name} --private --source . --remote origin --push
   ```
   ※ GitHub の [Use this template] でも作れるが、履歴が切れるため初回だけ
   `git merge upstream/main --allow-unrelated-histories` が必要になる
2. `.env` を作成（`.env.template` 参照。Backlog/GitHub/Brave の各キー）
3. MCP は `.mcp.json` に同梱済み（github / backlog / brave-search。`.env` のキーを参照する）。
   Slack・Google Workspace・Notion 等は claude.ai のコネクタを接続しておく（無ければ手動アシストに落ちる）
4. Claude Code でフォルダを開き、**`/setup`** を実行する
   （対話でプロフィール・案件・人物・優先事項を登録し、使えるツールを棚卸しする。約15分・再実行可）
5. 定期実行を登録（`/setup` Step 6。デスクトップの定期タスクか `tools/schedule/` を OS のスケジューラへ）
6. upstream の確認（手順1の clone 方式なら登録済み）：
   ```bash
   git remote -v   # upstream = 本体 / origin = 自分のインスタンス になっていること
   ```

---

## 2b. 旧版のインスタンスからの移行（別の PC で古い版を使っていた場合）

古い版をその場で merge するより、**新しいインスタンスに記憶（データ）だけを移す**ほうが安全（旧ツール向けファイルの削除や
雛形の構造変更で衝突が多いため）。古いフォルダはバックアップとして残す。

1. 新しいインスタンスを用意する（§2。既に作ってあれば、その PC で clone する）
2. 新しいインスタンスのフォルダで、まず計画を見る（何も変えない）：
   ```bash
   python tools/import_legacy.py --from <古いフォルダ>
   ```
3. 問題なければ実行する（写す → `mem.py migrate` → `check` → 点検 → 報告）：
   ```bash
   python tools/import_legacy.py --from <古いフォルダ> --apply
   ```
   - 写す：約束台帳・気づき・inbox・人物ファイル・projects/・memos/・archive/
   - 載せ替え用に `memory/legacy-import/` へ置く：状況・プロフィール・登録簿（版で構造が違う）、書き換えていた委任設定、
     写し先にも中身があったファイル、今の版に無いファイル
   - 写さない：雛形と設定、使えるツールの記録、`task-log/`（承認の判定方法が違うので委任の根拠に使えない）
   - 何度実行しても同じ結果。上書きはしない
4. コミットして push し、Claude Code で `/setup` を実行する（legacy-import の載せ替えと、案件が今も進行中かの確認をする）
5. 別の PC では `git pull` で同じ記憶を使う。`.env` は PC ごとに置く

**Mac の場合**：コマンドは `python` を `python3` に読み替える（フックとボードは `tools/py.sh` が自動で選ぶ）。
git と python3 は Xcode Command Line Tools に入っている（`xcode-select --install`）。非公開リポジトリの取得には
GitHub の認証が要る（`brew install gh` → `gh auth login`、または GitHub Desktop）。

**旧版の PC で git を使っていない場合**：import_legacy.py は旧版のフォルダを読むだけなので、旧版側に git は要らない。
ただし新しいインスタンスを使い続ける PC には git が要る（本体の更新の取り込みと、PC 間で記憶を揃えるため）。
どうしても git を入れられない PC では、旧版の `memory/` フォルダを git のある PC へ運んで（USB・クラウドドライブ等）
そこで `--from` に指定して移行し、push する。その PC で新しい版を使うなら GitHub の画面から ZIP で取得できるが、
更新も記憶の同期も手作業になるので、日常使いの PC には git を入れることを勧める。

## 3. 本体アップデートの取り込み（インスタンス側・随時）

秘書に「本体を最新にして」と頼めばこの手順を実行する：

```bash
git fetch upstream
git merge upstream/main
```

- **コンフリクトの解決方針**：コア領域＝本体側（`theirs`）を採用／データ領域＝自分側（`ours`）を採用。
  境界（§1）通りに運用していればコンフリクトはほぼ起きない。例外は **memory/ の雛形構造が変わった**とき
  （表の列追加・節の移動）で、`memory/` のファイルが衝突する。このときの解消は：
  - **表（commitments.md・decisions.md）は自分側（`ours`）をそのまま残す**（旧ヘッダのまま）。列の追加や状態行の補完は
    次の `mem.py migrate` が行う。本体側の新ヘッダだけを採ると「新ヘッダ＋旧行」になり migrate が効かない
  - **説明文・見出し・節の構成（dashboard.md・user-profile.md・service-capabilities.md）は本体側**を採り、自分の値
    （名前・座標・観測記録）を新しい構造へ載せ替える（横持ち→縦持ちなどは手作業）
- マージ後は `python tools/mem.py migrate`（冪等）を実行し、`python tools/mem.py check` で表が崩れていないか確認する。
  migrate が decisions に補った状態行（提案／決定の推定）は一度目を通す
- マージ後、`PORTABILITY.md` 冒頭と本体のリリースノート（コミットログ）を確認し、
  **移行手順（下記 §5）があれば秘書が実行を提案**する

---

## 4. 現場からのフィードバック（インスタンス → 本体）

- スキルの不備・改善案・新スキルの必要性に気づいたら：`skill-improvement` が改善案をまとめる →
  ユーザー承認 → **本体リポジトリへ PR**（または Issue）。インスタンス内での抱え込みを禁止する
- **記憶（memory/）の中身は絶対に本体へ送らない**（個人情報・業務情報を含むため）。
  還流するのは「仕組みの改善」のみ
- 複数人の学びを本体に集約することで、全インスタンスが賢くなる（これが分離方式の利点）

---

## 5. 本体側の変更規律（本体メンテナ向け）

- **既存インスタンスへの移行（2026-11-04 の保存の安全性対応）**：本体を merge したら `python tools/mem.py migrate` を1回。
  決定に ID（D-001…）が振られる。以後の承認は ID で指定する。操作ログの「承認済」は確認画面が出たものだけになる
  （それより前の記録は昇格の根拠に使わない）

- **自動テスト**：`python -m unittest discover -s tests -t tests`（標準ライブラリのみ）。push と PR のたびに GitHub Actions が
  Windows / Linux × Python 3.11 / 3.12 で回す。テストは `tests/fixtures/memory/`（雛形の写し）の上で動き、リポジトリの `memory/`
  には触らないので、インスタンスで回しても実データは読まない

- **本体フォルダではメンテナンス層を有効化する**（Claude Code）：`python tools/dev/enable-maintenance.py` を1回実行。
  製品の CLAUDE.md・フック・スキル・MCP を本体フォルダでだけ機械的に止め（`.claude/settings.local.json`）、
  メンテナ向けの `CLAUDE.local.md` と `.claude/skills/maint-*` を置く。生成物はすべて gitignore 済みで
  インスタンスには届かない。雛形は `tools/dev/`（コミット対象）。詳細は `tools/dev/README.md`。
  有効化後、新しいセッションで「あなたは誰？」と聞いて第一秘書と名乗らないことを確認する。
- **本体で `/setup` や `/intake` を実行しない**。動作確認は使い捨てのテストインスタンスで行う
  （`maint-test-instance` スキル：`git clone . ../ai-agency-test`。push しない。削除は手動）。
- `permissions.deny` は局所設定で外せないため本体でも効く。ディレクトリ削除は `git rm -r` か Python で行う。

- `memory/` 配下のファイルは**初期化用の雛形**。リリース後に雛形の構造を変える場合は、
  上書きでなく **移行手順** をコミットメッセージと `PORTABILITY.md` に明記する
  （例：「commitments.md に関連欄を追加 → 既存インスタンスは列を手動追加 or 秘書に依頼」）
- 正典は `CLAUDE.md`（常時ルール）・`.claude/agents/`（人格）・`.claude/skills/`（手順）・`.claude/commands/`（コマンド）。
  構造の整合は `maint-check-structure` スキルで検査する
- 破壊的変更（ファイル名変更・ワークフロー削除）は README のリリースノートに明記する

---

## 6. 片方向同期（1人で2台のPCを使う・オフライン運搬対応）

> 想定：PC2 → PC1 へは情報を送れるが、逆は不可（閉域・機密環境等）。両方で同じインスタンスを使い、
> どこかのタイミングで統合する。**単純上書きではなく git の 3-way マージで統合する**（両側で同じ
> ファイルを編集していても、行単位で自動統合され、無理な箇所だけコンフリクトとして明示される）。

### 手順（スクリプト化済み・検証済み）

| 手順 | 実行 | 内容 |
|---|---|---|
| ① 送り側（PC2） | `bash tools/sync/export.sh [出力先]` | **全自動**：git 未使用でも初期化→ローカル変更を自動コミット→ bundle 作成 |
| ② 運搬 | 手動 | bundle ファイル1個を USB 等で受け側へ（**媒体は暗号化推奨**） |
| ③ 受け側（PC1） | `bash tools/sync/import.sh <bundle>` | **全自動**：受け側の変更を先に自動コミット→取得→3-wayマージ（初回の独立履歴も自動統合） |

**Windows（Git Bash なし・PowerShell 標準環境）**では同一ロジックの `.ps1` 版を使う（git.exe のみ必要）：

```powershell
# 送り側
powershell -ExecutionPolicy Bypass -File tools\sync\export.ps1 [-OutDir E:\] [-GitHub]
# 受け側
powershell -ExecutionPolicy Bypass -File tools\sync\import.ps1 -Bundle <ファイル>   # または -GitHub
```

- コンフリクト時：スクリプトは上書きせず対象ファイルを列挙して終了する。
  秘書に「**同期のコンフリクトを解決して。送り側の内容は[提案]として統合して**」と依頼すれば解決される
- 統合後の仕上げ（推奨）：秘書に「同期後の重複を整理して」（memory-maintenance）
- `.env` と `memory/task-log/` は gitignore のため同期されない（仕様。task-log を集約したい場合は手動コピー）

### GitHub が使える場合（運搬なしの同一手順）

- 送り側：`bash tools/sync/export.sh --github`（origin へ push）
- 受け側：`bash tools/sync/import.sh --github`（fetch＋マージのみ。**受け側からは push しない**
  ── 片方向の規律は GitHub 経由でも同じ）

### 両方で使うときの役割規約（記憶の食い違いを防ぐ）

送り側は受け側の記録を永遠に知らない（逆方向が閉じているため）。そこで：

1. **確定は受け側のみ**：`decisions.md` への「決定」の確定記録は受け側（フル記憶側）だけが行う。
   送り側では見出しに `[提案/{ホスト名}]` を付けて提案として記録する
2. **送り側の書き込みは新規ファイル中心**：詳細案は `memory/projects/{name}/proposals/YYYY-MM-DD-{論点}.md`
   に書く（共有ファイルの直接編集を減らす＝マージ衝突がほぼ消える）
3. **並行検討は「独立2案→受け側で裁定」と最初から宣言する**：食い違いはバグではなく
   クロスレビューの材料。ただし受け側の確定済み方針を送り側は知れないため、
   送り側に向かう前に「そこで検討する範囲」を決めるのは人間の役割
4. 機密の規律は git の外にも及ぶ：**受け側でしか扱えない機密を送り側の秘書に話さない**
   （会話に書けばそのインスタンスに記録される）

## 7. なぜこの方式か（設計判断の記録）

- **単一リポジトリに記憶を蓄積すると本体の更新が不可能になる**（git 履歴も個人情報で汚染される）
- サブモジュール方式は不採用：スキル・コマンドは Claude Code がリポジトリ直下の規約パス（`.claude/` 等）に
  要求するため、コアを別リポジトリに切り出せない
- テンプレート＋upstream merge 方式は、境界規律（§1）さえ守れば git 標準機能だけで回る
