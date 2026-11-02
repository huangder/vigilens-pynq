# Explicit network entry; ordinary jtag_boot.tcl is untouched.
# Uses the same VIGI_IMAGE_DIR / VIGI_BITSTREAM environment as ordinary boot.
set here [file dirname [file normalize [info script]]]
source [file join $here gem0_init.tcl]
set stream [open [file join $here jtag_boot.tcl] r]
set script [read $stream]
close $stream
set anchor {mizar_usb::prepare}
if {[regexp -all $anchor $script] != 1} { error {network boot anchor changed; review jtag_boot.tcl} }
set script [string map [list $anchor "$anchor\nmizar_gem0::prepare"] $script]
eval $script
