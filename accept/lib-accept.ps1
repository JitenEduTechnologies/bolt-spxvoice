# SPX Voice acceptance - shared helpers (dot-source; PowerShell 5.1, ASCII only).
# Contains no secrets and prints none.

# --- Non-secret settings from accept/.env.accept ------------------------------
# Ports and URLs come from configuration, never from literals in the scripts.
$script:AcceptSecretNamePattern = 'KEY|SECRET|TOKEN|PASS|PWD|CREDENTIAL'

function Get-AcceptSetting {
    param([Parameter(Mandatory)][string]$EnvFile, [Parameter(Mandatory)][string]$Name)
    if ($Name -match $script:AcceptSecretNamePattern) {
        throw "Get-AcceptSetting refuses to read secret-looking name '$Name'."
    }
    if (-not (Test-Path $EnvFile)) { return $null }
    foreach ($line in (Get-Content $EnvFile)) {
        $t = $line.Trim()
        if ($t -and -not $t.StartsWith('#')) {
            $p = $t -split '=', 2
            if ($p.Count -eq 2 -and $p[0].Trim() -eq $Name) { return $p[1].Trim() }
        }
    }
    return $null
}

function Get-AcceptPorts {
    param([Parameter(Mandatory)][string]$EnvFile)
    $api = Get-AcceptSetting -EnvFile $EnvFile -Name 'ACCEPT_API_PORT'
    $ui  = Get-AcceptSetting -EnvFile $EnvFile -Name 'ACCEPT_UI_PORT'
    foreach ($pair in @(@('ACCEPT_API_PORT', $api), @('ACCEPT_UI_PORT', $ui))) {
        $n = 0
        if (-not [int]::TryParse("$($pair[1])", [ref]$n) -or $n -lt 1 -or $n -gt 65535) {
            throw "$($pair[0]) is missing or invalid in $EnvFile (set it to a free TCP port)."
        }
    }
    return [pscustomobject]@{ Api = [int]$api; Ui = [int]$ui }
}

# --- Owner-only ACLs ----------------------------------------------------------
function Set-OwnerOnlyAcl {
    param([Parameter(Mandatory)][string]$Path)
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $isDir = (Get-Item -LiteralPath $Path -Force).PSIsContainer
    $inherit = if ($isDir) { [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [System.Security.AccessControl.InheritanceFlags]::None }
    # Start from the object's real ACL (keeps owner/group, so no special
    # privilege is needed), cut inheritance, drop every ACE, grant only us.
    $sections = [System.Security.AccessControl.AccessControlSections]::Access
    $acl = if ($isDir) { [System.IO.Directory]::GetAccessControl($Path, $sections) } else { [System.IO.File]::GetAccessControl($Path, $sections) }
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($existing in @($acl.Access)) { [void]$acl.RemoveAccessRule($existing) }
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $sid, 'FullControl', $inherit, 'None', 'Allow')
    $acl.AddAccessRule($rule)
    # Write the DACL only (Set-Acl would also write the audit ACL, which needs
    # SeSecurityPrivilege that ordinary users do not hold).
    if ($isDir) { [System.IO.Directory]::SetAccessControl($Path, $acl) } else { [System.IO.File]::SetAccessControl($Path, $acl) }
}

function Test-OwnerOnlyAcl {
    param([Parameter(Mandatory)][string]$Path)
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $isDir = (Get-Item -LiteralPath $Path -Force).PSIsContainer
    $sections = [System.Security.AccessControl.AccessControlSections]::Access
    $acl = if ($isDir) { [System.IO.Directory]::GetAccessControl($Path, $sections) } else { [System.IO.File]::GetAccessControl($Path, $sections) }
    $rules = @($acl.Access)
    if ($rules.Count -ne 1) { return $false }
    $r = $rules[0]
    if ($r.IsInherited -or $r.AccessControlType -ne 'Allow') { return $false }
    $sid = $r.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier])
    return ($sid.Value -eq $me.Value)
}

# --- Configuration encryption key store ---------------------------------------
# The key lives OUTSIDE the candidate tree so packaging, manifests, evidence
# bundles and copies of the candidate cannot include it by construction.
function Get-AcceptKeyDir {
    if ($env:SPX_ACCEPT_KEY_DIR) { return $env:SPX_ACCEPT_KEY_DIR }
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is not set; set SPX_ACCEPT_KEY_DIR.' }
    return (Join-Path $env:LOCALAPPDATA 'SPXVoiceAccept')
}

function Test-EncryptionKeyText {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }
    foreach ($item in ($Text.Trim() -split ',')) {
        if ($item.Trim() -notmatch '^([A-Za-z0-9_]{1,16}:)?[A-Za-z0-9_-]{43}=$') { return $false }
    }
    return $true
}

# $HasEncryptedData: scriptblock returning $true when the database already holds
# enc:v1 values. In that case a missing key file is an error, never a reason to
# generate a new key (that would orphan every saved secret).
function Initialize-AcceptEncryptionKey {
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][scriptblock]$HasEncryptedData,
        [string]$KeyDir = (Get-AcceptKeyDir),
        [string]$LegacyPath
    )
    $keyFile = Join-Path $KeyDir 'config-encryption.key'

    if (-not (Test-Path -LiteralPath $KeyDir)) {
        New-Item -ItemType Directory -Force -Path $KeyDir | Out-Null
    }
    Set-OwnerOnlyAcl -Path $KeyDir

    # One-time move from the old in-tree location (never copy: no second copy).
    if ($LegacyPath -and (Test-Path -LiteralPath $LegacyPath) -and -not (Test-Path -LiteralPath $keyFile)) {
        [System.IO.File]::Move($LegacyPath, $keyFile)
        Set-OwnerOnlyAcl -Path $keyFile
        Write-Host "   moved encryption key out of the candidate tree (not shown)"
    }

    if (-not (Test-Path -LiteralPath $keyFile)) {
        if (& $HasEncryptedData) {
            throw ("Encrypted provider keys exist in the database but the encryption key file is missing " +
                   "($keyFile). Refusing to generate a new key: that would make them unreadable. " +
                   "Restore the key from your backup, or - only if you accept re-entering every saved key - " +
                   "clear the encrypted rows first. See docs/developer/secret-storage.md.")
        }
        $generated = & $Python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
        if ($LASTEXITCODE -ne 0 -or -not (Test-EncryptionKeyText "$generated")) {
            throw 'Could not generate the configuration encryption key.'
        }
        $tmp = Join-Path $KeyDir ('config-encryption.key.' + [guid]::NewGuid().ToString('N') + '.tmp')
        $fs = [System.IO.File]::Open($tmp, 'CreateNew', 'Write', 'None')   # created empty first
        $fs.Close()
        try {
            Set-OwnerOnlyAcl -Path $tmp                                     # ACL before any content
            [System.IO.File]::WriteAllText($tmp, ("$generated").Trim(), [System.Text.Encoding]::ASCII)
            try {
                [System.IO.File]::Move($tmp, $keyFile)                      # atomic; fails if it exists
            } catch [System.IO.IOException] {
                # A concurrent starter created it first: use theirs, drop ours.
                Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            }
        } finally {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
        }
        $generated = $null
        Write-Host "   generated configuration encryption key (stored outside the candidate, not shown)"
    }

    if (-not (Test-OwnerOnlyAcl -Path $keyFile)) { Set-OwnerOnlyAcl -Path $keyFile }
    $text = ([System.IO.File]::ReadAllText($keyFile)).Trim()
    if (-not (Test-EncryptionKeyText $text)) {
        throw "The encryption key file is not a valid key list ($keyFile). Restore it from backup."
    }
    return $text
}

# True when any enc:v1 value is stored in ANY text/json column of the public
# schema (provider configs, workflow overrides, telephony credentials, ...).
# Fails CLOSED: if the database cannot be queried the function throws, so a
# missing key file is never treated as "safe to generate a new key".
# Row counts only; no values are read out.
function Test-AcceptDbHasEncryptedData {
    param([string]$Container, [string]$Database, [string]$User = 'postgres')
    $sql = @'
SELECT format('SELECT count(*) FROM %I.%I WHERE %I::text LIKE %L',
              table_schema, table_name, column_name, '%enc:v1:%')
FROM information_schema.columns
WHERE table_schema = 'public'
  AND data_type IN ('json', 'jsonb', 'text', 'character varying')
\gexec
'@
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = $sql | docker exec -i $Container psql -U $User -d $Database -At -v ON_ERROR_STOP=1 2>$null
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) {
        throw "Could not check the database for encrypted data (exit $code). Refusing to decide about the encryption key."
    }
    $total = 0
    foreach ($line in @($out)) {
        $t = "$line".Trim()
        if (-not $t) { continue }
        $n = 0
        if (-not [int]::TryParse($t, [ref]$n)) {
            throw "Unexpected output while checking for encrypted data. Refusing to decide about the encryption key."
        }
        $total += $n
    }
    return ($total -gt 0)
}
