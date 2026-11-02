# ps7_uart0.tcl —— 追加打开 PS UART0（MIO 14..15），用于判定板上 CP210x 到底接在哪一组 MIO
#
# 背景：U-Boot 已经跑起来（PC 在 0x040A8844），UART1(0xE0001000) 的寄存器也被初始化了
# （CR=0x114 MR=0x20 BAUDGEN=0x7C BAUDDIV=0x06），但 COM11 上一个字节都收不到。
# 两种可能：① 串口根本接在 UART0(MIO14/15)；② MIO48/49 在 bank1，1.8V 驱动不到 3.3V 的 CP210x。
# 打开 UART0 后，ps7_init.c 会给出 **MIO14/15 的权威寄存器值**，再用 JTAG 直接写 UART0 就能判定。
#
# 用法：vivado -mode batch -source board\w7_buildroot\ps7_uart0.tcl -nojournal -nolog
set UART0_IO "MIO 14 .. 15"

set SCRIPT_DIR [file normalize [file dirname [info script]]]
set TOP_DIR    [file normalize [file join $SCRIPT_DIR ..]]
set PROJ       [file join $TOP_DIR vivado_project vigilens_bd.xpr]
open_project $PROJ
open_bd_design [get_files system.bd]
set ps7 [get_bd_cells ps7_0]

proc dumpu {ps7 label} {
    puts "   -- $label"
    foreach p {PCW_UART0_PERIPHERAL_ENABLE PCW_UART0_UART0_IO PCW_UART0_BAUD_RATE \
               PCW_UART1_PERIPHERAL_ENABLE PCW_UART1_UART1_IO} {
        if {[catch {set v [get_property CONFIG.$p $ps7]}]} { set v "<n/a>" }
        puts [format "   %-32s = %s" $p $v]
    }
}

puts "== 改之前"
dumpu $ps7 "现况"
puts "== 打开 UART0 = $UART0_IO"
if {[catch {
    set_property -dict [list \
        CONFIG.PCW_UART0_PERIPHERAL_ENABLE {1} \
        CONFIG.PCW_UART0_UART0_IO $UART0_IO \
        CONFIG.PCW_UART0_BAUD_RATE {115200} \
    ] $ps7
} e]} { puts "SET ERROR: $e"; close_project; exit 1 }
puts "== 改之后"
dumpu $ps7 "新配置"
if {[catch {validate_bd_design} msg]} { puts "ERROR: validate: $msg"; close_project; exit 1 }
save_bd_design
puts "== saved: UART0 + UART1 都打开（互不冲突）"
close_project
exit 0
