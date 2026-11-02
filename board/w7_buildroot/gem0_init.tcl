# Opt-in XSDB patch. Call after validated ps7_init + USB prepare, BEFORE Linux.
# Derived from build_gem0_ps.tcl output; does not reconfigure any PLL or DDR.
namespace eval mizar_gem0 {
    proc read32 {a} { return [expr {[mrd -force -value $a] & 0xffffffff}] }
    proc update32 {a mask value} {
        set old [read32 $a]
        set new [expr {(($old & ~$mask) | ($value & $mask)) & 0xffffffff}]
        mwr -force $a $new
        if {[read32 $a] != $new} { error [format {GEM0 write failed at 0x%08X} $a] }
    }
    proc prepare {} {
        # Existing validated init has IO_PLL_FBDIV=48, crystal=33.333333MHz.
        # Fail closed if this assumption changes; never change a shared PLL.
        if {([read32 0xF8000108] & 0x7f000) != 0x30000} {
            error {GEM0 profile requires existing IO PLL FBDIV=48}
        }
        mwr -force 0xF8000008 0xDF0D
        for {set pin 16} {$pin <= 27} {incr pin} {
            set value [expr {$pin <= 21 ? 0x1202 : 0x1203}]
            update32 [expr {0xF8000700 + 4*$pin}] 0x3fff $value
        }
        foreach pin {52 53} {
            update32 [expr {0xF8000700 + 4*$pin}] 0x3fff 0x1280
        }
        update32 0xF800012C 0x40 0x40
        update32 0xF8000138 0x11 0x01
        update32 0xF8000140 0x03f03f71 0x00800801
        # Release only GEM0 reference and CPU_1x resets; preserve GEM1.
        update32 0xF8000214 0x11 0
        puts {GEM0_PREPARE_OK bank501=1.8V MIO=16..27 MDIO=52..53 TX=25MHz max-speed=100}
    }
}
