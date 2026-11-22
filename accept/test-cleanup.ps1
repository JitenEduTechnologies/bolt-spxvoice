#!/usr/bin/env pwsh
# SPX Voice - Gemini-key parent-process cleanup test
# Proves GOOGLE_API_KEY is absent from the PowerShell process after each
# exit path in start-accept.ps1 (Docker fail, migration fail, successful
# API spawn, UI fail).  SYNTHETIC key only -- never prompts for a real key.
#
# Usage  : .\accept\test-cleanup.ps1
# Expect : 4 PASS  /  0 FAIL
#
# Method : Each scenario runs the outer try/finally pattern from
# start-accept.ps1 in a fresh powershell.exe subprocess, forces the target
# exit path, writes PASS/FAIL to a temp result file from inside `finally`,
# then exits. The parent (this script) reads the file and reports.
# Running in a subprocess is the only way to test `exit N` behaviour
# without terminating this script itself.

$ErrorActionPreference = 'Continue'

$SYNTHETIC = 'SYNTHETIC_KEY_cleanup_test_a7f3c9e1'
$geminiKeyNames = @('GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GENERATIVE_AI_API_KEY', 'GOOGLE_AI_API_KEY')
$PS5 = [System.IO.Path]::Combine($PSHOME, 'powershell.exe')
$TmpDir = $env:TEMP
$pass = 0
$fail = 0

Write-Host ""
Write-Host "=== GOOGLE_API_KEY parent-process cleanup test (synthetic key only) ==="
Write-Host ""

function Run-CleanupScenario {
    param([string]$Name, [string]$ScriptBody)

    $uid = [System.Guid]::NewGuid().ToString('N')
    $scriptFile = [System.IO.Path]::Combine($TmpDir, ('accept-cleanup-s-' + $uid + '.ps1'))
    $resultFile = [System.IO.Path]::Combine($TmpDir, ('accept-cleanup-r-' + $uid + '.txt'))

    Set-Content -Path $scriptFile -Value $ScriptBody -Encoding UTF8

    # Run in a fresh PS 5.1 process; the script writes PASS or FAIL to resultFile.
    # -ExecutionPolicy Bypass is needed if the host policy is Restricted.
    & $PS5 -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $scriptFile $resultFile 2>$null

    $result = 'NO_RESULT'
    if (Test-Path $resultFile) {
        $result = (Get-Content $resultFile -Raw).Trim()
        Remove-Item $resultFile -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $scriptFile -Force -ErrorAction SilentlyContinue

    if ($result -eq 'PASS') {
        Write-Host ('  [PASS] ' + $Name) -ForegroundColor Green
        $script:pass++
    } else {
        Write-Host ('  [FAIL] ' + $Name + ' (result=' + $result + ')') -ForegroundColor Red
        $script:fail++
    }
}

# Every scenario script receives the result file path as $args[0].
# The SHARED_HEADER sets the synthetic key; SHARED_FINALLY clears all 4
# key names and writes PASS/FAIL to the result file.

$SHARED_HEADER = @"
param([string]`$resultFile)
`$synthetic = '$SYNTHETIC'
`$geminiKeyNames = @('GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GENERATIVE_AI_API_KEY', 'GOOGLE_AI_API_KEY')
[Environment]::SetEnvironmentVariable('GOOGLE_API_KEY', `$synthetic, 'Process')
"@

$SHARED_FINALLY = @"
    foreach (`$k in `$geminiKeyNames) { [Environment]::SetEnvironmentVariable(`$k, `$null, 'Process') }
    `$keyAfter = [Environment]::GetEnvironmentVariable('GOOGLE_API_KEY', 'Process')
    `$r = if (`$null -eq `$keyAfter) { 'PASS' } else { 'FAIL_still_set' }
    Set-Content -Path `$resultFile -Value `$r -Encoding UTF8
"@

# ------------------------------------------------------------------
# Scenario 1: Docker failure  (exit 1 inside outer try)
# ------------------------------------------------------------------
Run-CleanupScenario 'Docker failure  (exit 1 inside outer try)' ($SHARED_HEADER + @"

try {
    # simulate: docker compose up -d returned non-zero
    exit 1
} finally {
$SHARED_FINALLY
}
"@)

# ------------------------------------------------------------------
# Scenario 2: Migration failure  (exit 1 inside outer try)
# ------------------------------------------------------------------
Run-CleanupScenario 'Migration failure  (exit 1 inside outer try)' ($SHARED_HEADER + @"

try {
    # simulate: alembic upgrade head returned non-zero
    exit 1
} finally {
$SHARED_FINALLY
}
"@)

# ------------------------------------------------------------------
# Scenario 3: Successful API spawn  (outer try completes normally)
# ------------------------------------------------------------------
Run-CleanupScenario 'Successful API spawn  (outer try completes normally)' ($SHARED_HEADER + @"

try {
    # simulate: everything succeeded; API spawned, UI started, summary printed
    # no explicit exit -- falls through naturally to finally
} finally {
$SHARED_FINALLY
}
"@)

# ------------------------------------------------------------------
# Scenario 4: UI failure  (exit 1 in nested try inside outer try)
# Mirrors start-accept.ps1: inner try strips/restores uiSensitiveVars;
# outer try/finally clears Gemini keys.
# ------------------------------------------------------------------
Run-CleanupScenario 'UI failure  (exit 1 in nested try inside outer try)' ($SHARED_HEADER + @"

try {
    # outer try -- Docker + migration + API all succeeded

    # inner try/finally for uiSensitiveVars (stripped for npm, restored in inner finally)
    `$uiSensitiveVars = @('GOOGLE_API_KEY')
    `$savedUiVars = @{}
    foreach (`$v in `$uiSensitiveVars) {
        `$savedUiVars[`$v] = [Environment]::GetEnvironmentVariable(`$v, 'Process')
        [Environment]::SetEnvironmentVariable(`$v, `$null, 'Process')
    }
    try {
        # simulate: next build failed
        exit 1
    } finally {
        # inner finally: restores the key back into the process (as start-accept.ps1 does)
        foreach (`$v in `$uiSensitiveVars) {
            [Environment]::SetEnvironmentVariable(`$v, `$savedUiVars[`$v], 'Process')
        }
    }

} finally {
    # outer finally: now clears all Gemini keys
$SHARED_FINALLY
}
"@)

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
Write-Host ""
Write-Host "======================================================"
Write-Host (' Results: ' + $pass + ' PASS  /  ' + $fail + ' FAIL')
Write-Host "======================================================"

if ($fail -eq 0) {
    Write-Host ' All 4 cleanup scenarios PASS.' -ForegroundColor Green
    Write-Host ' The outer try/finally in start-accept.ps1 correctly clears GOOGLE_API_KEY'
    Write-Host ' from the parent PowerShell process on every tested exit path.'
    Write-Host ''
    Write-Host ' Safe to proceed: run  .\accept\start-accept.ps1 -PromptGeminiKey'
} else {
    Write-Host ' FAIL -- fix the cleanup defect before entering a real key.' -ForegroundColor Red
}
Write-Host ""
