# 朝のブリーフを無人で実行する（Windows タスク スケジューラから呼ぶ）
#
# 使い方（インスタンスのフォルダで1回だけ登録。平日 8:30 の例）:
#   schtasks /Create /TN "ai-agency morning brief" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:30 `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"<インスタンスのパス>\tools\schedule\morning-brief.ps1\""
#   解除: schtasks /Delete /TN "ai-agency morning brief"
#
# 前提: このフォルダを一度 Claude Code で開いて「信頼」済みであること（無人実行は信頼ダイアログに答えられない）。
# 出力: memory/briefs/YYYY-MM-DD.md（gitignore 済み）。実行ログは memory/briefs/_last-run.log。
# 安全: 許可するのは読み取りと mem.py と memory/briefs/ への書き込みだけ。外部への送信・作成は、
#       普段の会話で「常に許可」したもの（.claude/settings*.json の allow）以外は自動で拒否される。

param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [double]$MaxBudgetUsd = 2
)

Set-Location $Root
if (Test-Path ".ai-agency-template") { Write-Output "本体（テンプレート）では実行しない"; exit 0 }
if (-not (Test-Path "memory/dashboard.md")) { Write-Output "memory/ が無い: $Root"; exit 1 }

New-Item -ItemType Directory -Force "memory/briefs" | Out-Null
$env:AI_AGENCY_UNATTENDED = "1"   # operation-logger が「承認済」ではなく「自動実行[unattended]」と記録する

$allowed = @(
    "Read", "Glob", "Grep",
    "Bash(python tools/mem.py *)", "Bash(python3 tools/mem.py *)",
    "Write(memory/briefs/**)", "Edit(memory/briefs/**)"
)
$log = "memory/briefs/_last-run.log"
"[{0}] start" -f (Get-Date -Format "yyyy-MM-dd HH:mm") | Out-File -Encoding utf8 $log
claude -p "/morning-brief --unattended" --allowedTools @allowed --max-budget-usd $MaxBudgetUsd --no-session-persistence 2>&1 |
    Out-File -Encoding utf8 -Append $log
$code = $LASTEXITCODE
"[{0}] exit {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm"), $code | Out-File -Encoding utf8 -Append $log
exit $code
