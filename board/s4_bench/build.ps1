# Build the S4 bare-metal firmware (Cortex-A9, freestanding, no libc).
#
#   powershell -ExecutionPolicy Bypass -File board\s4_bench\build.ps1
#
# Outputs (gitignored):
#   s4_bench.bin       640x480 tier (3/5 sampling -> 384x288 gray)
#   s4_bench_720.bin   1280x720 tier (3/8 sampling -> 480x270 gray)
#
# ASCII-only on purpose: Windows PowerShell 5.1 decodes BOM-less .ps1 as GBK.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$gcc = if ($env:GCC) { $env:GCC } else { "D:/Xilinx/2026.1/Vitis/gnu/aarch32/nt/gcc-arm-none-eabi/bin/arm-none-eabi-gcc.exe" }
$ocp = if ($env:OCP) { $env:OCP } else { "D:/Xilinx/2026.1/Vitis/gnu/aarch32/nt/gcc-arm-none-eabi/bin/arm-none-eabi-objcopy.exe" }
if (-not (Test-Path $gcc)) { throw "gcc not found: $gcc" }
if (-not (Test-Path $ocp)) { throw "objcopy not found: $ocp" }

$flags = @(
  "-mcpu=cortex-a9", "-marm", "-nostdlib", "-nostartfiles", "-ffreestanding", "-O2",
  "-Wl,--build-id=none", "-Wl,-T,link.ld"
)

& $gcc @flags -o s4_bench.elf start.S s4_bench.c
if ($LASTEXITCODE -ne 0) { throw "gcc (640x480) failed: $LASTEXITCODE" }
& $ocp -O binary s4_bench.elf s4_bench.bin
if ($LASTEXITCODE -ne 0) { throw "objcopy (640x480) failed: $LASTEXITCODE" }

& $gcc @flags -DFRAME_W=1280u -DFRAME_H=720u -DGRAY_W=480u -DGRAY_H=270u `
  -o s4_bench_720.elf start.S s4_bench.c
if ($LASTEXITCODE -ne 0) { throw "gcc (1280x720) failed: $LASTEXITCODE" }
& $ocp -O binary s4_bench_720.elf s4_bench_720.bin
if ($LASTEXITCODE -ne 0) { throw "objcopy (1280x720) failed: $LASTEXITCODE" }

Get-Item s4_bench.bin, s4_bench_720.bin | Select-Object Name, Length, LastWriteTime
