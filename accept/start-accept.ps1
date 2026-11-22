#!/usr/bin/env pwsh
# SPX Voice  -  Acceptance environment startup
# Usage: .\accept\start-accept.ps1                      (default: no key; save it in the dashboard)
#        .\accept\start-accept.ps1 -SeedGeminiKeyToDb   (persistent, encrypted DB seeding)
#        .\accept\start-accept.ps1 -AllowFileBasedGeminiKey (legacy fallback)
#
# What this script starts (ports come from accept/.env.accept: ACCEPT_API_PORT,
# ACCEPT_UI_PORT; nothing here hardcodes them):
#   1. Docker isolated services (Postgres, Redis, LiveKit)
#   2. Alembic migrations, then encryption of any legacy stored secrets
#   3. Candidate API
#   4. Candidate UI (builds ui/ if needed, ~3-5 min first time)
#
# Worker: started automatically by the API on first Test Audio request
#         (LIVEKIT_WORKER_MANAGED_BY_API=true)
#
# Gemini key handling:
#   (no flag)                 DEFAULT. The stack starts with NO Gemini key.
#                             Any Gemini key variable inherited from the parent
#                             shell is cleared first. API health reports
#                             provider_status=provider_unconfigured (expected,
#                             not a failure) and Test Audio is refused with an
#                             actionable message until the owner saves a key in
#                             the candidate dashboard. Saved keys are encrypted
#                             at rest (api/services/configuration/at_rest.py).
#   -SeedGeminiKeyToDb        PERSISTENT, NOT EPHEMERAL. Prompts for a key
#                             (hidden input) and passes it to the API process,
#                             which stores it - encrypted - in the acceptance
#                             database when the first user logs in and has no
#                             model configuration yet. After that the key lives
#                             in the DB until it is changed or the DB is reset.
#                             The prompt asks you to confirm this. The key is
#                             never written to .env.accept, logs or evidence.
#                             (Formerly -PromptGeminiKey, a name that implied
#                             ephemeral storage; the old name is now refused.)
#   -AllowFileBasedGeminiKey  Legacy fallback: reads a key already saved in
#                             accept/.env.accept (plaintext on disk). It is then
#                             also seeded into the DB at first login.
# Pass at most one flag.
#
# Encryption key: stored OUTSIDE the candidate tree (%LOCALAPPDATA%\SPXVoiceAccept,
# owner-only ACL; override with SPX_ACCEPT_KEY_DIR). See
# docs/developer/secret-storage.md for backup, rotation and recovery.
#
# Prerequisites:
#   - Docker Desktop must be running
#   - D:\spx-voice-mvp\.venv must exist (it does  -  verified 2026-11-21)

Param(
    [switch]$SeedGeminiKeyToDb,
    [switch]$PromptGeminiKey,
    [switch]$AllowFileBasedGeminiKey
)

$ErrorActionPreference = 'Stop'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Candidate  = Split-Path -Parent $ScriptDir
$EnvFile    = Join-Path $ScriptDir '.env.accept'
$RunDir     = Join-Path $ScriptDir 'run'
$LogsDir    = Join-Path $ScriptDir 'logs'
$Python     = 'D:\spx-voice-mvp\.venv\Scripts\python.exe'
. (Join-Path $ScriptDir 'lib-accept.ps1')
$ports      = Get-AcceptPorts -EnvFile $EnvFile
$ApiPort    = $ports.Api
$UiPort     = $ports.Ui

Write-Host ""
Write-Host "=== SPX Voice Acceptance Environment Startup ==="
Write-Host "Candidate : $Candidate"
Write-Host "API port  : $ApiPort"
Write-Host "UI port   : $UiPort"
Write-Host ""

# --- Verify prerequisites ---
if (-not (Test-Path $EnvFile)) {
    Write-Host "ERROR: $EnvFile not found." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $Python)) {
    Write-Host "ERROR: Python venv not found at $Python" -ForegroundColor Red
    exit 1
}

# --- Gemini-key strategy ---
$geminiKeyNames = @('GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GENERATIVE_AI_API_KEY', 'GOOGLE_AI_API_KEY')
if ($PromptGeminiKey) {
    Write-Host "ERROR: -PromptGeminiKey was renamed to -SeedGeminiKeyToDb." -ForegroundColor Red
    Write-Host "       The key is NOT ephemeral: it is stored (encrypted) in the acceptance database"
    Write-Host "       at first login. The default (no flag) lets you enter it in the dashboard instead."
    exit 1
}
if ($SeedGeminiKeyToDb -and $AllowFileBasedGeminiKey) {
    Write-Host "ERROR: pass only one of -SeedGeminiKeyToDb / -AllowFileBasedGeminiKey, not both." -ForegroundColor Red
    exit 1
}
$dashboardKeyMode = (-not $SeedGeminiKeyToDb) -and (-not $AllowFileBasedGeminiKey)

# --- Load non-secret infrastructure values from accept/.env.accept ---
# DATABASE_URL, REDIS_URL, LIVEKIT_* (local dev mode), etc. These live only
# in this PowerShell process's environment from here on, inherited by the
# API/alembic child processes below, and are deliberately NOT inherited by
# the npm/next UI build further down (see $uiSensitiveVars). The four
# Gemini key names are never read from the file here, even if a stray value
# sits in it, unless -AllowFileBasedGeminiKey was requested.
$loadedEnvNames = New-Object System.Collections.ArrayList
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#')) {
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) {
            $key = $parts[0].Trim()
            if (($SeedGeminiKeyToDb -or $dashboardKeyMode) -and ($geminiKeyNames -contains $key)) { return }
            [Environment]::SetEnvironmentVariable($key, $parts[1].Trim(), 'Process')
            [void]$loadedEnvNames.Add($key)
        }
    }
}

if ($dashboardKeyMode) {
    # --- Dashboard key mode: start with no Gemini key in the environment ---
    # A key inherited from the parent shell would be seeded into the DB at
    # first login, defeating the point of entering it through the dashboard.
    foreach ($k in $geminiKeyNames) {
        [Environment]::SetEnvironmentVariable($k, $null, 'Process')
    }
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    Set-Content -Path (Join-Path $RunDir 'gemini-key-status.txt') -Value 'source=dashboard;present=false'
    Write-Host ""
    Write-Host "-> No Gemini key requested: starting in dashboard-key mode."
    Write-Host "   Provider status will be provider_unconfigured until a key is saved at"
    Write-Host "   http://127.0.0.1:$UiPort (Test Audio stays blocked until then)."
} elseif ($SeedGeminiKeyToDb) {
    # --- Persistent, encrypted DB seeding (NOT ephemeral) ---
    # Read-Host -AsSecureString does not echo input and is not captured by
    # PSReadLine history or PowerShell transcription (only command text is).
    # The key is handed to the API process through its environment. On the first
    # login with no saved model configuration, ensure_default_user_setup stores
    # it, encrypted, in the acceptance database. This script never writes it to
    # accept/.env.accept, any log file or any evidence file.
    Write-Host ""
    Write-Host "-> PERSISTENT SEEDING: the key you enter will be stored, encrypted, in the"
    Write-Host "   acceptance database at first login. It is NOT process-only/ephemeral."
    $confirm = Read-Host -Prompt "   Type SEED to continue, anything else to cancel"
    if ($confirm -cne 'SEED') {
        Write-Host "Cancelled; nothing was entered." -ForegroundColor Yellow
        exit 1
    }
    $secureGeminiKey = Read-Host -AsSecureString -Prompt "   Enter Gemini API key (hidden)"
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($secureGeminiKey)
    try {
        $plainGeminiKey = [System.Runtime.InteropServices.Marshal]::PtrToStringUni($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($bstr)
    }
    if ([string]::IsNullOrWhiteSpace($plainGeminiKey)) {
        Write-Host "ERROR: no key entered." -ForegroundColor Red
        $secureGeminiKey.Dispose()
        exit 1
    }
    [Environment]::SetEnvironmentVariable('GOOGLE_API_KEY', $plainGeminiKey.Trim(), 'Process')
    # Drop the temporary plaintext/secure representations. $plainGeminiKey is a
    # managed [string] (immutable), so this drops the reference rather than
    # zeroing memory; SecureString.Dispose() zeroes its own buffer.
    $plainGeminiKey = $null
    $secureGeminiKey.Dispose()
    $secureGeminiKey = $null
    Write-Host "OK Gemini key staged for the API process (value not shown); it will be stored encrypted at first login"
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    Set-Content -Path (Join-Path $RunDir 'gemini-key-status.txt') -Value 'source=seeded-to-db;present=true'
} else {
    # --- Legacy fallback: explicitly requested, read from accept/.env.accept ---
    $envText = Get-Content $EnvFile -Raw
    $geminiSet = $false
    foreach ($k in $geminiKeyNames) {
        if ($envText -match "(?m)^$k\s*=\s*\S+") { $geminiSet = $true; break }
    }
    if (-not $geminiSet) {
        Write-Host "ERROR: -AllowFileBasedGeminiKey was passed but no Gemini key is set in $EnvFile." -ForegroundColor Red
        Write-Host "       Uncomment ONE of the GOOGLE_API_KEY / GEMINI_API_KEY lines and enter your key,"
        Write-Host "       or run without flags and save the key in the dashboard instead."
        exit 1
    }
    foreach ($k in $geminiKeyNames) {
        if ($envText -match "(?m)^$k\s*=\s*(\S+)") {
            [Environment]::SetEnvironmentVariable($k, $Matches[1], 'Process')
        }
    }
    Write-Host "WARN: using -AllowFileBasedGeminiKey (legacy fallback). The key sits in plaintext in" -ForegroundColor Yellow
    Write-Host "      accept\.env.accept on disk and is also seeded (encrypted) into the DB at first login." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    Set-Content -Path (Join-Path $RunDir 'gemini-key-status.txt') -Value 'source=file;present=true'
}

# OUTER CLEANUP GUARD - runs on EVERY exit path (exception, bare `exit N`,
# or normal completion). PowerShell 5.1 guarantees `finally` executes even
# when `exit N` is called inside `try`. This clears all Gemini API key env
# vars from this PowerShell process's environment before the script ends,
# so the key is never left in the parent shell after start-accept.ps1 returns.
# The matching `} finally { ... }` is at the very bottom of this script.
try {

# Python should not write .pyc bytecode caches into the read-only venv tree.
$env:PYTHONDONTWRITEBYTECODE = '1'

# --- Name this stack's own URLs explicitly ---
# Without these the API falls back to its built-in defaults, which can point at
# a DIFFERENT instance on the same machine. Everything the candidate reports
# must be about the ports configured in accept/.env.accept.
$env:UI_APP_URL           = "http://127.0.0.1:$UiPort"
$env:APP_URL              = "http://127.0.0.1:$UiPort"
$env:BACKEND_API_ENDPOINT = "http://127.0.0.1:$ApiPort"

# --- At-rest encryption settings (the key itself is provisioned after Postgres is up) ---
# Only one key variable may be set (crypto.py refuses conflicting names).
foreach ($n in @('TELEPHONY_ENCRYPTION_KEYS', 'TELEPHONY_CREDENTIALS_ENCRYPTION_KEY', 'TELEPHONY_ENCRYPTION_KEY')) {
    [Environment]::SetEnvironmentVariable($n, $null, 'Process')
}
$env:REQUIRE_CONFIG_ENCRYPTION = 'true'
# Loguru's default handler (used by processes that never call setup_logging,
# e.g. the LiveKit worker) prints local variables in tracebacks; those can hold
# decrypted provider keys. Off for every child process.
$env:LOGURU_DIAGNOSE = 'False'
# --- Start isolated Docker services ---
Write-Host ""
Write-Host "-> Starting Docker services..."
$composeFile = Join-Path $ScriptDir 'docker-compose.accept.yaml'
docker compose -f $composeFile up -d
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: docker compose failed" -ForegroundColor Red; exit 1 }

# Wait for Postgres
Write-Host "   Waiting for Postgres on 127.0.0.1:55444..."
$pgReady = $false
for ($i = 1; $i -le 20; $i++) {
    # stderr from a not-yet-ready container must mean "retry", not abort ('Stop').
    $_pgEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $result = docker exec spx-accept-pg-20261121 pg_isready -U postgres 2>&1
    $_pgExit = $LASTEXITCODE
    $ErrorActionPreference = $_pgEap
    if ($_pgExit -eq 0) { $pgReady = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $pgReady) { Write-Host "ERROR: Postgres did not become ready" -ForegroundColor Red; exit 1 }
Write-Host "   OK Postgres ready"

# --- Provision the configuration encryption key (before migrations and API) ---
# Stored outside the candidate (owner-only ACL), created atomically, and NEVER
# regenerated while the database already holds encrypted values.
Write-Host ""
Write-Host "-> Configuration encryption key..."
$pgContainer = 'spx-accept-pg-20261121'
$env:TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS = Initialize-AcceptEncryptionKey `
    -Python $Python `
    -LegacyPath (Join-Path $RunDir 'config-encryption.key') `
    -HasEncryptedData { Test-AcceptDbHasEncryptedData -Container $pgContainer -Database 'spx_voice_accept' }
Write-Host "   OK encryption key ready (owner-only ACL, outside the candidate tree)"
# Wait for Redis
Write-Host "   Waiting for Redis on 127.0.0.1:6384..."
$redisReady = $false
for ($i = 1; $i -le 20; $i++) {
    # Pass the password via REDISCLI_AUTH env var inside the container rather
    # than the `-a` flag. `-a` causes redis-cli to emit a security warning to
    # stderr; in PowerShell 5.1, native-command stderr piped through 2>&1
    # becomes NativeCommandError and sets $? = $false even when exit code is 0.
    # REDISCLI_AUTH produces no warning and no stderr. No `2>&1` needed: a
    # clean ping has no stderr; any docker-level error is a genuine failure
    # that should surface on the console.
    $result = docker exec -e REDISCLI_AUTH=REDIS_PASSWORD_PLACEHOLDER spx-accept-redis-20261121 redis-cli ping
    if ($LASTEXITCODE -eq 0 -and "$result" -match 'PONG') { $redisReady = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $redisReady) { Write-Host "ERROR: Redis did not become ready" -ForegroundColor Red; exit 1 }
Write-Host "   OK Redis ready"

# Wait for LiveKit
Write-Host "   Waiting for LiveKit on 127.0.0.1:7880..."
Start-Sleep -Seconds 3
$lkReady = $false
for ($i = 1; $i -le 10; $i++) {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:7880' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        $lkReady = $true; break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $lkReady) { Write-Host "WARN: LiveKit health check inconclusive (may still be starting)" -ForegroundColor Yellow }
else { Write-Host "   OK LiveKit reachable" }

# --- Pre-flight: verify pipecat-ai is importable from .deps ---
# .deps is the candidate-local offline package store (see .env.p101test for the
# PYTHONPATH contract). pipecat-ai is mandatory: it backs Text Chat, the
# Premium STT/LLM/TTS pipeline (Deepgram-DeepSeek-Cartesia), and the agent-
# stream runner. Missing it crashes the ASGI worker at import time.
Write-Host ""
Write-Host "-> Checking candidate dependencies (.deps)..."
$depsDir = Join-Path $Candidate '.deps'
if (-not (Test-Path $depsDir)) {
    Write-Host "ERROR: .deps directory not found at $depsDir" -ForegroundColor Red
    Write-Host "       The candidate requires the .deps offline package store."
    Write-Host "       Run the candidate's dependency preparation step or restore .deps."
    exit 1
}
# Pass the .deps path through PYTHONPATH rather than embedding it as a Windows
# path literal inside `python -c`. Backslashes in paths like C:\Users\... map
# to \U escape sequences in Python string literals, causing SyntaxError or
# DeprecationWarning. An OS-level env var is never parsed as Python source.
# pipecat prints its startup banner on stderr. This script runs with
# $ErrorActionPreference = 'Stop', and in PowerShell 5.1 a native command's
# stderr merged through 2>&1 becomes a terminating NativeCommandError even on
# exit code 0, so the preference is relaxed for this one call and restored.
$_prevPP = $env:PYTHONPATH
$_prevEAP = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $env:PYTHONPATH = "$depsDir;$_prevPP"
    $pipecatCheck = & $Python -c "import pipecat" 2>&1
    $pipecatOk = ($LASTEXITCODE -eq 0)
} finally {
    $ErrorActionPreference = $_prevEAP
    $env:PYTHONPATH = $_prevPP
}
if (-not $pipecatOk) {
    Write-Host "ERROR: pipecat-ai is not importable from .deps" -ForegroundColor Red
    Write-Host "       Details: $pipecatCheck"
    Write-Host "       pipecat-ai is required for Text Chat and Premium audio pipeline."
    Write-Host "       Ensure .deps\pipecat is present (candidate ships it pre-extracted)."
    exit 1
}
Write-Host "   OK pipecat-ai importable from .deps"

# --- Run Alembic migrations ---
Write-Host ""
Write-Host "-> Running Alembic migrations against acceptance DB..."
# PYTHONPATH must include both the candidate root (for api.*) and .deps (for
# pipecat and other packages not installed in the shared venv). The .deps
# directory is the candidate-local offline store; see .env.p101test.
$env:PYTHONPATH = "$Candidate\.deps;$Candidate"
Push-Location $Candidate
& $Python -m alembic -c api\alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: Alembic migrations failed" -ForegroundColor Red; Pop-Location; exit 1 }
Write-Host "   OK migrations complete"

# --- Encrypt any legacy stored provider secrets (idempotent) ---
# Plaintext model-provider keys in user configurations or workflow overrides are
# encrypted in place; nothing is printed except counts. A failure here stops the
# start: a stack must not run with secrets we could not protect.
Write-Host "-> Encrypting any legacy stored provider secrets..."
$_maintEap = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $maintOut = & $Python -m api.scripts.config_secrets_maintenance --apply 2>$null
    $maintExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $_maintEap
}
foreach ($l in @($maintOut)) { if ("$l" -match '^(FAIL |[a-z_]+: \d+ secret|\d+ row)') { Write-Host "   $l" } }
Pop-Location
if ($maintExit -ne 0) { Write-Host "ERROR: secret maintenance failed (see messages above)" -ForegroundColor Red; exit 1 }

# --- Prepare run/log directories ---
New-Item -ItemType Directory -Force -Path $RunDir  | Out-Null
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

# Kill any previous acceptance API
$prevPid = (Get-Content (Join-Path $RunDir 'api.pid') -ErrorAction SilentlyContinue)
if ($prevPid) {
    # taskkill reports a missing PID on stderr; under 'Stop' that would abort.
    $_eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    taskkill /PID $prevPid /T /F 2>&1 | Out-Null
    $ErrorActionPreference = $_eap
    Remove-Item (Join-Path $RunDir 'api.pid') -Force -ErrorAction SilentlyContinue
}

# --- Start acceptance API ---
Write-Host ""
Write-Host "-> Starting candidate API on port $ApiPort..."
$apiLog  = Join-Path $LogsDir 'api.log'
$apiCmd  = "cd /d `"$Candidate`" && `"$Python`" -m uvicorn api.app:app --host 127.0.0.1 --port $ApiPort --reload --reload-dir api >> `"$apiLog`" 2>&1"
$apiProc = Start-Process cmd.exe -ArgumentList '/c', $apiCmd -PassThru -WindowStyle Hidden
Set-Content (Join-Path $RunDir 'api.pid') $apiProc.Id
Write-Host "   PID $($apiProc.Id) -> $apiLog"

# Wait for API health
$healthUrl = "http://127.0.0.1:$ApiPort/api/v1/health"
Write-Host "   Waiting for API health check..."
$apiReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { $apiReady = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $apiReady) {
    Write-Host "ERROR: API did not become healthy. Check: Get-Content `"$apiLog`" -Tail 40" -ForegroundColor Red
    exit 1
}
Write-Host "   OK API healthy"

# --- Build and start candidate UI ---
Write-Host ""
$uiDir = Join-Path $Candidate 'ui'
$uiLog = Join-Path $LogsDir 'ui.log'

# Kill any previous acceptance UI
$prevUiPid = (Get-Content (Join-Path $RunDir 'ui.pid') -ErrorAction SilentlyContinue)
if ($prevUiPid) {
    $_eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    taskkill /PID $prevUiPid /T /F 2>&1 | Out-Null
    $ErrorActionPreference = $_eap
    Remove-Item (Join-Path $RunDir 'ui.pid') -Force -ErrorAction SilentlyContinue
}

# The UI build/runtime never needs LiveKit or Gemini secrets. Strip them from
# this process's environment before invoking npm/next so neither the build
# nor the long-running UI server process ever holds them, even though
# npm/node do not dump arbitrary env vars today. Restored in `finally` below.
$uiSensitiveVars = @(
    'LIVEKIT_API_SECRET', 'LIVEKIT_API_KEY',
    'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GENERATIVE_AI_API_KEY', 'GOOGLE_AI_API_KEY',
    'OSS_JWT_SECRET', 'DATABASE_URL', 'REDIS_URL',
    'TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS',
    # Not secrets, but a NEXT_PUBLIC_BACKEND_URL inherited from the parent shell
    # (e.g. the dev stack's http://localhost:8001/8000) is inlined into the
    # client bundle at build time and would point this UI at another backend.
    # apiClient.ts then falls back to window.location.origin (this UI's own origin).
    'NEXT_PUBLIC_BACKEND_URL'
)
$savedUiVars = @{}
foreach ($v in $uiSensitiveVars) {
    $savedUiVars[$v] = [Environment]::GetEnvironmentVariable($v, 'Process')
    [Environment]::SetEnvironmentVariable($v, $null, 'Process')
}

$_uiPrevEAP = $ErrorActionPreference
try {
    # npm writes warnings to stderr; with 2>&1 and 'Stop' PowerShell 5.1 would
    # abort on a harmless warning. Success is judged by $LASTEXITCODE below.
    $ErrorActionPreference = 'Continue'
    if (-not (Test-Path (Join-Path $uiDir 'node_modules'))) {
        Write-Host "-> Installing UI dependencies (npm ci  -  first time, ~3-5 min)..."
        Push-Location $uiDir
        npm ci 2>&1 | Tee-Object (Join-Path $LogsDir 'npm-ci.log') | Select-String "warn|error|added" | Select-Object -Last 5
        if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: npm ci failed" -ForegroundColor Red; Pop-Location; exit 1 }
        Pop-Location
        Write-Host "   OK npm ci complete"
    }

    # next.config.ts (output: 'standalone') bakes BACKEND_URL into
    # .next/routes-manifest.json at build time. `next start` does NOT
    # re-resolve it from a live env var  -  confirmed against the running
    # .ui-verify build, whose manifest still reads 127.0.0.1:8014 from its
    # original build regardless of later env changes. So an existing
    # ui/.next from a prior run or a different port must be verified against
    # this run's $ApiPort, never trusted just because the directory exists  - 
    # otherwise the UI silently proxies to the wrong backend.
    $manifestPath = Join-Path $uiDir '.next\routes-manifest.json'
    $targetMarker = "127.0.0.1:$ApiPort"
    $needsBuild = -not (Test-Path (Join-Path $uiDir '.next'))
    if (-not $needsBuild) {
        if ((Test-Path $manifestPath) -and ((Get-Content $manifestPath -Raw) -match [regex]::Escape($targetMarker))) {
            Write-Host "   OK existing ui/.next already targets http://$targetMarker"
        } else {
            Write-Host "WARN: existing ui/.next does not target http://$targetMarker. Rebuilding..." -ForegroundColor Yellow
            Remove-Item (Join-Path $uiDir '.next') -Recurse -Force -ErrorAction SilentlyContinue
            $needsBuild = $true
        }
    }

    if ($needsBuild) {
        Write-Host "-> Building candidate UI (next build  -  ~2-3 min)..."
        Push-Location $uiDir
        $env:BACKEND_URL = "http://127.0.0.1:$ApiPort"
        npm run build 2>&1 | Tee-Object (Join-Path $LogsDir 'next-build.log') | Select-String "error|warn|Route" | Select-Object -Last 10
        $buildExit = $LASTEXITCODE
        Pop-Location
        if ($buildExit -ne 0) { Write-Host "ERROR: next build failed" -ForegroundColor Red; exit 1 }
        if (-not (Test-Path $manifestPath) -or ((Get-Content $manifestPath -Raw) -notmatch [regex]::Escape($targetMarker))) {
            Write-Host "ERROR: build completed but routes-manifest.json still does not reference $targetMarker. Aborting." -ForegroundColor Red
            exit 1
        }
        Write-Host "   OK next build complete and verified target http://$targetMarker"
    }

    Write-Host "-> Starting candidate UI on port $UiPort..."
    # BACKEND_URL goes through the process environment, not `set X=Y && ...` in
    # cmd.exe: that form keeps the space before `&&` as part of the value, so the
    # UI server's own calls to the API (e.g. /api/config/version) built an
    # invalid URL and reported the API as unavailable.
    $env:BACKEND_URL = "http://127.0.0.1:$ApiPort"
    # --hostname 127.0.0.1: without it Next binds every interface, exposing the
    # dashboard (where a provider key is typed) over plain HTTP to the LAN.
    $uiCmd  = "cd /d `"$uiDir`" && npx next start --hostname 127.0.0.1 --port $UiPort >> `"$uiLog`" 2>&1"
    $uiProc = Start-Process cmd.exe -ArgumentList '/c', $uiCmd -PassThru -WindowStyle Hidden
    Set-Content (Join-Path $RunDir 'ui.pid') $uiProc.Id
    Write-Host "   PID $($uiProc.Id) -> $uiLog"
    # Do not report RUNNING until the UI server answers AND can reach the API;
    # a fixed sleep left health-check.ps1 racing a UI that was still starting.
    $uiReady = $false
    for ($i = 1; $i -le 45; $i++) {
        try {
            $v = Invoke-RestMethod -Uri "http://127.0.0.1:$UiPort/api/config/version" -TimeoutSec 3 -ErrorAction Stop
            if ($v.api -and $v.api -ne 'unavailable') { $uiReady = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $uiReady) {
        Write-Host "ERROR: UI did not become ready on port $UiPort. See: $uiLog" -ForegroundColor Red
        exit 1
    }
    Write-Host "   OK UI ready and reaching the API"
}
finally {
    $ErrorActionPreference = $_uiPrevEAP
    # Restore secrets for the remainder of this script. Nothing after this
    # point currently needs them; kept predictable for future extension.
    foreach ($v in $uiSensitiveVars) {
        [Environment]::SetEnvironmentVariable($v, $savedUiVars[$v], 'Process')
    }
}

# --- Summary ---
Write-Host ""
Write-Host "======================================================"
Write-Host " Acceptance environment is RUNNING"
Write-Host "======================================================"
Write-Host " API        : http://127.0.0.1:$ApiPort"
Write-Host " UI         : http://127.0.0.1:$UiPort"
Write-Host " LiveKit    : ws://127.0.0.1:7880  (dev mode)"
Write-Host " Postgres   : 127.0.0.1:55444      (spx_voice_accept)"
Write-Host " Redis      : 127.0.0.1:6384"
Write-Host ""
Write-Host " Health     : .\accept\health-check.ps1"
Write-Host " Stop       : .\accept\stop-accept.ps1"
Write-Host " API log    : Get-Content `"$apiLog`" -Wait"
Write-Host " UI log     : Get-Content `"$uiLog`" -Wait"
Write-Host "======================================================"
Write-Host ""
Write-Host "NEXT STEP: Run .\accept\health-check.ps1 to verify all checks."
if ($dashboardKeyMode) {
    Write-Host "Dashboard-key mode: provider_unconfigured is EXPECTED until the owner saves"
    Write-Host "a Gemini key at http://127.0.0.1:$UiPort. Test Audio is refused until then."
}
Write-Host "Do not start Test Audio until the owner authorizes it."

} finally {
    # Clear all Gemini API key names from this process's environment regardless
    # of which exit path was taken above (Docker fail, migration fail, API fail,
    # UI fail, or normal completion). $geminiKeyNames is defined at the top of
    # this script (line ~70) and is in scope here.
    foreach ($k in $geminiKeyNames) {
        [Environment]::SetEnvironmentVariable($k, $null, 'Process')
    }
    [Environment]::SetEnvironmentVariable('TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS', $null, 'Process')
    # Everything loaded from .env.accept (OSS_JWT_SECRET, LIVEKIT_API_SECRET,
    # DATABASE_URL, ...) is for the child processes only; do not leave it behind.
    foreach ($n in @($loadedEnvNames)) { [Environment]::SetEnvironmentVariable($n, $null, 'Process') }
}
