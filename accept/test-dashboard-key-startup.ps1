#!/usr/bin/env pwsh
# SPX Voice - dashboard-key startup mode test (PowerShell 5.1, synthetic values only)
# Proves, without starting Docker/API/UI and without any real key:
#   1. start-accept.ps1 parses cleanly.
#   2. No flag = dashboard-key mode: the key block exits 0, clears a Gemini key
#      inherited from the parent shell, ignores a Gemini line in the env file,
#      still loads non-secret infra values, and records present=false.
#   3. Passing both key flags is still rejected.
#   4. -AllowFileBasedGeminiKey legacy path still loads a file key.
#   5. The script names its own UI/API URLs, requires config encryption, keeps the
#      encryption key out of .env.accept/output, strips it from the UI build env
#      and clears it in the outer finally.
#   6. A Fernet key can be generated with the project venv (no path in -c source).
#
# Usage  : .\accept\test-dashboard-key-startup.ps1
# Expect : 6 PASS  /  0 FAIL

$ErrorActionPreference = 'Continue'

$Script    = Join-Path $PSScriptRoot 'start-accept.ps1'
$Python    = 'D:\spx-voice-mvp\.venv\Scripts\python.exe'
$PS5       = [System.IO.Path]::Combine($PSHOME, 'powershell.exe')
$TmpDir    = $env:TEMP
$SYNTHETIC = 'SYNTHETIC_PARENT_KEY_c4d5e6f7'
$FILEKEY   = 'SYNTHETIC_FILE_KEY_9a8b7c6d'
$pass = 0
$fail = 0

function Check($label, $ok, $detail) {
    $status = if ($ok) { 'PASS' } else { 'FAIL' }
    $color  = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ('  [' + $status + '] ' + $label) -ForegroundColor $color
    if ($detail) { Write-Host ('         ' + $detail) -ForegroundColor Gray }
    if ($ok) { $script:pass++ } else { $script:fail++ }
}

Write-Host ""
Write-Host "=== Dashboard-key startup mode test (synthetic values only) ==="
Write-Host ""

$text = Get-Content $Script -Raw

# 1. Parse
$errs = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$errs)
Check "1. start-accept.ps1 parses under PowerShell 5.1" ($errs.Count -eq 0) "parse errors=$($errs.Count)"

# Extract the key-handling region: from the strategy check to the outer cleanup guard.
$startMarker = '$geminiKeyNames = @('
$endMarker   = '# OUTER CLEANUP GUARD'
$s = $text.IndexOf($startMarker)
$e = $text.IndexOf($endMarker)
$region = if ($s -ge 0 -and $e -gt $s) { $text.Substring($s, $e - $s) } else { '' }

function Run-KeyRegion([string]$Mode, [string]$EnvText) {
    $uid     = [System.Guid]::NewGuid().ToString('N')
    $dir     = Join-Path $TmpDir ('dk-' + $uid)
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $envFile = Join-Path $dir '.env.accept'
    Set-Content -Path $envFile -Value $EnvText -Encoding ASCII
    $runDir  = Join-Path $dir 'run'
    $harness = Join-Path $dir 'h.ps1'
    $result  = Join-Path $dir 'r.txt'
    $flags = switch ($Mode) {
        'none'  { '' }
        'both'  { '-SeedGeminiKeyToDb -AllowFileBasedGeminiKey' }
        'old'   { '-PromptGeminiKey' }
        'file'  { '-AllowFileBasedGeminiKey' }
    }
    $paramLine = switch ($Mode) {
        'none'  { '$SeedGeminiKeyToDb = $false; $PromptGeminiKey = $false; $AllowFileBasedGeminiKey = $false' }
        'both'  { '$SeedGeminiKeyToDb = $true; $PromptGeminiKey = $false; $AllowFileBasedGeminiKey = $true' }
        'old'   { '$SeedGeminiKeyToDb = $false; $PromptGeminiKey = $true; $AllowFileBasedGeminiKey = $false' }
        'file'  { '$SeedGeminiKeyToDb = $false; $PromptGeminiKey = $false; $AllowFileBasedGeminiKey = $true' }
    }
    $body = @"
`$ErrorActionPreference = 'Stop'
$paramLine
`$EnvFile = '$envFile'
`$RunDir  = '$runDir'
`$UiPort  = 3001
[Environment]::SetEnvironmentVariable('GOOGLE_API_KEY', '$SYNTHETIC', 'Process')
$region
`$out = @(
  ('GOOGLE_API_KEY=' + [Environment]::GetEnvironmentVariable('GOOGLE_API_KEY', 'Process')),
  ('GEMINI_API_KEY=' + [Environment]::GetEnvironmentVariable('GEMINI_API_KEY', 'Process')),
  ('INFRA=' + [Environment]::GetEnvironmentVariable('SPX_TEST_INFRA', 'Process')),
  ('MARKER=' + (Get-Content (Join-Path `$RunDir 'gemini-key-status.txt') -ErrorAction SilentlyContinue))
)
Set-Content -Path '$result' -Value `$out -Encoding ASCII
exit 0
"@
    Set-Content -Path $harness -Value $body -Encoding ASCII
    $null = & $PS5 -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $harness 2>&1
    $code = $LASTEXITCODE
    $lines = @()
    if (Test-Path $result) { $lines = Get-Content $result }
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    return [pscustomobject]@{ Code = $code; Lines = $lines }
}

$envText = "SPX_TEST_INFRA=infra-ok`r`nGEMINI_API_KEY=$FILEKEY`r`n"

# 2. No flag => dashboard-key mode
$r2 = Run-KeyRegion 'none' $envText
$joined2 = ($r2.Lines -join ';')
$ok2 = ($r2.Code -eq 0) -and ($joined2 -match 'GOOGLE_API_KEY=;') -and ($joined2 -match 'GEMINI_API_KEY=;') `
    -and ($joined2 -match 'INFRA=infra-ok') -and ($joined2 -match 'present=false') `
    -and ($joined2 -notmatch [regex]::Escape($SYNTHETIC)) -and ($joined2 -notmatch [regex]::Escape($FILEKEY))
Check "2. No flag: starts without a key, inherited/file Gemini keys ignored, infra loaded" $ok2 "exit=$($r2.Code)  $joined2"

# 3. Both flags still rejected
$r3 = Run-KeyRegion 'both' $envText
Check "3. Both key flags together are rejected (exit 1)" ($r3.Code -eq 1) "exit=$($r3.Code)"

# 3b. The old -PromptGeminiKey name is refused (it implied ephemeral storage)
$r3b = Run-KeyRegion 'old' $envText
Check "3b. Old -PromptGeminiKey name is refused (exit 1)" ($r3b.Code -eq 1) "exit=$($r3b.Code)"

# 3c. Persistent seeding is never called ephemeral in the script text
$ephemeralClaims = @($text -split "`n" | Where-Object { $_ -match '(?i)ephemeral' -and $_ -notmatch '(?i)not\s+(process-only/)?ephemeral|NOT EPHEMERAL|never call|refused|implied' })
Check "3c. No line calls persistent storage ephemeral" ($ephemeralClaims.Count -eq 0) "offending lines=$($ephemeralClaims.Count)"

# 4. Legacy file path still works
$r4 = Run-KeyRegion 'file' $envText
$joined4 = ($r4.Lines -join ';')
Check "4. -AllowFileBasedGeminiKey legacy path still loads the file key" `
    (($r4.Code -eq 0) -and ($joined4 -match ('GEMINI_API_KEY=' + [regex]::Escape($FILEKEY)))) "exit=$($r4.Code)"

# 5. Static contract
$c5 = @(
    ($text -match '\$env:UI_APP_URL\s*=\s*"http://127\.0\.0\.1:\$UiPort"'),
    ($text -match '\$env:BACKEND_API_ENDPOINT\s*=\s*"http://127\.0\.0\.1:\$ApiPort"'),
    ($text -match "\`$env:REQUIRE_CONFIG_ENCRYPTION\s*=\s*'true'"),
    ($text -match "'NEXT_PUBLIC_BACKEND_URL'"),
    ($text -match "'TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS'\s*,\s*\r?\n?\s*#" -or $text -match "uiSensitiveVars = @\([\s\S]*?'TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS'"),
    ($text -match "SetEnvironmentVariable\('TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS', \`$null, 'Process'\)"),
    # the key must never be echoed
    (-not ($text -split "`n" | Where-Object { $_ -match 'Write-Host' -and $_ -match 'TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS' })),
    ($text -notmatch '\$ApiPort\s*=\s*\d' -and $text -notmatch '\$UiPort\s*=\s*\d'),
    ($text -match 'Get-AcceptPorts')
)
Check "5. URLs explicit, encryption required, key stripped from UI env and cleared in finally, never echoed" `
    (($c5 | Where-Object { -not $_ }).Count -eq 0) "checks=$($c5 -join ',')"

# 6. Fernet key generation via the project venv; nothing path-like inside python -c
$keyOk = $false
if (Test-Path $Python) {
    $k = & $Python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    $keyOk = ($LASTEXITCODE -eq 0) -and ("$k".Trim().Length -eq 44)
    $k = $null
}
Check "6. Fernet key generation works with the project venv" $keyOk ""

# 7. UI launch must not use cmd's `set VAR=value && cmd` (trailing space in value)
$uiLaunchOk = ($text -notmatch 'set\s+BACKEND_URL=\S+\s+&&') -and ($text -match '\$env:BACKEND_URL\s*=\s*"http://127\.0\.0\.1:\$ApiPort"\s*\r?\n(\s*#[^\r\n]*\r?\n)*\s*\$uiCmd')
Check "7. UI BACKEND_URL passed via process env, not cmd 'set X=Y &&'" $uiLaunchOk ""
# 8. UI must bind to loopback only (the dashboard takes provider keys over plain HTTP)
$uiBindOk = ($text -match 'next start --hostname 127\.0\.0\.1 --port')
Check "8. UI server is bound to 127.0.0.1 only (no LAN exposure)" $uiBindOk ""
$script:total = 8

Write-Host ""
Write-Host "======================================================"
Write-Host (' Results: ' + $pass + ' PASS  /  ' + $fail + ' FAIL')
Write-Host "======================================================"
if ($fail -eq 0) { Write-Host ' All checks PASS.' -ForegroundColor Green } else { Write-Host ' Fix failures before proceeding.' -ForegroundColor Red }
Write-Host ""
