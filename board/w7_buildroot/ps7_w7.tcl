# ps7_w7.tcl —— 给 W7（板上 Linux + UVC）补齐 PS7 外设开关
#
# 【状态 2026-10-25 18:2x】下面这些结论都来自本机 Vivado 2026.1 实跑（见 docs/42）：
#   · SD0 / USB0 生效需要**两类**参数，缺一不可：
#       ① `PCW_SD0_PERIPHERAL_ENABLE` / `PCW_USB0_PERIPHERAL_ENABLE` + `*_IO`（管 ps7_init 与引脚）
#       ② `PCW_EN_SDIO0` / `PCW_EN_USB0`（管**设备树**：sdtgen 把这两个键翻译成
#          `xlnx,pcw-en-sdio0` / `xlnx,pcw-en-usb0`）
#     实证：手改 system.hwh 里这两条 0→1 后重跑 sdtgen，`pcw.dtsi` 立刻从
#     `xlnx,pcw-en-usb0 = <0>` 变成 `<1>`（只改 ① 时它仍是 <0>）。
#   · `PCW_USE_SDIO0` / `PCW_USE_USB0` **不存在**（`[BD 41-1276] Cannot set the parameter ... does not exist`）。
#   · 改完 BD 直接导 XSA **不会**进设备树：XSA 里的 `.hwh` 由**综合**产出 ⇒ 必须重跑 synth_1。
#   · ⚠️ `PCW_USB0_RESET_ENABLE` / `PCW_USB0_RESET_IO` 在本机**写不进去**（MIO 7/10/15/46 全试过，
#     回读恒为 0 / `<Select>`；组开关 `PCW_USB_RESET_SELECT` 也拒收）。⇒ PHY 复位只能：
#     (a) 在 Vitis/Vivado **GUI** 里勾（MIO Configuration → USB0 → Reset），或
#     (b) 硬件上把 USB3320C 的 RESETB 上拉/由 PL GPIO 驱动。
#     2026-10-28 已验证第三条：PS GPIO46 + Linux phy-generic reset-gpios。
#     当前 JTAG 入口调用 usb_phy.tcl，设备树维护同一脚的 active-low 复位。
#
# 用法（普通终端，仓库可写）：
#   . .\board\w7_buildroot\w7env.ps1
#   vivado -mode batch -source board\w7_buildroot\ps7_w7.tcl -nojournal -nolog
#   → 再按脚本末尾打印的顺序走：synth → export_xsa → sdtgen → check_dts
#
# ---------------- MIO 分配（2026-10-25 用户按 Mizar-Z7_R11 原理图给出）----------------
# UART1      MIO 48..49   ← R11 原理图第2页已核对；Bank501 供电为1.8V。
#                            手册正文中的 MIO14/15 与原理图冲突，以原理图/实测为准。
# SD0        MIO 40..45   ← CLK=40 / CMD=41 / D0..D3=42..45（与 Zynq-7000 TRM 标准分配一致）
#                            ⚠️ docs/12:203 说 MIO40 接了 K2 按键，与原理图冲突；以原理图为准。
#                            卡检测(CD)/写保护(WP)未接 → 保持关闭，由内核轮询。
# USB0       MIO 28..39   ← 标准 ULPI：D4=28 DIR=29 STP=30 NXT=31 D0..D3=32..35
#                            CLK=36 D5..D7=37..39（与 TRM 的 USB0 专用段完全吻合）
# USB0_RST   MIO 46       ← PS GPIO + Linux reset-gpios；不依赖 GUI 的专用复位项

set UART_IO "MIO 48 .. 49"
set SD0_IO  "MIO 40 .. 45"
set USB0_IO "MIO 28 .. 39"

# ---------------- 执行 ----------------
set SCRIPT_DIR [file normalize [file dirname [info script]]]
set TOP_DIR    [file normalize [file join $SCRIPT_DIR ..]]
set PROJ       [file join $TOP_DIR vivado_project vigilens_bd.xpr]
if {![file exists $PROJ]} { puts "ERROR: 找不到 $PROJ"; exit 1 }

open_project $PROJ
# 2026.1 起必须先显式打开 BD，否则 get_bd_cells 拿不到对象
open_bd_design [get_files system.bd]
set ps7 [get_bd_cells ps7_0]
if {$ps7 eq ""} { puts "ERROR: 找不到 ps7_0"; close_project; exit 1 }

proc dump {ps7 label} {
    puts "   -- $label"
    foreach p {PCW_UART1_UART1_IO PCW_EN_SDIO0 PCW_SD0_PERIPHERAL_ENABLE PCW_SD0_SD0_IO \
               PCW_EN_USB0 PCW_USB0_PERIPHERAL_ENABLE PCW_USB0_USB0_IO} {
        if {[catch {set v [get_property CONFIG.$p $ps7]}]} { set v "<n/a>" }
        puts [format "   %-32s = %s" $p $v]
    }
}

puts "== 改之前"
dump $ps7 "现况"

puts "== 应用 W7 配置（UART1=$UART_IO / SD0=$SD0_IO / USB0=$USB0_IO）"
set_property -dict [list \
    CONFIG.PCW_PRESET_BANK1_VOLTAGE {LVCMOS 1.8V} \
    CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1} \
    CONFIG.PCW_UART1_UART1_IO $UART_IO \
    CONFIG.PCW_UART1_BAUD_RATE {115200} \
    CONFIG.PCW_EN_SDIO0 {1} \
    CONFIG.PCW_SD0_PERIPHERAL_ENABLE {1} \
    CONFIG.PCW_SD0_SD0_IO $SD0_IO \
    CONFIG.PCW_EN_USB0 {1} \
    CONFIG.PCW_USB0_PERIPHERAL_ENABLE {1} \
    CONFIG.PCW_USB0_USB0_IO $USB0_IO \
] $ps7

puts "== 改之后"
dump $ps7 "新配置"

# SD0/USB0 打开后 DRC 仍应全绿；失败说明 MIO 撞车（例如某个 MIO 被别的外设占了）
if {[catch {validate_bd_design} msg]} {
    puts "ERROR: validate_bd_design 失败：$msg"
    close_project
    exit 1
}
save_bd_design
puts "== BD 已保存（此步**不会**碰综合结果）"

puts "== 下一步（顺序不能颠倒）"
puts "   0) USB PHY 复位由 usb_phy.tcl（JTAG）与 system-user.dtsi（Linux GPIO46 active-low）管理。"
puts "      R11 Bank501/VDDIO 是 1.8V；不要恢复成 LVCMOS33。"
puts "   1) 重跑综合（.hwh 由综合产生，不重跑设备树就不会变）："
puts "      reset_run synth_1 ; launch_runs synth_1 -jobs 4 ; wait_on_run synth_1"
puts "   2) 导 XSA：vivado -mode batch -source board\\w7_buildroot\\export_xsa.tcl -nojournal -nolog"
puts "   3) 生成设备树：sdtgen -xsa XSA -dir OUT（路径用正斜杠；sdtgen 失败也可能 exit 0，看产物）"
puts "   4) 用新产物跑 check_dts.ps1 -Dt OUT，并确认 pcw.dtsi 里"
puts "      xlnx,pcw-en-sdio0 = 1 / xlnx,pcw-en-usb0 = 1"
close_project
