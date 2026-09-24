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
#        - **Mizar-Z7020 的 PS7 配置（DDR = 1 GB，异于 PYNQ-Z2 的 512 MB）—— 见下面"板级 preset"**
#        - 每个 IP 的 BD 接口 pin 名（s_axi_ctrl 等，取决于导出的 IP-XACT）
#     上板前逐条核到 validate_bd_design 无 ERROR，并把真实数字回填 m3 预算第 3 节。
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
#   · 若没有（**当前默认**）：保持空 + 0，然后本脚本会在 PS7 之后**主动停下**，
#     要求你把 DDR 参数按 MicroPhase 参考设计手工补进来。
#     ⚠️ **本项目不允许猜 DDR 参数** —— 猜错只会得到一块起不来的 PS（AGENTS.md 铁律 1）。
set BOARD_PART        ""
set USE_BOARD_PRESET  0

# 四个 HLS 组件的导出 IP 目录（相对 fpga/）
set IP_REPO_ROOT "D:/Desktop/AMD/fpga"
set ip_repos [list \
    "$IP_REPO_ROOT/component_roi_statistic/hls/impl/ip" \
    "$IP_REPO_ROOT/component_rgb2gray/hls/impl/ip" \
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
    puts "    下面 PS7 的 DDR/UART/Ethernet/SD 配置**必须**按 MicroPhase 参考设计手工填写，"
    puts "    本脚本会在 PS7 建好后主动停止，直到你补齐。"
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

# ⛔ 硬守卫：没有板级 preset 就**不许继续**。
#    理由：PS7 的 DDR 配置（Mizar = 1 GB DDR3）如果沿用 PYNQ-Z2 的 512 MB 预设，
#    生成的 FSBL/bitstream 会让 PS 在 DDR 初始化阶段挂住，症状是"板子完全没反应"，
#    极易被误判为板子坏了或镜像坏了。宁可在这里停下并给出明确指引。
if {!$USE_BOARD_PRESET} {
    puts "========================================================================"
    puts "ERROR: 本脚本已停止 —— Mizar-Z7020 的 PS7 配置尚未落实。"
    puts ""
    puts "  1) 在 Vivado GUI 里打开 ps7_0 → Re-customize IP → DDR Configuration，"
    puts "     按 **MicroPhase 提供的 Mizar-Z7 参考设计/原理图** 填写 DDR3 参数"
    puts "     （容量 1 GB、两片 16-bit DDR3 —— 见官方《Mizar-Z7 Reference Manual》DDR3 节）。"
    puts "  2) 同时确认 UART1(MIO14/15, CH340)、Ethernet(RTL8211E)、SD 的 MIO 分配。"
    puts "  3) 用 `write_bd_tcl` 或对照 GUI 里的 CONFIG.PCW_* 列表，把这些行补进本脚本后再重跑。"
    puts ""
    puts "  ⚠️ 也可以走另一条路：装上 MicroPhase 的 Mizar 板级文件后设 USE_BOARD_PRESET 1。"
    puts "  ⚠️ **不要**用猜测的 DDR 参数往下跑 —— 本项目不允许编造/猜测硬件参数。"
    puts "========================================================================"
    exit 1
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
