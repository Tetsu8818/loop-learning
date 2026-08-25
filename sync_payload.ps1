<#
.SYNOPSIS
    ~/.claude/ の実体ファイル（正本）を payload/ へ同期する。

.DESCRIPTION
    このシステムの正本は ~/.claude/ 配下にある（README.md 参照）。
    payload/ は他PCへ移植するための配布用スナップショットで、
    hooks・rules・skills を改修したら、コミット前に本スクリプトを
    実行して payload/ を最新化すること。

    一方向コピー（~/.claude/ → payload/）。逆方向は install.ps1。

.EXAMPLE
    powershell -File sync_payload.ps1
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ClaudeHome = Join-Path $env:USERPROFILE ".claude"
$PayloadRoot = Join-Path $PSScriptRoot "payload"

Write-Host "=== payload/ 同期（正本: $ClaudeHome） ==="

$hookFiles = @(
    "lib_transcript.py",
    "lib_extract.py",
    "session_end_learn.py",
    "session_start_catchup.py",
    "session_start_inbox.py",
    "post_edit_log.py",
    "pre_compact_snapshot.py",
    "memory_scan.py"
)

$payloadHooks = Join-Path $PayloadRoot "hooks"
New-Item -ItemType Directory -Force -Path $payloadHooks | Out-Null
foreach ($f in $hookFiles) {
    $src = Join-Path $ClaudeHome "hooks\$f"
    if (-not (Test-Path $src)) {
        Write-Warning "見つかりません（スキップ）: $src"
        continue
    }
    Copy-Item -Path $src -Destination (Join-Path $payloadHooks $f) -Force
    Write-Host "同期: hooks\$f"
}

$payloadRules = Join-Path $PayloadRoot "rules"
New-Item -ItemType Directory -Force -Path $payloadRules | Out-Null
Copy-Item -Path (Join-Path $ClaudeHome "rules\self-improve.md") -Destination (Join-Path $payloadRules "self-improve.md") -Force
Write-Host "同期: rules\self-improve.md"

foreach ($skill in @("learn", "memory-review")) {
    $payloadSkill = Join-Path $PayloadRoot "skills\$skill"
    New-Item -ItemType Directory -Force -Path $payloadSkill | Out-Null
    Copy-Item -Path (Join-Path $ClaudeHome "skills\$skill\SKILL.md") -Destination (Join-Path $payloadSkill "SKILL.md") -Force
    Write-Host "同期: skills\$skill\SKILL.md"
}

Write-Host "=== 完了。差分を確認してからコミットしてください ==="
