# =============================================================================
#  mipi_csi2_rx_bd.tcl —— MIPI CSI-2 RX 子系统接入 system BD 的**脚手架**（C 线 M4）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  依据：docs/19 §2（目标链路）、§6（接口提案）、§8（授权风险）；docs/12 §2.4（Mizar MIPI 口）。
#
#  目标链路（docs/19 §2，逐字）：
#     IMX219（15-pin FPC, 2-lane CSI-2, RAW10，每 5 字节装 4 像素）
#       → [1] MIPI CSI-2 RX Subsystem（Xilinx IP，AXI4-Stream 输出）
#       → [2] raw10_unpack   （5 字节 → 4 个 10 bit 像素）
#       → [3] bayer_demosaic （RGGB → RGB888，整数双线性）
#       → [4]（可选 frame_scale）→ roi_statistic → rgb2gray → motion_quality
#
#  运行（Vivado 2026.1，完整权限终端；**须在 build_bd.tcl 跑通之后**）：
#     call D:\Xilinx\2026.1\Vivado\settings64.bat
#     cd /d D:\Desktop\AMD\board
#     vivado -mode batch -source mipi_csi2_rx_bd.tcl
#
#  ⚠️⚠️【未验证】本脚本是"照做脚手架"，**从未在 Vivado 2026.1 里跑过**。以下几处必须在
#     实机首跑时逐条核对并就地修正（都已用 [TODO-verify] 标出）：
#       ① MIPI CSI-2 RX Subsystem 的 VLNV 版本号与参数名前缀（CM_ vs C_，不同版本差异大）；
#       ② 200 MHz D-PHY 参考时钟的来源（Mizar PL 晶振 50 MHz @H16，还是 PS FCLK0 100 MHz）；
#       ③ D-PHY 差分对引脚约束（Mizar 15-pin MIPI 口的 XDC）——**本脚本不含 XDC，需另写**；
#       ④ raw10_unpack 的 beat 宽度/打包假设（docs/27 §4 B6，唯一集成假设，需上板 ILA 抓一次）；
#       ⑤ MIPI CSI-2 RX IP 的**授权**（说法冲突，见 docs/19 §8）——先在 IP Catalog 核实。
# =============================================================================

# ---- 可调参数 ---------------------------------------------------------------
set MIPI_RX_NAME   "mipi_csi2_rx_subsystem_0"
set RAW10_NAME     "raw10_unpack_0"
set BAYER_NAME     "bayer_demosaic_0"
set CLKWIZ_NAME    "clk_wiz_mipi_0"
set MIPI_LANES     2
set MIPI_LINE_RATE 672          ;# Mbps/lane —— Mizar 手册实测 672（docs/12 §2.4）
set MIPI_HRES      1280         ;# 主档 720p60（docs/19 §3）；备档 1080p45 改 1920/1080
set MIPI_VRES      720

# ---- 0) 授权核实（先跑这个，别等综合到一半才发现没授权）-----------------------
puts "== 请先在 Vivado Tcl Console 核实 MIPI CSI-2 RX IP 是否存在/已授权 =="
puts "   get_ipdefs -filter {NAME =~ \"*mipi_csi2_rx*\"}"
puts "   （若返回空，说明本机未装该 IP 或未授权，勿继续 —— docs/19 §8 有说法冲突的出处）"

# ---- 1) 实例化 MIPI CSI-2 RX Subsystem -------------------------------------
# [TODO-verify] VLNV 版本号以本机 IP catalog 为准（5.1 只是示例）
create_bd_cell -type ip -vlnv xilinx.com:ip:mipi_csi2_rx_subsystem:5.1 $MIPI_RX_NAME

# ---- 2) 配置 MIPI RX 参数 ---------------------------------------------------
# [TODO-verify] 参数名前缀（CM_ / C_）与含义逐条在 GUI「Re-customize IP」里核对；
#    下面的名字是 Xilinx MIPI CSI-2 RX 的常见参数名，不保证在当前版本命中。
set_property -dict [list \
    CONFIG.CM_NUM_LANES          $MIPI_LANES \
    CONFIG.CM_DPHY_LINE_RATE     $MIPI_LINE_RATE \
    CONFIG.CM_HRES               $MIPI_HRES \
    CONFIG.CM_VRES               $MIPI_VRES \
    CONFIG.CM_DF_IS_CRC          1 \
] [get_bd_cells $MIPI_RX_NAME]
#   [TODO-verify] **数据格式必须是 RAW10**（CSI-2 Data Type 0x2B）。参数名可能是
#     CM_DATATYPE / C_DATATYPE / datatype，须与 raw10_unpack 的"5 字节装 4 像素"一致。

# ---- 3) 实例化 raw10_unpack + bayer_demosaic（已 HLS_EXEC=3 导出）-------------
# [TODO-verify] VLNV 末段版本号以 export_design 产物（component_*/impl/ip）为准
create_bd_cell -type ip -vlnv xilinx.com:hls:raw10_unpack:1.0   $RAW10_NAME
create_bd_cell -type ip -vlnv xilinx.com:hls:bayer_demosaic:1.0 $BAYER_NAME

# ---- 4) 200 MHz D-PHY 参考时钟（Mizar 的 50 MHz PL 晶振不足以直接喂 D-PHY）----
# [TODO-verify] 输入时钟来源：优先 PS FCLK0（100 MHz，更稳），次选 PL 50 MHz @H16。
create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 $CLKWIZ_NAME
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ                   100.0 \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ     200.0 \
] [get_bd_cells $CLKWIZ_NAME]
#   连接示例（假设 build_bd.tcl 里已有 ps7_0/FCLK_CLK0 时钟网）：
#     connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]          [get_bd_pins $CLKWIZ_NAME/clk_in1]
#     connect_bd_net [get_bd_pins $CLKWIZ_NAME/clk_out1]    [get_bd_pins $MIPI_RX_NAME/dphy_clk_200M]
#     connect_bd_net [get_bd_pins $CLKWIZ_NAME/clk_out1]    [get_bd_pins $MIPI_RX_NAME/lite_aclk]
#     connect_bd_net [get_bd_pins $CLKWIZ_NAME/clk_out1]    [get_bd_pins $MIPI_RX_NAME/video_aclk]

# ---- 5) 数据通路：MIPI RX → raw10_unpack → bayer_demosaic --------------------
# [TODO-verify] MIPI RX 的视频输出 pin 名（video_out）与 raw10_unpack 的输入 pin（raw_in），
#   以导出 IP-XACT 为准。
connect_bd_intf_net [get_bd_intf_pins $MIPI_RX_NAME/video_out] [get_bd_intf_pins $RAW10_NAME/raw_in]
connect_bd_intf_net [get_bd_intf_pins $RAW10_NAME/pix_out]    [get_bd_intf_pins $BAYER_NAME/raw_in]
#   bayer_demosaic 的 rgb_out 接 roi_statistic.video_in（**替换** build_bd.tcl 里的 DMA MM2S 源）：
#     connect_bd_intf_net [get_bd_intf_pins $BAYER_NAME/rgb_out] [get_bd_intf_pins roi_statistic_0/video_in]
#   ⚠️ 这样 DMA MM2S 不再是像素源；S2MM 回读通路保留（供 hw_sw_compare 软硬件比对）。

# ---- 6) AXI-Lite 控制面 -----------------------------------------------------
# [TODO-verify] 三个新 IP 的 s_axi_ctrl pin 名以导出 IP-XACT 为准；且 build_bd.tcl 的
#   SmartConnect 从口可能不够（它已挂 4 HLS + DMA + 2 FIFO），需按实际情况加 SmartConnect
#   级联或换 axi_interconnect。这里只给连接示例，不写死：
#     connect_bd_intf_net [get_bd_intf_pins axi_smc/M0?_AXI] [get_bd_intf_pins $MIPI_RX_NAME/s_axi_ctrl]
#     connect_bd_intf_net [get_bd_intf_pins axi_smc/M0?_AXI] [get_bd_intf_pins $RAW10_NAME/s_axi_ctrl]
#     connect_bd_intf_net [get_bd_intf_pins axi_smc/M0?_AXI] [get_bd_intf_pins $BAYER_NAME/s_axi_ctrl]

# ---- 7) 校验 / 保存 / 地址分配 ----------------------------------------------
# [TODO-verify] 只在上面 [TODO-verify] 全部核对完、无 ERROR 后取消注释：
# assign_bd_address
# validate_bd_design
# save_bd_design

puts "==== mipi_csi2_rx_bd.tcl 脚手架完成（未验证）。===="
puts "==== 下一步：① 核对授权与参数名；② 写 D-PHY XDC；③ 上板 ILA 抓 B6 的 beat 宽度。===="
