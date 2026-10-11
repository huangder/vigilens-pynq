# =============================================================================
#  read_ila.tcl —— 烧 ILA 版 bitstream 并用 JTAG 把探针结论读回来
#
#  ILA probe 映射（与 pl_imx219_probe_dbg.v / build_dbg.tcl 严格一致，共 4 个 probe；
#  受 Vivado BASIC 授权「最多 5 个 probe」限制）：
#     probe0 = {dbg_sq[2:0], dbg_hit_n[3:0]}   7 bits
#     probe1 = {dbg_hit1[7:0], dbg_hit0[7:0]}  16 bits
#     probe2 = {dbg_id1[7:0],  dbg_id0[7:0]}   16 bits  16bit 地址方式的 MODEL_ID
#     probe3 = {dbg_id81[7:0], dbg_id80[7:0]}  16 bits  8bit  地址方式的 MODEL_ID
#
#  dbg_sq = 6 表示 SQ_HOLD（测完）；dbg_hit_n = 扫描命中数；
#  dbg_hit0 / dbg_hit1 = 前两个命中地址（预期 EEPROM 0xA0 + 相机 0x20）。
#
#  探针在 SQ_HOLD 后这些值就冻结，所以直接 -trigger_now 抓当前值。
# =============================================================================

set OUT "D:/Desktop/AMD/_cam_build/dbg_out"
set LTX "$OUT/probe_dbg.ltx"

# bitstream 位置随项目式流程的目录层级而变，逐个候选试
set BIT ""
foreach c [list \
    "$OUT/probe_dbg/probe_dbg.runs/impl_1/pl_imx219_probe_dbg.bit" \
    "$OUT/probe_dbg.runs/impl_1/pl_imx219_probe_dbg.bit" \
    "$OUT/pl_imx219_probe_dbg.bit" ] {
    if {[file exists $c]} { set BIT $c; break }
}

puts "=== READ_ILA_BEGIN ==="
puts "BIT_EXISTS: [file exists $BIT]   $BIT"
puts "LTX_EXISTS: [file exists $LTX]   $LTX"
if {![file exists $BIT]} { puts "ERR bitstream missing"; exit 1 }

if {[catch {open_hw_manager} e]} { puts "ERR open_hw_manager: $e"; exit 1 }
if {[catch {connect_hw_server} e]} { puts "ERR connect_hw_server: $e"; exit 1 }
set tgts [get_hw_targets]
if {[llength $tgts] == 0} { puts "ERR no hw_target"; exit 1 }
current_hw_target [lindex $tgts 0]
if {[catch {open_hw_target} e]} { puts "ERR open_hw_target: $e"; exit 1 }

set dev ""
foreach d [get_hw_devices] { if {[string match "xc7z020*" $d]} { set dev $d } }
if {$dev eq ""} { puts "ERR no xc7z020"; exit 1 }
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev

set_property PROGRAM.FILE $BIT $dev
if {[file exists $LTX]} {
    set_property PROBES.FILE     $LTX $dev
    set_property FULL_PROBES.FILE $LTX $dev
    puts "PROBES_FILE: $LTX"
} else {
    puts "WARN ltx missing -> ILA 可能认不到"
}
if {[catch {program_hw_devices $dev} e]} { puts "ERR program: $e"; exit 1 }
puts "PROGRAM: OK"
refresh_hw_device $dev

# 等探针跑完（上电 200ms + 扫描约 16ms + 读 3ms + UART 8ms），给足 2 秒
after 2500

set ilas [get_hw_ilas -of_objects $dev]
puts "HW_ILAS: [llength $ilas]"
foreach i $ilas { puts "  ILA: $i" }
if {[llength $ilas] == 0} { puts "ERR no hw_ila (检查 .ltx 是否匹配 bitstream)"; exit 1 }
set ila [lindex $ilas 0]

puts "--- probes ---"
foreach p [get_hw_probes -of_objects $ila] {
    puts "  PROBE: $p   WIDTH=[get_property WIDTH $p]"
}

if {[catch {run_hw_ila $ila -trigger_now} e]} { puts "ERR run_hw_ila: $e"; exit 1 }
if {[catch {wait_on_hw_ila $ila} e]}        { puts "ERR wait_on_hw_ila: $e"; exit 1 }
set data [upload_hw_ila_data $ila]
puts "UPLOADED: $data"

set csv "D:/Desktop/AMD/_cam_build/ila_capture.csv"
# ⚠️ 覆盖已有文件必须加 -force，否则报
#    "ERROR: [Labtoolstcl 44-54] You must use the -force option to overwrite existing file"
write_hw_ila_data -force -csv_file $csv $data
puts "CSV: $csv"

catch {close_hw_target}
catch {disconnect_hw_server}
catch {close_hw_manager}
puts "=== READ_ILA_END ==="
exit 0
