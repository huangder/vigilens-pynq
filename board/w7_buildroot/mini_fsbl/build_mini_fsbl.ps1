# build_mini_fsbl.ps1 -- build the hand-written first stage (mini_fsbl) with Xilinx arm-none-eabi-gcc
#
# Usage (from repo root, normal PowerShell):
#   powershell -NoProfile -ExecutionPolicy Bypass -File board\w7_buildroot\mini_fsbl\build_mini_fsbl.ps1 `
#       -Dt C:\Users\me\w7\dt -Out C:\Users\me\w7\mini_fsbl
#
# -Dt   : directory that contains ps7_init.c + ps7_init.h (sdtgen output)
# -Out  : directory for ps7_init.c copy, fsbl.elf, fsbl.map (default: %TEMP%\mini_fsbl)
# -Bootgen : also run bootgen to produce boot.bin (needs boot_mini.bif, generated on the fly)
#
# Exit codes: 0 ok; 1 missing input; 2 compile failed; 3 link/elf check failed; 4 bootgen failed
#
# NOTE: output goes OUTSIDE the repo by default. Under the restricted sandbox child
# processes cannot write into the repository, so do not point -Out inside D:\Desktop\AMD.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Dt,
    [string]$Out = (Join-Path $env:TEMP 'mini_fsbl'),
    [string]$Gcc = 'D:\Xilinx\2026.1\Vitis\gnu\aarch32\nt\gcc-arm-none-eabi\bin\arm-none-eabi-gcc.exe',
    [string]$Bootgen = 'D:\Xilinx\2026.1\Vitis\bin\bootgen.bat',
    [switch]$BootgenSwitch
)

# Native tools (gcc/bootgen) write warnings to stderr; with ErrorActionPreference=Stop
# PowerShell 5.1 turns that into a terminating error. Keep Continue and check $LASTEXITCODE.
$ErrorActionPreference = 'Continue'

function Fail([int]$code, [string]$msg) {
    Write-Host "ERROR: $msg"
    exit $code
}

# ---------------------------------------------------------------- inputs
if (-not (Test-Path $Gcc)) { Fail 1 "arm-none-eabi-gcc not found: $Gcc" }
if (-not (Test-Path (Join-Path $Dt 'ps7_init.c'))) { Fail 1 "missing ps7_init.c in $Dt (run sdtgen first)" }
if (-not (Test-Path (Join-Path $Dt 'ps7_init.h'))) { Fail 1 "missing ps7_init.h in $Dt" }

$bins = Split-Path $Gcc -Parent
$size = Join-Path $bins 'arm-none-eabi-size.exe'
$readelf = Join-Path $bins 'arm-none-eabi-readelf.exe'
foreach ($t in @($size, $readelf)) { if (-not (Test-Path $t)) { Fail 1 "missing tool: $t" } }

# self-contained shim headers (no Vitis BSP generated headers needed)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$shim = Join-Path $here 'shim'
if (-not (Test-Path (Join-Path $shim 'xil_io.h'))) { Fail 1 "missing shim/xil_io.h" }

New-Item -ItemType Directory -Force $Out | Out-Null
Copy-Item (Join-Path $Dt 'ps7_init.c') (Join-Path $Out 'ps7_init.c') -Force
Copy-Item (Join-Path $Dt 'ps7_init.h') (Join-Path $Out 'ps7_init.h') -Force

Write-Host "== gcc      : $Gcc"
Write-Host "== includes : $shim (shim headers)"
Write-Host "== out      : $Out"

# ---------------------------------------------------------------- compile + link
$elf = Join-Path $Out 'fsbl.elf'
$map = Join-Path $Out 'fsbl.map'
$src = @(
    (Join-Path $here 'start.S'),
    (Join-Path $here 'mini_fsbl.c'),
    (Join-Path $Out 'ps7_init.c')
)
$gccArgs = @(
    '-mcpu=cortex-a9', '-marm', '-mfloat-abi=hard', '-mfpu=vfpv3',
    '-Os', '-ffreestanding', '-fno-builtin', '-fno-common',
    '-Wall', '-Wextra', '-Wno-unused-parameter',
    '-nostartfiles', '-nostdlib', '-nostdlib',
    "-I$shim", "-I$Out",
    '-T', (Join-Path $here 'mini_fsbl.ld'),
    ('-Wl,-Map=' + $map),
    '-Wl,--gc-sections',
    '-o', $elf
) + $src

Write-Host "== compiling ..."
$log = Join-Path $Out 'build.log'
& $Gcc @gccArgs *>&1 | Tee-Object -FilePath $log | Out-Host
if ($LASTEXITCODE -ne 0) { Fail 2 "compile/link failed (see $log)" }

# ---------------------------------------------------------------- verify the ELF
$sizeOut = & $size $elf 2>&1
Write-Host "== size:"
$sizeOut | ForEach-Object { Write-Host "   $_" }

$hdrs = & $readelf -h $elf 2>&1
$entry = ($hdrs | Select-String -Pattern 'Entry point address:\s*(0x[0-9a-fA-F]+)' |
    ForEach-Object { $_.Matches[0].Groups[1].Value } | Select-Object -First 1)
$machine = ($hdrs | Select-String -Pattern 'Machine:\s*(.+)' |
    ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() } | Select-Object -First 1)
Write-Host "== entry: $entry / machine: $machine"

# readelf prints 8 hex digits (0x00000000), so compare numerically, not as text
if ([Convert]::ToInt64($entry, 16) -ne 0) { Fail 3 "entry point is $entry, expected 0x0 (BootROM jumps to the ELF entry)" }
if ($machine -notmatch 'ARM') { Fail 3 "ELF machine is '$machine', expected ARM" }

$loads = & $readelf -l $elf 2>&1
$vaddr = ($loads | Select-String -Pattern 'LOAD\s+0x[0-9a-f]+\s+(0x[0-9a-f]+)' |
    ForEach-Object { $_.Matches[0].Groups[1].Value } | Select-Object -First 1)
Write-Host "== first LOAD vaddr: $vaddr"
if ([Convert]::ToInt64($vaddr, 16) -ne 0) { Fail 3 "first LOAD segment is at $vaddr, expected 0x0 (must live in OCM)" }

Write-Host "== dtc: elf looks loadable (entry 0x0, OCM)"

# ---------------------------------------------------------------- optional bootgen
if ($BootgenSwitch) {
    if (-not (Test-Path $Bootgen)) { Fail 4 "bootgen not found: $Bootgen" }
    $bif = Join-Path $Out 'boot_mini.bif'
    @"
// BOOT.BIN with only the hand-written first stage (no bitstream, no U-Boot).
the_ROM_image:
{
	[bootloader] fsbl.elf
}
"@ | Set-Content -Path $bif -Encoding ASCII
    Push-Location $Out
    try {
        & $Bootgen -image $bif -arch zynq -o (Join-Path $Out 'boot.bin') -w on 2>&1 | Out-Host
        $rc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rc -ne 0) { Fail 4 "bootgen failed (see output above)" }
    if (-not (Test-Path (Join-Path $Out 'boot.bin'))) { Fail 4 "bootgen reported success but boot.bin is missing" }
    $len = (Get-Item (Join-Path $Out 'boot.bin')).Length
    Write-Host "== boot.bin : $len bytes"
}

Write-Host "== RESULT: PASS"
exit 0
