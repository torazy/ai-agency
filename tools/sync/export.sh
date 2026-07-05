#!/usr/bin/env bash
# 片方向同期・送り側（例: PC2）── OPERATIONS.md §6
#
# やること（全自動）:
#   1. git リポジトリでなければ初期化（「ただローカルで使っていた」インスタンスをそのまま救済）
#   2. ローカル変更をすべて自動コミット
#   3. bundle ファイルを作成（既定）または GitHub へ push（--github）
#
# 使い方:
#   bash tools/sync/export.sh                # bundle を ../ai-manager-sync/ に作成
#   bash tools/sync/export.sh /path/to/usb   # bundle を指定先に作成
#   bash tools/sync/export.sh --github       # origin へ push（GitHub が使える構成のとき）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

MODE="bundle"
OUT_DIR="$(dirname "$REPO_ROOT")/ai-manager-sync"
if [ "${1:-}" = "--github" ]; then
  MODE="github"
elif [ -n "${1:-}" ]; then
  OUT_DIR="$1"
fi

# 1) git リポジトリでなければ初期化
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  git init -b main 2>/dev/null || { git init && git checkout -b main; }
  echo "[export] git リポジトリを初期化しました"
fi
# コミット者情報が未設定でも止まらないように（ローカル設定のみ）
git config user.name  >/dev/null 2>&1 || git config user.name  "ai-manager-sync"
git config user.email >/dev/null 2>&1 || git config user.email "sync@local"

# 2) ローカル変更を自動コミット
HOST_TAG="$(hostname)"
if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -q -m "sync(${HOST_TAG}): $(date '+%Y-%m-%d %H:%M')"
  echo "[export] ローカル変更をコミットしました"
else
  echo "[export] 変更なし（作業ツリーはクリーン）"
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# 3) bundle 作成 or push
if [ "$MODE" = "github" ]; then
  git push origin "$BRANCH"
  echo "[export] origin/$BRANCH へ push しました"
  echo "[export] 受け側では: bash tools/sync/import.sh --github"
else
  mkdir -p "$OUT_DIR"
  BUNDLE="$OUT_DIR/ai-manager-${HOST_TAG}-$(date +%Y%m%d-%H%M).bundle"
  git bundle create "$BUNDLE" "$BRANCH"
  git bundle verify "$BUNDLE" >/dev/null
  echo "[export] 作成: $BUNDLE"
  echo "[export] これを受け側PCへ運び、次を実行: bash tools/sync/import.sh <bundleファイル>"
fi

echo "[export] 注意: .env と memory/task-log/ は gitignore のため同期されません（仕様）"
