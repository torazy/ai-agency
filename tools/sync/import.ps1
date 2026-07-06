# 片方向同期・受け側（Windows PowerShell 版）── OPERATIONS.md §6
# bash 版（import.sh）と同一ロジック。単純上書きはしない（3-way マージ）。
#
# 使い方:
#   powershell -ExecutionPolicy Bypass -File tools\sync\import.ps1 -Bundle E:\ai-agency-PC2-20260705.bundle
#   powershell -ExecutionPolicy Bypass -File tools\sync\import.ps1 -GitHub [-Remote origin]
param(
    [string]$Bundle = "",
    [switch]$GitHub,
    [string]$Remote = "origin"
)
# 注: PS5.1 では native コマンドの stderr をリダイレクトすると誤動作するため、
#     無音化したい git 呼び出しは cmd /c 経由で行う（$LASTEXITCODE は伝播する）。
$ErrorActionPreference = "Stop"

if (-not $GitHub -and $Bundle -eq "") {
    Write-Host "使い方: import.ps1 -Bundle <bundleファイル> | -GitHub [-Remote origin]"
    exit 1
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

cmd /c "git rev-parse HEAD >nul 2>nul"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[import] エラー: 受け側は本体から clone したインスタンス（コミットのある git リポジトリ）で実行してください"
    exit 1
}
$n = cmd /c "git config user.name 2>nul";  if (-not $n) { git config user.name  "ai-agency-sync" }
$e = cmd /c "git config user.email 2>nul"; if (-not $e) { git config user.email "sync@local" }

# 1) 受け側のローカル変更を先にコミット（ダーティなままマージしない）
$HostTag = $env:COMPUTERNAME
$dirty = git status --porcelain
if ($dirty) {
    git add -A
    git commit -q -m ("sync(" + $HostTag + "): import前のローカル変更 " + (Get-Date -Format "yyyy-MM-dd HH:mm"))
    if ($LASTEXITCODE -ne 0) { Write-Host "[import] エラー: 事前コミットに失敗しました"; exit 1 }
    Write-Host "[import] 受け側のローカル変更をコミットしました"
}

$Branch = (git rev-parse --abbrev-ref HEAD).Trim()

# 2) 送り側の履歴を取得
if ($GitHub) {
    cmd /c ("git fetch " + $Remote + " " + $Branch + " 2>nul")
    if ($LASTEXITCODE -ne 0) { Write-Host "[import] エラー: fetch に失敗しました"; exit 1 }
    $SrcDesc = "$Remote/$Branch"
} else {
    cmd /c ('git bundle verify "' + $Bundle + '" >nul 2>nul')
    if ($LASTEXITCODE -ne 0) { Write-Host "[import] エラー: bundle が不正です: $Bundle"; exit 1 }
    cmd /c ('git fetch "' + $Bundle + '" ' + $Branch + ' 2>nul')
    if ($LASTEXITCODE -ne 0) { Write-Host "[import] エラー: bundle からの取得に失敗しました"; exit 1 }
    $SrcDesc = "bundle: " + (Split-Path $Bundle -Leaf)
}
Write-Host "[import] 取得完了（$SrcDesc）。マージします..."

function Test-MergeInProgress {
    $gitDir = (git rev-parse --git-dir).Trim()
    return (Test-Path (Join-Path $gitDir "MERGE_HEAD"))
}
function Show-Conflict {
    Write-Host ""
    Write-Host "!! [import] コンフリクトが発生しました（単純上書きはしていません）。対象:"
    git diff --name-only --diff-filter=U | ForEach-Object { Write-Host ("    - " + $_) }
    Write-Host ""
    Write-Host "  解決方法（どちらか）:"
    Write-Host "  A. 秘書に依頼: 「同期のコンフリクトを解決して。送り側の内容は[提案]として統合して」"
    Write-Host "  B. 手動: 各ファイルの <<<<<<< 部分を編集 → git add -A; git commit"
    exit 2
}

# 3) マージ（初回の独立履歴にも自動対応）
cmd /c "git merge --no-edit FETCH_HEAD >nul 2>nul"
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK [import] マージ完了"
} elseif (Test-MergeInProgress) {
    Show-Conflict
} else {
    cmd /c "git merge --no-edit --allow-unrelated-histories FETCH_HEAD >nul 2>nul"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK [import] 初回同期（独立した履歴）を統合しました"
    } elseif (Test-MergeInProgress) {
        Show-Conflict
    } else {
        Write-Host "[import] エラー: マージに失敗しました。git status を確認してください"
        exit 1
    }
}

Write-Host ""
Write-Host "[import] 仕上げの推奨（秘書に依頼）:"
Write-Host "  - 「同期後の重複を整理して」（memory-maintenance ── 両側で同じ事実を記録した行の統合）"
Write-Host "  - 送り側の [提案] に確定できるものがあれば decisions.md で確定する（確定は受け側のみ）"
