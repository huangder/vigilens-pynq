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
# ENCODING: this file contains Chinese comments, so it MUST be saved as UTF-8 WITH BOM.
#   Windows PowerShell 5.1 decodes a BOM-less .ps1 as GBK; the Chinese then decodes into bytes
#   that can break the parser ("Unexpected token" / "The assignment expression is not valid"),
#   which looks like a code bug but is purely an encoding problem.  AGENTS.md 6.4 documents
#   this trap.  Keep the file ASCII if you would rather not depend on the BOM.
#
# USAGE (one command: starts api.py + the camera stream; add -WithMetrics for numbers)
#   powershell -NoProfile -ExecutionPolicy Bypass -File metrics\scripts\omv_camera_demo.ps1 -WithMetrics
#   ... -ApiBase http://127.0.0.1:8031 -StaleSeconds 8 -TotalSeconds 3600
#   ... -Mode usb_jpeg -Quality 50         # fall back to the old on-camera-JPEG path
#   ... -NoApi                             # api.py is already running / started by hand
#   ... -SelfTest                          # offline check of the BUG-031 drop detector only
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
    # "[face_landmark] ... MediaPipe FaceMesh"); with --stub the metrics are meaningless.
    [switch]$WithMetrics,
    [switch]$OpenBrowser,
    # BUG-031: run only the offline self-test of Get-OVMDropReason (no API, no camera).
    [switch]$SelfTest
)

$ErrorActionPreference = 'Continue'

# ⚠️ CAPTURE THIS BEFORE DOT-SOURCING THE BRIDGE (2026-10-01).
# omv_stream_bridge.ps1 declares its own `param([switch]$SelfTest)`.  Dot-sourcing a script
# runs its param block IN THIS SCOPE, so `$SelfTest` gets rebound to $false and our own
# `-SelfTest` silently stops working -- the run then falls through and starts api.py plus a
# full demo cycle.  That is exactly what happened on the first version of this self-test:
# `-SelfTest` was ignored.  So snapshot the switch under a name the bridge cannot touch.
$Script:RunSelfTest = [bool]$SelfTest

. (Join-Path $PSScriptRoot 'omv_stream_bridge.ps1')
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

function Get-FrameAge {
    $r = $null
    try { $r = Invoke-RestMethod "$ApiBase/api/video_status" -TimeoutSec 5 } catch { return $null }
    return $r.age_s
}

# ---------------------------------------------------------------------------
# BUG-031: separate "the camera is no longer on USB" from "this cycle just stalled"
# ---------------------------------------------------------------------------
# Why: the watchdog used to ask only "is there a fresh frame?".  So when the camera vanished
# from USB mid-demo, the real cause (`The port 'COM10' does not exist.`) was buried in
# %TEMP%\omv_cycle_N.log while the script spun 60+ rounds saying only `bridge process exited`
# (real hardware, 2026-09-27, cycles 3..64).  That sends whoever is debugging in the wrong
# direction: it looks like "the camera is alive, only the stream stalled".
#
# The decision is a PURE function so it can be regression-tested offline (no camera needed):
#   input  = this cycle's log text + the COM ports that currently exist
#   output = a conclusion string, or $null meaning "nothing special, keep going"
# It does no IO; the IO lives in the preflight below.
function Get-OVMDropReason {
    param(
        [string]$LogText = '',
        [string[]]$Ports = @()
    )
    # (1) the port is gone from the system (unplugged / lost power / reset without re-enumeration)
    if ($LogText -match 'does not exist' -or $LogText -match 'PORT_ERROR') {
        if ($Ports.Count -eq 0) {
            return 'camera is NOT on USB any more (no COM port present; log says "does not exist")'
        }
    }
    # (2) not a single COM port -- stop even if this cycle's log has not named the cause yet
    if ($Ports.Count -eq 0) {
        return 'camera is NOT on USB any more (no COM port present at all)'
    }
    # (3) port present but held by someone else: a DIFFERENT fault, do not report it as unplugged
    if ($LogText -match 'Access is denied' -or $LogText -match 'being used by another process') {
        return 'COM port exists but is BUSY (another program holds it) -- not a cable problem'
    }
    return $null
}

# Preflight before every cycle: if there is no COM port at all, stop immediately instead of
# spinning (BUG-031 acceptance item 1).  Deliberately does NOT guess which port: this script
# does not own the port number (omv_stream_bridge.ps1's Get-OMVPort picks it).  On this machine
# the COM ports are the camera, so "none at all" is a reliable unplug signal.
function Test-OVMCameraPresent {
    $ports = @([System.IO.Ports.SerialPort]::GetPortNames())
    if ($ports.Count -gt 0) { return $true }
    Write-Output "[demo] ------------------------------------------------------------------"
    Write-Output "[demo] STOP: camera is NOT on USB any more (GetPortNames() returned EMPTY)"
    Write-Output "[demo]       The cause is NOT a stalled stream; restarting it cannot help."
    Write-Output "[demo]       Replug the camera (check cable/power) and run this script again."
    Write-Output "[demo]       Cross-check: %TEMP%\omv_cycle_N.log should hold PORT_ERROR."
    Write-Output "[demo] ------------------------------------------------------------------"
    return $false
}

# ---------------------------------------------------------------------------
# Offline self-test (BUG-031): only the decision logic; no camera, no API.
#     powershell -NoProfile -ExecutionPolicy Bypass -File metrics\scripts\omv_camera_demo.ps1 -SelfTest
# Worth keeping because this decision decides whether to STOP and call a human.  Both ways of
# getting it wrong cost something:
#   treating "unplugged" as "stalled"  -> 60+ wasted cycles (BUG-031 itself);
#   treating "port busy" as "unplugged" -> someone needlessly replugs the cable.
# Declared after the functions it calls, because PowerShell resolves names at call time.
# ---------------------------------------------------------------------------
if ($Script:RunSelfTest) {
    $ok = 0
    $bad = @()

    $r = Get-OVMDropReason -LogText 'PORT_ERROR: The port COM10 does not exist.' -Ports @()
    if ($null -ne $r -and $r -match 'NOT on USB') { $ok++ } else { $bad += 'unplug(with log)' }

    $r = Get-OVMDropReason -LogText '' -Ports @()
    if ($null -ne $r -and $r -match 'NOT on USB') { $ok++ } else { $bad += 'unplug(no log)' }

    $r = Get-OVMDropReason -LogText 'streaming fine' -Ports @('COM10')
    if ($null -eq $r) { $ok++ } else { $bad += 'stall-not-unplug' }

    $r = Get-OVMDropReason -LogText 'Access is denied' -Ports @('COM10')
    if ($null -ne $r -and $r -match 'BUSY' -and $r -notmatch 'NOT on USB') { $ok++ } else { $bad += 'port-busy' }

    Write-Output ('[SelfTest] BUG-031 Get-OVMDropReason: {0} passed, {1} failed' -f $ok, $bad.Count)
    foreach ($b in $bad) { Write-Output ('  FAIL: ' + $b) }
    # NOTE: a bare `exit` is NOT enough here.  When the script is launched with -File, `exit`
    # only sets the exit code and the remaining script body still runs -- which made
    # `-SelfTest` also start api.py and run a full demo cycle (caught 2026-10-01 by reading
    # the captured output).  Terminate the process for real.
    if ($bad.Count -eq 0) { Write-Output 'RESULT: PASS'; [Environment]::Exit(0) }
    Write-Output 'RESULT: FAIL'; [Environment]::Exit(1)
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
# ORDER MATTERS: the A-line pipeline reads /video.mjpg.  Starting it before the camera has
# posted its first frame makes that read time out ("[warn] MJPEG stream interrupted ...
# timed out") and the pipeline then never receives anything -- the page shows a picture but
# zero numbers.  So it is started from inside the cycle loop, right after the first fresh frame
# (measured 2026-09-27: the wrong order produced published=0 for a whole 110 s run).
$script:metricsProc = $null
$metricsLog = Join-Path $env:TEMP 'omv_demo_metrics.log'

function Start-DemoMetrics {
    if (-not $WithMetrics) { return }
    if ($script:metricsProc -and -not $script:metricsProc.HasExited) { return }
    # --preview-post is what puts the SAME camera frame on the page's same-source preview
    # panel (B line's /api/preview, added 2026-09-27). Without it the page shows numbers
    # but an empty preview box. The URL must be EXPLICIT: preview_url() defaults to port
    # 8000, while this demo may run on 8031. The box drawn on the page is the bbox of the
    # very same frame, so picture and numbers cannot drift apart.
    $script:metricsProc = Start-Process $python -ArgumentList @('backend/run_pipeline.py',
        '--source', "$ApiBase/video.mjpg", '--wall-clock',
        '--post', "$ApiBase/api/ingest", '--preview-post', "$ApiBase/api/preview",
        '--summary', 'metrics/logs/demo_web_summary.json',
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
if (-not $WithMetrics) {
    # Since 2026-09-27 the page (B-line main) takes its picture from the SAME-SOURCE preview
    # bypass (/api/preview), which is posted by the A-line pipeline -- not by this bridge.
    # So without -WithMetrics the page shows an empty preview box AND no numbers.
    Write-Output "[demo] WARNING: -WithMetrics is NOT set, so the page will look empty:"
    Write-Output "[demo]   the new page's picture comes from the pipeline's /api/preview post,"
    Write-Output "[demo]   and the numbers come from the pipeline's /api/ingest post."
    Write-Output "[demo]   Re-run with -WithMetrics (or start run_pipeline.py yourself)."
}
Write-Output "[demo] ------------------------------------------------------------------"
if ($OpenBrowser) { Start-Process $demoUrl | Out-Null }

$deadline = (Get-Date).AddSeconds($TotalSeconds)
$cycle = 0
Write-Output ("[demo] api={0} mode={1} quality={2} burst={3}s stale>{4}s total={5}s" -f `
    $ApiBase, $Mode, $Quality, $BurstSeconds, $StaleSeconds, $TotalSeconds)

while ((Get-Date) -lt $deadline) {
    $cycle++

    # BUG-031 acceptance item 1: after an unplug the script must say "camera is not on USB"
    # and exit within <=2 cycles.  Probing before each cycle catches it on cycle 1.
    if (-not (Test-OVMCameraPresent)) {
        Write-Output ("[demo] done after {0} cycles (stopped: camera not on USB)" -f $cycle)
        Stop-DemoMetrics
        if ($apiProc) { Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue }
        exit 2
    }

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

    # ---- BUG-031: decide "is the camera still on USB?" BEFORE restarting ----
    # Order matters: read the log and the port list FIRST, kill the process second -- otherwise
    # the evidence is gone.  Previously only "is there a fresh frame?" was asked, so an unplug
    # produced 62 useless cycles (real hardware, 2026-09-27).
    $cycleLog = if (Test-Path $log) { (Get-Content $log -Raw -ErrorAction SilentlyContinue) } else { '' }
    $portsNow = @([System.IO.Ports.SerialPort]::GetPortNames())
    $dropWhy = Get-OVMDropReason -LogText $cycleLog -Ports $portsNow

    Stop-Process -Id $b.Id -Force -ErrorAction SilentlyContinue
    # A sensor reset invalidates the pipeline's MJPEG reader, so restart both together and keep
    # the pair in sync instead of leaving a live-but-deaf pipeline behind.
    Stop-DemoMetrics

    if ($dropWhy) {
        Write-Output "[demo] ------------------------------------------------------------------"
        Write-Output ("[demo] cycle {0}: {1}" -f $cycle, $dropWhy)
        Write-Output "[demo] STOPPING: restarting the stream cannot fix 'camera not on USB' (BUG-031)."
        Write-Output ("[demo]           this cycle's log: {0}" -f $log)
        Write-Output "[demo]           replug the camera (or check power/cable), then run again."
        Write-Output "[demo] ------------------------------------------------------------------"
        break
    }

    Start-Sleep -Seconds 2
}

Write-Output ("[demo] done after {0} cycles" -f $cycle)

# Leave the machine as we found it: only stop the processes THIS run started.
Stop-DemoMetrics
if ($apiProc) {
    Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
    Write-Output "[demo] api stopped"
}
