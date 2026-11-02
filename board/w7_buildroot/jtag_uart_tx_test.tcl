# jtag_uart_tx_test.tcl -- PS 侧单向发送自检（不需要 U-Boot、不需要 Linux）
#
# 作用：用 JTAG 把 CPU 复位到 OCM，跑 ps7_init 初始化 DDR/MIO，
#       然后直接向 UART1(0xE0001000) 和 UART0(0xE0000000) 各发一串 ASCII。
#       若 PC 侧抓包能收到其中任何一串，说明该 UART 的 TX 线确实接到了适配器；
#       两串都收不到 = 板子 TX 根本没接到 PC（线没插 / 插错排针 / 适配器不是 COMx 那个）。
#
# 依赖：board/w7_buildroot/dt/ps7_init.tcl（由 sdtgen 从 XSA 生成）
# 调用方：serial_selftest.ps1（它会先开串口抓包，再跑本脚本）
#
# 判据（PS 侧）：SR(0xE000102C) 的 TXEMPTY 位(0x08) 置 1 = FIFO 已排空 = 字节确实发到了引脚。

connect
after 600
targets -set -filter {name =~ "*Cortex-A9 MPCore #0*"}
catch {stop}
rst -processor
after 1500
source [file join [file dirname [info script]] dt ps7_init.tcl]
set rc [ps7_init]
puts "PS7INIT_RC=$rc"
catch {ps7_post_config}

proc uart_init {base} {
    mwr [expr {$base + 0x00}] 0x00000114   ;# CR: TX_EN|RX_EN|TXRST|RXRST 之后=0x114
    mwr [expr {$base + 0x04}] 0x00000020   ;# MR: 8N1, no parity
    mwr [expr {$base + 0x18}] 124          ;# BAUDGEN: 100MHz/(124*...) -> 115200
    mwr [expr {$base + 0x34}] 6            ;# BAUDDIV
}

proc uart_putc {base value} {
    set sr   [expr {$base + 0x2C}]
    set fifo [expr {$base + 0x30}]
    set guard 0
    while {([mrd -value $sr] & 0x10) != 0 && $guard < 20000} { incr guard }
    mwr $fifo [expr {$value}]
}

proc uart_puts {base text} {
    foreach ch [split $text ""] {
        uart_putc $base [scan $ch %c]
    }
}

uart_init 0xE0001000
uart_init 0xE0000000

puts "U1_CR=[mrd -value 0xE0001000] U0_CR=[mrd -value 0xE0000000]"

uart_puts 0xE0001000 "U1-TX-OK\r\n"
after 300
uart_puts 0xE0000000 "U0-TX-OK\r\n"

after 800
puts "SR_U1=[mrd -value 0xE000102C] SR_U0=[mrd -value 0xE000002C]"
puts "DUAL_TX_DONE"
exit
