# =============================================================================
# run_xsim_chain.ps1 - RTL simulation: ROI -> rgb2gray -> motion_quality
# -----------------------------------------------------------------------------
# Usage (repo root, Windows PowerShell):
#   powershell -ExecutionPolicy Bypass -File board\sim\run_xsim_chain.ps1
#
# Outputs under %TEMP%\vigilens_xsim\ by default:
#   clip\frames.bin / expected.json / sim_config.vh   input frames + golden values
#   work\                             xvlog/xelab/xsim work directory
#   sim.log                           real simulation log
#
# This is RTL simulation, NOT a board run. It bypasses PS7/DMA/AXI interconnect
# and does not measure JTAG bandwidth.
# =============================================================================
param(
    [string]$WorkDir = (Join-Path $env:TEMP "vigilens_xsim"),
    [int]$Frames = 3,
    [string]$Source = "",
    [switch]$AllowResize
)

$ErrorActionPreference = "Stop"
if ($Frames -lt 2) { throw "-Frames must be >= 2 (motion_quality frame 1 has no valid diff)" }
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Project interpreter not found: $Python"
}

$VivadoBin = "D:\Xilinx\2026.1\Vivado\bin"
if (-not (Test-Path (Join-Path $VivadoBin "xvlog.bat"))) {
    throw "Vivado Simulator not found: $VivadoBin"
}
$env:PATH = "$VivadoBin;$env:PATH"

$Clip = Join-Path $WorkDir "clip"
$Work = Join-Path $WorkDir "work"
New-Item -ItemType Directory -Force -Path $Clip, $Work | Out-Null

Write-Host "== 1/4 generate frames + expected values =="
$genArgs = @("--out", $Clip, "--frames", $Frames)
if ($Source) {
    $genArgs += @("--source", $Source)
    if ($AllowResize) { $genArgs += "--allow-resize" }
}
& $Python (Join-Path $Root "board\sim\gen_sim_frames.py") @genArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== 2/4 xvlog: compile 3 HLS IPs + testbench =="
$files = @()
$files += Get-ChildItem (Join-Path $Root "fpga\component_roi_statistic\hls\impl\ip\hdl\verilog\*.v") |
    ForEach-Object FullName
$files += Get-ChildItem (Join-Path $Root "fpga\component_rgb2gray\hls\impl\ip\hdl\verilog\*.v") |
    ForEach-Object FullName
$files += Get-ChildItem (Join-Path $Root "fpga\component_motion_quality\hls\impl\ip\hdl\verilog\*.v") |
    ForEach-Object FullName
$files += (Join-Path $Root "board\sim\tb_pl_chain.v")

Push-Location $Work
try {
    # TB includes sim_config.vh for W/H/GW/GH/NFRAMES. Copy it here and add this
    # directory to the include search path (xvlog cannot take -d name=value on Windows).
    Copy-Item (Join-Path $Clip "sim_config.vh") (Join-Path $Work "sim_config.vh") -Force
    & xvlog -sv --work work -i $Work @files
    if ($LASTEXITCODE -ne 0) {
        Write-Host "xvlog failed, see $Work\xvlog.log"
        exit $LASTEXITCODE
    }

    Write-Host "== 3/4 xelab: build snapshot =="
    & xelab -debug typical --nolog tb_pl_chain -s tb_pl_chain
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "== 4/4 xsim: run $Frames frames =="
    # tb reads frames.bin from its current directory. Also copy the BRAM init .dat,
    # otherwise XSim warns and prev_buf reads as X.
    Copy-Item (Join-Path $Clip "frames.bin") (Join-Path $Work "frames.bin") -Force
    Copy-Item (Join-Path $Root "fpga\component_motion_quality\hls\impl\ip\hdl\verilog\motion_quality_motion_quality_Pipeline_VITIS_LOOP_90_1_prev_buf_RAM_2P_BRAM_1R1W.dat") $Work -Force
    $log = Join-Path $WorkDir "sim.log"
    & xsim tb_pl_chain -R 2>&1 | Out-File -Encoding utf8 $log
    if ($LASTEXITCODE -ne 0) {
        Write-Host "xsim failed, see $log"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host "== compare =="
& $Python (Join-Path $Root "board\sim\check_xsim_log.py") `
    --log (Join-Path $WorkDir "sim.log") `
    --expected (Join-Path $Clip "expected.json")
exit $LASTEXITCODE
