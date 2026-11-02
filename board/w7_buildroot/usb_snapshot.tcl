# Read-only physical snapshot, without halting either ARM core.
source [file join [file dirname [info script]] usb_phy.tcl]
connect
targets -set -filter {name == "APU"}
mizar_usb::snapshot
disconnect
exit 0
