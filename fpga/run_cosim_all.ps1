# run_cosim_all.ps1 -- C-line: run cosim (HLS_EXEC=2) for all 7 IPs, logging to files.
#
# WHY THIS SCRIPT EXISTS (instead of typing commands in a terminal):
#   1) `csim` does NOT model `hls::stream` FIFO depth, so a deadlock caused by an
#      undersized stream is only exposed by cosim. cosim is a DIFFERENT check,
#      not "the same test again".
#   2) NEVER watch progress with `| Select-Object -Last N`: it only emits after the
#      upstream command finishes, which makes "running for 10 minutes" look like
#      "hung". This script always redirects to a log file; watch the file instead.
#   3) Use the SMALL vectors (data_*_small) or C/RTL cosim takes far too long.
#
# NOTE ON ENCODING -- deliberately ASCII-only.
#   Windows PowerShell 5.1 decodes a BOM-less .ps1 as GBK, which corrupts non-ASCII
#   text and can break the parser ("The string is missing the terminator").
#   This repo documents that trap; do not "restore" any Chinese text here unless
#   you save the file as UTF-8 WITH BOM.
#
# USAGE (any terminal; vitis-run itself may need full permissions):
#   powershell -NoProfile -ExecutionPolicy Bypass -File fpga\run_cosim_all.ps1
# OUTPUT: D:\Desktop\AMD\_cosim_<ip>.log  -- look for RESULT / cosim summary / deadlock

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$fpga = Join-Path $repo 'fpga'
Set-Location $fpga

# ip -> small-size data dir (relative to fpga/); rgb2gray/motion_quality use the
# 720p60 tier ratio (3/8), which is what the two new pixel-source tiers need.
$plan = @(
    @{ ip = 'roi_statistic';  data = 'sim\data_small';        tier = $null },
    @{ ip = 'rgb2gray';       data = 'sim\data_motion_small'; tier = '720p60' },
    @{ ip = 'motion_quality'; data = 'sim\data_motion_small'; tier = '720p60' },
    @{ ip = 'fir_filter';     data = 'sim\data_fir_small';    tier = $null },
    @{ ip = 'raw10_unpack';   data = 'sim\data_mipi';         tier = $null },
    @{ ip = 'bayer_demosaic'; data = 'sim\data_mipi';         tier = $null },
    @{ ip = 'frame_scale';    data = 'sim\data_scale';        tier = $null }
)

foreach ($p in $plan) {
    $env:HLS_IP = $p.ip
    $env:HLS_COMPONENT = "component_cosim_$($p.ip)"
    $env:HLS_EXEC = '2'
    $env:ROI_DATA_DIR = Join-Path $fpga $p.data
    if ($p.tier) {
        $env:VIGILENS_TIER = $p.tier
    } else {
        Remove-Item Env:\VIGILENS_TIER -ErrorAction SilentlyContinue
    }

    $log = Join-Path $repo "_cosim_$($p.ip).log"
    Write-Host "===== cosim: $($p.ip)  data=$($p.data)  tier=$($p.tier) ====="
    $sw = [Diagnostics.Stopwatch]::StartNew()
    cmd /c "call D:\Xilinx\2026.1\Vitis\settings64.bat >nul 2>&1 && vitis-run --mode hls --tcl run_hls.tcl > `"$log`" 2>&1"
    $rc = $LASTEXITCODE
    Write-Host ("      exit=$rc  elapsed=$([int]$sw.Elapsed.TotalSeconds)s  log=$log")
    if (Test-Path $log) {
        Select-String -Path $log -Pattern 'Layer [12]:|RESULT|C/RTL|cosim|PASS|FAIL|deadlock|ERROR' |
            Select-Object -Last 6 | ForEach-Object { "      " + $_.Line.Trim() }
    }
}

Write-Host ''
Write-Host 'DONE. Inspect each _cosim_<ip>.log. Pass criteria: RESULT: PASS and no deadlock/timeout in the cosim summary.'
