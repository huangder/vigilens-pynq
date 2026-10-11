# =============================================================================
#  syn_probe.tcl —— 只做综合 + 实现（不生成 bitstream，先看能不能过）
#  用法：call D:\Xilinx\2026.1\Vivado\settings64.bat
#        vivado -mode batch -source D:\Desktop\AMD\_cam_build\syn_probe.tcl
# =============================================================================

set PART "xc7z020clg400-1"
set SRC  "D:/Desktop/AMD/_cam_build"
set OUT  "D:/Desktop/AMD/_cam_build/syn_out"

file mkdir $OUT

read_verilog $SRC/pl_imx219_probe.v
read_xdc     $SRC/probe_pins.xdc

synth_design -top pl_imx219_probe -part $PART
report_utilization    -file $OUT/util_syn.rpt
report_timing_summary -file $OUT/timing_syn.rpt
write_checkpoint -force $OUT/probe_syn.dcp

puts "SYNTH_DONE"

# 走一遍实现，把引脚/时序问题在【上板之前】暴露出来。
# 未约束的 mclk/sccb_* 会在这里报警告——那是预期的，等拿到原理图引脚号再补。
opt_design
place_design
route_design
report_utilization    -file $OUT/util_impl.rpt
report_timing_summary -file $OUT/timing_impl.rpt
report_drc            -file $OUT/drc_impl.rpt
write_checkpoint -force $OUT/probe_routed.dcp
write_bitstream   -force $OUT/pl_imx219_probe.bit

puts "IMPL_DONE"
puts "PROBE_BUILD_ALL_DONE"
