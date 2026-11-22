#!/usr/bin/env pwsh
# SPX Voice - configuration encryption key store test (PowerShell 5.1, ASCII only).
# Uses throwaway directories under %TEMP%; never touches the real key store and
# prints no key material.
#
# Usage  : .\accept\test-key-store.ps1
# Expect : 11 PASS  /  0 FAIL

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'lib-accept.ps1')

$Python = 'D:\spx-voice-mvp\.venv\Scripts\python.exe'
$pass = 0; $fail = 0
function Check($label, $ok, $detail) {
    $s = if ($ok) { 'PASS' } else { 'FAIL' }
    Write-Host ('  [' + $s + '] ' + $label) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
    if ($detail) { Write-Host ('         ' + $detail) -ForegroundColor Gray }
    if ($ok) { $script:pass++ } else { $script:fail++ }
}
function New-TempDir { $d = Join-Path $env:TEMP ('ks-' + [guid]::NewGuid().ToString('N')); $d }

Write-Host ""
Write-Host "=== Configuration encryption key store test ==="
Write-Host ""

$no  = { $false }
$yes = { $true }

# 1. Generation: file exists, valid, owner-only ACL on dir and file, no temp left
$d1 = New-TempDir
$k1 = Initialize-AcceptEncryptionKey -Python $Python -KeyDir $d1 -HasEncryptedData $no
$f1 = Join-Path $d1 'config-encryption.key'
$tmpLeft = @(Get-ChildItem $d1 -Filter '*.tmp' -ErrorAction SilentlyContinue).Count
Check "1. Key generated, valid, no temp file left" ((Test-Path $f1) -and (Test-EncryptionKeyText $k1) -and $tmpLeft -eq 0) "tmpLeft=$tmpLeft"
Check "2. Directory and file are current-user-only (no inherited ACEs)" ((Test-OwnerOnlyAcl $d1) -and (Test-OwnerOnlyAcl $f1)) ""

# 3. Second run returns the same key (no silent regeneration)
$k1b = Initialize-AcceptEncryptionKey -Python $Python -KeyDir $d1 -HasEncryptedData $yes
Check "3. Existing key is reused, never regenerated" ($k1b -ceq $k1) ""

# 4. Widened ACL is repaired on next start
$acl = [System.IO.File]::GetAccessControl($f1, [System.Security.AccessControl.AccessControlSections]::Access)
$acl.SetAccessRuleProtection($false, $true)
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule("BUILTIN\Users", "Read", "Allow")))
[System.IO.File]::SetAccessControl($f1, $acl)
$wasOwnerOnly = Test-OwnerOnlyAcl $f1
$null = Initialize-AcceptEncryptionKey -Python $Python -KeyDir $d1 -HasEncryptedData $no
Check "4. Loosened ACL is detected and repaired" ((-not $wasOwnerOnly) -and (Test-OwnerOnlyAcl $f1)) "looseBefore=$(-not $wasOwnerOnly)"

# 5. Missing key + encrypted data in DB => refuse, create nothing
$d5 = New-TempDir
$threw = $false; $msg = ''
try { $null = Initialize-AcceptEncryptionKey -Python $Python -KeyDir $d5 -HasEncryptedData $yes } catch { $threw = $true; $msg = $_.Exception.Message }
Check "5. Missing key with encrypted data present: refuses to generate" ($threw -and -not (Test-Path (Join-Path $d5 'config-encryption.key')) -and $msg -match 'Refusing to generate') ""

# 6. Corrupt key file => error, file untouched
$d6 = New-TempDir
$null = Initialize-AcceptEncryptionKey -Python $Python -KeyDir $d6 -HasEncryptedData $no
$f6 = Join-Path $d6 'config-encryption.key'
[System.IO.File]::WriteAllText($f6, 'not-a-key')
$threw6 = $false
try { $null = Initialize-AcceptEncryptionKey -Python $Python -KeyDir $d6 -HasEncryptedData $no } catch { $threw6 = $true }
Check "6. Corrupt key file is an error, not silently replaced" ($threw6 -and ([System.IO.File]::ReadAllText($f6) -eq 'not-a-key')) ""

# 7. Atomic creation under a race: two starters, one key
$d7 = New-TempDir
$lib = Join-Path $PSScriptRoot 'lib-accept.ps1'
$jobBody = {
    param($lib, $py, $dir)
    . $lib
    Initialize-AcceptEncryptionKey -Python $py -KeyDir $dir -HasEncryptedData { $false }
}
$jobs = 1..2 | ForEach-Object { Start-Job -ScriptBlock $jobBody -ArgumentList $lib, $Python, $d7 }
$res = $jobs | Wait-Job -Timeout 90 | Receive-Job
$jobs | Remove-Job -Force
$keys = @($res | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
$tmp7 = @(Get-ChildItem $d7 -Filter '*.tmp' -ErrorAction SilentlyContinue).Count
Check "7. Concurrent starters agree on one key; no temp files left" (($keys.Count -eq 2) -and ($keys[0] -ceq $keys[1]) -and $tmp7 -eq 0) "keys=$($keys.Count) tmp=$tmp7"

# 8. Legacy in-tree key is moved (not copied) to the store
$d8 = New-TempDir; $leg = Join-Path (New-TempDir) 'config-encryption.key'
New-Item -ItemType Directory -Force -Path (Split-Path $leg) | Out-Null
$seed = (& $Python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
[System.IO.File]::WriteAllText($leg, "$seed".Trim())
$k8 = Initialize-AcceptEncryptionKey -Python $Python -KeyDir $d8 -HasEncryptedData $yes -LegacyPath $leg
Check "8. Legacy key moved out of the tree, content preserved, ACL applied" `
    ((-not (Test-Path $leg)) -and ($k8 -ceq "$seed".Trim()) -and (Test-OwnerOnlyAcl (Join-Path $d8 'config-encryption.key'))) ""

# 9. Real candidate tree holds no key file and no key-shaped content in run/logs
$cand = Split-Path -Parent $PSScriptRoot
$keyFiles = @(Get-ChildItem $cand -Recurse -Force -Filter 'config-encryption.key*' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\node_modules\\|\\\.next\\' })
$shaped = 0
foreach ($dir in @((Join-Path $PSScriptRoot 'run'), (Join-Path $PSScriptRoot 'logs'))) {
    if (Test-Path $dir) {
        $shaped += @(Get-ChildItem $dir -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 5MB } |
            Select-String -Pattern '(^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{43}=($|[^A-Za-z0-9=])' -ErrorAction SilentlyContinue).Count
    }
}
Check "9. No encryption key file or key-shaped value inside the candidate tree" (($keyFiles.Count -eq 0) -and ($shaped -eq 0)) "files=$($keyFiles.Count) shaped=$shaped"

# 10. Settings reader refuses secret-looking names; ports validated
$threwSecret = $false
try { $null = Get-AcceptSetting -EnvFile (Join-Path $PSScriptRoot '.env.accept') -Name 'GOOGLE_API_KEY' } catch { $threwSecret = $true }
$badEnv = Join-Path (New-TempDir) 'e.env'; New-Item -ItemType Directory -Force -Path (Split-Path $badEnv) | Out-Null
Set-Content $badEnv 'ACCEPT_API_PORT=abc'
$threwPort = $false
try { $null = Get-AcceptPorts -EnvFile $badEnv } catch { $threwPort = $true }
$real = Get-AcceptPorts -EnvFile (Join-Path $PSScriptRoot '.env.accept')
Check "10. Settings reader refuses secret names; ports validated and read from .env.accept" ($threwSecret -and $threwPort -and $real.Api -gt 0 -and $real.Ui -gt 0) "api=$($real.Api) ui=$($real.Ui)"

# 11. Encrypted-data check fails CLOSED when the database cannot be queried
$threw11 = $false
try { $null = Test-AcceptDbHasEncryptedData -Container 'spx-no-such-container-xyz' -Database 'none' } catch { $threw11 = $true }
Check "11. DB check throws (never 'no data') when the database cannot be queried" $threw11 ""

foreach ($d in $d1, $d5, $d6, $d7, $d8) { Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "======================================================"
Write-Host (' Results: ' + $pass + ' PASS  /  ' + $fail + ' FAIL')
Write-Host "======================================================"
if ($fail -eq 0) { Write-Host ' All checks PASS.' -ForegroundColor Green } else { Write-Host ' Fix failures before proceeding.' -ForegroundColor Red }
Write-Host ""
