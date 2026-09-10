# =============================================================================
#  run_hls.tcl —— C 线 IP 的 C 仿真 + C 综合（多 IP 通用）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#
#  执行（在 fpga/ 目录下，且已 call settings64.bat）：
#      vitis-run --mode hls --tcl run_hls.tcl
#
#  选择要跑的 IP（环境变量 HLS_IP，默认 roi_statistic）：
#      set HLS_IP=motion_quality
#      vitis-run --mode hls --tcl run_hls.tcl
#
#  前置（生成测试向量与 Python 黄金参考；数据变化时才需重跑）：
#      python fpga/sim/gen_frames.py           -> sim/data/        (roi_statistic)
#      python fpga/sim/gen_motion_vectors.py   -> sim/data_motion/ (rgb2gray, motion_quality)
#
#  契约：docs/interface.md —— 器件/时钟/寄存器映射/比对口径均已冻结。
# =============================================================================

# ---- 选择 IP ---------------------------------------------------------------
set ip_name "roi_statistic"
if {[info exists ::env(HLS_IP)] && $::env(HLS_IP) ne ""} {
    set ip_name $::env(HLS_IP)
}

set known_ips [list roi_statistic rgb2gray motion_quality]
if {[lsearch -exact $known_ips $ip_name] < 0} {
    puts "ERROR: unknown HLS_IP '$ip_name'. Known: $known_ips"
    exit 1
}

# IP -> 默认数据目录
set default_data "sim/data"
if {$ip_name eq "rgb2gray" || $ip_name eq "motion_quality"} {
    set default_data "sim/data_motion"
}

set src_file "src/$ip_name.cpp"
set tb_file  "sim/tb_$ip_name.cpp"
if {![file exists $src_file] || ![file exists $tb_file]} {
    puts "ERROR: missing $src_file or $tb_file (run from the fpga/ directory)"
    exit 1
}

puts "INFO: HLS_IP    = $ip_name"
puts "INFO: source    = $src_file"
puts "INFO: testbench = $tb_file"

# Create project / component
open_component -reset component_$ip_name -flow_target vivado

add_files $src_file
add_files -tb $tb_file
set_top $ip_name

# Solution: device + clock（docs/interface.md 第 0 节冻结：PYNQ-Z2 / 100 MHz）
set_part  {xc7z020clg400-1}
create_clock -period 10

# ---- 定位黄金参考数据目录 ---------------------------------------------------
# 测试台通过 argv[1] 收到该目录；找不到时测试台会硬失败（防止"假通过"）。
set data_dir ""
set cand_list [list]
if {[info exists ::env(ROI_DATA_DIR)] && $::env(ROI_DATA_DIR) ne ""} {
    lappend cand_list [file normalize $::env(ROI_DATA_DIR)]
}
catch {
    lappend cand_list [file normalize "[file dirname [file normalize [info script]]]/$default_data"]
}
lappend cand_list [file normalize "[pwd]/$default_data"]
lappend cand_list [file normalize "[pwd]/fpga/$default_data"]

foreach c $cand_list {
    if {[file exists "$c/golden_roi.csv"] || [file exists "$c/golden_motion.csv"]} {
        set data_dir $c
        break
    }
}
if {$data_dir eq ""} {
    puts "WARNING: golden reference not found. Candidates tried:"
    foreach c $cand_list { puts "         $c" }
    puts "WARNING: run the matching gen_*.py first."
    set data_dir [lindex $cand_list 0]
}
puts "INFO: data dir  = $data_dir"

# =============================================================================
#  hls_exec: 1 = 仅 C 综合; 2 = 综合 + RTL 协同仿真(cosim); 3 = 再加导出
# -----------------------------------------------------------------------------
#  csynth 的验收只需 1。
#  ⚠️ csim **不建模 hls::stream 的 FIFO 深度**，流深度不足导致的死锁只有 cosim 才暴露。
#     M3 上板前建议至少跑一次 hls_exec = 2（用 64x48 小向量，见 fpga/README.md）。
# =============================================================================
set hls_exec 1

# C 仿真（两层验证：内嵌边界用例 + 跨语言黄金参考比对）
# 若怀疑测试台没重新编译，改成:  csim_design -clean -argv "$data_dir"
csim_design -argv "$data_dir"

if {$hls_exec == 1} {
    csynth_design
} elseif {$hls_exec == 2} {
    csynth_design
    cosim_design
} elseif {$hls_exec == 3} {
    csynth_design
    cosim_design
    export_design
} else {
    csynth_design
}

exit
