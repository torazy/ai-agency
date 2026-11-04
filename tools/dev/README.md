# tools/dev ── 本体メンテナンス（メンテモード）

本体（テンプレート）フォルダで Claude Code を開くと、配布物そのもの（秘書の CLAUDE.md・スキル・フック・MCP）が
動いてしまう。メンテナンス中は製品を止め、メンテナ用の常時ルール・状況台帳・道具に切り替える。これが**メンテモード**。
設計（何をどう切り離し、何を守らせるか）は **`DESIGN.md`**。

**インスタンスは有効化しない**。`tools/dev/` は届くが、スクリプトを実行しなければ何も起きない。

## 有効化（本体フォルダで1回・別の PC で clone したときも）

```bash
python tools/dev/enable-maintenance.py
```

その後**新しいセッションを開き**、「あなたは誰？」と聞いて「第一秘書」と**名乗らない**ことを確認し、続けて：

```bash
python tools/dev/maint.py start
```

名乗ったら `claudeMdExcludes` のパターンが合っていない（glob は実行時のフォルダ名から作る。フォルダを改名したら再実行）。

戻すとき（git フックも外す）：

```bash
python tools/dev/disable-maintenance.py
```

## ファイル

| ファイル | 役目 |
|---|---|
| `DESIGN.md` | メンテモードの設計 |
| `claude-local.md` | メンテナの常時ルールと毎回の手順の正本（有効化でルートの `CLAUDE.local.md` に写る） |
| `maint.py` | 状況台帳の読み書き・`start`（写し直し・分離の点検・稼働中のものと git・CI の確認）・git フックの中身 |
| `isolation.json` | 本体 `.claude/settings.json` の各項目がメンテモードでどう効くか（承認済みの一覧） |
| `enable-maintenance.py` / `disable-maintenance.py` | メンテモードの有効化・写し直し／無効化 |
| `memory/maint-state.md` | 状況台帳：引き継ぎ・要求・未検証・稼働中のもの・経過（毎回の会話に読み込まれる） |
| `memory/maint-archive.md` | 台帳から移した完了・確認済み・古い経過 |
| `memory/roadmap.md` / `memory/design-decisions.md` | 製品の改善計画と設計判断 |
| `skills/maint-*` | メンテ用スキル（構造検査・テストインスタンス） |

## よく使う

```bash
python tools/dev/maint.py add "要求の要約" --state 着手
```

```bash
python tools/dev/maint.py done R-007 --result "abc1234 …" --unverified "Mac で未確認"
```

```bash
python tools/dev/maint.py check
```
