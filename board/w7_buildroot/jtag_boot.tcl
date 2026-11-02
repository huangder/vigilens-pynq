# JTAG boot; environment paths are supplied by boot_after_powercycle.ps1.
# All image loads fail closed. Nothing is written to QSPI/SD.
set here [file dirname [file normalize [info script]]]
source [file join $here usb_phy.tcl]
proc halt_cpu {} {
    if {[catch {stop} reason] && ![string match {*Already stopped*} $reason]} { error $reason }
}
set images $::env(VIGI_IMAGE_DIR)
foreach image {zImage uInitrd system.dtb boot_jtag.scr u-boot-dtb.bin} {
    if {![file isfile [file join $images $image]]} { error "missing image: $image" }
}
connect
# hw_server can connect before its USB cable inventory is ready. Select only
# after a bounded retry; no reset/register writes happen while it is absent.
set apu_ready 0
for {set attempt 0} {$attempt < 10} {incr attempt} {
    if {![catch {targets -set -filter {name == "APU"}}]} {set apu_ready 1; break}
    after 500
}
if {!$apu_ready} {error {APU target unavailable after cable discovery timeout}}
puts {== USB snapshot before reset ==}
mizar_usb::snapshot
if {[info exists ::env(VIGI_REQUIRE_COLD)] && $::env(VIGI_REQUIRE_COLD) eq "1"} {
    mizar_usb::cold_usb_baseline
}
targets -set -filter {name =~ "*Cortex-A9 MPCore #0*"}
halt_cpu
rst -system
after 1500
halt_cpu
if {[info exists ::env(VIGI_BITSTREAM)] && $::env(VIGI_BITSTREAM) ne ""} {
    set bit $::env(VIGI_BITSTREAM)
    if {![file isfile $bit]} { error "missing bitstream: $bit" }
    jtag targets -set -filter {name =~ "*xc7z020*"}
    fpga -file $bit
    puts "PL_BITSTREAM_LOADED $bit"
    targets -set -filter {name =~ "*Cortex-A9 MPCore #0*"}
    halt_cpu
}
source [file join $here dt ps7_init.tcl]
ps7_init
ps7_post_config
# Use physical DAP accesses, independent of the ARM debug memory context.
targets -set -filter {name == "APU"}
mizar_usb::prepare
puts {== USB snapshot after prepare ==}
mizar_usb::snapshot
if {[catch {mizar_usb::identify} reason]} { puts "ULPI_PROBE_FAILED $reason" }
targets -set -filter {name =~ "*Cortex-A9 MPCore #0*"}
halt_cpu
foreach {image address} {
    zImage 0x02000000 uInitrd 0x18000000 system.dtb 0x1C000000
    boot_jtag.scr 0x03000000 u-boot-dtb.bin 0x04000000
} {
    dow -data [file join $images $image] $address
    puts "LOADED $image"
}
rwr pc 0x04000000
rwr cpsr 0x000001D3
con
puts {JTAG_BOOT_STARTED}
disconnect
exit 0
