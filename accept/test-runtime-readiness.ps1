#!/usr/bin/env pwsh
# SPX Voice - test of check-runtime-readiness.ps1 non-network logic (PS 5.1)
# Synthetic inputs only; no real key, no API/UI/Docker needed. Paths with
# spaces are used on purpose.
#
# Usage  : .\accept\test-runtime-readiness.ps1
# Expect : all PASS / 0 FAIL

$ErrorActionPreference = 'Continue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = Join-Path $ScriptDir 'check-runtime-readiness.ps1'
. $Target -FunctionsOnly

$pass = 0
$fail = 0
function Check($label, $ok, $detail) {
    $status = 'FAIL'; $color = 'Red'
    if ($ok) { $status = 'PASS'; $color = 'Green' }
    Write-Host ('  [' + $status + '] ' + $label) -ForegroundColor $color
    if ($detail) { Write-Host ('         ' + $detail) -ForegroundColor Gray }
    if ($ok) { $script:pass++ } else { $script:fail++ }
}

$work = Join-Path $env:TEMP ('spx rt test ' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$SYNTH_SECRET = 'SYNTHETIC_LK_SECRET_5d2e91ab'

try {
    Write-Host ''
    Write-Host '=== check-runtime-readiness.ps1 logic tests ==='
    Write-Host ''

    # 1. URL classification
    $cases = @(
        @('ws://127.0.0.1:7880', $true),
        @('ws://localhost:7880', $true),
        @('wss://lk.example.com:7880', $true),
        @('ws://accept-livekit:7880', $false),
        @('ws://spx-accept-lk-20261121:7880', $false),
        @('ws://host.docker.internal:7880', $false),
        @('ws://127.0.0.1:7999', $false),
        @('', $false),
        @('not a url', $false),
        @('ftp://127.0.0.1:7880', $false)
    )
    $bad = @()
    foreach ($c in $cases) {
        $r = Test-BrowserReachableLiveKitUrl -Url $c[0] -ExpectedPort 7880
        if ($r.Ok -ne $c[1]) { $bad += $c[0] }
    }
    Check '1. LiveKit client URL classification (browser-reachable vs docker-internal)' ($bad.Count -eq 0) ('mismatches=' + ($bad -join ','))

    # 2. Compose port parsing and range membership
    $compose = Join-Path $work 'docker-compose.synthetic.yaml'
    @(
        'services:',
        '  lk:',
        '    ports:',
        '      - "127.0.0.1:7880:7880"',
        '      - "127.0.0.1:7881:7881"',
        '      - "127.0.0.1:51000-51019:51000-51019/udp"',
        '      - "6384:6379"'
    ) | Set-Content -Path $compose -Encoding ASCII
    $pub = Get-ComposePublishedPorts -Path $compose
    $ok2 = (Test-PortPublished -Published $pub -Port 7880 -Proto 'tcp') `
        -and (Test-PortPublished -Published $pub -Port 7881 -Proto 'tcp') `
        -and (Test-PortPublished -Published $pub -Port 51010 -Proto 'udp') `
        -and (-not (Test-PortPublished -Published $pub -Port 51010 -Proto 'tcp')) `
        -and (-not (Test-PortPublished -Published $pub -Port 51020 -Proto 'udp')) `
        -and (-not (Test-PortPublished -Published $pub -Port 7880 -Proto 'udp'))
    Check '2. Compose published TCP/UDP port parsing (path with spaces)' $ok2 ('entries=' + @($pub).Count)

    # 3. Env-file reader: allowlist only, secret lines never returned
    $envf = Join-Path $work '.env.synthetic'
    @(
        'LIVEKIT_URL=ws://127.0.0.1:7880',
        ('LIVEKIT_API_SECRET=' + $SYNTH_SECRET),
        'LIVEKIT_API_KEY=devkey'
    ) | Set-Content -Path $envf -Encoding ASCII
    $v = Get-EnvFileValue -Path $envf -Name 'LIVEKIT_URL'
    $threw = $false
    try { Get-EnvFileValue -Path $envf -Name 'LIVEKIT_API_SECRET' | Out-Null } catch { $threw = $true }
    $none = Get-EnvFileValue -Path $envf -Name 'LIVEKIT_CLIENT_URL'
    Check '3. Env reader returns only allowlisted URL names; secret names refused' (($v -eq 'ws://127.0.0.1:7880') -and $threw -and ($null -eq $none)) ('url=' + $v + ' secretNameRefused=' + $threw)

    # 4. Pipecat pin vs install (synthetic, path with spaces)
    $sha = '3e02e5d0b7cc4c9e91546ab075b3968389f5e9af'
    $req = Join-Path $work 'requirements.txt'
    Set-Content -Path $req -Encoding ASCII -Value ('livekit-api==1.1.0' + [Environment]::NewLine + 'pipecat-ai @ git+https://github.com/pipecat-ai/pipecat.git@' + $sha)
    $deps = Join-Path $work '.deps'
    $dist = Join-Path $deps 'pipecat_ai-0.0.0.dev0.dist-info'
    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    Set-Content -Path (Join-Path $dist 'direct_url.json') -Encoding ASCII -Value ('{"dir_info": {}, "url": "file:///app/.deps/pipecat/' + $sha + '"}')
    $pin = Get-PinnedPipecatCommit -RequirementsPath $req
    $inst = Get-InstalledPipecatCommit -DepsDir $deps
    Set-Content -Path (Join-Path $dist 'direct_url.json') -Encoding ASCII -Value '{"url": "file:///app/.deps/pipecat/not-a-commit"}'
    $instBad = Get-InstalledPipecatCommit -DepsDir $deps
    Check '4. Pin/install commit extraction; non-commit tail yields null' (($pin -eq $sha) -and ($inst -eq $sha) -and ($null -eq $instBad)) ('pin=' + $pin + ' inst=' + $inst)

    # 5. TCP probe: open listener passes, closed port fails
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $openPort = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $openRes = Test-TcpPort -HostName '127.0.0.1' -Port $openPort -TimeoutMs 2000
    $listener.Stop()
    $closedRes = Test-TcpPort -HostName '127.0.0.1' -Port $openPort -TimeoutMs 1000
    Check '5. TCP probe distinguishes open from closed port' ($openRes -and -not $closedRes) ('open=' + $openRes + ' afterClose=' + $closedRes)

    # 6. Python probe: PYTHONPATH from env (spaces), env restored in finally
    $py = 'D:\spx-voice-mvp\.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) {
        Write-Host '  [SKIP] 6. Python probe (shared venv python not found)' -ForegroundColor Yellow
        Write-Host '  [SKIP] 7. Probe env restored after failing probe (python not found)' -ForegroundColor Yellow
    } else {
        $cand = Join-Path $work 'Spaced Candidate'
        New-Item -ItemType Directory -Force -Path (Join-Path $cand '.deps') | Out-Null
        $env:PYTHONPATH = 'SENTINEL_PP_KEEP'
        $env:ENVIRONMENT = 'SENTINEL_ENV_KEEP'
        $probe = Invoke-PythonProbe -Python $py -Candidate $cand -Source @'
import json
import sys
hits = [p for p in sys.path if "Spaced Candidate" in p]
print("RESULT:" + json.dumps({"hits": hits}))
'@
        $hits = @($probe.Data.hits)
        $hasDeps = @($hits | Where-Object { $_.EndsWith('.deps') }).Count -ge 1
        $hasRoot = @($hits | Where-Object { $_.EndsWith('Spaced Candidate') }).Count -ge 1
        $restored = ($env:PYTHONPATH -eq 'SENTINEL_PP_KEEP') -and ($env:ENVIRONMENT -eq 'SENTINEL_ENV_KEEP')
        Check '6. Probe resolves PYTHONPATH with spaces/backslashes and restores env' ($hasDeps -and $hasRoot -and $restored) ('deps=' + $hasDeps + ' root=' + $hasRoot + ' restored=' + $restored)

        # 7. A failing probe still restores env and returns no data
        $env:PYTHONPATH = 'SENTINEL_PP_KEEP2'
        $badProbe = Invoke-PythonProbe -Python $py -Candidate $cand -Source 'raise SystemExit(3)'
        $restored2 = ($env:PYTHONPATH -eq 'SENTINEL_PP_KEEP2')
        Check '7. Failing probe: no data, non-zero exit, env still restored' (($null -eq $badProbe.Data) -and ($badProbe.ExitCode -eq 3) -and $restored2) ('exit=' + $badProbe.ExitCode + ' restored=' + $restored2)
        Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
        Remove-Item Env:\ENVIRONMENT -ErrorAction SilentlyContinue
    }

    # 8. Result bookkeeping: SKIP is not PASS; bad status rejected
    $script:Results.Clear()
    Add-Result 'X' 'a' 'PASS' ''
    Add-Result 'X' 'b' 'SKIP' ''
    Add-Result 'X' 'c' 'FAIL' ''
    $passN = @($script:Results | Where-Object { $_.Status -eq 'PASS' }).Count
    $skipN = @($script:Results | Where-Object { $_.Status -eq 'SKIP' }).Count
    $rejected = $false
    try { Add-Result 'X' 'd' 'CONNECTED' '' } catch { $rejected = $true }
    Check '8. Only PASS/FAIL/SKIP accepted; SKIP tallied separately' (($passN -eq 1) -and ($skipN -eq 1) -and $rejected) ('pass=' + $passN + ' skip=' + $skipN + ' badStatusRejected=' + $rejected)

    # 9. Script hygiene: ASCII only, no embedded-path python -c, no secret value
    $bytes = [System.IO.File]::ReadAllBytes($Target)
    $nonAscii = @($bytes | Where-Object { $_ -gt 127 }).Count
    $text = [System.IO.File]::ReadAllText($Target)
    $embeds = $text -match 'python[^\r\n]*\s-c\s'
    Check '9. Script is ASCII-only and never uses python -c' (($nonAscii -eq 0) -and (-not $embeds)) ('nonAscii=' + $nonAscii + ' usesDashC=' + $embeds)
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host '======================================================'
Write-Host (' Results: ' + $pass + ' PASS  /  ' + $fail + ' FAIL')
Write-Host '======================================================'
if ($fail -ne 0) { exit 1 }
