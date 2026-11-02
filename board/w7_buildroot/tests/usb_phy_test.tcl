# Runs the real reset sequence against a GPIO/MMIO model; no hardware access.
source [file join [file dirname [info script]] .. usb_phy.tcl]
array set mem {
    0xF800012C 0x00200004 0xF80007B8 0x00001201
    0xE000A244 0x00000080 0xE000A248 0x00000080
    0xE000A044 0x003FFFFF 0xF8000810 0x003FFFFF
    0xF80007C0 0x000016E0 0xF80007C4 0x000016E1
    0xE00021A8 0
}
for {set i 28} {$i <= 39} {incr i} {
    set mem([format 0x%08X [expr {0xF8000700 + 4*$i}]]) 0x00001604
}
set transitions {}
set stuck 0
proc key {a} { format 0x%08X $a }
proc mrd {args} {
    global mem stuck
    set a [key [lindex $args end]]
    if {$a eq "0xE000A064"} {
        if {$stuck} { return 0x4000 }
        if {($mem(0xF80007B8) & 0xff) || !($mem(0xE000A244)&0x4000) ||
            !($mem(0xE000A248)&0x4000) || ($mem(0xF8000810)&0x4000)} { return 0x4000 }
        return $mem(0xE000A044)
    }
    return $mem($a)
}
proc mwr {args} {
    global mem transitions
    lassign [lrange $args end-1 end] a v
    set a [key $a]
    if {$a eq "0xE000A008"} {
        set mask [expr {($v >> 16) & 0xffff}]
        set mem(0xE000A044) [expr {($mem(0xE000A044) & (0xffff0000 | $mask)) | ($v & 0xffff & ~$mask)}]
        lappend transitions [expr {($mem(0xE000A044) >> 14)&1}]
    } else { set mem($a) $v }
}
rename after real_after
proc after {ms} { if {$ms < 10} { error {reset delay too short} } }
proc check {condition message} {
    if {![uplevel 1 [list expr $condition]]} { error $message }
    puts "PASS $message"
}
check {![catch {mizar_usb::cold_usb_baseline}]} {reset USB baseline is accepted}
set mem(0xE00021A8) 3
check {[catch {mizar_usb::cold_usb_baseline}]} {running host cannot be misreported as cold baseline}
set mem(0xE00021A8) 0
mizar_usb::prepare
check {$transitions eq {0 1}} {GPIO reset produces low then high}
check {$mem(0xE000A044) == 0x003fffff} {masked write preserves all unrelated output latches}
check {$mem(0xE000A244) == 0x4080 && $mem(0xE000A248) == 0x4080} {direction and enable preserve unrelated pins}
check {$mem(0xF80007B8) == 0x1200} {reset pin stays LVCMOS18 with GPIO output mux}
check {$mem(0xF800012C) == 0x00600004} {only required peripheral clocks are enabled}
check {$mem(0xF80007C0) == 0x12e0 && $mem(0xF80007C4) == 0x12e1} {UART1 also uses verified 1.8V bank voltage without mux changes}
for {set i 28} {$i <= 39} {incr i} {
    check {$mem([key [expr {0xF8000700+4*$i}]]) == 0x1204} "MIO$i changes electrical type only"
}
set stuck 1
check {[catch {mizar_usb::prepare} reason] && [string match {*pad did not follow*} $reason]} {stuck reset input is rejected instead of using latch as evidence}
check {[lindex $transitions end] == 1} {reset is released even when low verification fails}
puts {RESULT: PASS}
