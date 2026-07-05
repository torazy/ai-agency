#!/usr/bin/env bash
# 片方向同期・受け側（例: PC1）── OPERATIONS.md §6
#
# やること（全自動・単純上書きはしない）:
#   1. 受け側のローカル変更を先に自動コミット（ダーティなままマージしない）
#   2. bundle（または GitHub）から送り側の履歴を取得
#   3. 3-way マージ（初回で履歴が独立していても自動対応）
#   4. コンフリクト時は上書きせず、対象ファイルを列挙して秘書/人間に引き継ぐ
#
# 使い方:
#   bash tools/sync/import.sh /path/to/ai-manager-PC2-20260705-0930.bundle
#   bash tools/sync/import.sh --github            # origin から取得（受け側は push しない）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

SRC="${1:?使い方: import.sh <bundleファイル | --github [リモート名]>}"

if ! git rev-parse --git-dir >/dev/null 2>&1 || ! git rev-parse HEAD >/dev/null 2>&1; then
  echo "[import] エラー: 受け側は本体から clone したインスタンス（コミットのある git リポジトリ）で実行してください" >&2
  exit 1
fi
git config user.name  >/dev/null 2>&1 || git config user.name  "ai-manager-sync"
git config user.email >/dev/null 2>&1 || git config user.email "sync@local"

# 1) 受け側のローカル変更を先にコミット
HOST_TAG="$(hostname)"
if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -q -m "sync(${HOST_TAG}): import前のローカル変更 $(date '+%Y-%m-%d %H:%M')"
  echo "[import] 受け側のローカル変更をコミットしました"
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# 2) 送り側の履歴を取得
if [ "$SRC" = "--github" ]; then
  REMOTE="${2:-origin}"
  git fetch "$REMOTE" "$BRANCH"
  SRC_DESC="$REMOTE/$BRANCH"
else
  git bundle verify "$SRC" >/dev/null
  git fetch "$SRC" "$BRANCH"
  SRC_DESC="bundle: $(basename "$SRC")"
fi
echo "[import] 取得完了（$SRC_DESC）。マージします..."

# 3) マージ（→ 4) コンフリクト対応）
finish_conflict() {
  echo ""
  echo "⚠️ [import] コンフリクトが発生しました（単純上書きはしていません）。対象:"
  git diff --name-only --diff-filter=U | sed 's/^/    - /'
  echo ""
  echo "  解決方法（どちらか）:"
  echo "  A. 秘書に依頼: 「同期のコンフリクトを解決して。送り側の内容は[提案]として統合して」"
  echo "  B. 手動: 各ファイルの <<<<<<< 部分を編集 → git add -A && git commit"
  exit 2
}

if git merge --no-edit FETCH_HEAD 2>/dev/null; then
  echo "✅ [import] マージ完了"
elif [ -f "$(git rev-parse --git-dir)/MERGE_HEAD" ]; then
  finish_conflict
elif git merge --no-edit --allow-unrelated-histories FETCH_HEAD 2>/dev/null; then
  echo "✅ [import] 初回同期（独立した履歴）を統合しました"
elif [ -f "$(git rev-parse --git-dir)/MERGE_HEAD" ]; then
  finish_conflict
else
  echo "[import] エラー: マージに失敗しました。git status を確認してください" >&2
  exit 1
fi

echo ""
echo "[import] 仕上げの推奨（秘書に依頼）:"
echo "  - 「同期後の重複を整理して」（memory-maintenance ── 両側で同じ事実を記録した行の統合）"
echo "  - 送り側の [提案] に確定できるものがあれば decisions.md で確定する（確定は受け側のみ）"
