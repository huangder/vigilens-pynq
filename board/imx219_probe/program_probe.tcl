# program_probe.tcl —— 把相机存活探针 bitstream 烧进 PL（JTAG）
puts "=== PROGRAM_PROBE_BEGIN ==="
if {[catch {open_hw_manager} e]} { puts "ERR open_hw_manager: $e"; exit 1 }
if {[catch {connect_hw_server} e]} { puts "ERR connect_hw_server: $e"; exit 1 }
set tgts {}
if {[catch {set tgts [get_hw_targets]} e]} { puts "ERR get_hw_targets: $e"; exit 1 }
if {[llength $tgts] == 0} { puts "ERR no hw_target"; exit 1 }
set t [lindex $tgts 0]
puts "TARGET: $t"
current_hw_target $t
if {[catch {open_hw_target} e]} { puts "ERR open_hw_target: $e"; exit 1 }
set dev ""
foreach d [get_hw_devices] {
  puts "CHAIN_DEVICE: $d"
  if {[string match "xc7z020*" $d]} { set dev $d }
}
if {$dev eq ""} { puts "ERR no xc7z020 in chain"; exit 1 }
puts "SELECTED: $dev"
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
set_property PROGRAM.FILE {D:/Desktop/AMD/_cam_build/syn_out/pl_imx219_probe.bit} $dev
puts "PROGRAM_FILE: [get_property PROGRAM.FILE $dev]"
if {[catch {program_hw_devices $dev} e]} { puts "ERR program_hw_devices: $e"; exit 1 }
puts "PROGRAM: OK"
catch {close_hw_target}
catch {disconnect_hw_server}
catch {close_hw_manager}
puts "=== PROGRAM_PROBE_END ==="
exit 0
