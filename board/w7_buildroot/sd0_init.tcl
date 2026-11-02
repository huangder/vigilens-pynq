# Opt-in only, before Linux probes SD. R11 sheet10: U13 TXS02612 A side=1.8V.
# Does not issue any SD command or touch the card; only repairs MIO standard.
namespace eval mizar_sd0 {
    proc prepare {} {
        mwr -force 0xF8000008 0xDF0D
        for {set pin 40} {$pin <= 45} {incr pin} {
            set address [expr {0xF8000700 + 4*$pin}]
            set old [expr {[mrd -force -value $address] & 0xffffffff}]
            # Expected SDIO0 mux. Reject a conflicting owner before rewriting.
            if {($old & 0xff) != 0x80} { error "SD0 MIO$pin is not in SDIO mode" }
            set value [expr {($old & ~0xe00) | 0x200}]
            mwr -force $address $value
            if {([mrd -force -value $address] & 0xffffffff) != $value} { error "SD0 MIO$pin write failed" }
        }
        puts {SD0_MIO_PREPARE_OK MIO40..45=1.8V no-card-commands-issued}
    }
}
