#!/usr/bin/env pwsh
# SPX Voice  -  Acceptance environment health check
# Runs all pre-Test-Audio verification checks.
# No secrets are printed, read into a variable and echoed, or persisted by
# this script at any point.
# Usage: .\accept\health-check.ps1

. (Join-Path $PSScriptRoot 'lib-accept.ps1')
$ports    = Get-AcceptPorts -EnvFile (Join-Path $PSScriptRoot '.env.accept')
$ApiPort  = $ports.Api
$UiPort   = $ports.Ui
$BaseApi  = "http://127.0.0.1:$ApiPort"

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'logs') | Out-Null

$pass = 0
$fail = 0
$skip = 0

function Check($label, $ok, $detail) {
    $status = if ($ok) { "PASS" } else { "FAIL" }
    $color  = if ($ok) { "Green" } else { "Red" }
    Write-Host ("  [{0,-4}] {1}" -f $status, $label) -ForegroundColor $color
    if ($detail) { Write-Host "         $detail" -ForegroundColor Gray }
    if ($ok) { $script:pass++ } else { $script:fail++ }
}

function Skip($label, $detail) {
    Write-Host ("  [{0,-4}] {1}" -f "SKIP", $label) -ForegroundColor Yellow
    if ($detail) { Write-Host "         $detail" -ForegroundColor Gray }
    $script:skip++
}

function Warn($label, $detail) {
    Write-Host ("  [{0,-4}] {1}" -f "WARN", $label) -ForegroundColor Yellow
    if ($detail) { Write-Host "         $detail" -ForegroundColor Gray }
}

Write-Host ""
Write-Host "=== SPX Voice Acceptance Health Check ==="
Write-Host ""

# 1. Candidate API health
# Note: the /api/v1/health route returns a hardcoded status="ok" whenever it
# responds at all (api/routes/health.py legacy_health()); this check confirms
# the endpoint is reachable and returns that literal value, nothing more.
try {
    $h = Invoke-RestMethod -Uri "$BaseApi/api/v1/health" -TimeoutSec 5
    Check "1. Candidate API health" ($h.status -eq "ok") "status=$($h.status) voice_runtime=$($h.voice_runtime)"
} catch {
    Check "1. Candidate API health" $false "ERROR: $($_.Exception.Message)"
}

# 2. LiveKit CONFIGURED (not the same claim as "worker running"  -  see #4)
# livekit_enabled = is_livekit_runtime() AND livekit_configured(), i.e. URL +
# key + secret are all non-empty. It says nothing about whether the worker
# process has actually started.
try {
    $h = Invoke-RestMethod -Uri "$BaseApi/api/v1/health" -TimeoutSec 5
    Check "2. LiveKit CONFIGURED (livekit_enabled=true)" ($h.livekit_enabled -eq $true) "livekit_enabled=$($h.livekit_enabled)  (this is 'configured', not 'worker running')"
} catch {
    Check "2. LiveKit CONFIGURED (livekit_enabled=true)" $false "Could not reach health endpoint"
}

# 3. Frontend and backend readiness agree  -  AND frontend actually targets
#    this run's API port. Next.js (output: 'standalone') bakes BACKEND_URL
#    into .next/routes-manifest.json at build time; next start does not
#    re-resolve it from env. A stale build pointed at a DIFFERENT backend
#    (e.g. the unrelated instance on port 8014) could coincidentally still
#    report livekitEnabled=true and produce a false agreement, so the baked
#    destination is checked directly rather than trusting the UI's own
#    response alone.
$manifestPath = Join-Path $PSScriptRoot '..\ui\.next\routes-manifest.json'
$targetMarker = "127.0.0.1:$ApiPort"
$manifestOk = (Test-Path $manifestPath) -and ((Get-Content $manifestPath -Raw) -match [regex]::Escape($targetMarker))
if (-not $manifestOk) {
    Check "3. Frontend/backend agree livekit" $false "ui/.next/routes-manifest.json does not reference $targetMarker  -  UI build is stale or targets the wrong API. Delete ui/.next and rerun start-accept.ps1."
} else {
    try {
        $cfg  = Invoke-RestMethod -Uri "http://127.0.0.1:$UiPort/api/config/version" -TimeoutSec 5
        $apiH = Invoke-RestMethod -Uri "$BaseApi/api/v1/health" -TimeoutSec 5
        $agree = ($cfg.livekitEnabled -eq $true) -and ($apiH.livekit_enabled -eq $true)
        Check "3. Frontend/backend agree livekit" $agree "ui.livekitEnabled=$($cfg.livekitEnabled)  api.livekit_enabled=$($apiH.livekit_enabled)  (manifest confirmed targeting $targetMarker)"
    } catch {
        Check "3. Frontend/backend agree livekit" $false "ERROR: $($_.Exception.Message)"
    }
}

# 4. Worker RUNNING  -  distinct claim from "CONFIGURED" (#2). The only field
#    that reports worker_running is on the authenticated /livekit/settings
#    route (Depends(get_user)); there is no unauthenticated equivalent. This
#    check cannot be satisfied by a fake bearer token, so without a real
#    session it is reported as SKIP, never as PASS  -  a 401 here proves only
#    that auth is enforced, not that the worker is running.
try {
    $lks = Invoke-RestMethod -Uri "$BaseApi/api/v1/livekit/settings" -TimeoutSec 5 -Headers @{Authorization = "Bearer skip-for-health-check"}
    Check "4. Worker RUNNING (worker_running=true)" ($lks.worker_running -eq $true) "worker_running=$($lks.worker_running) worker_pid=$($lks.worker_pid)"
} catch {
    $code = $null
    if ($_.Exception.Response) { $code = $_.Exception.Response.StatusCode.value__ }
    if ($code -eq 401 -or $code -eq 403) {
        Skip "4. Worker RUNNING  -  cannot verify without a real session" "HTTP $code. Log in at http://127.0.0.1:$UiPort, then GET /api/v1/livekit/settings with that session's cookie/token and confirm worker_running=true before Test Audio. Do NOT treat check #2 (CONFIGURED) as proof the worker is running."
    } else {
        Check "4. Worker RUNNING (worker_running=true)" $false "ERROR: $($_.Exception.Message)"
    }
}

# 5. Model-provider (Gemini) readiness, reported by the API itself.
#    provider_status comes from /api/v1/health. "provider_unconfigured" is the
#    EXPECTED state on a fresh stack: the owner saves the key in the dashboard
#    and Test Audio stays blocked until then. It is a pass for this check (the
#    stack is healthy and honest about its state) but is NOT provider readiness:
#    the summary below says so explicitly. "unknown" or a missing field fails.
#    No key, key fragment or env value is read or printed here.
$providerStatus = $null
try {
    $ph = Invoke-RestMethod -Uri "$BaseApi/api/v1/health" -TimeoutSec 5
    $providerStatus = $ph.provider_status
} catch { }
$providerKnown = @('configured', 'provider_unconfigured') -contains $providerStatus
Check "5. Provider status reported by API" $providerKnown "provider_status=$providerStatus  (provider_unconfigured is expected until the owner saves a key in the dashboard)"

# 6. DeepSeek, Cartesia, Deepgram visible in candidate source
$rtFile = Join-Path $PSScriptRoot '..\api\services\livekit\runtime_config.py'
$deepseekOk = $false; $cartesiaOk = $false; $deepgramOk = $false
if (Test-Path $rtFile) {
    $rtContent = Get-Content $rtFile -Raw
    $deepseekOk = $rtContent -match '"deepseek"'
    $cartesiaOk = $rtContent -match '"cartesia"'
    $deepgramOk = $rtContent -match '"deepgram"'
}
Check "6a. DeepSeek in LIVEKIT_SUPPORTED_PROVIDERS" $deepseekOk "runtime_config.py"
Check "6b. Cartesia in LIVEKIT_SUPPORTED_PROVIDERS" $cartesiaOk "runtime_config.py"
Check "6c. Deepgram in LIVEKIT_SUPPORTED_PROVIDERS" $deepgramOk "runtime_config.py"

# 7. Workflow 1 configuration
try {
    $resp = Invoke-WebRequest -Uri "$BaseApi/api/v1/workflow/fetch/1" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    Check "7. Workflow 1 endpoint reachable" $true "HTTP $($resp.StatusCode)"
} catch {
    $code = $null
    if ($_.Exception.Response) { $code = $_.Exception.Response.StatusCode.value__ }
    if ($code -eq 401 -or $code -eq 403) {
        Skip "7. Workflow 1  -  cannot verify without a real session" "HTTP $code  -  log in once, then re-check workflow 1's published configuration."
    } elseif ($code -eq 404) {
        Check "7. Workflow 1 exists" $false "HTTP 404  -  first login may not have run yet; log in once at http://127.0.0.1:$UiPort to trigger ensure_default_user_setup"
    } else {
        Check "7. Workflow 1 endpoint reachable" $false "HTTP $code : $($_.Exception.Message)"
    }
}

# 8. Startup failure DB-visibility (source presence check only)
# This confirms _record_livekit_startup_failure exists and writes to
# workflow_run.logs.livekit_events / annotations.livekit_startup_error. It
# does NOT prove the browser will show anything during a live call  -  that
# function persists to the database only and does not push a message over
# the LiveKit data channel. If Test Audio hangs with no greeting, a failure
# may still have occurred; check the workflow run's logs via the API/DB, not
# the browser, which will show no error at all in that case.
$wFile = Join-Path $PSScriptRoot '..\api\services\livekit\worker.py'
$hasRecord = $false
if (Test-Path $wFile) {
    $hasRecord = (Get-Content $wFile -Raw) -match '_record_livekit_startup_failure'
}
Check "8. Startup failure recorded to DB (source check only  -  NOT browser-visible)" $hasRecord "worker.py contains _record_livekit_startup_failure; browser receives no live notification on this path"

# 9. Provider-exception log sanitization  -  FIXED 2026-11-21, gated on tests
# actually passing, not just present. _record_livekit_startup_failure and
# LiveKitWorkflowAgent._speak_opening's opening_audio_cache_failed handler
# both now go through category-only redaction (exception class + stable
# error code + stage; see api/services/livekit/worker.py:_safe_error_text,
# _startup_error_code). api/tests/test_livekit_startup_redaction.py injects
# synthetic Gemini-key, LiveKit-secret, bearer-token, credential-URL,
# authorization-header, JSON-body and multiline-exception markers and
# asserts none survive into logs, persisted events, or annotations. This
# check actually RUNS that file (not a source-presence guess) so a future
# regression that reintroduces str(exc) fails this check immediately rather
# than silently reporting PASS. Requires the candidate's Python venv.
$py = 'D:\spx-voice-mvp\.venv\Scripts\python.exe'
$testFile = Join-Path $PSScriptRoot '..\api\tests\test_livekit_startup_redaction.py'
if (-not (Test-Path $py) -or -not (Test-Path $testFile)) {
    Check "9. Provider-exception sanitization tests pass" $false "Python venv or test file not found  -  cannot verify. Treat as unverified, not as fixed."
} else {
    $repoRoot = Join-Path $PSScriptRoot '..'
    Push-Location $repoRoot
    $savedPythonPath = $env:PYTHONPATH
    $savedDbUrl = $env:DATABASE_URL
    $savedRedisUrl = $env:REDIS_URL
    $savedEnvMode = $env:ENVIRONMENT
    try {
        $env:PYTHONPATH = (Resolve-Path $repoRoot).Path
        # These tests are pure unit tests (mocked logger/DB client, no real
        # connection) but api/conftest.py reads DATABASE_URL/REDIS_URL at
        # import time; dummy well-formed values satisfy that without
        # touching any real database.
        if (-not $env:DATABASE_URL) { $env:DATABASE_URL = 'postgresql+asyncpg://postgres:postgres@127.0.0.1:55444/spx_voice_accept' }
        if (-not $env:REDIS_URL) { $env:REDIS_URL = 'redis://:REDIS_PASSWORD_PLACEHOLDER@127.0.0.1:6384/0' }
        $env:ENVIRONMENT = 'test'
        $testOutput = & $py -m pytest 'api\tests\test_livekit_startup_redaction.py' -q 2>&1
        $testsOk = $LASTEXITCODE -eq 0
        $summaryLine = ($testOutput | Select-String -Pattern '\d+ passed' | Select-Object -Last 1).ToString()
        Check "9. Provider-exception sanitization tests pass" $testsOk "$summaryLine  (full output: accept/logs/sanitization-tests.log)"
        Set-Content -Path (Join-Path $PSScriptRoot 'logs\sanitization-tests.log') -Value $testOutput -ErrorAction SilentlyContinue
    } finally {
        $env:PYTHONPATH = $savedPythonPath
        $env:DATABASE_URL = $savedDbUrl
        $env:REDIS_URL = $savedRedisUrl
        $env:ENVIRONMENT = $savedEnvMode
        Pop-Location
    }
}

# --- Summary ---
Write-Host ""
Write-Host "======================================================"
Write-Host " Results: $pass PASS  /  $fail FAIL  /  $skip SKIP (not counted as pass)"
Write-Host "======================================================"

if ($providerStatus -eq 'provider_unconfigured') {
    Write-Host " Provider: provider_unconfigured. Save a Gemini key in the dashboard" -ForegroundColor Yellow
    Write-Host " (http://127.0.0.1:$UiPort); Test Audio is refused until a key is saved." -ForegroundColor Yellow
}
if ($fail -eq 0 -and $skip -eq 0) {
    Write-Host " All checks PASS. Await owner authorization before Test Audio." -ForegroundColor Green
} elseif ($fail -eq 0) {
    Write-Host " No FAILs, but $skip check(s) could not be verified without a real login session." -ForegroundColor Yellow
    Write-Host " Log in once at http://127.0.0.1:$UiPort and re-verify worker_running and workflow 1 manually before Test Audio."
} else {
    Write-Host " Fix FAILs above before proceeding." -ForegroundColor Red
    Write-Host ""
    Write-Host " Common fixes:"
    Write-Host "   Check 1 fails : API process not running or not healthy  -  see accept/logs/api.log"
    Write-Host "   Check 2 fails : LIVEKIT_URL/KEY/SECRET missing or LiveKit container not up"
    Write-Host "   Check 3 fails : UI build is stale/wrong-target  -  delete ui/.next and rerun start-accept.ps1"
    Write-Host "   Check 5 fails : API did not report provider_status (DB unreachable, or an old build)"
    Write-Host "   Check 6 fails : Using production source instead of remediation candidate"
    Write-Host "   Check 7 fails (404) : Log in once at http://127.0.0.1:$UiPort to trigger first-run setup"
    Write-Host "   Check 9 fails : Secret-redaction regression  -  see accept/logs/sanitization-tests.log. Do not proceed to Test Audio until this passes."
}
Write-Host ""
Write-Host "No secret value was printed, logged, or persisted by this script."
Write-Host ""
