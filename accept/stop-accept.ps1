#!/usr/bin/env pwsh
# SPX Voice  -  Acceptance environment shutdown
# Usage: .\accept\stop-accept.ps1 [-Wipe]
#
# -Wipe also removes the Docker volume (database data)

Param([switch]$Wipe)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunDir    = Join-Path $ScriptDir 'run'

Write-Host ""
Write-Host "=== Stopping SPX Voice Acceptance Environment ==="

# Stop API process
$apiPidFile = Join-Path $RunDir 'api.pid'
if (Test-Path $apiPidFile) {
    $pid_ = (Get-Content $apiPidFile -Raw).Trim()
    if ($pid_) {
        Write-Host "-> Stopping API (PID $pid_)..."
        taskkill /PID $pid_ /T /F 2>&1 | Out-Null
    }
    Remove-Item $apiPidFile -Force -ErrorAction SilentlyContinue
}

# Stop UI process
$uiPidFile = Join-Path $RunDir 'ui.pid'
if (Test-Path $uiPidFile) {
    $pid_ = (Get-Content $uiPidFile -Raw).Trim()
    if ($pid_) {
        Write-Host "-> Stopping UI (PID $pid_)..."
        taskkill /PID $pid_ /T /F 2>&1 | Out-Null
    }
    Remove-Item $uiPidFile -Force -ErrorAction SilentlyContinue
}

# Clear the non-secret Gemini-key-presence marker (never held the key
# itself, but its "present=true" claim is only valid for the session that
# just stopped).
Remove-Item (Join-Path $RunDir 'gemini-key-status.txt') -Force -ErrorAction SilentlyContinue

# Stop Docker services
$composeFile = Join-Path $ScriptDir 'docker-compose.accept.yaml'
Write-Host "-> Stopping Docker containers..."
if ($Wipe) {
    Write-Host "   -Wipe: removing volume spx_accept_pg_data_20261121"
    docker compose -f $composeFile down -v
} else {
    docker compose -f $composeFile down
}

Write-Host ""
Write-Host "Acceptance environment stopped."
if (-not $Wipe) {
    Write-Host "Database data preserved. Use -Wipe to also remove volumes."
}
Write-Host ""
