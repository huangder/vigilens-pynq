# Mizar-Z7 R11, schematic sheets 2/9: bank501/VDDIO=1.8V,
# USB0=MIO28..39, RESETB=MIO46, Y3=24MHz. Source from XSDB.
# Call prepare ONLY before the OS owns GPIO/USB (the JTAG boot script does this).
namespace eval mizar_usb {
    variable reset_bit 0x00004000

    proc read32 {address} { return [expr {[mrd -force -value $address] & 0xffffffff}] }
    proc update32 {address mask value} {
        set old [read32 $address]
        set new [expr {(($old & ~$mask) | ($value & $mask)) & 0xffffffff}]
        mwr -force $address $new
        if {[read32 $address] != $new} { error [format {register write failed at 0x%08X} $address] }
        return $new
    }
    proc snapshot {} {
        foreach {name address} {
            APER_CLK 0xF800012C MIO46 0xF80007B8
            GPIO_DIRM1 0xE000A244 GPIO_OEN1 0xE000A248
            GPIO_LATCH1 0xE000A044 GPIO_PINS1 0xE000A064
            MIO_TRI0 0xF800080C MIO_TRI1 0xF8000810
            USB_CMD 0xE0002140 USB_STATUS 0xE0002144
            USB_VIEWPORT 0xE0002170 USB_PORTSC 0xE0002184 USB_MODE 0xE00021A8
        } { puts [format {%s=0x%08X} $name [read32 $address]] }
        for {set pin 28} {$pin <= 39} {incr pin} {
            puts [format {MIO%d=0x%08X} $pin [read32 [expr {0xF8000700 + 4*$pin}]]]
        }
    }
    proc cold_usb_baseline {} {
        # A power-cycle claim also needs a reset USB/GPIO baseline, before rst.
        # This is evidence about these peripherals, not a measurement of rails.
        set mode [expr {[read32 0xE00021A8] & 3}]
        set direction [expr {[read32 0xE000A244] & 0x4000}]
        set output [expr {[read32 0xE000A248] & 0x4000}]
        set mux [expr {[read32 0xF80007B8] & 255}]
        if {$mode != 0 || $direction != 0 || $output != 0 || $mux != 1} {
            error {cold USB baseline not observed; previous USB/GPIO state remains, do not count as cold boot}
        }
        puts {COLD_USB_BASELINE_OK}
    }
    proc reset_level {high} {
        variable reset_bit
        # Mask bits are ACTIVE HIGH: clear only bit14 in the upper half.
        set word [expr {((0xffff ^ $reset_bit) << 16) | ($high ? $reset_bit : 0)}]
        mwr -force 0xE000A008 $word
    }
    proc viewport_wait {mask} {
        set deadline [expr {[clock milliseconds] + 1000}]
        while {[clock milliseconds] < $deadline} {
            set value [read32 0xE0002170]
            if {($value & $mask) == 0} { return $value }
            after 1
        }
        error [format {ULPI timeout mask=0x%08X viewport=0x%08X} $mask $value]
    }
    proc phy_read {reg} {
        # Mainline phy-ulpi-viewport.c: WAKEUP=31, RUN=30, read data=15:8.
        mwr -force 0xE0002170 0xA0000000
        viewport_wait 0x80000000
        mwr -force 0xE0002170 [expr {0x40000000 | (($reg & 255) << 16)}]
        return [expr {([viewport_wait 0x40000000] >> 8) & 255}]
    }
    proc identify {} {
        set vendor [expr {[phy_read 0] | ([phy_read 1] << 8)}]
        set product [expr {[phy_read 2] | ([phy_read 3] << 8)}]
        puts [format {ULPI_ID vendor=0x%04X product=0x%04X} $vendor $product]
        if {$vendor == 0 || $vendor == 65535} { error {invalid ULPI vendor ID} }
    }
    proc check_level {high} {
        variable reset_bit
        set level [expr {([read32 0xE000A064] & $reset_bit) != 0}]
        puts [format {RESETB expected=%d sampled=%d} $high $level]
        if {$level != $high} { error {RESETB pad did not follow GPIO; stop before probing PHY} }
    }
    proc prepare {} {
        variable reset_bit
        mwr -force 0xF8000008 0xDF0D
        # APER GPIO bit22 and USB0 bit2; preserve all other clock gates.
        update32 0xF800012C 0x00400004 0x00400004
        # Fix only USB0 electrical standard, preserving mux/direction/edge/pull.
        for {set pin 28} {$pin <= 39} {incr pin} {
            update32 [expr {0xF8000700 + 4*$pin}] 0x00000e00 0x00000200
        }
        # R11 UART1 is also bank501: LVCMOS33 blocked CP210x -> PS RX.
        foreach pin {48 49} {
            update32 [expr {0xF8000700 + 4*$pin}] 0x00000e00 0x00000200
        }
        # GPIO mux, output enabled, LVCMOS18; preserve pull/edge/receiver settings.
        update32 0xF80007B8 0x00000eff 0x00000200
        update32 0xE000A244 $reset_bit $reset_bit
        update32 0xE000A248 $reset_bit $reset_bit
        # Release master tri-state override for this GPIO only.
        update32 0xF8000810 $reset_bit 0
        reset_level 0
        # Allow pad synchronizers to settle after mux/output-enable changes.
        after 50
        set failed [catch {check_level 0} reason]
        reset_level 1
        after 10
        check_level 1
        if {$failed} { error $reason }
        puts {USB_PREPARE_OK bank501=1.8V RESETB=low->high}
    }
}
