# Run the morning brief unattended (called from Windows Task Scheduler).
# This file is ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less UTF-8 as the ANSI code page.
# Docs (Japanese): README.md and USAGE.md, section on scheduled runs.
#
# Register once (weekdays 08:30):
#   schtasks /Create /TN "ai-agency morning brief" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:30 `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"<instance path>\tools\schedule\morning-brief.ps1\""
#   Remove: schtasks /Delete /TN "ai-agency morning brief"
#
# Prerequisite: open this folder in Claude Code once and trust it (an unattended run cannot answer the trust dialog).
# Output: memory/briefs/YYYY-MM-DD.md (gitignored). Run log: memory/briefs/_last-run.log.
# Safety: only reads, tools/mem.py and writes under memory/briefs/ are allowed here. Any external write is denied
#         unless the user already allowed it in .claude/settings*.json.

param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [double]$MaxBudgetUsd = 2
)

Set-Location $Root
if (Test-Path ".ai-agency-template") { Write-Output "template repository: skipped"; exit 0 }
if (-not (Test-Path "memory/dashboard.md")) { Write-Output "memory/ not found: $Root"; exit 1 }

New-Item -ItemType Directory -Force "memory/briefs" | Out-Null
# operation-logger records these runs as automatic (unattended), not as user-approved.
$env:AI_AGENCY_UNATTENDED = "1"

$allowed = @(
    "Read", "Glob", "Grep",
    "Bash(python tools/mem.py *)", "Bash(python3 tools/mem.py *)",
    "Write(memory/briefs/**)", "Edit(memory/briefs/**)"
)
$log = "memory/briefs/_last-run.log"
$stamp = { Get-Date -Format "yyyy-MM-dd HH:mm" }
"[$(& $stamp)] start" | Out-File -Encoding utf8 $log
claude -p "/morning-brief --unattended" --allowedTools @allowed --max-budget-usd $MaxBudgetUsd --no-session-persistence 2>&1 |
    Out-File -Encoding utf8 -Append $log
$code = $LASTEXITCODE
"[$(& $stamp)] exit $code" | Out-File -Encoding utf8 -Append $log
exit $code
