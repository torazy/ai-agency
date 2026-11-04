# Run the periodic review (triage, start work sessions, collect results) unattended. Called from Windows Task Scheduler.
# This file is ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less UTF-8 as the ANSI code page.
# Docs (Japanese): tools/schedule/dispatch.py and .claude/commands/dispatch.md.
#
# Register once (every 15 minutes; the real interval is "jikkou kankaku" in memory/work/settings.md):
#   schtasks /Create /TN "ai-agency dispatch" /SC MINUTE /MO 15 `
#     /TR "conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"<instance path>\tools\schedule\dispatch.ps1\""
#   (conhost --headless keeps a console window from flashing every 15 minutes; Windows 10 1903+ / 11)
#   Run once now: schtasks /Run /TN "ai-agency dispatch"
#   Remove: schtasks /Delete /TN "ai-agency dispatch"
#
# Prerequisite: open this folder in Claude Code once and trust it. claude and python must be on PATH.

param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

Set-Location $Root
python tools/schedule/dispatch.py --root $Root
exit $LASTEXITCODE
