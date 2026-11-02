# serial_selftest.ps1 -- PS 串口单向自检：板子发、PC 收
#
# 用途：判断"板子 UART 到 PC 的线到底接没接"。
#   1) 后台开 COM 口抓包；
#   2) JTAG 复位 CPU -> ps7_init -> 直接往 UART1/UART0 发 "U1-TX-OK" / "U0-TX-OK"；
#   3) 看抓到的字节里有没有这两串。
#
# 判读：
#   VERDICT=U1_OK   -> UART1(MIO48/49) 的 TX 接到 PC，console 该走它
#   VERDICT=U0_OK   -> UART0(MIO14/15) 的 TX 接到 PC
#   VERDICT=NO_BYTES-> 两路都没到 PC：线没插 / 插错排针 / 适配器不是这个 COM
#
# 参数：-Port COM11 -Seconds 8
# 注意：本脚本会复位 CPU（板子当前跑的东西会被打断），但不动 flash、不写卡。

param(
    [string]$Port    = "COM11",
    [int]   $Seconds = 8
)

$ErrorActionPreference = 'Continue'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent (Split-Path -Parent $Here)      # board\w7_buildroot -> repo
$Tcl  = Join-Path $Here 'jtag_uart_tx_test.tcl'
$Cap  = Join-Path $Here 'serial_cap2.ps1'
$Out  = Join-Path $env:TEMP 'serial_selftest.txt'
$Xil  = if ($env:XILINX_VITIS) { $env:XILINX_VITIS } else { 'D:\Xilinx\2026.1\Vitis' }
$Xsdb = Join-Path $Xil 'bin\xsdb.bat'

Write-Host "== serial selftest: port=$Port seconds=$Seconds"
Write-Host "== repo: $Repo"

foreach ($p in @($Tcl, $Xsdb)) {
    if (-not (Test-Path $p)) { Write-Host "ERROR: missing $p"; exit 2 }
}

# 1) 后台抓包
if (Test-Path $Out) { Remove-Item $Out -Force -ErrorAction SilentlyContinue }
$capArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$Cap,'-Seconds',($Seconds+6),'-Out',$Out,'-Port',$Port)
$cap = Start-Process powershell -ArgumentList $capArgs -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3

# 2) JTAG 发送
Push-Location $Repo
& $Xsdb $Tcl 2>&1 | ForEach-Object { Write-Host "   xsdb| $_" }
$rc = $LASTEXITCODE
Pop-Location
Write-Host "== xsdb exit=$rc"

# 3) 收集
$captured = ""
for ($i = 0; $i -lt 40; $i++) {
    if ($cap.HasExited) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $cap.HasExited) { Write-Host "== capture still running, waiting..." }
$cap.WaitForExit(15000) | Out-Null
if (Test-Path $Out) {
    $raw = Get-Content $Out -Raw -Encoding UTF8
    $captured = ($raw -replace '\[\[.*?\]\]','').Trim()
}
$bytes = if ($captured) { $captured.Length } else { 0 }
Write-Host "== captured payload bytes: $bytes"
if ($bytes -gt 0) { Write-Host "== payload: $captured" }

if ($captured -match 'U1-TX-OK')      { Write-Host "VERDICT=U1_OK  (UART1 MIO48/49 -> PC)"; exit 0 }
elseif ($captured -match 'U0-TX-OK')  { Write-Host "VERDICT=U0_OK  (UART0 MIO14/15 -> PC)"; exit 0 }
else {
    Write-Host "VERDICT=NO_BYTES"
    Write-Host "  说明：PS 侧 FIFO 已排空（见上面 SR_U1/SR_U0 的 TXEMPTY），但 PC 一个字节都没收到。"
    Write-Host "  先查三根线：GND、板 TX -> 适配器 RX、适配器 TX -> 板 RX；再确认设备管理器里 $Port 就是这根适配器。"
    exit 3
}
