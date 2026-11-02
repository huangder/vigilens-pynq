# build_dtb.ps1 -- compile the Zynq device tree into a .dtb on Windows (no Linux needed).
#
# Why this exists: check_dts.ps1 is a *checker* (it compiles into a throw-away temp dir and asserts).
# The SD card needs the actual artifact next to zImage, so this script does the same two steps
# (cpp -> dtc) and keeps the output where you ask.
#
# Usage:
#   .\build_dtb.ps1 -Dt C:\temp\dt_ps8 -Out D:\w7\images\system.dtb
#
# Deps: gcc on PATH (MinGW works as a C preprocessor) + dtc.exe from Vitis.
# ASCII-only on purpose (PowerShell 5.1 decodes BOM-less .ps1 as GBK; see AGENTS.md 6.4).

param(
    [string]$Dt = "",
    [string]$UserDts = "",
    [string]$Out = "",
    [int]$DdrMb = 512,
    [string]$Dtc = "D:\Xilinx\2026.1\Vitis\bin\dtc.exe"
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Dt)      { $Dt      = Join-Path $repo 'board\w7_buildroot\dt' }
if (-not $UserDts) { $UserDts = Join-Path $repo 'board\w7_buildroot\system-user.dtsi' }
if (-not $Out)     { $Out     = Join-Path $repo 'board\w7_buildroot\dt\system.dtb' }
$Out = [IO.Path]::GetFullPath($Out)

foreach ($f in @('system-top.dts', 'pcw.dtsi', 'pl.dtsi', 'zynq-7000.dtsi')) {
    if (-not (Test-Path (Join-Path $Dt $f))) { "ERROR: missing $Dt\$f"; exit 1 }
}
if (-not (Test-Path $UserDts)) { "ERROR: missing $UserDts"; exit 1 }
if (-not (Test-Path $Dtc))     { "ERROR: missing dtc: $Dtc"; exit 1 }
if (-not (Get-Command gcc -ErrorAction SilentlyContinue)) { "ERROR: gcc not on PATH"; exit 1 }

$work = Join-Path $env:TEMP ("dtbuild_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null
Copy-Item (Join-Path $Dt '*.dts*') $work -Force
Copy-Item $UserDts (Join-Path $work 'user-entry.dtsi') -Force
Set-Content -Path (Join-Path $work 'mizar-z7.dts') -Encoding ascii -Value @(
    '#include "system-top.dts"',
    '#include "user-entry.dtsi"'
)

Push-Location $work
try {
    function Invoke-Native([string]$exe, [string[]]$argv) {
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { & $exe @argv 2>&1 | Out-String } finally { $ErrorActionPreference = $prev }
    }

    $pre = Join-Path $work 'pre.dts'
    Invoke-Native 'gcc' @('-E', '-nostdinc', '-undef', '-x', 'assembler-with-cpp', '-I', '.', '-I', $PSScriptRoot, 'mizar-z7.dts', '-o', $pre) | Out-Null
    if (-not (Test-Path $pre)) { "ERROR: cpp failed (gcc as preprocessor)"; exit 1 }

    # A5 (DDR 512 MiB) 已改在 system-user.dtsi 里用同名节点 memory@00100000 覆盖 reg：
    #   sdtgen 生成的是 1 GiB（0x3FF00000），Mizar-Z7 7020 实物只有 512 MiB，
    #   设备树按节点路径合并，所以不需要在这里改文本（-DdrMb 仅作注释/兼容保留）。

    $dtb = Join-Path $work 'system.dtb'
    $dtcLog = Invoke-Native $Dtc @('-I', 'dts', '-O', 'dtb', '-o', $dtb, $pre)
    if (-not (Test-Path $dtb)) { "ERROR: dtc failed:"; $dtcLog; exit 1 }

    $outDir = Split-Path -Parent $Out
    if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force $outDir | Out-Null }
    Copy-Item $dtb $Out -Force
    $sz = (Get-Item $Out).Length
    "== dtb: $Out ($([math]::Round($sz / 1024, 1)) KB)"
    "== fdt header: " + ((Get-Content $Out -Encoding Byte -TotalCount 4 | ForEach-Object { $_.ToString('x2') }) -join ' ') + "  (expect d0 0d fe ed)"
    exit 0
}
finally {
    Pop-Location
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
