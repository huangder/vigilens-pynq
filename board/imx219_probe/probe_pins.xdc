# =============================================================================
#  probe_pins.xdc —— 相机存活探针的引脚约束（Mizar-Z7020）
#
#  来源：主时钟/LED/UART 照抄 board/openmv/mizar_z7_openmv_uart.xdc；
#       相机三脚（CAM_CLK/CAM_SCL/CAM_SDA）来自 Mizar-Z7_R11.pdf 原理图，
#       已用 AMD 官方封装表 xc7z020clg400pkg.txt 逐条核对（见 _mipi_research\REPORT.md）。
#
#  注意：
#    · SCCB 两脚（A20/E17）在 BANK35，与板载 24C32 EEPROM 共总线，板上已有 4.7K 上拉。
#    · CAM_CLK（U9）本次【不驱动】：板上 U19 50M_OSC 经 R156 直驱连接器 pin12，
#      PL 输出会两源对打。RTL 里 DRIVE_MCLK=0 把 mclk 置高阻。
# =============================================================================

# ---- 主时钟：PL 侧 50 MHz 有源晶振（U19）----------------------------------
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports clk]
create_clock -period 20.000 -name sys_clk -waveform {0.000 10.000} [get_ports clk]

# ---- 复位：PL_KEY1（按下为低）---------------------------------------------
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports rst_n]

# ---- UART TX：JP2 pin 7 = H15 ----------------------------------------------
set_property -dict {PACKAGE_PIN H15 IOSTANDARD LVCMOS33} [get_ports uart_tx]

# ---- 板载用户 LED（D6~D9，低电平点亮）--------------------------------------
set_property -dict {PACKAGE_PIN G14 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN C20 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN B20 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

# ---- 相机三脚（Mizar-Z7_R11 原理图 + 官方封装表核对）------------------------
# CAM_CLK  = 连接器 pin 12  ->  U9 = IO_L17P_T2_13（BANK13）
set_property -dict {PACKAGE_PIN U9  IOSTANDARD LVCMOS33} [get_ports mclk]
# CAM_SCL  = 连接器 pin 13  ->  A20 = IO_L2N_T0_AD8N_35（BANK35）
set_property -dict {PACKAGE_PIN A20 IOSTANDARD LVCMOS33} [get_ports sccb_scl]
# CAM_SDA  = 连接器 pin 14  ->  E17 = IO_L3P_T0_DQS_AD1P_35（BANK35）
set_property -dict {PACKAGE_PIN E17 IOSTANDARD LVCMOS33} [get_ports sccb_sda]

# ---- 时序：飞线/杜邦线，给输入留余量 ---------------------------------------
set_false_path -from [get_ports rst_n]
set_false_path -from [get_ports sccb_sda]

# ---- 配置 ------------------------------------------------------------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
