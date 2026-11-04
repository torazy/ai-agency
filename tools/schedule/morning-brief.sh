#!/usr/bin/env bash
# 朝のブリーフを無人で実行する（macOS / Linux の cron から呼ぶ）
#
# 使い方（crontab -e に1行。平日 8:30 の例）:
#   30 8 * * 1-5  /path/to/instance/tools/schedule/morning-brief.sh
#
# 前提: このフォルダを一度 Claude Code で開いて「信頼」済みであること。cron の PATH に claude と python があること。
# 出力: memory/briefs/YYYY-MM-DD.md（gitignore 済み）。実行ログは memory/briefs/_last-run.log。
# 安全: 許可するのは読み取りと mem.py と memory/briefs/ への書き込みだけ。外部への送信・作成は、
#       普段の会話で「常に許可」したもの（.claude/settings*.json の allow）以外は自動で拒否される。
set -u
ROOT="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
BUDGET="${MAX_BUDGET_USD:-2}"
cd "$ROOT" || exit 1
[ -f .ai-agency-template ] && { echo "本体（テンプレート）では実行しない"; exit 0; }
[ -f memory/dashboard.md ] || { echo "memory/ が無い: $ROOT"; exit 1; }

mkdir -p memory/briefs
export AI_AGENCY_UNATTENDED=1   # operation-logger が「承認済」ではなく「自動実行[unattended]」と記録する
LOG=memory/briefs/_last-run.log
echo "[$(date '+%Y-%m-%d %H:%M')] start" > "$LOG"
# 追記系と読み取りの mem.py だけを許す（mem.py 自身も AI_AGENCY_UNATTENDED の間は書き換えを拒否する）
ALLOWED=("Read" "Glob" "Grep" "Edit(memory/briefs/**)"
         "Bash(python tools/recall.py *)" "Bash(python3 tools/recall.py *)")
for op in "inbox add" "inbox list" "commitment add" "commitment list" "person observe" "person note" \
          "sync mark" "sync show" "check"; do
  ALLOWED+=("Bash(python tools/mem.py ${op}*)" "Bash(python3 tools/mem.py ${op}*)")
done
claude -p "/morning-brief --unattended" \
  --allowedTools "${ALLOWED[@]}" \
  --max-budget-usd "$BUDGET" --no-session-persistence >> "$LOG" 2>&1
code=$?
echo "[$(date '+%Y-%m-%d %H:%M')] exit $code" >> "$LOG"
exit $code
