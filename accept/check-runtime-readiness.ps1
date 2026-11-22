#!/usr/bin/env pwsh
# SPX Voice - LiveKit and Pipecat runtime readiness (reported SEPARATELY)
#
# Reads no secrets and prints none. The only .env.accept values read are the
# two non-secret names LIVEKIT_URL and LIVEKIT_CLIENT_URL. SKIP is never counted
# as PASS, and nothing here claims a call is "connected": these are static and
# reachability checks only. They do not prove audio, transcription, LLM replies,
# TTS, barge-in, latency or cleanup work live -- only an authorized Test Audio
# session can.
#
# Usage : .\accept\check-runtime-readiness.ps1
#         .\accept\check-runtime-readiness.ps1 -FunctionsOnly   (dot-source helpers)
# Exit  : 0 when no FAIL (SKIPs allowed), 1 when any FAIL.

Param(
    [switch]$FunctionsOnly,
    [int]$ApiPort = 0
)

$ErrorActionPreference = 'Continue'

# The API port comes from accept/.env.accept (ACCEPT_API_PORT) unless given.
if (-not $FunctionsOnly -and $ApiPort -eq 0) {
    . (Join-Path $PSScriptRoot 'lib-accept.ps1')
    $ApiPort = (Get-AcceptPorts -EnvFile (Join-Path $PSScriptRoot '.env.accept')).Api
}

$script:Results = New-Object System.Collections.ArrayList

function Add-Result {
    param([string]$Group, [string]$Name, [string]$Status, [string]$Detail)
    if (@('PASS', 'FAIL', 'SKIP') -notcontains $Status) { throw "bad status $Status" }
    [void]$script:Results.Add([pscustomobject]@{
        Group = $Group; Name = $Name; Status = $Status; Detail = $Detail
    })
}

function Show-Result {
    param($R)
    $color = 'Gray'
    if ($R.Status -eq 'PASS') { $color = 'Green' }
    elseif ($R.Status -eq 'FAIL') { $color = 'Red' }
    elseif ($R.Status -eq 'SKIP') { $color = 'Yellow' }
    Write-Host ('  [{0,-4}] {1}' -f $R.Status, $R.Name) -ForegroundColor $color
    if ($R.Detail) { Write-Host ('         ' + $R.Detail) -ForegroundColor Gray }
}

function Get-EnvFileValue {
    # Only the two non-secret LiveKit URL names may be read from the env file.
    param([string]$Path, [string]$Name)
    $allowed = @('LIVEKIT_URL', 'LIVEKIT_CLIENT_URL')
    if ($allowed -notcontains $Name) { throw "Refusing to read non-allowlisted name $Name" }
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in (Get-Content $Path)) {
        $t = $line.Trim()
        if ($t.StartsWith('#') -or -not $t) { continue }
        $parts = $t -split '=', 2
        if ($parts.Count -eq 2 -and $parts[0].Trim() -eq $Name) {
            return $parts[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Test-BrowserReachableLiveKitUrl {
    # A browser on the host must be able to open this URL. Docker-internal
    # service names and empty/non-URL values are not reachable from a browser.
    param([string]$Url, [int]$ExpectedPort = 7880)
    if ([string]::IsNullOrWhiteSpace($Url)) {
        return [pscustomobject]@{ Ok = $false; Reason = 'URL is empty' }
    }
    $uri = $null
    if (-not [System.Uri]::TryCreate($Url, [System.UriKind]::Absolute, [ref]$uri)) {
        return [pscustomobject]@{ Ok = $false; Reason = 'not an absolute URL' }
    }
    if (@('ws', 'wss', 'http', 'https') -notcontains $uri.Scheme) {
        return [pscustomobject]@{ Ok = $false; Reason = ('unsupported scheme ' + $uri.Scheme) }
    }
    $h = $uri.Host.ToLowerInvariant()
    $internal = @('livekit', 'accept-livekit', 'host.docker.internal', 'redis', 'postgres', 'api', 'ui')
    if ($internal -contains $h -or $h.StartsWith('spx-accept-')) {
        return [pscustomobject]@{ Ok = $false; Reason = ('host ' + $h + ' is docker-internal, not browser-reachable') }
    }
    if ($h -ne '127.0.0.1' -and $h -ne 'localhost' -and $h -notmatch '\.') {
        return [pscustomobject]@{ Ok = $false; Reason = ('single-label host ' + $h + ' is not resolvable from a browser') }
    }
    if ($uri.Port -ne $ExpectedPort) {
        return [pscustomobject]@{ Ok = $false; Reason = ('port ' + $uri.Port + ' does not match published signaling port ' + $ExpectedPort) }
    }
    return [pscustomobject]@{ Ok = $true; Reason = ('host=' + $h + ' port=' + $uri.Port) }
}

function Get-ComposePublishedPorts {
    # Parses "[ip:]host[-hostEnd]:container[-containerEnd][/proto]" port lines.
    param([string]$Path)
    $out = New-Object System.Collections.ArrayList
    if (-not (Test-Path $Path)) { return ,@() }
    $rx = '^\s*-\s*"?(?:(\d+\.\d+\.\d+\.\d+):)?(\d+)(?:-(\d+))?:(\d+)(?:-(\d+))?(?:/(tcp|udp))?"?\s*$'
    foreach ($line in (Get-Content $Path)) {
        $m = [regex]::Match($line, $rx)
        if (-not $m.Success) { continue }
        $proto = 'tcp'
        if ($m.Groups[6].Success) { $proto = $m.Groups[6].Value }
        $hostEnd = [int]$m.Groups[2].Value
        if ($m.Groups[3].Success) { $hostEnd = [int]$m.Groups[3].Value }
        [void]$out.Add([pscustomobject]@{
            HostIp = $m.Groups[1].Value
            HostStart = [int]$m.Groups[2].Value
            HostEnd = $hostEnd
            Proto = $proto
        })
    }
    return ,$out.ToArray()
}

function Test-PortPublished {
    param($Published, [int]$Port, [string]$Proto)
    foreach ($p in $Published) {
        if ($p.Proto -eq $Proto -and $Port -ge $p.HostStart -and $Port -le $p.HostEnd) { return $true }
    }
    return $false
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMs = 2000)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($iar)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PinnedPipecatCommit {
    param([string]$RequirementsPath)
    if (-not (Test-Path $RequirementsPath)) { return $null }
    $m = [regex]::Match((Get-Content $RequirementsPath -Raw), 'pipecat-ai\s*@\s*git\+\S+?@([0-9a-fA-F]{40})')
    if ($m.Success) { return $m.Groups[1].Value.ToLowerInvariant() }
    return $null
}

function Get-InstalledPipecatCommit {
    # The offline store records the source it was installed from; its URL tail
    # is the commit when installed from a commit-named directory.
    param([string]$DepsDir)
    $info = Get-ChildItem -Path $DepsDir -Directory -Filter 'pipecat_ai-*.dist-info' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $info) { return $null }
    $durl = Join-Path $info.FullName 'direct_url.json'
    if (-not (Test-Path $durl)) { return $null }
    try { $j = Get-Content $durl -Raw | ConvertFrom-Json } catch { return $null }
    if (-not $j.url) { return $null }
    $tail = ([string]$j.url).TrimEnd('/')
    $tail = $tail.Substring($tail.LastIndexOf('/') + 1)
    if ($tail -match '^[0-9a-fA-F]{40}$') { return $tail.ToLowerInvariant() }
    return $null
}

function Invoke-PythonProbe {
    # Runs probe source from a temp .py file (no inline source on the command
    # line, so Windows paths never reach a Python string literal) with
    # PYTHONPATH/env set through environment variables, restored in finally.
    # The probe prints one line "RESULT:<json>" on stdout; stderr is discarded.
    param([string]$Python, [string]$Candidate, [string]$Source)
    $saved = @{
        PP = $env:PYTHONPATH; E = $env:ENVIRONMENT; D = $env:DATABASE_URL
        R = $env:REDIS_URL; B = $env:PYTHONDONTWRITEBYTECODE
    }
    $tmp = Join-Path $env:TEMP ('spx-probe-' + [guid]::NewGuid().ToString('N') + '.py')
    $prevPref = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        Set-Content -Path $tmp -Value $Source -Encoding ASCII
        $env:PYTHONPATH = (Join-Path $Candidate '.deps') + ';' + $Candidate
        $env:ENVIRONMENT = 'test'
        $env:DATABASE_URL = 'postgresql+asyncpg://postgres:postgres@127.0.0.1:55444/spx_voice_accept'
        $env:REDIS_URL = 'redis://:REDIS_PASSWORD_PLACEHOLDER@127.0.0.1:6384/0'
        $env:PYTHONDONTWRITEBYTECODE = '1'
        Push-Location $Candidate
        try { $lines = & $Python $tmp 2>$null } finally { Pop-Location }
        $code = $LASTEXITCODE
        foreach ($l in @($lines)) {
            $s = [string]$l
            if ($s.StartsWith('RESULT:')) {
                try { return [pscustomobject]@{ ExitCode = $code; Data = ($s.Substring(7) | ConvertFrom-Json) } } catch { }
            }
        }
        return [pscustomobject]@{ ExitCode = $code; Data = $null }
    } finally {
        $ErrorActionPreference = $prevPref
        $env:PYTHONPATH = $saved.PP; $env:ENVIRONMENT = $saved.E
        $env:DATABASE_URL = $saved.D; $env:REDIS_URL = $saved.R
        $env:PYTHONDONTWRITEBYTECODE = $saved.B
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

if ($FunctionsOnly) { return }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Candidate = Split-Path -Parent $ScriptDir
$Python = 'D:\spx-voice-mvp\.venv\Scripts\python.exe'
$EnvFile = Join-Path $ScriptDir '.env.accept'
$ComposeFile = Join-Path $ScriptDir 'docker-compose.accept.yaml'
$BaseApi = 'http://127.0.0.1:' + $ApiPort
$LkContainer = 'spx-accept-lk-20261121'

Write-Host ''
Write-Host '=== SPX Voice runtime readiness (static + reachability only) ==='
Write-Host '    Not proof of a working call. SKIP is not PASS.'

# ---------------------------------------------------------------- LiveKit
$G = 'LiveKit'
Write-Host ''
Write-Host '--- LiveKit ---'

$httpOk = $false
$httpDetail = ''
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:7880' -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    $httpOk = ($r.StatusCode -eq 200)
    $httpDetail = 'HTTP ' + $r.StatusCode
} catch {
    $httpDetail = 'not reachable: ' + $_.Exception.GetType().Name
}
Add-Result $G 'server HTTP reachable on 127.0.0.1:7880' ($(if ($httpOk) { 'PASS' } else { 'FAIL' })) $httpDetail

$apiHealth = $null
try { $apiHealth = Invoke-RestMethod -Uri ($BaseApi + '/api/v1/health') -TimeoutSec 5 } catch { $apiHealth = $null }
$apiRuntime = $null
try { $apiRuntime = Invoke-RestMethod -Uri ($BaseApi + '/api/v1/livekit/runtime') -TimeoutSec 5 } catch { $apiRuntime = $null }
if ($null -eq $apiHealth -or $null -eq $apiRuntime) {
    Add-Result $G 'API credentials configured (livekit_enabled)' 'SKIP' ('API not reachable on ' + $BaseApi + '; cannot read health/runtime')
} else {
    $cfgOk = ($apiHealth.livekit_enabled -eq $true) -and ($apiRuntime.livekit_enabled -eq $true) -and ($apiRuntime.voice_runtime -eq 'livekit')
    Add-Result $G 'API credentials configured (livekit_enabled)' ($(if ($cfgOk) { 'PASS' } else { 'FAIL' })) ('health.livekit_enabled=' + $apiHealth.livekit_enabled + ' runtime.livekit_enabled=' + $apiRuntime.livekit_enabled + ' voice_runtime=' + $apiRuntime.voice_runtime + ' (configured only; says nothing about the worker)')
}

$workerSkip = 'worker_running is only on the authenticated /livekit/settings route; no session available to this script'
$settingsCode = $null
try {
    Invoke-RestMethod -Uri ($BaseApi + '/api/v1/livekit/settings') -TimeoutSec 5 | Out-Null
} catch {
    if ($_.Exception.Response) { $settingsCode = [int]$_.Exception.Response.StatusCode }
}
if ($null -ne $settingsCode) { $workerSkip = $workerSkip + ' (HTTP ' + $settingsCode + ')' }
Add-Result $G 'candidate worker registered' 'SKIP' ($workerSkip + '. Log in at the candidate UI and confirm worker_running=true.')
Add-Result $G 'dispatch accepted' 'SKIP' 'Only provable by creating a run and dispatching a job (authenticated, and it starts a call). Not attempted here.'

$envUrl = Get-EnvFileValue -Path $EnvFile -Name 'LIVEKIT_URL'
$clientUrl = Get-EnvFileValue -Path $EnvFile -Name 'LIVEKIT_CLIENT_URL'
$urlToCheck = $clientUrl
$urlLabel = 'LIVEKIT_CLIENT_URL'
if ([string]::IsNullOrWhiteSpace($urlToCheck)) { $urlToCheck = $envUrl; $urlLabel = 'LIVEKIT_URL' }
$u = Test-BrowserReachableLiveKitUrl -Url $urlToCheck -ExpectedPort 7880
Add-Result $G 'browser/client URL is browser-reachable' ($(if ($u.Ok) { 'PASS' } else { 'FAIL' })) ($urlLabel + ' (.env.accept) -> ' + $u.Reason)
Add-Result $G 'browser/client URL matches what the API serves' 'SKIP' 'livekit_client_url is only returned by the authenticated /livekit/settings route.'

$published = Get-ComposePublishedPorts -Path $ComposeFile
foreach ($port in @(7880, 7881)) {
    $declared = Test-PortPublished -Published $published -Port $port -Proto 'tcp'
    $open = Test-TcpPort -HostName '127.0.0.1' -Port $port
    $st = 'FAIL'
    if ($declared -and $open) { $st = 'PASS' }
    Add-Result $G ('TCP ' + $port + ' published and reachable') $st ('declared in compose=' + $declared + ' connect=' + $open)
}

$udpDeclared = (Test-PortPublished -Published $published -Port 51000 -Proto 'udp') -and (Test-PortPublished -Published $published -Port 51019 -Proto 'udp')
$composeText = ''
if (Test-Path $ComposeFile) { $composeText = Get-Content $ComposeFile -Raw }
$udpArgOk = $composeText -match '--udp-port",\s*"51000-51019"'
$dockerSeen = 'docker port not checked'
$dockerOk = $true
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $dp = & docker port $LkContainer 2>$null
    $ErrorActionPreference = $prevPref
    $dockerOk = (($dp | Out-String) -match '51000/udp') -and (($dp | Out-String) -match '51019/udp')
    $dockerSeen = 'docker port shows 51000-51019/udp=' + $dockerOk
}
$udpStatus = 'FAIL'
if ($udpDeclared -and $udpArgOk -and $dockerOk) { $udpStatus = 'PASS' }
Add-Result $G 'UDP media 51000-51019 published (not probed)' $udpStatus ('compose publishes=' + $udpDeclared + ' livekit --udp-port matches=' + $udpArgOk + '; ' + $dockerSeen + '. Published only: UDP reachability is NOT probed.')
Add-Result $G 'UDP media actually reachable from the browser' 'SKIP' 'UDP cannot be probed reliably without a real WebRTC session.'

# ---------------------------------------------------------------- Pipecat
$G2 = 'Pipecat'
Write-Host ''
Write-Host '--- Pipecat ---'

$depsDir = Join-Path $Candidate '.deps'
if (-not (Test-Path $Python)) {
    Add-Result $G2 'python available' 'FAIL' 'shared venv python not found'
} elseif (-not (Test-Path $depsDir)) {
    Add-Result $G2 'candidate .deps present' 'FAIL' '.deps directory not found'
} else {
    $imp = Invoke-PythonProbe -Python $Python -Candidate $Candidate -Source @'
import json
import pipecat
print("RESULT:" + json.dumps({"file": pipecat.__file__}))
'@
    $fromDeps = $false
    $impDetail = 'import failed'
    if ($imp.Data -and $imp.Data.file) {
        $fromDeps = ([string]$imp.Data.file).ToLowerInvariant().StartsWith($depsDir.ToLowerInvariant())
        $impDetail = 'resolved from ' + $(if ($fromDeps) { 'candidate .deps' } else { 'OUTSIDE candidate .deps' })
    }
    Add-Result $G2 'import pipecat from candidate .deps' ($(if ($fromDeps) { 'PASS' } else { 'FAIL' })) $impDetail

    $pin = Get-PinnedPipecatCommit -RequirementsPath (Join-Path $Candidate 'api\requirements.txt')
    $inst = Get-InstalledPipecatCommit -DepsDir $depsDir
    if (-not $pin -or -not $inst) {
        Add-Result $G2 'pinned commit matches .deps install' 'FAIL' ('pin=' + $pin + ' installed=' + $inst + ' (could not determine one side)')
    } else {
        Add-Result $G2 'pinned commit matches .deps install' ($(if ($pin -eq $inst) { 'PASS' } else { 'FAIL' })) ('requirements pin=' + $pin + ' .deps direct_url=' + $inst)
    }

    $rt = Invoke-PythonProbe -Python $Python -Candidate $Candidate -Source @'
import json
import inspect
out = {"text_chat": [], "adapters": {}, "error": None}
try:
    from api.routes import main
    paths = [getattr(r, "path", "") for r in main.router.routes]
    out["text_chat"] = [p for p in paths if "text-chat" in p]
    from api.services.pipecat import service_factory as sf
    out["adapters"]["deepgram_stt"] = hasattr(sf, "DeepgramSTTService") and callable(sf.create_stt_service)
    out["adapters"]["cartesia_tts"] = hasattr(sf, "CartesiaTTSService") and callable(sf.create_tts_service)
    src = inspect.getsource(sf.create_llm_service_from_provider)
    out["adapters"]["deepseek_llm_pipecat"] = "DEEPSEEK" in src or "deepseek" in src
    from api.services.livekit import runtime_config as rc
    out["adapters"]["deepgram_livekit"] = "deepgram" in rc.LIVEKIT_SUPPORTED_PROVIDERS["stt"]
    out["adapters"]["deepseek_livekit"] = "deepseek" in rc.LIVEKIT_SUPPORTED_PROVIDERS["llm"]
    out["adapters"]["cartesia_livekit"] = "cartesia" in rc.LIVEKIT_SUPPORTED_PROVIDERS["tts"]
except Exception as exc:
    out["error"] = type(exc).__name__
print("RESULT:" + json.dumps(out))
'@
    if (-not $rt.Data -or $rt.Data.error) {
        $why = 'probe failed'
        if ($rt.Data -and $rt.Data.error) { $why = 'probe raised ' + $rt.Data.error }
        Add-Result $G2 'Text Chat route registered' 'FAIL' $why
        Add-Result $G2 'provider adapters importable' 'FAIL' $why
    } else {
        $tc = @($rt.Data.text_chat)
        Add-Result $G2 'Text Chat route registered' ($(if ($tc.Count -gt 0) { 'PASS' } else { 'FAIL' })) ($tc.Count.ToString() + ' text-chat route(s) in api.routes.main')
        $a = $rt.Data.adapters
        Add-Result $G2 'Deepgram STT adapter (Pipecat factory)' ($(if ($a.deepgram_stt) { 'PASS' } else { 'FAIL' })) 'service_factory.create_stt_service'
        Add-Result $G2 'Cartesia TTS adapter (Pipecat factory)' ($(if ($a.cartesia_tts) { 'PASS' } else { 'FAIL' })) 'service_factory.create_tts_service'
        Add-Result $G2 'DeepSeek LLM adapter (Pipecat factory)' ($(if ($a.deepseek_llm_pipecat) { 'PASS' } else { 'FAIL' })) ($(if ($a.deepseek_llm_pipecat) { 'present' } else { 'ABSENT: Pipecat/Text Chat rejects provider deepseek (HTTP 400); LiveKit worker path supports it' }))
        $lkOk = ($a.deepgram_livekit -and $a.deepseek_livekit -and $a.cartesia_livekit)
        Add-Result $G2 'Premium providers in LiveKit supported set' ($(if ($lkOk) { 'PASS' } else { 'FAIL' })) 'runtime_config.LIVEKIT_SUPPORTED_PROVIDERS deepgram/deepseek/cartesia'
    }
}

# ---------------------------------------------------------------- Summary
foreach ($grp in @($G, $G2)) {
    $rows = @($script:Results | Where-Object { $_.Group -eq $grp })
    Write-Host ''
    Write-Host ('=== ' + $grp + ' ===')
    foreach ($r in $rows) { Show-Result $r }
    $p = @($rows | Where-Object { $_.Status -eq 'PASS' }).Count
    $f = @($rows | Where-Object { $_.Status -eq 'FAIL' }).Count
    $s = @($rows | Where-Object { $_.Status -eq 'SKIP' }).Count
    Write-Host ('  ' + $grp + ': ' + $p + ' PASS / ' + $f + ' FAIL / ' + $s + ' SKIP (SKIP is not PASS)')
}
$totalFail = @($script:Results | Where-Object { $_.Status -eq 'FAIL' }).Count
Write-Host ''
if ($totalFail -eq 0) {
    Write-Host 'No FAIL. SKIPs above remain unverified. No live call has been proven.' -ForegroundColor Yellow
    exit 0
}
Write-Host ('FAIL count: ' + $totalFail) -ForegroundColor Red
exit 1
