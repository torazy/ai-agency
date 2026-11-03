# tools/dev ── 本体メンテナンス用の資産（置き場所と有効化を分ける）

本体（テンプレート）フォルダで Claude Code を開くと、配布物そのもの（秘書の CLAUDE.md・スキル・
フック・MCP）が起動してしまう。メンテナンス中にそれが動くと、余計な制限（ガードレール）と
余計な記録（task-log・memory/ 雛形への書き込み）が発生する。

そこで **メンテ用の層** を用意し、本体フォルダでだけ有効化する：

| 層 | 置き場所（コミット・インスタンスにも届く） | 有効化後の実体（gitignore・本体フォルダにだけ存在） |
|---|---|---|
| メンテ用 CLAUDE.md | `tools/dev/CLAUDE.local.md` | `CLAUDE.local.md`（ルート） |
| 製品を止める設定 | `enable-maintenance.py` が生成 | `.claude/settings.local.json` に `claudeMdExcludes` / `disableAllHooks` / `skillOverrides` / `disabledMcpjsonServers` |
| メンテ用スキル | `tools/dev/skills/maint-*/SKILL.md`（maint-check-structure / maint-test-instance） | `.claude/skills/maint-*/`（コピー） |
| メンテ用メモリ | `tools/dev/memory/*.md`（そのまま読む・コピーしない） | ─ |
| 本体マーカー | ─ | `.ai-agency-template`（フック無効時は不要だが Gemini CLI 系フック向けに残す） |

**インスタンスは有効化しない**。`tools/dev/` は届くが、スクリプトを実行しなければ何も起きない。

## 使い方（本体メンテナ・1回だけ）

```bash
python tools/dev/enable-maintenance.py
```

その後 **新しいセッションを開き**、「あなたは誰？」と聞いて「第一秘書」と**名乗らない**ことを確認する
（名乗ったら `claudeMdExcludes` のパターンが合っていない。`.claude/settings.local.json` を確認）。

`claudeMdExcludes` の glob は**実行時のフォルダ名**（例：`**/ai-manager/CLAUDE.md`）から生成される。
フォルダを改名・別名で clone したときは、同じコマンドをもう一度実行して glob を更新する（冪等）。

戻すとき：

```bash
python tools/dev/disable-maintenance.py
```

## 製品の要素ごとの扱い（Claude Code）

| 製品の要素 | 止める手段 | 機械的か |
|---|---|---|
| CLAUDE.md → memory/dashboard.md | `claudeMdExcludes`（絶対パスの glob） | 機械的 |
| フック（SessionStart / PreToolUse / PostToolUse） | `disableAllHooks: true` | 機械的 |
| スキル・ワークフロー（コマンド） | `skillOverrides` で名前ごとに `"off"` | 機械的 |
| `.mcp.json` の MCP サーバー | `disabledMcpjsonServers` | 機械的 |
| `permissions.deny` | **外せない**（リストは層をまたいで合算）。削除は `git rm -r` か Python で | 残る |
| `.claude/agents/`（専門家） | 残る。害なし | 残る |

