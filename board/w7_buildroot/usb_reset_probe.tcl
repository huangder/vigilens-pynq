# Run only before Linux owns USB/GPIO, after a failed JTAG prepare.
source [file join [file dirname [info script]] usb_phy.tcl]
connect
targets -set -filter {name == "APU"}
puts {== Before reset pulse ==}
mizar_usb::snapshot
puts [format {GPIO_RESET_CTRL=0x%08X} [mizar_usb::read32 0xF800022C]]
mizar_usb::reset_level 0
after 100
puts {== Low: latch versus physical input ==}
mizar_usb::snapshot
mizar_usb::reset_level 1
after 100
puts {== High: latch versus physical input ==}
mizar_usb::snapshot
disconnect
exit 0
