#=============================================================================
# read_dump.tcl —— 编程板卡 + 触发 ILA + 导出 CSV（喂给 board/check_eeprom.py）
#-----------------------------------------------------------------------------
# 用法（**完整权限**终端，USB-JTAG 已连、板卡已上电）：
#     cd /d D:\Desktop\AMD\board\cam_detect
#     vivado -mode batch -source read_dump.tcl
#   成功标志：打印 `=== READ_DUMP_END ===`，并在 OUT 目录里出现 CSV。
#
# 之后在 PC 侧判读：
#     python board/check_eeprom.py _cam_build/eeprom_dump/eeprom_dump_ila.csv
#
# ⚠️ 为什么用 trigger_now 而不是等触发
#   探针上电即跑（PWRUP 200 ms → 读 A → 读 B → DONE ≈ 0.21 s），累计耗时远大于
#   ILA 的 2048 拍深度（@50 MHz = 41 µs），抓不住全过程；但 dbg0..3 是寄存器输出，
#   DONE 之后静止。所以「先编程 → 等 2 s → trigger_now 抓终态」是正确做法。
#=============================================================================

set SCRIPT_DIR [file dirname [file normalize [info script]]]
set REPO_DIR   [file dirname [file dirname $SCRIPT_DIR]]
set OUT        [file join $REPO_DIR _cam_build eeprom_dump]
set PROJ       [file join $OUT eeprom_dump]
set BIT        [file join $PROJ eeprom_dump.runs impl_1 pl_eeprom_dump_dbg.bit]
set LTX        [file join $OUT eeprom_dump.ltx]
set CSV        [file join $OUT eeprom_dump_ila.csv]

puts "=== READ_DUMP START ==="
foreach f [list $BIT $LTX] {
    if {![file exists $f]} { error "缺文件：$f（先跑 build_dump.tcl）" }
    puts "OK   $f"
}

open_hw_manager
connect_hw_server
set targets [get_hw_targets]
if {[llength $targets] == 0} { error "没有 JTAG 目标：检查 USB 线/驱动/板卡供电" }
puts "HW TARGETS = $targets"
open_hw_target [lindex $targets 0]

set dev ""
foreach d [get_hw_devices] {
    if {[string match "xc7z020*" $d]} { set dev $d; break }
}
if {$dev eq ""} { set dev [lindex [get_hw_devices] 0] }
puts "HW DEVICE = $dev"
current_hw_device $dev

refresh_hw_device -update_hw_probes false $dev
set_property PROGRAM.FILE     $BIT $dev
set_property PROBES.FILE      $LTX $dev
set_property FULL_PROBES.FILE $LTX $dev
program_hw_devices $dev
puts "PROGRAM: OK"

refresh_hw_device -update_hw_probes true $dev

set ilas [get_hw_ilas -of_objects $dev]
if {[llength $ilas] == 0} { error "器件里没找到 ILA：位流/lTX 不配套？" }
foreach ila $ilas {
    puts "ILA = $ila"
    foreach pb [get_hw_probes -of_objects $ila] {
        puts "  probe $pb  WIDTH=[get_property WIDTH $pb]"
    }
}

# 等整轮 dump 跑完（≈0.21 s；给 2 s 余量）
after 2000

run_hw_ila -trigger_now [lindex $ilas 0]
wait_on_hw_ila [lindex $ilas 0]
upload_hw_ila_data [lindex $ilas 0]
write_hw_ila_data -force -csv_file $CSV [get_hw_ila_datas -of_objects [lindex $ilas 0]]

puts "CSV = $CSV"
close_hw_target
close_hw_manager
puts "=== READ_DUMP_END ==="
