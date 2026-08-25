<#
.SYNOPSIS
    Claude Code 自己改善システムを、この payload/ から現在の PC に導入する。

.DESCRIPTION
    payload/ 配下の hooks・rules/self-improve.md・skills/learn・
    skills/memory-review を $env:USERPROFILE\.claude\ 配下へ配置し、
    settings.json の hooks ブロックにこのシステムの5エントリを追記する。

    前提: claude CLI の導入・ログイン・Python 3.12 系の導入は別途
    済ませておくこと（README.md の導入手順1〜3）。settings.json が
    まだ無い場合はエラーで止まる。

    settings.json は "settings.json.bak-<timestamp>" にバックアップして
    から書き換える。バックアップからの復元は uninstall.ps1 が使う。

.EXAMPLE
    powershell -File install.ps1
#>

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ClaudeHome = Join-Path $env:USERPROFILE ".claude"
$SettingsPath = Join-Path $ClaudeHome "settings.json"
$PayloadRoot = Join-Path $PSScriptRoot "payload"

Write-Host "=== Claude Code 自己改善システム インストール ==="

if (-not (Test-Path $PayloadRoot)) {
    Write-Error "payload\ が見つかりません（$PayloadRoot）。sync_payload.ps1 で payload を生成済みのリポジトリから実行してください。"
}
if (-not (Test-Path $SettingsPath)) {
    Write-Error "$SettingsPath が見つかりません。先に claude CLI を導入・起動してください（README.md の導入手順1〜3）。"
}
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "python が PATH 上に見つかりません。Python 3.12 系を導入してから再実行してください。"
}
$PythonExe = $pythonCmd.Source
Write-Host "Python: $PythonExe"

# 1. hooks / rules / skills を配置
New-Item -ItemType Directory -Force -Path (Join-Path $ClaudeHome "hooks") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ClaudeHome "rules") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ClaudeHome "skills") | Out-Null

Get-ChildItem (Join-Path $PayloadRoot "hooks") -Filter *.py | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $ClaudeHome "hooks\$($_.Name)") -Force
    Write-Host "配置: hooks\$($_.Name)"
}
Copy-Item -Path (Join-Path $PayloadRoot "rules\self-improve.md") -Destination (Join-Path $ClaudeHome "rules\self-improve.md") -Force
Write-Host "配置: rules\self-improve.md"
foreach ($skill in @("learn", "memory-review")) {
    $dest = Join-Path $ClaudeHome "skills\$skill"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -Path (Join-Path $PayloadRoot "skills\$skill\SKILL.md") -Destination (Join-Path $dest "SKILL.md") -Force
    Write-Host "配置: skills\$skill\SKILL.md"
}

# 2. settings.json のバックアップ
$backupPath = "$SettingsPath.bak-$(Get-Date -Format yyyyMMddHHmmss)"
Copy-Item -Path $SettingsPath -Destination $backupPath -Force
Write-Host "バックアップ: $backupPath"

# 3. hooks ブロックのマージ
$settings = Get-Content -Path $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Add-HookEntry {
    param(
        [Parameter(Mandatory)] $Settings,
        [Parameter(Mandatory)] [string]$EventName,
        [string]$Matcher,
        [Parameter(Mandatory)] [string]$ScriptFile,
        [Parameter(Mandatory)] [int]$Timeout,
        [switch]$Async
    )

    if (-not $Settings.PSObject.Properties["hooks"]) {
        $Settings | Add-Member -NotePropertyName "hooks" -NotePropertyValue ([PSCustomObject]@{}) -Force
    }
    if (-not $Settings.hooks.PSObject.Properties[$EventName]) {
        $Settings.hooks | Add-Member -NotePropertyName $EventName -NotePropertyValue @() -Force
    }

    $already = $Settings.hooks.$EventName | Where-Object {
        $_.hooks | Where-Object { ($_.args -join ",") -match [regex]::Escape($ScriptFile) }
    }
    if ($already) {
        Write-Host "スキップ（既存エントリあり）: $EventName / $ScriptFile"
        return
    }

    $hookCmd = [PSCustomObject]@{
        type    = "command"
        command = $PythonExe
        args    = @((Join-Path $ClaudeHome "hooks\$ScriptFile"))
        timeout = $Timeout
    }
    if ($Async) {
        $hookCmd | Add-Member -NotePropertyName "async" -NotePropertyValue $true
    }

    if ($Matcher) {
        $entry = [PSCustomObject]@{ matcher = $Matcher; hooks = @($hookCmd) }
    } else {
        $entry = [PSCustomObject]@{ hooks = @($hookCmd) }
    }

    $Settings.hooks.$EventName = @($Settings.hooks.$EventName) + $entry
    Write-Host "追加: $EventName / $ScriptFile"
}

Add-HookEntry -Settings $settings -EventName "SessionEnd" -ScriptFile "session_end_learn.py" -Timeout 180 -Async
Add-HookEntry -Settings $settings -EventName "SessionStart" -Matcher "startup|resume|clear" -ScriptFile "session_start_inbox.py" -Timeout 15
Add-HookEntry -Settings $settings -EventName "SessionStart" -Matcher "startup|resume|clear" -ScriptFile "session_start_catchup.py" -Timeout 180 -Async
Add-HookEntry -Settings $settings -EventName "PostToolUse" -Matcher "Edit|Write|NotebookEdit" -ScriptFile "post_edit_log.py" -Timeout 15 -Async
Add-HookEntry -Settings $settings -EventName "PreCompact" -ScriptFile "pre_compact_snapshot.py" -Timeout 120

$json = $settings | ConvertTo-Json -Depth 20
# Set-Content -Encoding utf8NoBOM は PowerShell 7 以降限定のため、
# Windows PowerShell 5.1 でも動く .NET API で BOM なし UTF-8 に統一する。
[System.IO.File]::WriteAllText($SettingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "settings.json を更新しました。"

Write-Host "=== 完了 ==="
Write-Host "次に必要な手動確認:"
Write-Host "  - claude --version / claude -p `"ping`" --model claude-haiku-4-5-20251001 で疎通確認"
Write-Host "  - 新規セッションを開き、hooks が正常に動くか確認（~/.claude/hooks/logs/ にログが出るか）"
Write-Host "元の settings.json は $backupPath に残っています。問題があれば uninstall.ps1 で復元できます。"
