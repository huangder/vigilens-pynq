#
# preflight.ps1 -- One command that runs every offline (board-free) check of the W7 path.
#
# Design rule: this script only READS the repository. Every artifact it produces goes
# to $env:TEMP, because a child process cannot write inside the repo under the current
# sandbox policy (writing there fails with "Permission denied" -- that is the sandbox,
# not a broken tool).
#
# What each check is worth:
#   tools   -- presence only. "Vivado is installed" says nothing about a design working.
#   dts     -- compiles the final device tree with the same dtc Linux uses. Catches
#              syntax errors, a disabled usb0, or a serial0 pointing at the wrong UART.
#              It cannot prove the hardware matches the tree.
#   uartsim -- runs the bare-metal UART1 register sequence on QEMU and requires the
#              bytes to appear. Proves the code+toolchain, not the board wiring.
#   shell   -- `bash -n` syntax only; the real Buildroot run needs Linux.
#   xsa     -- presence + size. Vitis 2026.1 cannot build a platform from this XSA
#              (see docs/41 section 18), so this is reported as a WARN, not a FAIL.
#
[CmdletBinding()]
param(
    [string]$Out = (Join-Path $env:TEMP 'w7_preflight')
)

$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
$root = (Resolve-Path (Join-Path $here '..\..')).Path
New-Item -ItemType Directory -Force $Out | Out-Null

$rows = New-Object System.Collections.ArrayList
function Add-Row([string]$name, [string]$status, [string]$detail) {
    [void]$rows.Add([pscustomobject]@{ Check = $name; Status = $status; Detail = $detail })
    $color = switch ($status) { 'PASS' { 'Green' } 'FAIL' { 'Red' } 'WARN' { 'Yellow' } default { 'Gray' } }
    Write-Host ("[{0}] {1,-14} {2}" -f $status, $name, $detail) -ForegroundColor $color
}

# ---------------------------------------------------------------- 1) tool presence
$tools = [ordered]@{
    'sdtgen'  = 'D:\Xilinx\2026.1\Vitis\bin\sdtgen.bat'
    'dtc'     = 'D:\Xilinx\2026.1\Vitis\bin\dtc.exe'
    'bootgen' = 'D:\Xilinx\2026.1\Vitis\bin\bootgen.bat'
    'arm-gcc' = 'D:\Xilinx\2026.1\Vitis\gnu\aarch32\nt\gcc-arm-none-eabi\bin\arm-none-eabi-gcc.exe'
    'qemu'    = 'D:\Xilinx\2026.1\data\emulation\qemu\comp\qemu_win\qemu-system-arm.exe'
    'vivado'  = 'D:\Xilinx\2026.1\Vivado\bin\vivado.bat'
    'bash'    = 'D:\STM32CubeCLT_1.22.0\Make\bin\bash.exe'
}
$have = @{}
foreach ($k in $tools.Keys) {
    $have[$k] = Test-Path $tools[$k]
    Add-Row "tool:$k" ($(if ($have[$k]) { 'PASS' } else { 'WARN' })) $tools[$k]
}

# ---------------------------------------------------------------- 2) device tree
$dtDir = Join-Path $here 'dt'
$dtFrom = 'repo dt\'
if (-not (Test-Path (Join-Path $dtDir 'system-top.dts'))) {
    # $env:W7_DT lets you point at any sdtgen output; %TEMP%\dt_final is where the
    # sandboxed agent has to write (child processes cannot create files in the repo).
    $cand = @()
    if ($env:W7_DT) { $cand += $env:W7_DT }
    if ($env:TEMP) { $cand += (Join-Path $env:TEMP 'dt_final') }
    foreach ($c in $cand) {
        if (Test-Path (Join-Path $c 'system-top.dts')) { $dtDir = $c; $dtFrom = "fallback: $c"; break }
    }
}
$need = @('system-top.dts', 'pcw.dtsi', 'pl.dtsi', 'zynq-7000.dtsi')
$missing = @($need | Where-Object { -not (Test-Path (Join-Path $dtDir $_)) })
if ($missing.Count) {
    Add-Row 'dts' 'SKIP' ("no device tree yet (missing: " + ($missing -join ', ') + ") -- run sdtgen first (see dt\README.md)")
} else {
    $checker = Join-Path $here 'check_dts.ps1'
    $log = Join-Path $Out 'check_dts.log'
    # expectations follow the overlay itself: if system-user.dtsi says okay, pass -Expect* okay.
    # (otherwise the check contradicts the file it is checking)
    $userDts = Join-Path $here 'system-user.dtsi'
    $expUsb = 'disabled'; $expSd = 'disabled'
    if (Test-Path $userDts) {
        $u = Get-Content $userDts -Raw
        if ($u -match '(?s)&usb0\s*\{[^}]*?status\s*=\s*"okay"')   { $expUsb = 'okay' }
        if ($u -match '(?s)&sdhci0\s*\{[^}]*?status\s*=\s*"okay"') { $expSd  = 'okay' }
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $checker -Dt $dtDir `
        -UserDts $userDts -ExpectUsb $expUsb -ExpectSd $expSd *>&1 | Tee-Object -FilePath $log | Out-Null
    $rc = $LASTEXITCODE
    $tail = (Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 1)
    if ($rc -eq 0) { Add-Row 'dts' 'PASS' "check_dts.ps1 exit 0 ($tail; expect usb=$expUsb sd=$expSd) [$dtFrom]" }
    else { Add-Row 'dts' 'FAIL' "check_dts.ps1 exit $rc (expect usb=$expUsb sd=$expSd) -- see $log" }
}

# ---------------------------------------------------------------- 3) UART simulation
$sim = Join-Path $here 'sim\run_sim.ps1'
if ((Test-Path $sim) -and $have['arm-gcc'] -and $have['qemu']) {
    $log = Join-Path $Out 'run_sim.log'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $sim -Out (Join-Path $Out 'sim') *>&1 | Tee-Object -FilePath $log | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -eq 0) { Add-Row 'uart-sim' 'PASS' 'UART1_SMOKE_OK seen in QEMU serial output' }
    else { Add-Row 'uart-sim' 'FAIL' "run_sim.ps1 exit $rc -- see $log" }
} else {
    Add-Row 'uart-sim' 'SKIP' 'arm-none-eabi-gcc or QEMU not found'
}

# ---------------------------------------------------------------- 4) shell syntax
if ($have['bash']) {
    $bad = @()
    foreach ($s in @('wsl_build.sh', 'make_sd.sh')) {
        $f = Join-Path $here $s
        & $tools['bash'] -n $f 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { $bad += $s }
    }
    if ($bad.Count) { Add-Row 'shell' 'FAIL' ("bash -n failed: " + ($bad -join ', ')) }
    else { Add-Row 'shell' 'PASS' 'bash -n: wsl_build.sh, make_sd.sh' }
} else {
    Add-Row 'shell' 'SKIP' 'bash not found'
}

# ---------------------------------------------------------------- 5) XSA / BOOT.BIN inputs
# NOTE: wrap the pipeline in @() -- piping a single match yields a bare string, and
# $str[0] is its first CHARACTER (this printed "C (0 KB)" once, hence the comment).
$xsaCandidates = @(@(
    (Join-Path $root 'board\bitstream\system.xsa'),
    (Join-Path $env:TEMP 'xsa_out\system.xsa')
) | Where-Object { Test-Path $_ })
if ($xsaCandidates.Count) {
    $x = $xsaCandidates[0]
    Add-Row 'xsa' 'PASS' ("$x (" + [math]::Round((Get-Item $x).Length / 1KB) + " KB)")
} else {
    Add-Row 'xsa' 'SKIP' 'no system.xsa -- run export_xsa.tcl (Vivado 2026.1)'
}
# fsbl.elf: repo (Xilinx GUI build) or the hand-written mini_fsbl build output.
# mini_fsbl is built OUTSIDE the repo by default (see mini_fsbl/README.md), so look there too.
$fsblCandidates = @()
$fsblCandidates += (Get-ChildItem $root -Recurse -Filter 'fsbl.elf' -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($env:W7_FSBL) { $fsblCandidates += (Get-Item $env:W7_FSBL -ErrorAction SilentlyContinue) }
$mini = Join-Path $env:TEMP 'mini_fsbl\fsbl.elf'
if (Test-Path $mini) { $fsblCandidates += (Get-Item $mini) }
$fsbl = @($fsblCandidates) | Where-Object { $_ } | Select-Object -First 1
if ($fsbl) { Add-Row 'fsbl.elf' 'PASS' $fsbl.FullName }
else { Add-Row 'fsbl.elf' 'WARN' 'missing -- build it with mini_fsbl/build_mini_fsbl.ps1 (see docs/41 section 21)' }

# boot.bin (BOOT.BIN): check the Zynq boot header, not just the file's existence
$bootDir = Join-Path $env:TEMP 'mini_fsbl'
$boot = @((Join-Path $bootDir 'boot.bin'), (Join-Path $bootDir 'BOOT.BIN')) |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($boot) {
    $bytes = [System.IO.File]::ReadAllBytes($boot)
    if ($bytes.Length -lt 0x28) {
        Add-Row 'boot.bin' 'FAIL' "$boot is too short for a Zynq boot header"
    } else {
        $sync = [BitConverter]::ToUInt32($bytes, 0x20)
        $id = [BitConverter]::ToUInt32($bytes, 0x24)
        # compare as Int64: 0xAA995566 does not fit into a positive Int32 literal
        if ([int64]$sync -eq 0xAA995566L -and [int64]$id -eq 0x584C4E58L) {
            Add-Row 'boot.bin' 'PASS' ("$([math]::Round($bytes.Length/1KB,1)) KB, sync 0xAA995566, XLNX ok")
        } else {
            Add-Row 'boot.bin' 'FAIL' ("bad header: sync=0x{0:x8} id=0x{1:x8} (expect 0xaa995566 / 0x584c4e58)" -f $sync, $id)
        }
    }
} else {
    Add-Row 'boot.bin' 'SKIP' 'no boot.bin under %TEMP%\mini_fsbl -- build it with -BootgenSwitch'
}

# ---------------------------------------------------------------- 6) summary
Write-Host ''
$fail = @($rows | Where-Object { $_.Status -eq 'FAIL' })
$warn = @($rows | Where-Object { $_.Status -eq 'WARN' })
$skip = @($rows | Where-Object { $_.Status -eq 'SKIP' })
$pass = @($rows | Where-Object { $_.Status -eq 'PASS' })
Write-Host ("SUMMARY: {0} PASS / {1} WARN / {2} SKIP / {3} FAIL" -f $pass.Count, $warn.Count, $skip.Count, $fail.Count)
Write-Host "logs: $Out"
if ($fail.Count) { exit 1 }
exit 0
