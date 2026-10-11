# =============================================================================
#  build_dbg.tcl —— 探针 + ILA 版本（供 JTAG 读回结果）
#  用法：call D:\Xilinx\2026.1\Vivado\settings64.bat
#        vivado -mode batch -source D:\Desktop\AMD\_cam_build\build_dbg.tcl
# =============================================================================

set PART "xc7z020clg400-1"
set SRC  "D:/Desktop/AMD/_cam_build"
set OUT  "D:/Desktop/AMD/_cam_build/dbg_out"

file mkdir $OUT
create_project -force probe_dbg $OUT/probe_dbg -part $PART

# ---- 源码与约束 ----
add_files -norecurse $SRC/pl_imx219_probe.v
add_files -norecurse $SRC/pl_imx219_probe_dbg.v
add_files -fileset constrs_1 -norecurse $SRC/probe_pins.xdc

# ---- ILA IP（不指定版本 = 用本机最新）----
# ⚠️ 两个坑（都实测踩过）：
#   1) 不要在这里调 get_ipdefs -filter：写法不对会触发 "[Ipptcl 7-1638] No Catalog
#      IPs found" 并把 IP 目录状态搞坏。
#   2) 刚 create_ip 出来的 IP 上【不能】读 get_property VLNV——会报
#      "Failed to get property 'VLNV' on IP" 并让整个 Tcl 脚本中断（这才是 build
#      一直 "失败" 的真正原因，create_ip 其实成功了）。改成查 get_ips 列表。
create_ip -name ila -vendor xilinx.com -library ip -module_name ila_0
if {[lsearch -exact [get_ips] ila_0] < 0} {
    puts "ERR create_ip ila_0 failed"
    exit 1
}
puts "ILA_CREATED_OK"
set_property -dict [list \
    CONFIG.C_NUM_OF_PROBES   {4} \
    CONFIG.C_PROBE0_WIDTH    {7} \
    CONFIG.C_PROBE1_WIDTH    {16} \
    CONFIG.C_PROBE2_WIDTH    {16} \
    CONFIG.C_PROBE3_WIDTH    {16} \
    CONFIG.C_DATA_DEPTH      {1024} \
    CONFIG.C_INPUT_PIPE_STAGES {0} \
    CONFIG.C_EN_STRG_QUAL    {0} \
] [get_ips ila_0]
generate_target all [get_ips ila_0]

set_property top pl_imx219_probe_dbg [current_fileset]
update_compile_order -fileset sources_1

# ---- 综合 + 实现 + bitstream ----
launch_runs synth_1 -jobs 4
wait_on_run synth_1
puts "SYNTH_STATUS: [get_property STATUS [get_runs synth_1]]  PROGRESS: [get_property PROGRESS [get_runs synth_1]]"

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
puts "IMPL_STATUS: [get_property STATUS [get_runs impl_1]]  PROGRESS: [get_property PROGRESS [get_runs impl_1]]"

# ---- 调试探针文件（.ltx，ILA 读回必需）----
open_run impl_1
write_debug_probes -force $OUT/probe_dbg.ltx
report_utilization -file $OUT/util_dbg.rpt
puts "LTX_WRITTEN"
puts "DBG_BUILD_ALL_DONE"
exit 0
