# run_tier_sim_all.ps1 -- C-line: run the v1.5 measurement-tier simulation matrix.
#
# WHY THIS SCRIPT EXISTS
#   1) The v1.5 tiers (720p60 / 1080p45) each need their OWN golden reference and
#      their OWN csim run. Typing five `vitis-run` invocations by hand is exactly how
#      the "silent data-dir fallback" trap happened on 2026-10-01 (a run that was
#      believed to be 480x270 was actually 384x288). One script, one place to be right.
#   2) `vitis-run` overwrites fpga/logs/hls_run_tcl.log on every run, so each entry
#      writes its own log under the repo root; the passing evidence must be copied to
#      fpga/report/logs/ afterwards.
#   3) cosim is a DIFFERENT check from csim (csim does not model hls::stream FIFO
#      depth) and it is SLOW, so it is opt-in via -WithCosim.
#
# NOTE ON ENCODING -- deliberately ASCII-only.
#   Windows PowerShell 5.1 decodes a BOM-less .ps1 as GBK, which corrupts non-ASCII
#   text and can break the parser. Same rule as run_cosim_all.ps1; do not add Chinese
#   here unless you save the file as UTF-8 WITH BOM.
#
# USAGE
#   powershell -NoProfile -ExecutionPolicy Bypass -File fpga\run_tier_sim_all.ps1
#     -> the four csim+csynth entries (fast, ~12 min total)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File fpga\run_tier_sim_all.ps1 `
#              -WithCosim -Name 'roi_720p_cosim'
#     -> one cosim entry (slow: the 720p cosim is ~25 min, the 1080p one is ~55 min)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File fpga\run_tier_sim_all.ps1 `
#              -WithCosim -Name 'roi_*'
#     -> both tier cosims
#
# OUTPUT: D:\Desktop\AMD\_tier_<name>.log -- grep for 'RESULT' / 'co-simulation finished'.

param(
    # Wildcard filter over the entry name (default: every non-cosim entry).
    [string]$Name = '*',
    # Also run the entries flagged cosim=$true (slow; skipped by default).
    [switch]$WithCosim
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$fpga = Join-Path $repo 'fpga'
Set-Location $fpga

# ---------------------------------------------------------------------------
# The matrix.  data = explicit ROI_DATA_DIR (relative to fpga/);
#              tier = VIGILENS_TIER instead (rgb2gray decimation + default data dir);
#              neither = the built-in 640x480 default of that IP.
# ---------------------------------------------------------------------------
$plan = @(
    @{ name = 'mq_640';            ip = 'motion_quality'; data = 'sim\data_motion';          tier = $null;      exec = 2; cosim = $true  },
    @{ name = 'mq_1080p45';        ip = 'motion_quality'; data = $null;                      tier = '1080p45';  exec = 1; cosim = $false },
    @{ name = 'mq_1080p45_cosim';  ip = 'motion_quality'; data = $null;                      tier = '1080p45';  exec = 2; cosim = $true  },
    @{ name = 'roi_720p';          ip = 'roi_statistic';  data = 'sim\data_roi_720p';        tier = $null;      exec = 1; cosim = $false },
    @{ name = 'roi_1080p';         ip = 'roi_statistic';  data = 'sim\data_roi_1080p';       tier = $null;      exec = 1; cosim = $false },
    @{ name = 'roi_720p_cosim';    ip = 'roi_statistic';  data = 'sim\data_roi_720p_cosim';  tier = $null;      exec = 2; cosim = $true  },
    @{ name = 'roi_1080p_cosim';   ip = 'roi_statistic';  data = 'sim\data_roi_1080p_cosim'; tier = $null;      exec = 2; cosim = $true  }
)

$ran = 0; $failed = 0
foreach ($p in $plan) {
    if ($p.name -notlike $Name) { continue }
    if ($p.cosim -and -not $WithCosim) { continue }
    $ran++

    $env:HLS_IP = $p.ip
    $env:HLS_COMPONENT = "component_tier_$($p.name)"
    $env:HLS_EXEC = "$($p.exec)"
    if ($p.data) {
        $env:ROI_DATA_DIR = Join-Path $fpga $p.data
    } else {
        Remove-Item Env:\ROI_DATA_DIR -ErrorAction SilentlyContinue
    }
    if ($p.tier) {
        $env:VIGILENS_TIER = $p.tier
    } else {
        Remove-Item Env:\VIGILENS_TIER -ErrorAction SilentlyContinue
    }

    $log = Join-Path $repo "_tier_$($p.name).log"
    Write-Host "===== tier run: $($p.name)  ip=$($p.ip)  exec=$($p.exec)  data=$($p.data)$($p.tier) ====="
    $sw = [Diagnostics.Stopwatch]::StartNew()
    cmd /c "call D:\Xilinx\2026.1\Vitis\settings64.bat >nul 2>&1 && vitis-run --mode hls --tcl run_hls.tcl > `"$log`" 2>&1"
    $rc = $LASTEXITCODE
    Write-Host ("      exit=$rc  elapsed=$([int]$sw.Elapsed.TotalSeconds)s  log=$log")
    if (Test-Path $log) {
        Select-String -Path $log -Pattern 'RESULT|Layer [12]:|co-simulation finished|ERROR|FATAL' |
            Select-Object -Last 6 | ForEach-Object { "      " + $_.Line.Trim() }
    }
    if ($rc -ne 0) { $failed++ }
}

if ($ran -eq 0) {
    Write-Host "No entry matched -Name '$Name' (with -WithCosim=$WithCosim). Nothing ran."
    exit 2
}

Write-Host ''
Write-Host "DONE. ran=$ran failed=$failed"
Write-Host 'Pass criteria: every entry shows "==== RESULT: PASS ====" and'
Write-Host '(when -WithCosim) "C/RTL co-simulation finished: PASS" with no deadlock.'
exit ([int]($failed -gt 0))
