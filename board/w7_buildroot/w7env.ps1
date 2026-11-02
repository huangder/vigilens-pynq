# w7env.ps1 -- make vivado / vitis / bootgen usable from any PowerShell prompt,
# and put you in the repo root so the relative paths in the docs just work.
#
# Usage (dot-source, so the changes stay in YOUR shell):
#     . D:\Desktop\AMD\board\w7_buildroot\w7env.ps1
#
# Optional: set $env:XILINX_ROOT first if your tools are not in D:\Xilinx\2026.1
#
# Notes
#  * ASCII only: Windows PowerShell 5.1 decodes BOM-less .ps1 as GBK, and a
#    stray CJK byte pair can break the parser (docs/41 sec 16.3).
#  * This only fixes PATH + working directory. It does NOT fix a locked-down
#    %APPDATA% (that makes Vivado print "Could not open 'C' for writing"); in a
#    normal user shell %APPDATA% is writable, so this is not an issue there.

if (-not $env:XILINX_ROOT) { $env:XILINX_ROOT = 'D:\Xilinx\2026.1' }
$XilinxRoot = $env:XILINX_ROOT

# Vivado falls back to the CURRENT DIRECTORY when HOME is unset and drops
# "<version>\XilinxTclStore" there (seen in a locked-down shell: the repo grew a
# stray 2026.1/ directory). Point HOME at the user profile if it is empty.
if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Need      = @(
    (Join-Path $XilinxRoot 'Vivado\bin'),
    (Join-Path $XilinxRoot 'Vitis\bin')
)

foreach ($dir in $Need) {
    if (-not (Test-Path $dir)) {
        Write-Host "[w7env] MISSING dir: $dir" -ForegroundColor Yellow
        Write-Host "[w7env] set `$env:XILINX_ROOT=<your Xilinx install root> and retry" -ForegroundColor Yellow
        return
    }
    if ($env:PATH -notlike "*$dir*") { $env:PATH = "$dir;$env:PATH" }
}

# Make the tools agree with the PATH above (their .bat wrappers read these too).
$env:XILINX_VIVADO = Join-Path $XilinxRoot 'Vivado'
$env:XILINX_VITIS  = Join-Path $XilinxRoot 'Vitis'

if ((Get-Location).Path -ne $RepoRoot) { Set-Location $RepoRoot }

$probe = @('vivado', 'bootgen', 'vitis', 'xsdb', 'sdtgen', 'dtc', 'arm-none-eabi-gcc')
foreach ($t in $probe) {
    $cmd = Get-Command $t -ErrorAction SilentlyContinue
    if ($cmd) { Write-Host ("[w7env] ok   {0,-20} {1}" -f $t, $cmd.Source) }
    else      { Write-Host ("[w7env] --   {0,-20} not on PATH" -f $t) -ForegroundColor DarkGray }
}

Write-Host "[w7env] cwd      = $((Get-Location).Path)"
Write-Host "[w7env] XSA out  = (export_xsa.tcl writes to the first writable of: `$VIGILENS_XSA_OUT, <repo>\board\bitstream)"
Write-Host "[w7env] next     : vivado -mode batch -source board\w7_buildroot\export_xsa.tcl -nojournal -nolog"
