#=============================================================================
# build_dump.tcl —— 生成「EEPROM dump 探针」的带 ILA 位流
#-----------------------------------------------------------------------------
# 目的：把 SCCB 总线上 0x50 / 0x58 两台「EEPROM 类」器件的前 16 字节读出来，
#       用内容比对判定 0x58 是不是相机模块自带的 EEPROM（见 docs/34 §6）。
#
# 用法（**完整权限**终端，Vivado 2026.1）：
#     cd /d D:\Desktop\AMD\board\cam_detect
#     vivado -mode batch -source build_dump.tcl
#   成功标志：最后打印 `DUMP_BUILD_ALL_DONE` 且给出 .bit / .ltx 路径。
#
# 产物（仓库外，不入库）：
#   %REPO%\_cam_build\eeprom_dump\eeprom_dump\eeprom_dump.runs\impl_1\pl_eeprom_dump_dbg.bit
#   %REPO%\_cam_build\eeprom_dump\eeprom_dump.ltx
#   %REPO%\_cam_build\eeprom_dump\util_dump.rpt
#
# 复用 `cam_detect_pins.xdc`：端口的名字与类型和 pl_cam_detect 完全一致
#   （clk/rst_n/mclk/sccb_scl/sccb_sda/led[3:0]），其中 `CLOCK_DEDICATED_ROUTE FALSE`
#   那条约束依赖 mclk 仍有输入路径（本设计保留测频读路径，故仍然成立）。
#
# 两个 Vivado 坑（与 build_dbg.tcl 相同，避开）：不用 get_ipdefs；create_ip 之后
#   先 generate_target 再访问该 IP 的属性。
#=============================================================================

set SCRIPT_DIR [file dirname [file normalize [info script]]]
set REPO_DIR   [file dirname [file dirname $SCRIPT_DIR]]
set OUT        [file join $REPO_DIR _cam_build eeprom_dump]
set PROJ       [file join $OUT eeprom_dump]
set PART       "xc7z020clg400-1"

puts "=== EEPROM_DUMP BUILD START ==="
puts "SCRIPT_DIR = $SCRIPT_DIR"
puts "REPO_DIR   = $REPO_DIR"
puts "OUT        = $OUT"

file mkdir $OUT
create_project -force eeprom_dump $PROJ -part $PART

add_files -norecurse [list \
    [file join $SCRIPT_DIR pl_eeprom_dump.v] \
    [file join $SCRIPT_DIR pl_eeprom_dump_dbg.v] \
]
add_files -norecurse -fileset constrs_1 [file join $SCRIPT_DIR cam_detect_pins.xdc]
set_property top pl_eeprom_dump_dbg [current_fileset]

# ILA：4 探针 = dbg0[31:0] / dbg1[63:0] / dbg2[63:0] / dbg3[63:0]
#   名字必须是 ila_0（pl_eeprom_dump_dbg.v 里按这个名字实例化）
#   整轮 dump 约 2×(4 事务 + 约 190 个 SCL 拍) ≈ 2.4 ms(SCL 100k)；DONE 后 dbg 静止，
#   所以同样用 trigger_now 抓终态，depth 2048 足够。
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
write_debug_probes -force [file join $OUT eeprom_dump.ltx]
report_utilization -file [file join $OUT util_dump.rpt]
close_project

set BIT [file join $PROJ eeprom_dump.runs impl_1 pl_eeprom_dump_dbg.bit]
puts "BIT = $BIT"
puts "LTX = [file join $OUT eeprom_dump.ltx]"
puts "=== DUMP_BUILD_ALL_DONE ==="
