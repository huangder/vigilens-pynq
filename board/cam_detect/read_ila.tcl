#=============================================================================
# read_ila.tcl —— 编程板卡 + 触发 ILA + 导出 CSV（喂给 board/check_camera.py）
#-----------------------------------------------------------------------------
# 用法（**完整权限**终端，USB-JTAG 已连、板卡已上电）：
#     cd /d D:\Desktop\AMD\board\cam_detect
#     vivado -mode batch -source read_ila.tcl
#   成功标志：打印 `=== READ_ILA_END ===`，并在 OUT 目录里出现 CSV。
#
# 之后在 PC 侧判读（**结论由这一步给出，不在 Vivado 里判**）：
#     python board/check_camera.py _cam_build/cam_detect_dbg/cam_detect_ila.csv
#
# ⚠️ 时序要点（为什么不是"先 arm 再上电"）
#   本探针**上电即自动开跑**（STEP_IDLE → PWRUP → SCAN → 记录 → DONE，见 pl_cam_detect.v），
#   整轮约 0.23 s。ILA 只有 2048 拍深度（@50 MHz = 41 µs），**抓不住整轮过程**。
#   但 dbg0..dbg3 是**寄存器输出**：跑到 DONE 之后它们就静止不动了。
#   所以正确做法是「先编程 → 等 2 s 让它跑完 → trigger_now 抓当前静止值」，
#   抓到的这 2048 拍全是终态 ⇒ check_camera.py 取任意一行都对，它另外还会
#   在所有行里挑"step=DONE 且 hit_n 最大"的那一档，防止将来改成持续刷新时读错。
#=============================================================================

set SCRIPT_DIR [file dirname [file normalize [info script]]]
set REPO_DIR   [file dirname [file dirname $SCRIPT_DIR]]
set OUT        [file join $REPO_DIR _cam_build cam_detect_dbg]
set PROJ       [file join $OUT cam_detect_dbg]
set BIT        [file join $PROJ cam_detect_dbg.runs impl_1 pl_cam_detect_dbg.bit]
set LTX        [file join $OUT cam_detect_dbg.ltx]
set CSV        [file join $OUT cam_detect_ila.csv]

puts "=== READ_ILA START ==="
foreach f [list $BIT $LTX] {
    if {![file exists $f]} { error "缺文件：$f（先跑 build_dbg.tcl）" }
    puts "OK   $f"
}

open_hw_manager
connect_hw_server
set targets [get_hw_targets]
if {[llength $targets] == 0} { error "没有 JTAG 目标：检查 USB 线/驱动/板卡供电" }
puts "HW TARGETS = $targets"
open_hw_target [lindex $targets 0]

# 挑 Zynq 那颗器件（xc7z020*）
set dev ""
foreach d [get_hw_devices] {
    if {[string match "xc7z020*" $d]} { set dev $d; break }
}
if {$dev eq ""} { set dev [lindex [get_hw_devices] 0] }
puts "HW DEVICE = $dev"
current_hw_device $dev

refresh_hw_device -update_hw_probes false $dev
set_property PROGRAM.FILE       $BIT $dev
set_property PROBES.FILE        $LTX $dev
set_property FULL_PROBES.FILE   $LTX $dev
program_hw_devices $dev
puts "PROGRAM: OK"

refresh_hw_device -update_hw_probes true $dev

set ilas [get_hw_ilas -of_objects $dev]
if {[llength $ilas] == 0} { error "器件里没找到 ILA：位流/lTX 不配套？" }
foreach ila $ilas {
    puts "ILA = $ila"
    foreach p [get_hw_probes -of_objects $ila] {
        puts "  probe $p  WIDTH=[get_property WIDTH $p]"
    }
}

# 等整轮探测跑完（0.23 s 余量足够；这里给 2 s）
after 2000

run_hw_ila -trigger_now [lindex $ilas 0]
wait_on_hw_ila [lindex $ilas 0]
upload_hw_ila_data [lindex $ilas 0]
write_hw_ila_data -force -csv_file $CSV [get_hw_ila_datas -of_objects [lindex $ilas 0]]

puts "CSV = $CSV"
close_hw_target
close_hw_manager
puts "=== READ_ILA_END ==="
