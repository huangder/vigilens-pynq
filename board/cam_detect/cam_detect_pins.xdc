#=============================================================================
# cam_detect_pins.xdc —— 摄像头连接检测探针的引脚约束
#-----------------------------------------------------------------------------
# 参考 board/imx219_probe/probe_pins.xdc（2026-10-11 那次真机实验用的，已归档、
# 不许改动）。本文件是它的后继，引脚取值与其一致，差异只有：
#   · 没有 uart_tx（本探针不靠 UART，结果走 JTAG + ILA）
#   · 相机侧保留 mclk 引脚；dbg 包装里 DRIVE_MCLK_SEL=1'b1 ⇒ 由 PL 驱动 25 MHz
#     （板上没有第二个驱动源，见 docs/34 §2）
#
# 【相机 FFC 连接器引脚表】（docs/31 §2 + 2026-10-19 原理图复核 docs/34 §2）
#   pin11 = CAM_GPIO  ← ★ FPGA ball = H17（= PL_LED4），**但中间串了一颗 R33 (0R, NC 不贴)**
#                        （Mizar-Z7_R11.pdf p.5 右栏：PL_LED4 → R33 0R(NC) → CAM_GPIO 14）
#                        ⇒ R33 不补，pin11 就是悬空；补上后由 RTL 的 led[3] 常高驱动
#                          （树莓派/Arducam 定义该脚为 POWER-EN，active-high）
#   pin12 = CAM_CLK   ← FPGA = U9   （板上 U19 的 50M_OSC 经 R156 33R 进的是 PL_CLK_50M/H16，
#                                     与 CAM_CLK 无关 —— 见 docs/34 §2，旧说法已更正）
#   pin13 = CAM_SCL   ← FPGA = A20  （BANK35）
#   pin14 = CAM_SDA   ← FPGA = E17  （BANK35，与板载 24C32 EEPROM 共总线）
#   pin15 = VCC_3V3   （p.14：pin15 接 VCC_3V3，C141 470nF 旁路；pin4/7/10/16 = GND）
#                      ⚠️ 旧版这里写"未知"、并误把 pin15 当未定，已按原理图更正
#
# ⚠️ 两个必须记住的坑：
#   1. SCCB 两条线**与板载 EEPROM 共用**，板上已有 4.7K 上拉 ⇒
#      "总线上有人应答" **不等于** "相机在"，必须看 0x20 这个地址。
#   2. **已更正**：板上晶振经 R156 送的是 PL_CLK_50M(H16)，不是 CAM_CLK；
#      CAM_CLK(U9) 全网只有「FPGA 引脚 + 连接器 pin12」两处 ⇒ 相机 INCK 只能由 PL 给
#      （docs/34 §2 有逐页证据）。IMX219 的 INCK 规格 6~27 MHz，本探针打 25 MHz。
#=============================================================================

# ---- PL 时钟：板上 U19 有源晶振 50 MHz（经 H16 进 PL）------------------------
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports clk]
create_clock -period 20.000 -name sys_clk -waveform {0.000 10.000} [get_ports clk]

# ---- 复位：PL_KEY1（按下为低）------------------------------------------------
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports rst_n]

# ---- 相机侧：CAM_CLK（PL 驱动 25 MHz，DRIVE_MCLK_SEL=1'b1）-------------------
set_property -dict {PACKAGE_PIN U9 IOSTANDARD LVCMOS33} [get_ports mclk]

# ⚠️ U9 不是时钟专用（CCIO/MRCC）引脚，RTL 却用 `posedge mclk` 测频，导致 IBUF→BUFG
#    走不了专用布线，Place 30-574（rule_gclkio_bufg）判 FAIL。测频只关心边沿计数、
#    不在乎时钟插入延迟，故允许非专用布线（Vivado 官方建议的降级；cam_detect_top.v
#    里的 MCLK_MEAS=0 是另一种"绕开"法，但会丢掉第 4 层 MCLK 频率测量，故不采用）。
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets mclk_IBUF]

# ---- 相机侧：SCCB（与板载 EEPROM 共总线，已上拉）------------------------------
set_property -dict {PACKAGE_PIN A20 IOSTANDARD LVCMOS33} [get_ports sccb_scl]
set_property -dict {PACKAGE_PIN E17 IOSTANDARD LVCMOS33} [get_ports sccb_sda]

# ---- 四颗 LED（D6~D9，低电平点亮）-------------------------------------------
set_property -dict {PACKAGE_PIN G14 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN C20 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN B20 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

# ---- 异步输入：按键与 SCCB 数据线都不做时序要求 ------------------------------
set_false_path -from [get_ports rst_n]
set_false_path -from [get_ports sccb_sda]

# CAM_CLK 是外来时钟，只穿过一对 ASYNC_REG 同步器（见 RTL 里的 (* ASYNC_REG *)）。
# 这里不写 -from/-to：Vivado 会为驱动时钟引脚的输入口自动派生时钟，
# 并且 ASYNC_REG 属性会让它按同步器处理 —— 比写死路径名经得起改名。

# ---- 配置电压（与板载一致）--------------------------------------------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
