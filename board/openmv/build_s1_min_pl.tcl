# =============================================================================
#  build_s1_min_pl.tcl —— S1「最小 PL 上板」离线半程构建脚本
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  依据：docs/30_上板测试前准备与执行手册_20261005.md §S1；board/openmv/README.md §T3。
#
#  这一步只做**不需要板卡**的那一半：
#      综合(synth) → 实现(impl) → 生成 bitstream(.bit) → 导出 util/timing 报告
#  剩下需要板卡的那一半（Vivado Hardware Manager 下载 + OpenMV 测 clk_test 1kHz + echo）
#  必须有人在场接 JTAG + OpenMV，脚本无法替代。
#
#  运行（Vivado 2026.1，完整权限终端，工作目录 = board/openmv）：
#      cmd /c "call D:\Xilinx\2026.1\Vivado\settings64.bat && vivado -mode batch -source build_s1_min_pl.tcl"
#
#  前置：无（不依赖任何 HLS IP / Block Design / DDR，是第一次上板的最小设计）。
#
#  ⚠️【未验证】本脚本 + pl_uart_echo.v + mizar_z7_openmv_uart.xdc 均未在 Vivado 跑过，
#     本次首跑即为「本地综合 + 实现」验证（AGENTS.md 铁律 2）。所有数字以本脚本产出的
#     .rpt / .log 为准，不得手填。
# =============================================================================

# ---- 可调参数 ---------------------------------------------------------------
set PROJECT_DIR "./s1_vivado_project"
set PART        "xc7z020clg400-1"     ;# 与实物 XC7Z020-1CLG400C 同一颗，见 build_bd.tcl 头注
set TOP         "pl_uart_echo"

# =============================================================================
# 0. 建工程
# =============================================================================
create_project -force s1_minimal_pl $PROJECT_DIR -part $PART

# ---- 源文件 + 约束 ----------------------------------------------------------
add_files -norecurse pl_uart_echo.v
add_files -fileset constrs_1 -norecurse mizar_z7_openmv_uart.xdc
set_property top $TOP [current_fileset]
update_compile_order -fileset sources_1

# =============================================================================
# 1. 综合
# =============================================================================
puts "== [S1] launch synth_1 =="
launch_runs synth_1 -jobs 8
wait_on_run synth_1
set synth_status [get_property STATUS [get_runs synth_1]]
puts "== [S1] synth_1 STATUS: $synth_status =="
if {[string first "Complete" $synth_status] == -1} {
    puts "ERROR: synth_1 未完成（status=$synth_status）。查看 s1_vivado.log 与工程日志。"
    exit 1
}

# =============================================================================
# 2. 实现 + 生成 bitstream
# =============================================================================
puts "== [S1] launch impl_1 -> write_bitstream =="
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set impl_status [get_property STATUS [get_runs impl_1]]
puts "== [S1] impl_1 STATUS: $impl_status =="
if {[string first "Complete" $impl_status] == -1} {
    puts "ERROR: impl_1 未完成（status=$impl_status）。查看 s1_vivado.log 与工程日志。"
    exit 1
}

# =============================================================================
# 3. 导出报告（数字以这里为准，回填时逐行引用）
# =============================================================================
open_run impl_1
report_utilization        -file s1_utilization.rpt
report_timing_summary     -file s1_timing_summary.rpt
report_clock_interaction  -file s1_clock_interaction.rpt
close_design

# bitstream 位置：<PROJECT_DIR>/s1_minimal_pl.runs/impl_1/<TOP>.bit
set bit_file [file join $PROJECT_DIR "s1_minimal_pl.runs" "impl_1" "$TOP.bit"]
if {![file exists $bit_file]} {
    puts "ERROR: 未找到 bitstream：$bit_file"
    exit 1
}
puts "== [S1] BITSTREAM OK: $bit_file =="
puts "== [S1] BUILD DONE =="
exit 0
