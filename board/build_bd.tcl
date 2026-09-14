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
#
#  前置：
#      1) 四个 IP 已导出为 Vivado IP（在 fpga/ 下依次跑，HLS_EXEC=3 会调 export_design）：
#            set "HLS_EXEC=3" && set "HLS_IP=roi_statistic" && vitis-run --mode hls --tcl fpga\run_hls.tcl
#            （rgb2gray / motion_quality / fir_filter 同理）
#         导出产物落在 fpga/component_<ip>/hls/impl/ip/。
#      2) 本脚本会把这些 impl/ip 目录登记为 ip_repo。
#
#  ⚠️【未验证】本脚本是"照做脚手架"：Vivado 2026.1 未在本机跑过 BD，下列几处
#     必须在实机首跑时核对并就地修正（都已用 [TODO-verify] 标出）：
#        - PS7 / DMA / FIFO / SmartConnect 的 IP 版本号（VLNV 末段）
#        - PYNQ-Z2 板级 preset（board_part / apply_board_preset）
#        - 每个 IP 的 BD 接口 pin 名（s_axi_ctrl 等，取决于导出的 IP-XACT）
#     上板前逐条核到 validate_bd_design 无 ERROR，并把真实数字回填 m3 预算第 3 节。
# =============================================================================

# ---- 可调参数 ---------------------------------------------------------------
set PROJECT_DIR  "./vivado_project"
set PART         "xc7z020clg400-1"
set BOARD_PART   "tul.com.tw:pynq-z2:part0:1.0"   ;# [TODO-verify] 本机板库是否含此 preset
set CLK_MHZ      100

# 四个 HLS 组件的导出 IP 目录（相对 fpga/）
set IP_REPO_ROOT "D:/Desktop/AMD/fpga"
set ip_repos [list \
    "$IP_REPO_ROOT/component_roi_statistic/hls/impl/ip" \
    "$IP_REPO_ROOT/component_rgb2gray/hls/impl/ip" \
    "$IP_REPO_ROOT/component_motion_quality/hls/impl/ip" \
    "$IP_REPO_ROOT/component_fir_filter/hls/impl/ip" \
]

# 实例名（PYNQ 侧 load_overlay.py 按这些名字检索）
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
set_property board_part $BOARD_PART [current_project]
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
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} \
    [get_bd_cells ps7_0]
# 使能 GP0（接 AXI-Lite 控制面）+ HP0（接 DMA 数据面）；FCLK0 = 100 MHz
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
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_fifo_mm_s:4.2   $FIFO_TX
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_fifo_mm_s:4.2   $FIFO_RX
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0    axi_smc
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0  rst_ps7_0

# =============================================================================
# 2. 时钟 / 复位
# =============================================================================
# FCLK_CLK0 -> 各 IP 的 ap_clk + DMA/FIFO/SmartConnect 时钟
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins rst_ps7_0/slowest_sync_clk]
foreach ip [list $ROI_IP $RGB_IP $MOT_IP $FIR_IP $DMA_IP $FIFO_TX $FIFO_RX axi_smc] {
    connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins $ip/ap_clk]
}
# 复位：Processor System Reset 的 peripheral_aresetn -> 各 IP ap_rst_n
foreach ip [list $ROI_IP $RGB_IP $MOT_IP $FIR_IP] {
    connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins $ip/ap_rst_n]
}
connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins $DMA_IP/axi_resetn]
connect_bd_net [get_bd_pins rst_ps7_0/interconnect_aresetn] [get_bd_pins axi_smc/aresetn]

# =============================================================================
# 3. AXI-Lite 控制面：PS7 M_AXI_GP0 -> SmartConnect -> 各 IP ctrl
# =============================================================================
connect_bd_intf_net [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]
# [TODO-verify] 四个 HLS IP 的 AXI-Lite 从口 pin 名：导出后查 get_bd_intf_pins，
#   通常是 s_axi_ctrl（bundle=ctrl）。若不同，改下面的 pin 名。
foreach {ip pin} [list \
    $ROI_IP s_axi_ctrl \
    $RGB_IP s_axi_ctrl \
    $MOT_IP s_axi_ctrl \
    $FIR_IP s_axi_ctrl \
    $DMA_IP S_AXI_LITE \
    $FIFO_TX S_AXI \
    $FIFO_RX S_AXI \
] {
    connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins $ip/$pin]
}

# =============================================================================
# 4. 数据面（像素链 + FIR 时间序列），对应 m3 预算推荐方案③
# =============================================================================
# 4.1 DMA MM2S -> roi_statistic.video_in（发 640x480 RGB 帧）
connect_bd_intf_net [get_bd_intf_pins $DMA_IP/M_AXIS_MM2S] [get_bd_intf_pins $ROI_IP/video_in]
# 4.2 像素链直连：roi.video_out -> rgb.rgb_in -> mot.gray_in
connect_bd_intf_net [get_bd_intf_pins $ROI_IP/video_out] [get_bd_intf_pins $RGB_IP/rgb_in]
connect_bd_intf_net [get_bd_intf_pins $RGB_IP/gray_out]   [get_bd_intf_pins $MOT_IP/gray_in]
# 4.3 motion.gray_out -> S2MM（回读 384x288 灰度，供软硬件一致性比对）
connect_bd_intf_net [get_bd_intf_pins $MOT_IP/gray_out]   [get_bd_intf_pins $DMA_IP/S_AXIS_S2MM]
# 4.4 DMA 数据面走 HP0（PS -> DDR）
connect_bd_intf_net [get_bd_intf_pins ps7_0/S_AXI_HP0] [get_bd_intf_pins $DMA_IP/M_AXI_SG]
connect_bd_intf_net [get_bd_intf_pins ps7_0/S_AXI_HP0] [get_bd_intf_pins $DMA_IP/M_AXI_MM2S]
connect_bd_intf_net [get_bd_intf_pins ps7_0/S_AXI_HP0] [get_bd_intf_pins $DMA_IP/M_AXI_S2MM]

# 4.5 FIR 时间序列：TX FIFO M_AXIS -> fir_in，fir_out -> RX FIFO S_AXIS
connect_bd_intf_net [get_bd_intf_pins $FIFO_TX/M_AXIS] [get_bd_intf_pins $FIR_IP/fir_in]
connect_bd_intf_net [get_bd_intf_pins $FIR_IP/fir_out]  [get_bd_intf_pins $FIFO_RX/S_AXIS]

# =============================================================================
# 5. 校验 / 保存 / 综合 / 报告
# =============================================================================
# 地址映射（PS 侧 register_map / mmio 依赖这一步）
assign_bd_address
save_bd_design
validate_bd_design

# 综合 + 资源/时序报告 —— 把真实数字回填 m3_system_budget_v1.md 第 3 节
generate_target all [get_files  $PROJECT_DIR/vigilens_bd.srcs/sources_1/bd/system/system.bd]
synth_design -top system_wrapper
report_utilization -hierarchical -file util.rpt
report_timing_summary -file timing.rpt

puts "==== build_bd.tcl 完成。==== "
puts "下一步：open_run / write_bitstream + write_hw_platform 导出 .bit/.hwh 到 board/bitstream/，"
puts "        并逐条跑 board/bringup_check.py -> dma_test.py -> hw_sw_compare.py（对应门限 3~8）。"
