#!/usr/bin/env bash
# 定期の見直し（割り振り・作業セッションの起動・結果の回収）を無人で回す（macOS / Linux の cron から呼ぶ）
#
# 使い方（crontab -e に1行。15分ごとに呼び、実際の間隔は memory/work/settings.md の「実行間隔（分）」で間引く）:
#   */15 * * * *  /path/to/instance/tools/schedule/dispatch.sh
#
# 前提: このフォルダを一度 Claude Code で開いて「信頼」済み。cron の PATH に claude と python3 があること。
# 中身は tools/schedule/dispatch.py（説明・安全・上限はそちら）。
set -u
ROOT="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT" || exit 1
exec sh tools/py.sh tools/schedule/dispatch.py --root "$ROOT"
