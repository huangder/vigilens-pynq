# =============================================================================
#  build_bd.tcl —— C8 Block Design 构建脚本（C 线 M3 上板）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  依据：fpga/report/m3_system_budget_v1.md 第 7 节「C8 执行清单」。
#
#  运行（Vivado 2026.1，完整权限终端）：
#      call D:\Xilinx\2026.1\Vivado\settings64.bat
#      cd /d D:\Desktop\AMD\board
#      vivado -mode batch -source build_bd.tcl
#  只要校验 BD（跳过综合，秒级）：
#      set BD_ONLY=1 && vivado -mode batch -source build_bd.tcl
#
#  前置：
#      1) 四个 IP 已导出为 Vivado IP（在 fpga/ 下依次跑，HLS_EXEC=3 会调 export_design）：
#            set "HLS_EXEC=3" && set "HLS_IP=roi_statistic" && vitis-run --mode hls --tcl fpga\run_hls.tcl
#            （rgb2gray / motion_quality / fir_filter 同理）
#         导出产物落在 fpga/component_<ip>/hls/impl/ip/。
#      2) 本脚本会把这些 impl/ip 目录登记为 ip_repo。
#
#  ✅ 2026-10-18 起本脚本已在 Vivado 2026.1 实机跑通（综合 + 实现 + 导出 bitstream），
#     产物见 board/bitstream/system_wrapper.bit 与 system.hdf；S2/S4 上板实测 DDR 读写正常。
#
#  🚧 v1.3 草案（2026-09-23）：目标板卡 PYNQ-Z2 → **Mizar-Z7020**，见 docs/interface.md §0 与 §6。
#     **器件不变**（实物 XC7Z020-1CLG400C 与 `xc7z020clg400-1` 是同一颗），所以 PART 不动。
#     本脚本相对原版的**唯一实质改动**：不再无条件套用 PYNQ-Z2 的板级 preset。
# =============================================================================

# ---- 可调参数 ---------------------------------------------------------------
set PROJECT_DIR  "./vivado_project"
set PART         "xc7z020clg400-1"
set CLK_MHZ      100

# ---- 板级 preset（v1.3 的关键开关）------------------------------------------
# 为什么加这个开关：原脚本无条件 `set_property board_part tul.com.tw:pynq-z2:...`
# 并 `apply_board_preset 1`，那会把 **PYNQ-Z2 的 DDR/外设配置**套到 Mizar 上 ——
# Mizar 是 1 GB DDR3、PL 晶振 50 MHz、扩展口引脚全不同，套错的后果是**板子起不来**，
# 而且报错会发生在很久以后，很难定位。
#
#   · 若你已安装 MicroPhase 的 Mizar 板级文件：填 BOARD_PART，并设 USE_BOARD_PRESET 1
#       例：set BOARD_PART "microphase.com:mizar_z7:part0:1.0"   ;# 名字以本机 get_board_parts 为准
#   · 若没有（**当前默认**）：保持空 + 0，脚本会走下面「手工配置 PS7」分支，
#     把 DDR/UART 参数按 MicroPhase 参考设计显式写进 PS7。
#     ⚠️ **本项目不允许猜 DDR 参数** —— 猜错只会得到一块起不来的 PS（AGENTS.md 铁律 1）。
set BOARD_PART        ""
set USE_BOARD_PRESET  0

# 四个 HLS 组件的导出 IP 目录（相对 fpga/）
set IP_REPO_ROOT "D:/Desktop/AMD/fpga"

# ---- 灰度档位（2026-10-23 新增；**不设就一切照旧**）-------------------------
#    set "VIGILENS_GRAY_TIER=720p60"
#      -> rgb2gray 换成 3/8 抽取的那份导出 IP（component_rgb2gray_720p60，灰度 480×270）
#      -> 产物名换成 system_wrapper_720p60.bit / .hdf，**不覆盖**现行 640×480 的产物
#   为什么要一个开关而不是直接改：两档的 rgb2gray 是**同一份 RTL 的两次编译**
#   （抽取比是编译期宏），而 640×480 档是现行契约 v1.1 的档位，不能被实验覆盖掉。
#   其余三个 IP 与档位无关（roi/motion 的宽高是运行时寄存器，fir 是另一档），直接复用。
set gray_tier "640x480"
if {[info exists ::env(VIGILENS_GRAY_TIER)] && $::env(VIGILENS_GRAY_TIER) ne ""} {
    set gray_tier $::env(VIGILENS_GRAY_TIER)
}
set RGB_IP_REPO "$IP_REPO_ROOT/component_rgb2gray/hls/impl/ip"
set OUT_STEM "system_wrapper"
if {$gray_tier eq "720p60"} {
    set RGB_IP_REPO "$IP_REPO_ROOT/component_rgb2gray_720p60/hls/impl/ip"
    set OUT_STEM "system_wrapper_720p60"
    if {![file exists [file join $RGB_IP_REPO component.xml]]} {
        puts "ERROR: 找不到 720p60 档的 rgb2gray IP：$RGB_IP_REPO"
        puts "       先跑：set \"HLS_IP=rgb2gray\"; set \"VIGILENS_TIER=720p60\"; set \"HLS_COMPONENT=component_rgb2gray_720p60\";"
        puts "             set \"HLS_EXEC=1\"; vitis-run --mode hls --tcl run_hls.tcl"
        puts "       再跑：set \"HLS_COMPONENT=component_rgb2gray_720p60\"; vitis-run --mode hls --tcl export_ip.tcl"
        exit 1
    }
    puts "INFO: 灰度档位 720p60（3/8 抽取 → 480×270），rgb2gray 来自 $RGB_IP_REPO"
} elseif {$gray_tier ne "640x480"} {
    puts "ERROR: unknown VIGILENS_GRAY_TIER '$gray_tier'. Known: 640x480 / 720p60"
    exit 1
}

set ip_repos [list \
    "$IP_REPO_ROOT/component_roi_statistic/hls/impl/ip" \
    "$RGB_IP_REPO" \
    "$IP_REPO_ROOT/component_motion_quality/hls/impl/ip" \
    "$IP_REPO_ROOT/component_fir_filter/hls/impl/ip" \
]

# 实例名（board/overlay/load_overlay.py 按这些名字检索）
set ROI_IP  "roi_statistic_0"
set RGB_IP  "rgb2gray_0"
set MOT_IP  "motion_quality_0"
set FIR_IP  "fir_filter_0"
set DMA_IP  "axi_dma_0"
set FIFO_TX "axi_fifo_mm_s_tx"
set FIFO_RX "axi_fifo_mm_s_rx"

# =============================================================================
# 0. 建工程
# =============================================================================
create_project -force vigilens_bd $PROJECT_DIR -part $PART

if {$USE_BOARD_PRESET} {
    if {$BOARD_PART eq ""} {
        puts "ERROR: USE_BOARD_PRESET=1 但 BOARD_PART 是空的。请先填板级文件名。"
        exit 1
    }
    # catch 住：板级文件没装时 board_part 赋值会失败，必须**当场报错**而不是继续往下跑
    if {[catch {set_property board_part $BOARD_PART [current_project]} err]} {
        puts "ERROR: 板级 preset '$BOARD_PART' 在本机不可用：$err"
        puts "       请在 Vivado Tcl Console 里跑 `get_board_parts` 看本机板库到底有哪些，"
        puts "       或把 USE_BOARD_PRESET 设回 0 并走手工配置 PS7 的路线。"
        exit 1
    }
    puts "== 已套用板级 preset: $BOARD_PART =="
} else {
    puts "========================================================================"
    puts "⚠️  未套用板级 preset（USE_BOARD_PRESET=0）"
    puts "    目标板是 Mizar-Z7020（1 GB DDR3），不是 PYNQ-Z2（512 MB）。"
    puts "    下面 PS7 的 DDR/UART 配置按 MicroPhase 参考设计显式写入（见 §1 的硬守卫段）。"
    puts "========================================================================"
}
set_property target_language Verilog [current_project]

# 登记 HLS 导出 IP
foreach r $ip_repos {
    if {![file isdirectory $r]} {
        puts "ERROR: 未找到导出 IP 目录 $r（先跑 HLS_EXEC=3 导出）"
        exit 1
    }
}
set_property ip_repo_paths $ip_repos [current_fileset]
update_ip_catalog

# =============================================================================
# 1. 建 Block Design
# =============================================================================
create_bd_design "system"

# ---- PS7（Zynq-7020）--------------------------------------------------------
# [TODO-verify] processing_system7 版本号以本机 IP catalog 为准
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0
# ⚠️ 这里必须用 [list ...] 而**不能**用 {..}：Tcl 的花括号会阻止变量替换，
#    写成 {.. apply_board_preset "$USE_BOARD_PRESET" ..} 会把字面量 "$USE_BOARD_PRESET"
#    传给 Vivado，结果是**永远按 preset=1 或直接报错**，而且很难看出原因。
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config [list make_external "FIXED_IO, DDR" \
                  apply_board_preset $USE_BOARD_PRESET \
                  Master "Disable" Slave "Disable"] \
    [get_bd_cells ps7_0]

# PS7 参数（无板级 preset 时手工填写）。
# 为什么不再 `exit 1`：DDR/UART 参数已按 MicroPhase Mizar-Z7 参考设计（1 GB DDR3 = 两片
# 16-bit MT41K256M16、533.333 MHz）显式落进本脚本，且**已由 S2 / S4 上板实测**：
# 0x08000000 起写入再读回逐字一致、无 "controller is held in reset"。
# ⚠️ 这些是照参考设计填的实值，不是猜的；换板/换料必须重新核，不许"差不多就行"。
if {!$USE_BOARD_PRESET} {
    set_property -dict [list \
        CONFIG.PCW_CRYSTAL_PERIPHERAL_FREQMHZ {33.333333} \
        CONFIG.PCW_APU_PERIPHERAL_FREQMHZ {667} \
        CONFIG.PCW_UIPARAM_DDR_ENABLE {1} \
        CONFIG.PCW_UIPARAM_DDR_PARTNO {MT41K256M16 RE-125} \
        CONFIG.PCW_UIPARAM_DDR_DRAM_WIDTH {16 Bits} \
        CONFIG.PCW_UIPARAM_DDR_DEVICE_CAPACITY {4096 MBits} \
        CONFIG.PCW_UIPARAM_DDR_BUS_WIDTH {32 Bit} \
        CONFIG.PCW_UIPARAM_DDR_FREQ_MHZ {533.333333} \
        CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1} \
        CONFIG.PCW_UART1_UART1_IO {MIO 48 .. 49} \
        CONFIG.PCW_UART1_BAUD_RATE {115200} \
        CONFIG.PCW_UART0_PERIPHERAL_ENABLE {0} \
        CONFIG.PCW_EN_CLK0_PORT {1} \
    ] [get_bd_cells ps7_0]
    puts "== PS7 手工配置完成（DDR 1GB / UART1 MIO48-49 / 33.333 MHz crystal / APU 667 MHz / FCLK0 on）=="
}

# 使能 GP0（接 AXI-Lite 控制面）+ HP0（接 DMA 数据面）；FCLK0 = 100 MHz
# 注意：FCLK0 由 PS 的 33.333 MHz 时钟经 PLL 产生，**与 PL 侧那颗 50 MHz 晶振无关**，
#       所以换板卡不影响 §3.5 的 100 MHz 目标时钟（契约 v1.3 草案已核）。
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ $CLK_MHZ \
] [get_bd_cells ps7_0]

# ---- 四个 HLS IP ------------------------------------------------------------
# [TODO-verify] VLNV 末段版本号（1.0）以 export_design 产物为准
create_bd_cell -type ip -vlnv xilinx.com:hls:roi_statistic:1.0 $ROI_IP
create_bd_cell -type ip -vlnv xilinx.com:hls:rgb2gray:1.0       $RGB_IP
create_bd_cell -type ip -vlnv xilinx.com:hls:motion_quality:1.0 $MOT_IP
create_bd_cell -type ip -vlnv xilinx.com:hls:fir_filter:1.0     $FIR_IP

# ---- 数据通路 IP -------------------------------------------------------------
# [TODO-verify] axi_dma / axi_fifo_mm_s / smartconnect / proc_sys_reset 版本号
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1         $DMA_IP
# ⚠️ 必须**显式关掉 Scatter-Gather**：axi_dma 7.1 的默认值是 c_include_sg=1，
#    而本项目全部上位机与测试代码都走**简单模式**（直接写 MM2S/S2MM LENGTH 寄存器）：
#      · board/overlay/load_overlay.py  run_pixel_chain() 的 send/recv.transfer()
#      · board/dma_test.py / board/hw_sw_compare.py
#      · docs/30 §7 门限 5~8
#    SG 模式下 LENGTH 寄存器不生效（必须建描述符链），且 c_sg_include_stscntrl_strm=1
#    会额外引出 m_axis_mm2s_cntrl / s_axis_s2mm_sts 两条流——BD 里没有任何连接，
#    于是 MM2S 的控制流没人收、S2MM 的状态流没人喂，DMA 卡死：
#    软复位完不成（CR.Reset 恒 1）、SR.DMAIntErr 恒 1、一帧都搬不动
#    （2026-10-19 S4 门限5 实测，见 docs/16 BUG-035）。
#    另外一帧 640x480 RGB=921600 B 在 c_sg_length_width=14 下要 57 个描述符，
#    也不可能是本意。→ 这里改成简单模式，与测试代码口径一致。
# 2026-10-19 S4 第二处实测修正（门限 5 仍搬不动数据）：
#   · c_sg_length_width 保持 14 时，simple mode 的 MM2S_LENGTH 只能存 14 位
#     （921600 & 0x3FFF = 4096）：裸机写 921600 读回 0x1000，整帧根本表达不出来。→ 抬到 23 位。
#   · c_m_axis_mm2s_tdata_width 原来是 32，而 conv_rgb 是 axis_dwidth_converter 32→24
#     （S_TDATA_NUM_BYTES 4 → M 3）。AXI4-Stream 的**非整数**位宽收窄依赖 TKEEP 标记有效字节，
#     而 DMA 简单模式每拍 TKEEP 全 1（4 个有效字节），4 不可能是 3 的整数倍 ⇒ 转换器无法成帧，
#     M_AXIS 永远不给 TREADY，MM2S 连 4 字节都完成不了（SR 停在 0、无错误位，实测）。
#     → MM2S 流宽改 8bit（DDR 里仍是紧密 RGB888，3 B/像素），由 8→24 的整数比转换器打包。
# 2026-10-24 第五处实测（gearbox 落地：把 MM2S 改回 32 bit，用自写 RTL 做 4→3 重整）：
#   t11 定位到瓶颈是"8 bit 流宽 ⇒ 3 时钟/像素 ⇒ 720p 27.68 ms（36 fps）"。
#   IP 不接受 24 bit、官方 dwidth_converter 又做不了 4→3 非整数比（上面 10-19 那条），
#   所以自写 board/axis_32to24_gearbox.v（16 字节 FIFO：入口 4 B/拍、出口 3 B/拍 = 1 像素/时钟，
#   字节序与原来的 conv_rgb(8→24) 一致，下游 IP 不用改）。xsim 自检 8 像素/字节序/tlast/tuser 全过。
#   预期每帧 3 时钟/像素 → 1 时钟/像素：720p ≈ 9.2 ms（≈108 fps）、640 档 ≈ 3.1 ms（≈325 fps）。
set_property -dict [list \
    CONFIG.c_include_sg {0} \
    CONFIG.c_sg_include_stscntrl_strm {0} \
    CONFIG.c_sg_length_width {26} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} \
] [get_bd_cells $DMA_IP]
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_fifo_mm_s:4.3   $FIFO_TX
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_fifo_mm_s:4.3   $FIFO_RX
# ⚠️ axi_fifo_mm_s 的 stream 数据宽是**固定 32bit**：端口 axi_str_txd_tdata 宽度由 C_S_AXI4_DATA_WIDTH
# 决定（component.xml 里 spirit:dependency=(C_S_AXI4_DATA_WIDTH - 1)），C_AXI_STR_TXD_TDATA_WIDTH 参数
# 其实**不控端口宽度**（实测设 16 后 validate 仍报 TXD(4)=32bit）。所以 FIR 16bit 流也必须用
# axis_dwidth_converter 桥接（32↔16），跟 RGB 24bit 同理。
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0    axi_smc
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0    axi_smc_dma
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0  rst_ps7_0
# RGB 24bit 与 DMA 流宽之间的宽度转换：axi_dma 流宽只支持 8/16/32/64...（24bit 不支持），
# 而 roi_statistic/rgb2gray 的 video_in/rgb_in 是 ap_axiu<24>，所以必须桥接。
# 🔧 2026-10-19 S4 实测修正（BUG-035 后半）：原先是 4→3（32bit→24bit）的**非整数**收窄。
#    AXI4-Stream 的非整数收窄依赖 TKEEP 指出每拍哪些字节有效，而 axi_dma 简单模式每拍 TKEEP 全 1，
#    4 个有效字节凑不出 3 字节一拍的整数个像素 ⇒ 转换器不成帧、TREADY 恒 0（MM2S 连 4 字节都完不成）。
#    当时的改法是 MM2S 流宽降到 8bit + 官方 1→3 转换器（能跑，但 3 时钟/像素 = 720p 只有 36 fps）。
# 🔧 2026-10-24（t11 定位瓶颈后）：换成**自写 gearbox**（board/axis_32to24_gearbox.v），
#    MM2S 回到 32bit，由它在 PL 里做 4→3 的字节重整（16 字节 FIFO，入口 4 B/拍、出口 3 B/拍）。
#    它与官方转换器的区别只有一条：**不依赖 TKEEP**，按字节流自己数，所以非整数比也能成帧。
add_files -norecurse D:/Desktop/AMD/board/axis_32to24_gearbox.v
create_bd_cell -type module -reference axis_32to24_gearbox conv_rgb
# ---- 数据面取证核：已于 2026-10-23 从 BD 摘除（C1 收尾）----
# 2026-10-19 排查 S4 门限 5 时，曾在 MM2S→conv_rgb 之间插 board/axis_tap.v、
# 在 DMA M_AXI_MM2S→SmartConnect 之间插 board/axi_tap.v，并用普通 ila（5 个单 bit 探针）
# 观测 TVALID/TREADY/ARVALID/RVALID。取证目的已达成（BUG-036 关闭），验收版必须直连：
#   ① tap 是额外的组合路径，会给时序与资源加噪声，也会让"300 帧不死锁"的结论多一个变量；
#   ② 去掉 tap 后地址编辑器不再需要 exclude_bd_addr_seg，validate 的 CRITICAL WARNING 归零。
# 三个 tap 源文件（axis_tap.v / axi_tap.v / axis_tlast_suppress.v）仍留在 board/ 下，
# 其中 axis_tlast_suppress 是**功能件**（抑制行末 TLAST），下面的实例保持不变。

# FIR 16bit 与 FIFO 32bit 之间的宽度转换（fir_filter.cpp ap_axiu<16>）
create_bd_cell -type ip -vlnv xilinx.com:ip:axis_dwidth_converter:1.1 conv_fir_in
set_property -dict [list CONFIG.S_TDATA_NUM_BYTES {4} CONFIG.M_TDATA_NUM_BYTES {2}] [get_bd_cells conv_fir_in]
create_bd_cell -type ip -vlnv xilinx.com:ip:axis_dwidth_converter:1.1 conv_fir_out
set_property -dict [list CONFIG.S_TDATA_NUM_BYTES {2} CONFIG.M_TDATA_NUM_BYTES {4}] [get_bd_cells conv_fir_out]
# TLAST 抑制器（自研 RTL 模块）：rgb2gray 输出 TLAST=行末（契约冻结，不能改），经 motion_quality
# 透传直连 S2MM 会在第一行末尾提前完成传输（只回读 384B）。此模块强制 m_axis_tlast=0，
# 让 S2MM 按 110592 字节长度完成整帧回读。见 board/axis_tlast_suppress.v。
add_files -norecurse D:/Desktop/AMD/board/axis_tlast_suppress.v
update_compile_order -fileset sources_1
create_bd_cell -type module -reference axis_tlast_suppress tlast_supp

# =============================================================================
# 2. 时钟 / 复位
# =============================================================================
# FCLK_CLK0 -> proc_sys_reset + 各 HLS IP 的 ap_clk
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins rst_ps7_0/slowest_sync_clk]
# PS7 各 AXI 接口的时钟输入也必须接 FCLK_CLK0（否则 validate 报 BD 41-758 clock pins not connected）
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/M_AXI_GP0_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/S_AXI_HP0_ACLK]
foreach ip [list $ROI_IP $RGB_IP $MOT_IP $FIR_IP] {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $ip/ap_clk]
}
# AXI 基础设施 IP 的时钟（pin 名各自不同，不能共用 ap_clk）
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $DMA_IP/s_axi_lite_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $DMA_IP/m_axi_mm2s_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $DMA_IP/m_axi_s2mm_aclk]
# ⚠️ 简单模式（c_include_sg=0）下 DMA 没有 SG 引擎，也就**没有 m_axi_sg_aclk 这个端口**，
#    原来这里对 $DMA_IP/m_axi_sg_aclk 的 connect 会报 pin not found，故删除（2026-10-19 BUG-035）。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $FIFO_TX/s_axi_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $FIFO_RX/s_axi_aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_smc/aclk]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins axi_smc_dma/aclk]
# ext_reset_in 必须接 PS 的 FCLK_RESET0_N，否则 proc_sys_reset 没有复位源
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0/ext_reset_in]
# 复位：Processor System Reset 的 peripheral_aresetn -> 各 IP 复位
foreach ip [list $ROI_IP $RGB_IP $MOT_IP $FIR_IP] {
    connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins $ip/ap_rst_n]
}
connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins $DMA_IP/axi_resetn]
connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins $FIFO_TX/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins $FIFO_RX/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_ps7_0/interconnect_aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins rst_ps7_0/interconnect_aresetn] [get_bd_pins axi_smc_dma/aresetn]
# conv_rgb / conv_fir_in / conv_fir_out（axis_dwidth_converter）的时钟与复位
foreach cv [list conv_rgb conv_fir_in conv_fir_out] {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $cv/aclk]
    connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins $cv/aresetn]
}
# tlast_supp（axis_tlast_suppress 模块）的时钟与复位（组合逻辑透传，aclk 仅用于接口时钟域关联）
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins tlast_supp/aclk]
connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins tlast_supp/aresetn]

# =============================================================================
# 3. AXI-Lite 控制面：PS7 M_AXI_GP0 -> SmartConnect -> 各 IP ctrl
# =============================================================================
connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]
# 7 个 AXI-Lite 从口 → SmartConnect 的 7 个主口（先把 NUM_MI 扩到 7，才有 M00..M06）
# HLS IP 的从口 pin 名 = s_axi_ctrl（已由 export_design 日志确认）
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {7}] [get_bd_cells axi_smc]
set slaves [list \
    $ROI_IP s_axi_ctrl \
    $RGB_IP s_axi_ctrl \
    $MOT_IP s_axi_ctrl \
    $FIR_IP s_axi_ctrl \
    $DMA_IP S_AXI_LITE \
    $FIFO_TX S_AXI \
    $FIFO_RX S_AXI \
]
set mi 0
foreach {ip pin} $slaves {
    connect_bd_intf_net [get_bd_intf_pins axi_smc/M[format %02d $mi]_AXI] [get_bd_intf_pins $ip/$pin]
    incr mi
}

# =============================================================================
# 4. 数据面（像素链 + FIR 时间序列），对应 m3 预算推荐方案③
# =============================================================================
# 4.1 DMA MM2S -> conv_rgb(8→24) -> roi_statistic.video_in（2026-10-23 起直连，取证 tap 已摘除）
connect_bd_intf_net [get_bd_intf_pins $DMA_IP/M_AXIS_MM2S] [get_bd_intf_pins conv_rgb/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins conv_rgb/M_AXIS] [get_bd_intf_pins $ROI_IP/video_in]
# 4.2 像素链直连：roi.video_out -> rgb.rgb_in -> mot.gray_in
connect_bd_intf_net [get_bd_intf_pins $ROI_IP/video_out] [get_bd_intf_pins $RGB_IP/rgb_in]
connect_bd_intf_net [get_bd_intf_pins $RGB_IP/gray_out]   [get_bd_intf_pins $MOT_IP/gray_in]
# 4.3 motion.gray_out -> tlast_supp(抑制行末 TLAST) -> S2MM（回读 384x288 灰度整帧）
connect_bd_intf_net [get_bd_intf_pins $MOT_IP/gray_out]   [get_bd_intf_pins tlast_supp/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins tlast_supp/M_AXIS]  [get_bd_intf_pins $DMA_IP/S_AXIS_S2MM]
# 4.4 DMA 数据面：PS7 HP0 是 AXI3、axi_dma 的 M_AXI_* 是 AXI4，不能直连，
#     必须经一个 SmartConnect 做 AXI3↔AXI4 转换 + 多主口合一。
#     ⚠️ 简单模式只有 MM2S/S2MM 两个主口（没有 M_AXI_SG），所以 NUM_SI 由 3 降为 2。
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] [get_bd_cells axi_smc_dma]
#    MM2S 直连 SmartConnect S00（取证用 mm2s_axi_tap 已于 2026-10-23 摘除）
connect_bd_intf_net [get_bd_intf_pins $DMA_IP/M_AXI_MM2S] [get_bd_intf_pins axi_smc_dma/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins $DMA_IP/M_AXI_S2MM] [get_bd_intf_pins axi_smc_dma/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_smc_dma/M00_AXI] [get_bd_intf_pins ps7_0/S_AXI_HP0]

# 4.4.1 tap 观测点接线：2026-10-23 已随取证核一并摘除（ila_tap / mm2s_tap / mm2s_axi_tap）。

# 4.5 FIR 时间序列：TX FIFO AXI_STR_TXD(32bit) -> conv_fir_in(32→16) -> fir_in(16bit)，
#     fir_out(16bit) -> conv_fir_out(16→32) -> RX FIFO AXI_STR_RXD(32bit)
#     （axi_fifo_mm_s 的 stream 接口名是 AXI_STR_TXD/AXI_STR_TXC/AXI_STR_RXD，不是 M_AXIS/S_AXIS）
connect_bd_intf_net [get_bd_intf_pins $FIFO_TX/AXI_STR_TXD] [get_bd_intf_pins conv_fir_in/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins conv_fir_in/M_AXIS] [get_bd_intf_pins $FIR_IP/fir_in]
connect_bd_intf_net [get_bd_intf_pins $FIR_IP/fir_out]  [get_bd_intf_pins conv_fir_out/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins conv_fir_out/M_AXIS] [get_bd_intf_pins $FIFO_RX/AXI_STR_RXD]

# =============================================================================
# 5. 校验 / 保存 / 综合 / 报告
# =============================================================================
# 地址映射（PS 侧 register_map / mmio 依赖这一步）
#    按 docs/interface 冻结的固定偏移逐条显式分配 —— 地址本来就不该靠猜。
#    （2026-10-19 曾因插了 mm2s_axi_tap 而必须手工分配；tap 已于 2026-10-23 摘除，
#      手工分配继续保留：它是可复核的、与契约一致的写法，不依赖 auto assign 的偶然结果。）
assign_bd_address -offset 0x40000000 -range 0x00001000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs axi_fifo_mm_s_rx/S_AXI/Mem0]
assign_bd_address -offset 0x40001000 -range 0x00001000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs axi_fifo_mm_s_tx/S_AXI/Mem0]
assign_bd_address -offset 0x40010000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs fir_filter_0/s_axi_ctrl/Reg]
assign_bd_address -offset 0x40020000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs motion_quality_0/s_axi_ctrl/Reg]
assign_bd_address -offset 0x40030000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs rgb2gray_0/s_axi_ctrl/Reg]
assign_bd_address -offset 0x40040000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs roi_statistic_0/s_axi_ctrl/Reg]
assign_bd_address -offset 0x41E00000 -range 0x00010000 -target_address_space [get_bd_addr_spaces ps7_0/Data] [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg]
# 数据面：HP0 的 DDR 段给 DMA 的两个主口（MM2S / S2MM）
assign_bd_address -offset 0x00000000 -range 0x40000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_S2MM] [get_bd_addr_segs ps7_0/S_AXI_HP0/HP0_DDR_LOWOCM]
assign_bd_address -offset 0x00000000 -range 0x40000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] [get_bd_addr_segs ps7_0/S_AXI_HP0/HP0_DDR_LOWOCM]
save_bd_design
set vd [validate_bd_design]
# validate_bd_design 的返回值在本机 Vivado 2026.1 实测是**空串**（即使通过），不能只凭 $vd 判错；
# 改用 get_msg_config -count 统计 ERROR / CRITICAL WARNING 消息数（普通 WARNING 不算失败）。
set n_err  [get_msg_config -count -severity {ERROR}]
set n_crit [get_msg_config -count -severity {CRITICAL WARNING}]
puts "== validate_bd_design 完成：return='$vd'，ERROR=$n_err，CRITICAL_WARNING=$n_crit =="
# 取证核摘除后不再有"已知允许"的 CRITICAL WARNING：
# 任何 ERROR 或 CRITICAL WARNING 都算失败（2026-10-23 起，n_crit_allowed = 0）。
set n_crit_allowed 0
if {$n_crit <= $n_crit_allowed} {
    puts "（CRITICAL WARNING 预期为 0：取证 tap 已从 BD 摘除）"
}
if {$n_err > 0 || $n_crit > $n_crit_allowed} {
    puts "ERROR: 设计有错（ERROR=$n_err，CRITICAL_WARNING=$n_crit）—— 先修 BD 再综合（详情见上方日志）。"
    exit 1
}
# 快速迭代开关：设 BD_ONLY=1 时只建 BD + validate，跳过综合（省时排查连接错误）
if {[info exists ::env(BD_ONLY)] && $::env(BD_ONLY) eq "1"} {
    puts "== BD_ONLY=1：BD 校验通过，跳过综合。=="
    exit 0
}

# 综合 + 实现 + 资源/时序报告 + 导出 bitstream —— 把真实数字回填 m3_system_budget_v1.md 第 3 节
# 注意：本脚本用 create_project 建的是**工程流程**，必须用 launch_runs 跑综合/实现，
#       不能直接 synth_design（那样 wrapper 没进设计，报 Synth 8-439 module 'system_wrapper' not found）。
# 生成 BD wrapper 并设为顶层
make_wrapper -files [get_files $PROJECT_DIR/vigilens_bd.srcs/sources_1/bd/system/system.bd] -top
add_files -norecurse $PROJECT_DIR/vigilens_bd.srcs/sources_1/bd/system/hdl/system_wrapper.v
update_compile_order -fileset sources_1
set_property top system_wrapper [current_fileset]

# 综合
launch_runs synth_1 -jobs 2
if {[catch {wait_on_run synth_1} err]} {
    puts "ERROR: 综合失败：$err"
    exit 1
}
open_run synth_1
report_utilization -hierarchical -file util.rpt
report_timing_summary -file timing.rpt

# 实现（布局布线）+ 生成 bitstream —— 门限 2 的 WNS 以 place/route 后的 timing_impl.rpt 为准
launch_runs impl_1 -to_step write_bitstream -jobs 2
if {[catch {wait_on_run impl_1} err]} {
    puts "ERROR: 实现失败：$err"
    exit 1
}
open_run impl_1
report_utilization -hierarchical -file util_impl.rpt
report_timing_summary -file timing_impl.rpt

# 导出 .bit（板上烧录）+ .hdf（PYNQ/mmio 解析地址映射的硬件定义）
# ⚠️ 文件名随档位变（$OUT_STEM）：720p60 档写 system_wrapper_720p60.*，
#    免得"换档重综合"把现行 640×480 的产物悄悄覆盖掉（那是最难发现的一类事故）。
set BIT_DIR [file normalize [file join [file dirname [info script]] bitstream]]
file mkdir $BIT_DIR
file copy -force $PROJECT_DIR/vigilens_bd.runs/impl_1/system_wrapper.bit [file join $BIT_DIR "$OUT_STEM.bit"]
write_hwdef -force -file [file join $BIT_DIR "$OUT_STEM.hdf"]

puts "==== build_bd.tcl 完成。==== "
puts "产物：board/bitstream/$OUT_STEM.bit、board/bitstream/$OUT_STEM.hdf、util.rpt、timing.rpt、util_impl.rpt、timing_impl.rpt"
puts "下一步：逐条跑 board/bringup_check.py -> dma_test.py -> hw_sw_compare.py（对应门限 3~8）。"
