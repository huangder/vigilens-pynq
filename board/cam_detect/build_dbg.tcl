#=============================================================================
# build_dbg.tcl —— 生成「FFC 处 MIPI 摄像头是否连上」探针的带 ILA 位流
#-----------------------------------------------------------------------------
# 用法（在**完整权限**的终端里，Vivado 2026.1）：
#     cd /d D:\Desktop\AMD\board\cam_detect
#     vivado -mode batch -source build_dbg.tcl
#   （或用 Vitis 的 vitis-run；本质是同一个 Vivado）
#   成功标志：最后打印 `DBG_BUILD_ALL_DONE` 且给出 .bit / .ltx 路径。
#
# 产物（全部落在仓库外的 build 目录，不入库）：
#   %REPO%\_cam_build\cam_detect_dbg\cam_detect_dbg\cam_detect_dbg.runs\impl_1\pl_cam_detect_dbg.bit
#   %REPO%\_cam_build\cam_detect_dbg\cam_detect_dbg.ltx        ← ILA 探针定义（read_ila.tcl 要用）
#   %REPO%\_cam_build\cam_detect_dbg\util_dbg.rpt
#
# 路径全部由 [info script] 推导，**不写死绝对路径**（归档的 imx219_probe/*.tcl
# 就是因为写死了 D:/Desktop/AMD/_cam_build/... 而换机器就废）。
#
# 两个 Vivado 坑（归档脚本注释里记过，这里同样避开）：
#   ① `get_ipdefs -filter {...}` 会报 `[Ipptcl 7-1638] No Catalog IPs found` —— 不用它；
#   ② `create_ip` 之后**立刻**读该 IP 的 VLNV 属性会中断脚本 —— 中间隔一次
#      `generate_target` 再访问。
#=============================================================================

set SCRIPT_DIR [file dirname [file normalize [info script]]]
set REPO_DIR   [file dirname [file dirname $SCRIPT_DIR]]
set OUT        [file join $REPO_DIR _cam_build cam_detect_dbg]
set PROJ       [file join $OUT cam_detect_dbg]
set PART       "xc7z020clg400-1"

puts "=== CAM_DETECT_DBG BUILD START ==="
puts "SCRIPT_DIR = $SCRIPT_DIR"
puts "REPO_DIR   = $REPO_DIR"
puts "OUT        = $OUT"

file mkdir $OUT
create_project -force cam_detect_dbg $PROJ -part $PART

#---------------------------------------------------------------------------
# 源文件（只加这两份 + 约束；TB 不进综合）
#---------------------------------------------------------------------------
add_files -norecurse [list \
    [file join $SCRIPT_DIR pl_cam_detect.v] \
    [file join $SCRIPT_DIR pl_cam_detect_dbg.v] \
]
add_files -norecurse -fileset constrs_1 [file join $SCRIPT_DIR cam_detect_pins.xdc]
set_property top pl_cam_detect_dbg [current_fileset]

#---------------------------------------------------------------------------
# ILA：4 探针 = dbg0[31:0] / dbg1[63:0] / dbg2[63:0] / dbg3[63:0]
#   名字必须是 ila_0（pl_cam_detect_dbg.v 里按这个名字实例化）
#   DEPTH 2048 表示"上电后总线上最近 2048 个采样"，足够覆盖整段探测过程
#   （SCL_HZ=100k 时整轮扫描+记录约 0.23 s，50 MHz 采样 ⇒ 远超 2048 拍）。
#   ⇒ 因此**必须靠 trigger_now + 取"最后那几个采样"**，而不是只看采样 0；
#     check_camera.py 正是这么做的（扫描全部行，取 step=DONE 的那一档）。
#---------------------------------------------------------------------------
create_ip -name ila -vendor xilinx.com -library ip -module_name ila_0
set_property -dict [list \
    CONFIG.C_NUM_OF_PROBES {4} \
    CONFIG.C_PROBE0_WIDTH  {32} \
    CONFIG.C_PROBE1_WIDTH  {64} \
    CONFIG.C_PROBE2_WIDTH  {64} \
    CONFIG.C_PROBE3_WIDTH  {64} \
    CONFIG.C_DATA_DEPTH    {2048} \
    CONFIG.C_INPUT_PIPE_STAGES {0} \
    CONFIG.C_EN_STRG_QUAL  {0} \
] [get_ips ila_0]
generate_target all [get_ips ila_0]
puts "ILA_CREATED_OK"

#---------------------------------------------------------------------------
# 综合 + 实现 + 出位流
#---------------------------------------------------------------------------
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {
    error "SYNTH FAILED: [get_property STATUS [get_runs synth_1]]"
}
puts "SYNTH_DONE"

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {
    error "IMPL FAILED: [get_property STATUS [get_runs impl_1]]"
}
puts "IMPL_DONE"

open_run impl_1
write_debug_probes -force [file join $OUT cam_detect_dbg.ltx]
report_utilization -file [file join $OUT util_dbg.rpt]
close_project

set BIT [file join $PROJ cam_detect_dbg.runs impl_1 pl_cam_detect_dbg.bit]
puts "BIT = $BIT"
puts "LTX = [file join $OUT cam_detect_dbg.ltx]"
puts "=== DBG_BUILD_ALL_DONE ==="
