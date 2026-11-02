# check_dts.ps1 -- compile the Zynq device tree on Windows BEFORE burning a 90-minute Linux build.
#
# Why: dtc syntax errors (e.g. writing `&usb0: phy0@... {}` as a definition) only show up at
# compile time. On a Linux host that is minutes into the build. This script takes ~3 seconds.
#
# NOTE: this file is deliberately ASCII-only. Windows PowerShell 5.1 decodes a BOM-less .ps1 as
# GBK, which mangles non-ASCII text and breaks string parsing. Keep it ASCII (see AGENTS.md 6.4).
#
# Deps: gcc on PATH (MinGW) + dtc.exe shipped with Vitis.
#
# Usage:
#   .\check_dts.ps1                          # uses board\w7_buildroot\dt\ + system-user.dtsi
#   .\check_dts.ps1 -Dt "C:\path\to\dt"
#   .\check_dts.ps1 -ExpectUsb disabled      # PS7 has not enabled USB0 yet (current state)
#
# Exit code: 0 = pass, 1 = fail (the failing assertion is printed)

param(
    [string]$Dt = "",
    [string]$UserDts = "",
    [string]$Dtc = "D:\Xilinx\2026.1\Vitis\bin\dtc.exe",
    [ValidateSet('disabled', 'okay')][string]$ExpectUsb = 'okay',
    [ValidateSet('disabled', 'okay')][string]$ExpectSd = 'disabled'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Dt)      { $Dt      = Join-Path $repo 'board\w7_buildroot\dt' }
if (-not $UserDts) { $UserDts = Join-Path $repo 'board\w7_buildroot\system-user.dtsi' }

$script:fail = @()
foreach ($f in @('system-top.dts', 'pcw.dtsi', 'pl.dtsi', 'zynq-7000.dtsi')) {
    if (-not (Test-Path (Join-Path $Dt $f))) { $script:fail += "missing: $Dt\$f" }
}
if (-not (Test-Path $UserDts)) { $script:fail += "missing: $UserDts" }
if (-not (Test-Path $Dtc))     { $script:fail += "missing dtc: $Dtc (Vitis\bin\dtc.exe)" }
if (-not (Get-Command gcc -ErrorAction SilentlyContinue)) { $script:fail += "gcc not on PATH (needed as C preprocessor)" }
if ($script:fail) { $script:fail | ForEach-Object { "ERROR: $_" }; exit 1 }

$work = Join-Path $env:TEMP ("dtcheck_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null
Copy-Item (Join-Path $Dt '*.dts*') $work -Force
Copy-Item $UserDts $work -Force
$lines = @('#include "system-top.dts"', '#include "system-user.dtsi"')
Set-Content -Path (Join-Path $work 'mizar-z7.dts') -Value $lines -Encoding ascii

Push-Location $work
try {
    # Native tools write warnings to stderr; with ErrorActionPreference=Stop PowerShell 5.1 turns
    # those into terminating errors. Run them with Continue and judge by output files / exit code.
    function Invoke-Native([string]$exe, [string[]]$argv) {
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { & $exe @argv 2>&1 | Out-String } finally { $ErrorActionPreference = $prev }
    }

    $pre = Join-Path $work 'pre.dts'
    Invoke-Native 'gcc' @('-E', '-nostdinc', '-undef', '-x', 'assembler-with-cpp', '-I', '.', 'mizar-z7.dts', '-o', $pre) | Out-Null
    if (-not (Test-Path $pre)) { "ERROR: cpp preprocessing failed"; exit 1 }

    $dtb = Join-Path $work 'mizar-z7.dtb'
    $dtcOut = Invoke-Native $Dtc @('-I', 'dts', '-O', 'dtb', '-o', $dtb, $pre)
    if (-not (Test-Path $dtb)) { "ERROR: dtc failed:`n$dtcOut"; exit 1 }
    "== dtc ok, DTB = " + [math]::Round((Get-Item $dtb).Length / 1KB, 1) + " KB"

    $back = Join-Path $work 'back.dts'
    Invoke-Native $Dtc @('-I', 'dtb', '-O', 'dts', '-o', $back, $dtb) | Out-Null
    $txt = Get-Content $back -Raw

    function Assert($ok, $msg) {
        if ($ok) { "   PASS  $msg" } else { "   FAIL  $msg"; $script:fail += $msg }
    }

    Assert ($txt -match 'bootargs = "[^"]*console=ttyPS0,115200') 'chosen.bootargs has console=ttyPS0,115200'
    Assert ($txt -match 'bootargs = "[^"]*root=/dev/mmcblk0p2')   'chosen.bootargs has root=/dev/mmcblk0p2'

    $m = [regex]::Match($txt, 'serial0 = "([^"]+)"')
    $ser = if ($m.Success) { $m.Groups[1].Value } else { '<missing>' }
    Assert ($ser -match 'e0001000|e0000000') "serial0 points at a UART controller (found: $ser)"
    if ($ser -match 'e0001000') { "          note: serial0 = UART1 (matches current BD)" }

    # Assert on the controller serial0 actually points at -- uart0 is disabled in pcw.dtsi, so a
    # blanket 'serial@e000[01]000' regex would match uart0 and report a false failure.
    if ($ser -match 'e0001000|e0000000') {
        $nodeName = [regex]::Escape($ser.Split('/')[-1])
        $uart = [regex]::Match($txt, $nodeName + ' \{(?s).*?status = "([a-z]+)"')
        $uartVal = if ($uart.Success) { $uart.Groups[1].Value } else { '<missing>' }
        Assert ($uartVal -eq 'okay') "console UART ($($ser.Split('/')[-1])) status = okay (found: $uartVal)"
    }

    $usb = [regex]::Match($txt, 'usb@e0002000 \{(?s).*?status = "([a-z]+)"')
    $usbVal = if ($usb.Success) { $usb.Groups[1].Value } else { '<missing>' }
    Assert ($usbVal -eq $ExpectUsb) "usb0 status = $ExpectUsb (found: $usbVal)"

    $sd = [regex]::Match($txt, '(sdhci|mmc)@e0100[0-9]00 \{(?s).*?status = "([a-z]+)"')
    $sdVal = if ($sd.Success) { $sd.Groups[2].Value } else { '<missing>' }
    Assert ($sdVal -eq $ExpectSd) "sdhci status = $ExpectSd (found: $sdVal)"

    Assert ($txt -match 'compatible = "usb-nop-xceiv"') 'usb-nop-xceiv node present (USB PHY)'
    Assert ($txt -match 'reset-gpios = <0x[0-9a-f]+ 0x2e 0x01>') 'PHY RESETB is GPIO46 active-low'
    Assert ($txt -match 'groups = "usb0_0_grp"') 'USB0 ULPI pinctrl group exists'
    Assert ($txt -match 'groups = "gpio0_46_grp"') 'RESETB GPIO pinctrl group exists'
    Assert ($txt -match 'io-standard = <0x01>') 'USB pinctrl specifies LVCMOS18'
    Assert ($txt -match 'roi_statistic')           'pl.dtsi custom IP nodes included'

    if ($script:fail.Count) { "== RESULT: FAIL (" + $script:fail.Count + " assertion(s))"; exit 1 }
    "== RESULT: PASS"
}
finally { Pop-Location; Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue }
