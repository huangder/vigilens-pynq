# omv_camera_demo.ps1 -- one command: OpenMV camera -> web page picture (+ A-line metrics if a
# pipeline is already posting to the same API).
#
# WHY THIS EXISTS
#   The camera-side JPEG stream is not reliable indefinitely: after a few hundred frames
#   img.compress() starts raising "OSError: Compression Failed!" (memory pressure / heap
#   fragmentation -- see docs/16 BUG-027).  The host side therefore watches
#   /api/video_status and restarts the camera stream whenever the picture goes stale, so a
#   demo can run unattended instead of silently freezing.
#
#   THE FIX (default since 2026-09-28) is docs/18 route 2: -Mode usb_gray makes the camera send
#   RAW GRAYSCALE and never call img.compress() at all -- the JPEG is encoded on the PC
#   (omv_stream_bridge.ps1).  The failure mode is therefore gone by construction, and the
#   watchdog below is only a safety net.  If the picture is NOW flapping again, the thing to
#   look at is the bridge summary line (conv_fail=, gray=), not the encoder.
#
#   Quality only matters for the OLD path (-Mode usb_jpeg): measured on this camera, QVGA +
#   quality=80 fails outright while quality=50 streams (9337 B/frame).  See docs/16 BUG-027.
#
# ASCII-ONLY ON PURPOSE (PowerShell 5.1 decodes BOM-less .ps1 as GBK).
#
# USAGE (one command: starts api.py + the camera stream; add -WithMetrics for numbers)
#   powershell -NoProfile -ExecutionPolicy Bypass -File metrics\scripts\omv_camera_demo.ps1 -WithMetrics
#   ... -ApiBase http://127.0.0.1:8031 -StaleSeconds 8 -TotalSeconds 3600
#   ... -Mode usb_jpeg -Quality 50         # fall back to the old on-camera-JPEG path
#   ... -NoApi                             # api.py is already running / started by hand
#
# IGNORE THE PAGE'S DEFAULT WebSocket BOX: when api.py hosts the page, app.js fills it with
# ws://<host>/ws automatically (it probes /api/status).  Just open the URL this script prints.

param(
    [string]$ApiBase = 'http://127.0.0.1:8031',
    [ValidateSet('usb_gray', 'usb_jpeg')][string]$Mode = 'usb_gray',
    [int]$Quality = 50,
    [int]$StaleSeconds = 8,
    [int]$WarmupTimeoutSeconds = 45,
    [int]$TotalSeconds = 3600,
    # 0.25 s keeps the posted picture fresh (the page hides frames older than 1.5 s).
    # Cost: a burst boundary can split a frame, so roughly one frame per burst is dropped.
    [double]$BurstSeconds = 0.25,
    # Start backend/api.py (and therefore the web page) if nothing answers on $ApiBase.
    [switch]$NoApi,
    # Also run the A-line pipeline on the MJPEG bypass, so the page shows NUMBERS + curves,
    # not just the picture.  Uses real MediaPipe when it is installed (check the log line
    # "[face_landmark] 使用 MediaPipe FaceMesh"); with --stub the metrics are meaningless.
    [switch]$WithMetrics,
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'omv_stream_bridge.ps1')

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

function Get-FrameAge {
    $r = $null
    try { $r = Invoke-RestMethod "$ApiBase/api/video_status" -TimeoutSec 5 } catch { return $null }
    return $r.age_s
}

# ---- 0. web page (api.py) ------------------------------------------------------------------
$apiProc = $null
$apiUp = $false
try { $null = Invoke-RestMethod "$ApiBase/api/video_status" -TimeoutSec 3; $apiUp = $true } catch { }
if ($apiUp) {
    Write-Output "[demo] api already up at $ApiBase"
} elseif ($NoApi) {
    Write-Output "[demo] -NoApi and nothing answers at ${ApiBase}: the page will NOT update"
} else {
    $apiUri = [Uri]$ApiBase
    $apiPort = if ($apiUri.Port -gt 0) { $apiUri.Port } else { 8031 }
    $apiLog = Join-Path $env:TEMP 'omv_demo_api.log'
    $apiProc = Start-Process $python -ArgumentList @('backend/api.py', '--no-mock',
        '--host', $apiUri.Host, '--port', "$apiPort") -PassThru -NoNewWindow -RedirectStandardOutput $apiLog
    for ($i = 0; $i -lt 40 -and -not $apiUp; $i++) {
        Start-Sleep -Milliseconds 500
        try { $null = Invoke-RestMethod "$ApiBase/api/video_status" -TimeoutSec 3; $apiUp = $true } catch { }
    }
    Write-Output "[demo] api started on $($apiUri.Host):$apiPort : $apiUp (log: $apiLog)"
}

# ---- 0b. metrics channel: started LATER, see Start-DemoMetrics -----------------------------
# ⚠️ ORDER MATTERS: the A-line pipeline reads /video.mjpg.  Starting it before the camera has
# posted its first frame makes that read time out ("[warn] MJPEG 流中断 ... timed out") and the
# pipeline then never receives anything -- the page shows a picture but zero numbers.
# So it is started from inside the cycle loop, right after the first fresh frame (measured
# 2026-09-27: the wrong order produced published=0 for a whole 110 s run).
$script:metricsProc = $null
$metricsLog = Join-Path $env:TEMP 'omv_demo_metrics.log'

function Start-DemoMetrics {
    if (-not $WithMetrics) { return }
    if ($script:metricsProc -and -not $script:metricsProc.HasExited) { return }
    $script:metricsProc = Start-Process $python -ArgumentList @('backend/run_pipeline.py',
        '--source', "$ApiBase/video.mjpg", '--wall-clock',
        '--post', "$ApiBase/api/ingest", '--summary', 'metrics/logs/demo_web_summary.json',
        '--print-every', '15') -PassThru -NoNewWindow -RedirectStandardOutput $metricsLog
    Write-Output "[demo] metrics pipeline started (pid $($script:metricsProc.Id)) -> $metricsLog"
    Write-Output "[demo]   ^ check that log for: [face_landmark] ... MediaPipe FaceMesh  (stub => metrics meaningless)"
}

function Stop-DemoMetrics {
    if ($script:metricsProc -and -not $script:metricsProc.HasExited) {
        Stop-Process -Id $script:metricsProc.Id -Force -ErrorAction SilentlyContinue
        Write-Output "[demo] metrics pipeline stopped (restarts with the camera)"
    }
}

$demoUrl = "http://$(([Uri]$ApiBase).Host):$(([Uri]$ApiBase).Port)/"
Write-Output "[demo] ------------------------------------------------------------------"
Write-Output "[demo] OPEN THIS PAGE:  $demoUrl"
Write-Output "[demo]   - the WebSocket box fills itself in when api.py hosts the page"
Write-Output "[demo]   - picture = bypass (route 2 gray -> PC JPEG); numbers/curves = contract frames"
Write-Output "[demo]   - if the picture is black / status says unreliable, that is the quality gate"
Write-Output "[demo]     working: check the lens cap, the light, and that a face is in view"
Write-Output "[demo] ------------------------------------------------------------------"
if ($OpenBrowser) { Start-Process $demoUrl | Out-Null }

$deadline = (Get-Date).AddSeconds($TotalSeconds)
$cycle = 0
Write-Output ("[demo] api={0} mode={1} quality={2} burst={3}s stale>{4}s total={5}s" -f `
    $ApiBase, $Mode, $Quality, $BurstSeconds, $StaleSeconds, $TotalSeconds)

while ((Get-Date) -lt $deadline) {
    $cycle++
    $log = Join-Path $env:TEMP ("omv_cycle_{0}.log" -f $cycle)
    $cmd = ". '" + (Join-Path $PSScriptRoot 'omv_stream_bridge.ps1') + "'; " +
           "Send-OMVStreamer -Mode $Mode -Quality $Quality; " +
           "Send-OMVFramesToApi -TotalSeconds 600 -BurstSeconds $BurstSeconds -ApiBase '$ApiBase' -SaveMax 0"
    $b = Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $cmd) `
                                 -PassThru -NoNewWindow -RedirectStandardOutput $log

    # Warm-up: wait for the FIRST fresh frame before judging staleness (right after a restart
    # the last frame is minutes old, which would otherwise look like a stall immediately).
    $warm = $false
    $waited = 0
    while ($waited -lt $WarmupTimeoutSeconds -and -not $b.HasExited) {
        Start-Sleep -Seconds 3
        $waited += 3
        $age = Get-FrameAge
        if ($null -ne $age -and $age -lt 2.0) { $warm = $true; break }
    }

    if ($warm) {
        Write-Output ("[demo] cycle {0}: streaming (first fresh frame seen)" -f $cycle)
        # NOW there is something for the pipeline to read.
        Start-DemoMetrics
        while (-not $b.HasExited -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            $age = Get-FrameAge
            if ($null -ne $age -and $age -gt $StaleSeconds) {
                Write-Output ("[demo] cycle {0}: camera stream stalled (age={1:N1}s) -> restarting" -f $cycle, $age)
                break
            }
        }
    } else {
        $reason = if ($b.HasExited) { 'bridge process exited' } else { 'no fresh frame within warm-up' }
        Write-Output ("[demo] cycle {0}: {1} -> restarting" -f $cycle, $reason)
    }

    Stop-Process -Id $b.Id -Force -ErrorAction SilentlyContinue
    # A sensor reset invalidates the pipeline's MJPEG reader, so restart both together and keep
    # the pair in sync instead of leaving a live-but-deaf pipeline behind.
    Stop-DemoMetrics
    Start-Sleep -Seconds 2
}

Write-Output ("[demo] done after {0} cycles" -f $cycle)

# Leave the machine as we found it: only stop the processes THIS run started.
Stop-DemoMetrics
if ($apiProc) {
    Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
    Write-Output "[demo] api stopped"
}
