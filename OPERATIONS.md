# 運用設計（OPERATIONS.md）── 本体とインスタンスの分離

このリポジトリは **本体（テンプレート）** である。**ここに個人の記憶を蓄積しない。**
使う人はそれぞれ自分の **インスタンス（プライベートリポジトリ）** を作って運用する。

```
ai-manager（本体・テンプレート）          ← ツールの進化はここで起きる
   │  GitHub Template として複製
   ├── ai-manager-yamada（山田さんのインスタンス）← 山田さんの記憶が育つ
   ├── ai-manager-suzuki（鈴木さんのインスタンス）
   └── ...
```

- **本体の更新** → 各インスタンスが upstream として取り込む（下記手順）
- **現場の改善** → インスタンスで直接コアを書き換えず、本体への PR / Issue で還流する

---

## 1. 領域の境界（これが運用の核）

| 領域 | パス | 所有者 | アップデート時 |
|---|---|---|---|
| **コア（本体領域）** | `AGENTS.md` `CLAUDE.md` `GEMINI.md` `README.md` `USAGE.md` `PORTABILITY.md` `OPERATIONS.md` `.agents/` `.claude/`（settings.local.json除く） `.github/` `.mcp.json` `tools/` | 本体 | 本体の変更で上書きされる。**インスタンスで直接編集しない** |
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
   git clone https://github.com/n-ima/ai-manager.git ai-manager-{name}
   cd ai-manager-{name}
   git remote rename origin upstream
   gh repo create ai-manager-{name} --private --source . --remote origin --push
   ```
   ※ GitHub の [Use this template] でも作れるが、履歴が切れるため初回だけ
   `git merge upstream/main --allow-unrelated-histories` が必要になる
2. `.env` を作成（`.env.template` 参照。Backlog/GitHub/Brave の各キー）
3. MCP を登録（`tools/mcp_config.sample.json` 参照。ツールごとの置き場所はコメントに記載）
4. 好きなエージェント（Antigravity / Claude Code / Copilot）でフォルダを開き、秘書に自己紹介する：
   「`memory/dashboard.md` に私を登録して。名前=〇〇、役職=〇〇、判断基準=〇〇」
5. 定期実行を登録（README の `/schedule` 文例）
6. upstream の確認（手順1の clone 方式なら登録済み）：
   ```bash
   git remote -v   # upstream = 本体 / origin = 自分のインスタンス になっていること
   ```

---

## 3. 本体アップデートの取り込み（インスタンス側・随時）

秘書に「本体を最新にして」と頼めばこの手順を実行する：

```bash
git fetch upstream
git merge upstream/main
```

- **コンフリクトの解決方針**：コア領域＝本体側（`theirs`）を採用／データ領域＝自分側（`ours`）を採用。
  境界（§1）通りに運用していればコンフリクトはほぼ起きない
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

- `memory/` 配下のファイルは**初期化用の雛形**。リリース後に雛形の構造を変える場合は、
  上書きでなく **移行手順** をコミットメッセージと `PORTABILITY.md` に明記する
  （例：「commitments.md に関連欄を追加 → 既存インスタンスは列を手動追加 or 秘書に依頼」）
- コアの変更は必ず正典（`.agents/`）側で行い、ポインタ（`.claude/` `.github/`）を同期する
  （`PORTABILITY.md` の再生成スクリプト参照）
- 破壊的変更（ファイル名変更・ワークフロー削除）は README のリリースノートに明記する

---

## 6. なぜこの方式か（設計判断の記録）

- **単一リポジトリに記憶を蓄積すると本体の更新が不可能になる**（git 履歴も個人情報で汚染される）
- サブモジュール方式は不採用：スキル・ワークフローは各エージェントがリポジトリ直下の
  規約パス（`.agents/` 等）に要求するため、コアを別リポジトリに切り出せない
- テンプレート＋upstream merge 方式は、境界規律（§1）さえ守れば git 標準機能だけで回る
