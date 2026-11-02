# run_synth.tcl -- re-run synthesis so the BD's new PS7 settings reach system.hwh
#
# Why: `.hwh` (hardware handoff) is produced BY SYNTHESIS, not by saving the BD.
# sdtgen reads the XSA -> system.hwh -> pcw.dtsi, so without a synth re-run the new
# PCW_EN_SDIO0 / PCW_EN_USB0 values never appear in the device tree (docs/42 §3).
#
# Usage (normal terminal, repo writable):
#   . .\board\w7_buildroot\w7env.ps1
#   vivado -mode batch -source board\w7_buildroot\run_synth.tcl -nojournal -nolog
#
# Only synth_1 is re-run (PS-side settings do not change the PL bitstream), which is
# enough for the .hwh. Re-run impl_1 only if you also changed PL logic.

set SCRIPT_DIR [file normalize [file dirname [info script]]]
set TOP_DIR    [file normalize [file join $SCRIPT_DIR ..]]
set PROJ       [file join $TOP_DIR vivado_project vigilens_bd.xpr]
if {![file exists $PROJ]} { puts "ERROR: missing $PROJ"; exit 1 }

open_project $PROJ

puts "== reset_run synth_1"
reset_run synth_1

puts "== launch_runs synth_1 -jobs 8"
launch_runs synth_1 -jobs 8
wait_on_run synth_1

set st  [get_property STATUS   [get_runs synth_1]]
set pr  [get_property PROGRESS [get_runs synth_1]]
puts "SYNTH_PROGRESS=$pr"
puts "SYNTH_STATUS=$st"

if {$pr ne "100%"} {
    puts "ERROR: synthesis did not finish (progress=$pr status=$st)"
    close_project
    exit 1
}

set hwh [glob -nocomplain [file join $TOP_DIR vivado_project vigilens_bd.gen sources_1 bd system hw_handoff *.hwh]]
puts "HWH=$hwh"
if {[llength $hwh] == 0} { puts "ERROR: no .hwh produced"; close_project; exit 1 }

close_project
puts "== synth done"
