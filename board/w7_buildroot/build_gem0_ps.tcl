# Offline generation ONLY: no hardware, no changes to the existing board BD.
# vivado -mode batch -source board/w7_buildroot/build_gem0_ps.tcl -nojournal -nolog
set repo [file normalize [file join [file dirname [info script]] ../..]]
set out [file join $repo .tmp net_loop ps7_gem0]
file mkdir $out
create_project -force gem0_probe $out/project -part xc7z020clg400-1
create_bd_design gem0_probe
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 ps
set ps [get_bd_cells ps]
# R11 sheet 2: 33.333333 MHz crystal, bank501=1.8V, Ethernet pins16..27/52..53.
set_property -dict [list \
 CONFIG.PCW_CRYSTAL_PERIPHERAL_FREQMHZ {33.333333} \
 CONFIG.PCW_PRESET_BANK1_VOLTAGE {LVCMOS 1.8V} \
 CONFIG.PCW_EN_CLK0_PORT {0} \
 CONFIG.PCW_USE_M_AXI_GP0 {0} \
 CONFIG.PCW_EN_ENET0 {1} \
 CONFIG.PCW_ENET0_PERIPHERAL_ENABLE {1} \
 CONFIG.PCW_ENET0_ENET0_IO {MIO 16 .. 27} \
 CONFIG.PCW_ENET0_GRP_MDIO_ENABLE {1} \
 CONFIG.PCW_ENET0_GRP_MDIO_IO {MIO 52 .. 53} \
] $ps
set log [open [file join $out properties.txt] w]
foreach p {PCW_CRYSTAL_PERIPHERAL_FREQMHZ PCW_PRESET_BANK1_VOLTAGE PCW_EN_ENET0 PCW_ENET0_PERIPHERAL_ENABLE PCW_ENET0_ENET0_IO PCW_ENET0_GRP_MDIO_ENABLE PCW_ENET0_GRP_MDIO_IO PCW_ENET0_PERIPHERAL_DIVISOR0 PCW_ENET0_PERIPHERAL_DIVISOR1} {
 puts $log "$p=[get_property CONFIG.$p $ps]"
}
close $log
validate_bd_design
save_bd_design
generate_target all [get_files gem0_probe.bd]
puts "GEM0_OFFLINE_GENERATED $out"
close_project
