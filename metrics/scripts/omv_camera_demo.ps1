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
#   Quality default is 50, NOT the usual 80: measured on this camera, QVGA + quality=80
#   fails outright while quality=50 streams (9337 B/frame).  See docs/16 BUG-027.
#
# ASCII-ONLY ON PURPOSE (PowerShell 5.1 decodes BOM-less .ps1 as GBK).
#
# USAGE (API must already run: python backend\api.py --no-mock --host 127.0.0.1 --port 8031)
#   powershell -NoProfile -ExecutionPolicy Bypass -File metrics\scripts\omv_camera_demo.ps1
#   ... -ApiBase http://127.0.0.1:8031 -Quality 50 -StaleSeconds 8 -TotalSeconds 3600

param(
    [string]$ApiBase = 'http://127.0.0.1:8031',
    [int]$Quality = 50,
    [int]$StaleSeconds = 8,
    [int]$WarmupTimeoutSeconds = 45,
    [int]$TotalSeconds = 3600
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'omv_stream_bridge.ps1')

function Get-FrameAge {
    $r = $null
    try { $r = Invoke-RestMethod "$ApiBase/api/video_status" -TimeoutSec 5 } catch { return $null }
    return $r.age_s
}

$deadline = (Get-Date).AddSeconds($TotalSeconds)
$cycle = 0
Write-Output ("[demo] api={0} quality={1} stale>{2}s total={3}s" -f $ApiBase, $Quality, $StaleSeconds, $TotalSeconds)

while ((Get-Date) -lt $deadline) {
    $cycle++
    $log = Join-Path $env:TEMP ("omv_cycle_{0}.log" -f $cycle)
    $cmd = ". '" + (Join-Path $PSScriptRoot 'omv_stream_bridge.ps1') + "'; " +
           "Send-OMVStreamer -Quality $Quality; " +
           "Send-OMVFramesToApi -TotalSeconds 600 -ApiBase '$ApiBase' -SaveMax 0"
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
        Write-Output ("[demo] cycle {0}: streaming" -f $cycle)
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
    Start-Sleep -Seconds 2
}

Write-Output ("[demo] done after {0} cycles" -f $cycle)
