# =============================================================================
#  mizar_z7_openmv_uart.xdc —— Mizar-Z7 (7020) 上「OpenMV ↔ PL」首次点灯/UART 回环
#  项目：知倦 / VigiLens
#
#  ⚠️【未验证】本约束**没有在 Vivado 里跑过**（开发仓库的机器上没有板卡，
#     也没有跑过综合/实现）。引脚号来自 MicroPhase 官方《Mizar-Z7 Reference Manual》
#     （https://fpga-docs.microphase.cn/en/latest/DEV_BOARD/MIZAR-Z7/MIZAR-Z7_Reference_Manual.html），
#     但**必须**在 Vivado 里跑一次 Implementation、确认无 critical warning 之后再上板。
#
#  ⚠️ 与 PYNQ-Z2 的区别（别照抄 PYNQ-Z2 的 XDC）：
#     - PYNQ-Z2 的 PL 时钟是 125 MHz（H16 在 PYNQ-Z2 上是别的网络）；
#       本板 PL_CLK_50M = **H16**，是 **50 MHz**，所以 create_clock 周期是 **20 ns**，不是 8 ns。
#     - PYNQ-Z2 的 LED/KEY/PMOD 引脚号与本板**完全不同**。
#     - 器件本体相同（都是 XC7Z020-1CLG400C），所以 HLS IP 可以复用，**只有约束要换**。
#
#  连线（只接 3 根信号线 + 共地；OpenMV 用自己那根 USB 供电，不要从本板取电）:
#     OpenMV P4 (UART3 TX) ──► JP2 pin 5  = K14  (uart_rx)
#     OpenMV P5 (UART3 RX) ◄── JP2 pin 7  = H15  (uart_tx)
#     OpenMV GND           ─── JP2 pin 12 = GND
#     可选： clk_test ◄── JP2 pin 3 = G17  （PL 分频方波，用来反证 PL 时钟真的是 50 MHz）
#
#  ⚠️ 电平：OpenMV 的 IO 是 **3.3V 输出、5V 容忍**；本板 40-pin 扩展口默认 **3.3V**
#     （由 BANK34 的 VCCIO 决定，出厂焊接 R208 → 3.3V）。
#     **直连，不需要电平转换**。但上电前请确认板上 R208 在、R209/R210 不在。
#     绝对不要把 JP2 pin 11（VCC_5V）接到 OpenMV 的信号脚。
# =============================================================================

# ---- 主时钟：PL 侧 50 MHz 有源晶振（U19）----------------------------------
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports clk]
create_clock -period 20.000 -name sys_clk -waveform {0.000 10.000} [get_ports clk]

# ---- 复位按键 K4（PL_KEY1，按下为低）---------------------------------------
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports rst_n]

# ---- OpenMV UART（JP2 扩展口）----------------------------------------------
set_property -dict {PACKAGE_PIN K14 IOSTANDARD LVCMOS33} [get_ports uart_rx]
set_property -dict {PACKAGE_PIN H15 IOSTANDARD LVCMOS33} [get_ports uart_tx]

# ---- 时钟自证信号：PL 分频方波，OpenMV 侧测频用来反证时钟正确 --------------
set_property -dict {PACKAGE_PIN G17 IOSTANDARD LVCMOS33} [get_ports clk_test]

# ---- 板载用户 LED（D6~D9，**低电平点亮**）----------------------------------
set_property -dict {PACKAGE_PIN G14 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN C20 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN B20 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

# ---- 时序约束：扩展口是飞线/杜邦线，给输入留出余量 --------------------------
# 杜邦线 + 3.3V 单端，在 115200 波特率下 bit 宽 8.68 us，远慢于任何布线延迟，
# 所以这里不做 UART 的输入输出延迟约束；但把输入做成 false path 之外，
# 仍建议给 uart_rx 打两拍同步（RTL 里已做）。
set_false_path -from [get_ports uart_rx]
set_false_path -from [get_ports rst_n]

# ---- 配置 ------------------------------------------------------------------
# 生成 bitstream 时请确认：
#   * 未使用的 IO 不要设成 pull-down/float 导致扩展口意外驱动
#   * 若报 "Bank 34 VCCIO" 相关 warning，先在板上用万用表确认 VCCIO34 是 3.3V
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
