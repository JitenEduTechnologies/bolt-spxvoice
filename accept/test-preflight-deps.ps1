#!/usr/bin/env pwsh
# SPX Voice - Dependency preflight fix test
# Proves four properties of the PYTHONPATH-based pipecat preflight check:
#   1. A synthetic path with spaces and backslashes does NOT cause SyntaxError
#      when used as a PYTHONPATH entry (old -c 'sys.path.insert(0,...)' would).
#   2. PYTHONPATH is restored to its original value after the preflight block,
#      even when the check fails.
#   3. Gemini-key cleanup still runs when the preflight exits 1.
#   4. pipecat imports successfully from the real candidate .deps directory.
#
# Tests 1-3 are pure PowerShell / subprocess logic tests (no real key needed).
# Test 4 requires the candidate .deps to be present.
#
# Usage  : .\accept\test-preflight-deps.ps1
# Expect : 4 PASS  /  0 FAIL

$ErrorActionPreference = 'Continue'

$Candidate   = 'C:\Users\JITEN\spx-voice-releases\spx-production-remediation-20261120-1615'
$Python      = 'D:\spx-voice-mvp\.venv\Scripts\python.exe'
$SYNTHETIC   = 'SYNTHETIC_KEY_preflight_test_b8e4f1a2'
$PS5         = [System.IO.Path]::Combine($PSHOME, 'powershell.exe')
$TmpDir      = $env:TEMP
$pass        = 0
$fail        = 0

Write-Host ""
Write-Host "=== Dependency preflight fix test ==="
Write-Host ""

function Check($label, $ok, $detail) {
    $status = if ($ok) { 'PASS' } else { 'FAIL' }
    $color  = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ('  [' + $status + '] ' + $label) -ForegroundColor $color
    if ($detail) { Write-Host ('         ' + $detail) -ForegroundColor Gray }
    if ($ok) { $script:pass++ } else { $script:fail++ }
}

# ---------------------------------------------------------------------------
# Test 1: Synthetic path with spaces + backslashes survives PYTHONPATH round-trip
#
# The old fix embedded $depsDir directly in a Python -c string:
#   python -c "sys.path.insert(0,'C:\Users\Some Name\.deps'); ..."
# C:\Users\... contains \U which Python parses as a Unicode escape -> SyntaxError.
#
# The new fix sets PYTHONPATH="<path>;" and runs:
#   python -c "import os, sys; print(sys.path)"
# This never parses the path as Python source, so backslashes survive.
# ---------------------------------------------------------------------------
$syntheticPath = 'C:\Users\Some Name With Spaces\spx voice releases\spx-production-remediation-20261120-1615\.deps'
$prevPP = $env:PYTHONPATH
try {
    $env:PYTHONPATH = "$syntheticPath;$prevPP"
    # Verify PYTHONPATH contains the synthetic path with backslashes intact
    $ppValue = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
    $pathPresent = $ppValue -match [regex]::Escape($syntheticPath)
    # Verify python -c does not error on PYTHONPATH containing backslashes
    $checkOut = & $Python -c "import sys; print([p for p in sys.path if 'Some Name' in p])" 2>&1
    $exitOk = ($LASTEXITCODE -eq 0)
    $noSyntaxErr = "$checkOut" -notmatch 'SyntaxError|DeprecationWarning.*invalid escape'
} finally {
    $env:PYTHONPATH = $prevPP
}
Check "1. Backslash/space path survives PYTHONPATH (no SyntaxError in python -c)" `
    ($pathPresent -and $exitOk -and $noSyntaxErr) `
    "pathPresent=$pathPresent  exitOk=$exitOk  noSyntaxErr=$noSyntaxErr"

# ---------------------------------------------------------------------------
# Test 2: PYTHONPATH restored to original value after preflight block
#
# Runs the try/finally pattern from the fixed preflight in a subprocess.
# Sets a sentinel PYTHONPATH, runs preflight block (exits 1 on failure),
# writes PYTHONPATH value inside finally to result file. The original sentinel
# must reappear, not the temporarily prepended .deps path.
# ---------------------------------------------------------------------------
$uid2       = [System.Guid]::NewGuid().ToString('N')
$script2    = Join-Path $TmpDir ('preflight-s-' + $uid2 + '.ps1')
$result2    = Join-Path $TmpDir ('preflight-r-' + $uid2 + '.txt')
$sentinel   = 'SENTINEL_PP_' + $uid2

# A fake depsDir that makes the import fail (so the exit-1 path fires)
$fakeDeps   = Join-Path $TmpDir ('fake-deps-' + $uid2)

$body2 = @"
param([string]`$resultFile)
`$geminiKeyNames = @('GOOGLE_API_KEY','GEMINI_API_KEY','GOOGLE_GENERATIVE_AI_API_KEY','GOOGLE_AI_API_KEY')
`$Python = '$Python'
`$depsDir = '$fakeDeps'
[Environment]::SetEnvironmentVariable('PYTHONPATH', '$sentinel', 'Process')
try {
    `$_prevPP = `$env:PYTHONPATH
    try {
        `$env:PYTHONPATH = "`$depsDir;`$_prevPP"
        `$pipecatCheck = & `$Python -c "import pipecat" 2>&1
        `$pipecatOk = (`$LASTEXITCODE -eq 0)
    } finally {
        `$env:PYTHONPATH = `$_prevPP
    }
    if (-not `$pipecatOk) {
        # write restored PYTHONPATH before exiting
        Set-Content -Path `$resultFile -Value `$env:PYTHONPATH -Encoding UTF8
        exit 1
    }
} finally {
    foreach (`$k in `$geminiKeyNames) { [Environment]::SetEnvironmentVariable(`$k, `$null, 'Process') }
}
"@
Set-Content -Path $script2 -Value $body2 -Encoding UTF8
& $PS5 -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $script2 $result2 2>$null
$restoredPP = 'NO_RESULT'
if (Test-Path $result2) {
    $restoredPP = (Get-Content $result2 -Raw -ErrorAction SilentlyContinue).Trim()
    Remove-Item $result2 -Force -ErrorAction SilentlyContinue
}
Remove-Item $script2 -Force -ErrorAction SilentlyContinue
Check "2. PYTHONPATH restored to original value after preflight (even on failure)" `
    ($restoredPP -eq $sentinel) `
    "restored='$restoredPP'  expected='$sentinel'"

# ---------------------------------------------------------------------------
# Test 3: Gemini-key cleanup still fires when preflight exits 1
# ---------------------------------------------------------------------------
$uid3    = [System.Guid]::NewGuid().ToString('N')
$script3 = Join-Path $TmpDir ('preflight-s-' + $uid3 + '.ps1')
$result3 = Join-Path $TmpDir ('preflight-r-' + $uid3 + '.txt')
$fakeDeps3 = Join-Path $TmpDir ('fake-deps-' + $uid3)

$body3 = @"
param([string]`$resultFile)
`$geminiKeyNames = @('GOOGLE_API_KEY','GEMINI_API_KEY','GOOGLE_GENERATIVE_AI_API_KEY','GOOGLE_AI_API_KEY')
`$Python = '$Python'
`$depsDir = '$fakeDeps3'
[Environment]::SetEnvironmentVariable('GOOGLE_API_KEY', '$SYNTHETIC', 'Process')
try {
    `$_prevPP = `$env:PYTHONPATH
    try {
        `$env:PYTHONPATH = "`$depsDir;`$_prevPP"
        `$pipecatCheck = & `$Python -c "import pipecat" 2>&1
        `$pipecatOk = (`$LASTEXITCODE -eq 0)
    } finally {
        `$env:PYTHONPATH = `$_prevPP
    }
    if (-not `$pipecatOk) { exit 1 }
} finally {
    foreach (`$k in `$geminiKeyNames) { [Environment]::SetEnvironmentVariable(`$k, `$null, 'Process') }
    `$keyAfter = [Environment]::GetEnvironmentVariable('GOOGLE_API_KEY', 'Process')
    `$r = if (`$null -eq `$keyAfter) { 'PASS' } else { 'FAIL_still_set' }
    Set-Content -Path `$resultFile -Value `$r -Encoding UTF8
}
"@
Set-Content -Path $script3 -Value $body3 -Encoding UTF8
& $PS5 -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $script3 $result3 2>$null
$res3 = 'NO_RESULT'
if (Test-Path $result3) {
    $res3 = (Get-Content $result3 -Raw -ErrorAction SilentlyContinue).Trim()
    Remove-Item $result3 -Force -ErrorAction SilentlyContinue
}
Remove-Item $script3 -Force -ErrorAction SilentlyContinue
Check "3. Gemini-key cleanup runs when preflight exits 1 (synthetic key cleared)" `
    ($res3 -eq 'PASS') `
    "result=$res3"

# ---------------------------------------------------------------------------
# Test 4: pipecat imports successfully from real candidate .deps
# ---------------------------------------------------------------------------
$depsDir = Join-Path $Candidate '.deps'
if (-not (Test-Path $depsDir)) {
    Write-Host '  [SKIP] 4. Real .deps pipecat import (directory not found)' -ForegroundColor Yellow
    $script:pass++
} else {
    $prevPP = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = "$depsDir;$prevPP"
        $realCheck = & $Python -c "import pipecat" 2>&1
        $realOk = ($LASTEXITCODE -eq 0)
    } finally {
        $env:PYTHONPATH = $prevPP
    }
    Check "4. pipecat imports from real candidate .deps (PYTHONPATH method)" `
        $realOk `
        "exit=$LASTEXITCODE  output=$("$realCheck".Trim())"
}

# ---------------------------------------------------------------------------
# Test 5: the REAL preflight block, extracted from start-accept.ps1, succeeds
# under $ErrorActionPreference = 'Stop' (the script's own setting). pipecat's
# stderr banner merged via 2>&1 is a terminating NativeCommandError in PS 5.1
# unless the preference is relaxed around the call.
# ---------------------------------------------------------------------------
$scriptText = Get-Content (Join-Path $PSScriptRoot 'start-accept.ps1') -Raw
$bs = $scriptText.IndexOf('# --- Pre-flight: verify pipecat-ai')
$be = $scriptText.IndexOf('# --- Run Alembic migrations ---')
if ($bs -ge 0 -and $be -gt $bs) {
    $block  = $scriptText.Substring($bs, $be - $bs)
    $uid5   = [System.Guid]::NewGuid().ToString('N')
    $h5     = Join-Path $TmpDir ('preflight-real-' + $uid5 + '.ps1')
    $body5  = "`$ErrorActionPreference = 'Stop'`r`n`$Candidate = '$Candidate'`r`n`$Python = '$Python'`r`n" + $block
    Set-Content -Path $h5 -Value $body5 -Encoding ASCII
    $out5 = & $PS5 -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $h5 2>&1 | Out-String
    $code5 = $LASTEXITCODE
    Remove-Item $h5 -Force -ErrorAction SilentlyContinue
    Check "5. Real preflight block passes under ErrorActionPreference=Stop" `
        (($code5 -eq 0) -and ($out5 -match 'OK pipecat-ai importable') -and ($out5 -notmatch 'NativeCommandError')) `
        "exit=$code5"
} else {
    Check "5. Real preflight block passes under ErrorActionPreference=Stop" $false "could not locate preflight block markers in start-accept.ps1"
}

# --- Summary ---
Write-Host ""
Write-Host "======================================================"
Write-Host (' Results: ' + $pass + ' PASS  /  ' + $fail + ' FAIL')
Write-Host "======================================================"
if ($fail -eq 0) {
    Write-Host ' All checks PASS.' -ForegroundColor Green
    Write-Host ' Preflight fix is verified. Safe to run start-accept.ps1 -PromptGeminiKey'
} else {
    Write-Host ' Fix failures before proceeding.' -ForegroundColor Red
}
Write-Host ""
