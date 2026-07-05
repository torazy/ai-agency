# 片方向同期・送り側（Windows PowerShell 版）── OPERATIONS.md §6
# bash 版（export.sh）と同一ロジック。Windows PowerShell 5.1 / git.exe のみで動く。
#
# 使い方:
#   powershell -ExecutionPolicy Bypass -File tools\sync\export.ps1                # 既定の出力先
#   powershell -ExecutionPolicy Bypass -File tools\sync\export.ps1 -OutDir E:\    # USB 等へ
#   powershell -ExecutionPolicy Bypass -File tools\sync\export.ps1 -GitHub        # origin へ push
param(
    [string]$OutDir = "",
    [switch]$GitHub
)
# 注: PS5.1 では native コマンドの stderr をリダイレクトすると誤動作するため、
#     無音化したい git 呼び出しは cmd /c 経由で行う（$LASTEXITCODE は伝播する）。
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
if ($OutDir -eq "") { $OutDir = Join-Path (Split-Path $RepoRoot -Parent) "ai-manager-sync" }

# 1) git リポジトリでなければ初期化（ローカル利用のみだったインスタンスを救済）
cmd /c "git rev-parse --git-dir >nul 2>nul"
if ($LASTEXITCODE -ne 0) {
    cmd /c "git init -b main >nul 2>nul"
    if ($LASTEXITCODE -ne 0) { cmd /c "git init >nul 2>nul"; cmd /c "git checkout -b main >nul 2>nul" }
    Write-Host "[export] git リポジトリを初期化しました"
}
$n = cmd /c "git config user.name 2>nul";  if (-not $n) { git config user.name  "ai-manager-sync" }
$e = cmd /c "git config user.email 2>nul"; if (-not $e) { git config user.email "sync@local" }

# 2) ローカル変更を自動コミット
$HostTag = $env:COMPUTERNAME
$dirty = git status --porcelain
if ($dirty) {
    git add -A
    git commit -q -m ("sync(" + $HostTag + "): " + (Get-Date -Format "yyyy-MM-dd HH:mm"))
    if ($LASTEXITCODE -ne 0) { Write-Host "[export] エラー: コミットに失敗しました"; exit 1 }
    Write-Host "[export] ローカル変更をコミットしました"
} else {
    Write-Host "[export] 変更なし（作業ツリーはクリーン）"
}

$Branch = (git rev-parse --abbrev-ref HEAD).Trim()

# 3) bundle 作成 or push
if ($GitHub) {
    git push origin $Branch
    if ($LASTEXITCODE -ne 0) { Write-Host "[export] エラー: push に失敗しました"; exit 1 }
    Write-Host "[export] origin/$Branch へ push しました"
    Write-Host "[export] 受け側では: powershell -ExecutionPolicy Bypass -File tools\sync\import.ps1 -GitHub"
} else {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force $OutDir | Out-Null }
    $Bundle = Join-Path $OutDir ("ai-manager-" + $HostTag + "-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".bundle")
    cmd /c ('git bundle create "' + $Bundle + '" ' + $Branch + ' >nul 2>nul')
    if ($LASTEXITCODE -ne 0) { Write-Host "[export] エラー: bundle 作成に失敗しました"; exit 1 }
    cmd /c ('git bundle verify "' + $Bundle + '" >nul 2>nul')
    if ($LASTEXITCODE -ne 0) { Write-Host "[export] エラー: bundle 検証に失敗しました"; exit 1 }
    Write-Host "[export] 作成: $Bundle"
    Write-Host "[export] これを受け側PCへ運び、次を実行: powershell -ExecutionPolicy Bypass -File tools\sync\import.ps1 -Bundle <ファイル>"
}

Write-Host "[export] 注意: .env と memory/task-log/ は gitignore のため同期されません（仕様）"
