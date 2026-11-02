# Offline register-model test. No XSDB commands or real hardware.
set root [file dirname [file dirname [file normalize [info script]]]]
source [file join $root gem0_init.tcl]
array set regs {0xF8000108 0x30000}
set writes {}
proc mrd {args} {
    global regs
    set a [format 0x%08X [lindex $args end]]
    if {[info exists regs($a)]} { return $regs($a) }
    return 0xA55A5AA5
}
proc mwr {args} {
    global regs writes
    set a [format 0x%08X [lindex $args end-1]]
    set v [lindex $args end]
    set regs($a) $v
    lappend writes $a
}
proc assert {condition message} { if {![uplevel 1 [list expr $condition]]} { error $message } }
mizar_gem0::prepare
foreach pin {16 17 18 19 20 21} {
    set a [format 0x%08X [expr {0xF8000700+4*$pin}]]
    assert {($regs($a) & 0x3fff) == 0x1202} {TX mux/voltage wrong}
}
foreach pin {22 23 24 25 26 27} {
    set a [format 0x%08X [expr {0xF8000700+4*$pin}]]
    assert {($regs($a) & 0x3fff) == 0x1203} {RX mux/voltage wrong}
}
assert {($regs(0xF8000140) & 0x03f03f71) == 0x00800801} {GEM0 TX divider wrong}
foreach {a mask} {0xF800012C 0x40 0xF8000138 0x11 0xF8000140 0x03f03f71 0xF8000214 0x11} {
    assert {($regs($a) & ~$mask & 0xffffffff) == (0xA55A5AA5 & ~$mask & 0xffffffff)} {unrelated bits changed}
}
foreach a $writes {
    assert {$a != "0xF8000108" && ![string match 0xF8006* $a]} {PLL/DDR was written}
}
set regs(0xF8000108) 0x1e000
set writes {}
assert {[catch {mizar_gem0::prepare}]} {wrong PLL not rejected}
assert {[llength $writes] == 0} {wrong PLL caused partial writes}
puts {GEM0_MODEL_PASS mux-voltage divider shared-bit-preservation no-DDR-or-PLL-write wrong-PLL-rejected}
