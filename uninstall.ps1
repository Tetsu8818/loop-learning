<#
.SYNOPSIS
    Claude Code 自己改善システムをアンインストールする。

.DESCRIPTION
    settings.json のバックアップから復元し、hooks/ 配下の自己改善用スクリプト、
    rules/self-improve.md、skills/learn/ を削除する。
    learnings/ と snapshots/ は監査用に残す（-RemoveData を付けた場合のみ削除）。

.PARAMETER RemoveData
    ~/.claude/learnings と ~/.claude/snapshots も削除する。既定では残す。

.EXAMPLE
    powershell -File uninstall.ps1
    powershell -File uninstall.ps1 -RemoveData
#>
param(
    [switch]$RemoveData
)

$ClaudeHome = Join-Path $env:USERPROFILE ".claude"
$SettingsPath = Join-Path $ClaudeHome "settings.json"

Write-Host "=== Claude Code 自己改善システム アンインストール ==="

# 1. settings.json のバックアップから復元
$backups = Get-ChildItem -Path $ClaudeHome -Filter "settings.json.bak-*" -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending
if ($backups.Count -gt 0) {
    $latest = $backups[0]
    Write-Host "settings.json を $($latest.Name) から復元します。"
    Copy-Item -Path $latest.FullName -Destination $SettingsPath -Force
} else {
    Write-Warning "settings.json のバックアップが見つかりません。hooks ブロックのみ手動で削除してください。"
}

# 2. フックスクリプトを削除
$hookFiles = @(
    "lib_transcript.py",
    "session_end_learn.py",
    "session_start_inbox.py",
    "post_edit_log.py",
    "pre_compact_snapshot.py"
)
foreach ($f in $hookFiles) {
    $p = Join-Path $ClaudeHome "hooks\$f"
    if (Test-Path $p) {
        Remove-Item -Path $p -Force -Confirm:$false
        Write-Host "削除: $p"
    }
}

# 3. 規範・スキルを削除
$rulePath = Join-Path $ClaudeHome "rules\self-improve.md"
if (Test-Path $rulePath) {
    Remove-Item -Path $rulePath -Force -Confirm:$false
    Write-Host "削除: $rulePath"
}
$skillPath = Join-Path $ClaudeHome "skills\learn"
if (Test-Path $skillPath) {
    Remove-Item -Path $skillPath -Recurse -Force -Confirm:$false
    Write-Host "削除: $skillPath"
}

# 4. データディレクトリ（既定では残す）
if ($RemoveData) {
    foreach ($d in @("learnings", "snapshots")) {
        $p = Join-Path $ClaudeHome $d
        if (Test-Path $p) {
            Remove-Item -Path $p -Recurse -Force -Confirm:$false
            Write-Host "削除: $p"
        }
    }
} else {
    Write-Host "learnings/ と snapshots/ は残しました（-RemoveData で削除可能）。"
}

Write-Host "=== 完了 ==="
Write-Host "claude CLI 自体（$env:USERPROFILE\.local\bin\claude.exe）は削除していません。"
Write-Host "CLI ごと削除する場合は公式ドキュメントの Uninstall 手順を参照してください。"
