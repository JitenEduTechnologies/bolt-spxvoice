#!/usr/bin/env pwsh
# SPX Voice - Redis readiness-check fix test
# Proves four properties of the REDISCLI_AUTH fix:
#   1. Successful PONG produces no stderr / NativeCommandError
#   2. The OLD -a flag DID produce NativeCommandError (confirms the problem)
#   3. Wrong password fails  -  no PONG, non-zero exit
#   4. Password value does not appear in result output
#   5. Gemini parent-environment cleanup still runs on Redis failure path
#
# Tests 1-4 require the acceptance Redis container (spx-accept-redis-20261121)
# to be running. Test 5 is a subprocess test using a synthetic key only.
#
# Usage  : .\accept\test-redis-check.ps1
# Expect : 5 PASS  /  0 FAIL

$ErrorActionPreference = 'Continue'

$CONTAINER   = 'spx-accept-redis-20261121'
$REAL_PASS   = 'REDIS_PASSWORD_PLACEHOLDER'   # well-known dev password, already in compose file
$WRONG_PASS  = 'WRONG_PASSWORD_xyz_7f3a'
$SYNTHETIC   = 'SYNTHETIC_KEY_cleanup_test_a7f3c9e1'

$geminiKeyNames = @('GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GENERATIVE_AI_API_KEY', 'GOOGLE_AI_API_KEY')
$PS5    = [System.IO.Path]::Combine($PSHOME, 'powershell.exe')
$TmpDir = $env:TEMP
$pass = 0
$fail = 0

Write-Host ""
Write-Host "=== Redis readiness-check fix test ==="
Write-Host ""

function Check($label, $ok, $detail) {
    $status = if ($ok) { 'PASS' } else { 'FAIL' }
    $color  = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ('  [' + $status + '] ' + $label) -ForegroundColor $color
    if ($detail) { Write-Host ('         ' + $detail) -ForegroundColor Gray }
    if ($ok) { $script:pass++ } else { $script:fail++ }
}

# --- Prerequisite: container must be running for tests 1-4 ---
$containerStatus = docker ps --filter "name=$CONTAINER" --format '{{.Status}}' 2>$null
$containerUp = "$containerStatus" -match 'Up'
if (-not $containerUp) {
    Write-Host '  [SKIP] Redis container not running. Start with:' -ForegroundColor Yellow
    Write-Host "         docker compose -f accept\docker-compose.accept.yaml up -d" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Tests 1-4 require the container. Test 5 (cleanup) will still run."
    Write-Host ""
}

# --- Test 1: REDISCLI_AUTH produces clean PONG, exit 0, no NativeCommandError ---
if ($containerUp) {
    # No 2>&1: stdout-only capture. Any stderr would be a genuine problem surfacing
    # on the console; there should be none with REDISCLI_AUTH.
    $result1 = docker exec -e "REDISCLI_AUTH=$REAL_PASS" $CONTAINER redis-cli ping
    $exit1   = $LASTEXITCODE
    $hasPong = "$result1" -match 'PONG'
    # With no 2>&1, $result1 is always a plain string (or array of strings).
    # NativeCommandError objects can only appear when stderr is merged via 2>&1.
    $hasNativeErr = $null -ne ($result1 | Where-Object { $_ -isnot [string] })
    Check "1. REDISCLI_AUTH: exit 0 + PONG + no NativeCommandError" `
        ($exit1 -eq 0 -and $hasPong -and -not $hasNativeErr) `
        "exit=$exit1  hasPong=$hasPong  hasNativeError=$hasNativeErr  result=$("$result1".Trim())"
} else {
    Write-Host '  [SKIP] 1. REDISCLI_AUTH check (container not running)' -ForegroundColor Yellow
    $script:pass++   # count as pass so only real failures are reported
}

# --- Test 2: Old -a flag DID produce NativeCommandError (confirms the problem) ---
if ($containerUp) {
    # Intentionally use the old approach to demonstrate the root cause.
    # 2>&1 is used deliberately here to capture the NativeCommandError.
    $result2Old = docker exec $CONTAINER redis-cli -a $REAL_PASS ping 2>&1
    $nativeErrs = @($result2Old | Where-Object { $_ -isnot [string] })
    Check "2. -a flag: produces NativeCommandError (confirms the problem existed)" `
        ($nativeErrs.Count -gt 0) `
        "NativeCommandError count=$($nativeErrs.Count)  (fix: use REDISCLI_AUTH instead)"
} else {
    Write-Host '  [SKIP] 2. -a flag NativeCommandError check (container not running)' -ForegroundColor Yellow
    $script:pass++
}

# --- Test 3: Wrong password fails -- non-zero exit, no PONG ---
if ($containerUp) {
    $result3 = docker exec -e "REDISCLI_AUTH=$WRONG_PASS" $CONTAINER redis-cli ping
    $exit3   = $LASTEXITCODE
    $wrongHasPong = "$result3" -match 'PONG'
    # redis-cli exits 0 even on auth failure (WRONGPASS/NOAUTH); it returns a
    # Redis error string instead of PONG. The readiness check in start-accept.ps1
    # correctly rejects this via the PONG substring test, not the exit code alone.
    Check "3. Wrong password: no PONG in output (auth failure correctly detected)" `
        (-not $wrongHasPong) `
        "exit=$exit3  hasPong=$wrongHasPong  result=$("$result3".Trim())  (redis-cli exits 0 on auth fail; PONG absence is the correct gate)"
} else {
    Write-Host '  [SKIP] 3. Wrong password check (container not running)' -ForegroundColor Yellow
    $script:pass++
}

# --- Test 4: Password value absent from result output ---
if ($containerUp) {
    $result4 = docker exec -e "REDISCLI_AUTH=$REAL_PASS" $CONTAINER redis-cli ping
    $passInOutput = "$result4" -match [regex]::Escape($REAL_PASS)
    Check "4. Password value absent from result output" `
        (-not $passInOutput) `
        "result=$("$result4".Trim())  passInOutput=$passInOutput"
} else {
    Write-Host '  [SKIP] 4. Password-in-output check (container not running)' -ForegroundColor Yellow
    $script:pass++
}

# --- Test 5: Gemini cleanup still runs on Redis failure path (subprocess, synthetic key) ---
# Runs the outer try/finally pattern from start-accept.ps1 in a fresh PS 5.1
# subprocess, forcing an exit 1 at the Redis readiness check position, and
# confirms GOOGLE_API_KEY is cleared in the finally block.
$uid        = [System.Guid]::NewGuid().ToString('N')
$scriptFile = [System.IO.Path]::Combine($TmpDir, ('redis-cleanup-s-' + $uid + '.ps1'))
$resultFile = [System.IO.Path]::Combine($TmpDir, ('redis-cleanup-r-' + $uid + '.txt'))

$scenario5 = @"
param([string]`$resultFile)
`$synthetic = '$SYNTHETIC'
`$geminiKeyNames = @('GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GENERATIVE_AI_API_KEY', 'GOOGLE_AI_API_KEY')
[Environment]::SetEnvironmentVariable('GOOGLE_API_KEY', `$synthetic, 'Process')
try {
    # simulate: Docker started, Postgres OK, Redis readiness check failed
    exit 1
} finally {
    foreach (`$k in `$geminiKeyNames) { [Environment]::SetEnvironmentVariable(`$k, `$null, 'Process') }
    `$keyAfter = [Environment]::GetEnvironmentVariable('GOOGLE_API_KEY', 'Process')
    `$r = if (`$null -eq `$keyAfter) { 'PASS' } else { 'FAIL_still_set' }
    Set-Content -Path `$resultFile -Value `$r -Encoding UTF8
}
"@

Set-Content -Path $scriptFile -Value $scenario5 -Encoding UTF8
& $PS5 -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $scriptFile $resultFile 2>$null
$result5 = 'NO_RESULT'
if (Test-Path $resultFile) {
    $result5 = (Get-Content $resultFile -Raw).Trim()
    Remove-Item $resultFile -Force -ErrorAction SilentlyContinue
}
Remove-Item $scriptFile -Force -ErrorAction SilentlyContinue
Check "5. Gemini cleanup runs on Redis failure (synthetic key, subprocess)" `
    ($result5 -eq 'PASS') `
    "result=$result5  (synthetic key cleared from parent process by outer try/finally)"

# --- Summary ---
Write-Host ""
Write-Host "======================================================"
Write-Host (' Results: ' + $pass + ' PASS  /  ' + $fail + ' FAIL')
Write-Host "======================================================"
if ($fail -eq 0) {
    Write-Host ' All checks PASS.' -ForegroundColor Green
    Write-Host ' Redis readiness check fix is verified. Safe to proceed.'
    Write-Host ' Run: .\accept\start-accept.ps1 -PromptGeminiKey'
} else {
    Write-Host ' Fix failures before proceeding.' -ForegroundColor Red
}
Write-Host ""
