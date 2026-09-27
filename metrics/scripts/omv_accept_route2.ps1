# omv_accept_route2.ps1 -- one-command acceptance run for docs/18 route 2 (docs/16 BUG-027).
#
# WHY THIS EXISTS
#   Route 2 has a specific, time-boxed acceptance procedure (docs/18 section 3.1): run the camera
#   for N seconds and record FOUR things -- the command, the camera transcript, the receiver's
#   closing line, and the /api/video_status age sampling.  Doing that by hand leaves room for
#   "which parameters did I use?" and for the age sampler to be forgotten entirely, so it lives
#   here as one command.  This file is the harness that produced the BUG-027 evidence in docs/16.
#
# WHAT IT DOES
#   1. starts backend/api.py if it is not already listening (so POST /api/frame has a target)
#   2. Send-OMVStreamer      -> camera-side transcript (expects LINK_OK 1 12 2 3, payload=gray)
#   3. re-invokes ITSELF with -Sampler in a separate process for the age sampling
#      (a separate PROCESS, not Start-Job: named pipes are blocked in confined sandboxes)
#   4. Send-OMVFramesToApi   -> the closing "bursts= ... gray= conv_fail= ..." line
#   5. prints everything, then stops the camera stream
#
# PASS CRITERIA (docs/18 section 3.1, route 1/2 row): ZERO "Compression Failed!", no Traceback in
# the camera transcript, frames= keeps growing.  The age line is extra information: for route 2 we
# measured MAXAGE 0.29 s / VISIBLE_PCT 100 (the web page never shows a stale picture).
#
# NEEDS: the camera on COM10 (see $script:OMV_PORT in omv_stream_bridge.ps1), camera /flash
#        carrying a current vigilens_link.py, OpenMV IDE CLOSED (one process per COM port).
#
# ASCII-ONLY ON PURPOSE (PowerShell 5.1 decodes BOM-less .ps1 as GBK).
#
# USAGE
#   powershell -NoProfile -ExecutionPolicy Bypass -File metrics\scripts\omv_accept_route2.ps1
#   ... -Seconds 300 -BurstSeconds 0.25 -ApiBase http://127.0.0.1:8031
#   ... -Mode usb_jpeg          # the OLD on-camera-JPEG path, for comparison
#   ... -NoAge                  # skip the age sampler (faster smoke test)

param(
    [int]$Seconds = 300,
    [double]$BurstSeconds = 0.25,
    [string]$ApiBase = 'http://127.0.0.1:8031',
    [ValidateSet('usb_gray', 'usb_jpeg')][string]$Mode = 'usb_gray',
    [int]$Quality = 50,
    [switch]$NoAge,
    [switch]$KeepApi,
    # Internal: this same file runs as the age sampler when called with -Sampler.
    # ⚠️ This switch MUST NOT share a name (case-INSENSITIVELY!) with any local variable in the
    # driver part below.  `$sampler = Start-Process ...` once collided with `$Sampler`, and the
    # assignment died with "Cannot convert System.Diagnostics.Process to SwitchParameter" --
    # a process-launch failure whose message points nowhere near the real cause.
    [switch]$Sampler,
    [string]$OutFile = ''
)

$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------------------
# Sampler mode: docs/18 section 3.1, its own process.
# ---------------------------------------------------------------------------
if ($Sampler) {
    $maxAge = 0.0; $n = 0; $fresh = 0
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
        Start-Sleep -Milliseconds 500
        try { $a = (Invoke-RestMethod "$ApiBase/api/video_status" -TimeoutSec 5).age_s } catch { continue }
        $n++
        if ($null -ne $a) {
            if ($a -gt $maxAge) { $maxAge = $a }
            if ($a -lt 1.5) { $fresh++ }     # 1.5 s = the front end's staleness line (api.py)
        }
    }
    $pct = if ($n -gt 0) { 100.0 * $fresh / $n } else { 0 }
    $line = "SAMPLES=$n MAXAGE=$([math]::Round($maxAge,2)) FRESH=$fresh VISIBLE_PCT=$([math]::Round($pct,1))"
    if ($OutFile) { $line | Out-File -Encoding ascii $OutFile }
    Write-Output $line
    exit 0
}

. (Join-Path $PSScriptRoot 'omv_stream_bridge.ps1')

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

function Test-ApiUp {
    try { $null = Invoke-RestMethod "$ApiBase/api/video_status" -TimeoutSec 3; return $true }
    catch { return $false }
}

# ---- 1. API -------------------------------------------------------------------------------
$apiProc = $null
if (Test-ApiUp) {
    Write-Output "[accept] api already up at $ApiBase"
} else {
    # Start api.py on the SAME host/port the sampling and posting use -- if -ApiBase points
    # somewhere else, starting our own on a hard-coded port would silently test nothing.
    $apiUri = [Uri]$ApiBase
    $apiPort = if ($apiUri.Port -gt 0) { $apiUri.Port } else { 8031 }
    $apiLog = Join-Path $env:TEMP 'omv_accept_api.log'
    $apiProc = Start-Process $python -ArgumentList @('backend/api.py', '--no-mock',
        '--host', $apiUri.Host, '--port', "$apiPort") -PassThru -NoNewWindow -RedirectStandardOutput $apiLog
    $up = $false
    for ($i = 0; $i -lt 40 -and -not $up; $i++) { Start-Sleep -Milliseconds 500; $up = Test-ApiUp }
    Write-Output "[accept] api started on $($apiUri.Host):$apiPort : $up (log: $apiLog)"
    if (-not $up) { Write-Output "[accept] API DID NOT COME UP -- nothing to POST to, stopping"; exit 1 }
}

# ---- 2. camera side ------------------------------------------------------------------------
Write-Output "=== [1/4] Send-OMVStreamer (Mode=$Mode, BurstSeconds=$BurstSeconds) ==="
$streamOut = Send-OMVStreamer -Mode $Mode -Quality $Quality
Write-Output $streamOut

# ---- 3. age sampler, separate process ------------------------------------------------------
$samplerProc = $null
$ageLog = Join-Path $env:TEMP 'omv_age.txt'
if (-not $NoAge) {
    if (Test-Path $ageLog) { Remove-Item $ageLog -Force }
    # Re-invoke THIS file.  $PSScriptRoot is the reliable one: $PSCommandPath came back empty when
    # this script was started with `powershell -File` from a parent that was not itself a script,
    # and an empty -File makes the child sit waiting instead of sampling (cost: one silent run).
    $self = Join-Path $PSScriptRoot 'omv_accept_route2.ps1'
    if (-not (Test-Path $self)) { $self = $PSCommandPath }
    try {
        $samplerProc = Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', $self, '-Sampler', '-ApiBase', $ApiBase, '-Seconds', $Seconds,
            '-OutFile', $ageLog) -PassThru -NoNewWindow `
            -RedirectStandardOutput (Join-Path $env:TEMP 'omv_age_stdout.txt') `
            -RedirectStandardError (Join-Path $env:TEMP 'omv_age_stderr.txt') -ErrorAction Stop
    } catch {
        Write-Output "[accept] age sampler FAILED TO START: $($_.Exception.Message)"
    }
    if ($samplerProc) {
        Write-Output "[accept] age sampler started (pid $($samplerProc.Id))"
    }
}

# ---- 4. receiver ---------------------------------------------------------------------------
Write-Output "=== [2/4] Send-OMVFramesToApi (${Seconds}s) ==="
Write-Output (Send-OMVFramesToApi -TotalSeconds $Seconds -BurstSeconds $BurstSeconds -ApiBase $ApiBase -SaveMax 0)

# ---- teardown + collect --------------------------------------------------------------------
Write-Output "=== [3/4] age sampler (docs/18 section 3.1) ==="
if ($samplerProc) {
    $deadline = (Get-Date).AddSeconds(30)
    while (-not $samplerProc.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    if (-not $samplerProc.HasExited) { Stop-Process -Id $samplerProc.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}
if (Test-Path $ageLog) { Write-Output (Get-Content $ageLog -Raw) } else { Write-Output "(no age log)" }

Write-Output "=== [4/4] video_status + Stop-OMVStreamer ==="
try { Write-Output ((Invoke-RestMethod "$ApiBase/api/video_status" -TimeoutSec 5) | ConvertTo-Json -Compress) } catch { }
Write-Output (Stop-OMVStreamer)

if ($apiProc -and -not $KeepApi) {
    Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
    Write-Output "[accept] api stopped"
}
Write-Output "[accept] done"
