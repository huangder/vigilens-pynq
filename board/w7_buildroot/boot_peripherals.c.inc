/* Equivalent to usb_phy.tcl prepare followed by gem0_init.tcl prepare.
 * Applied before either U-Boot/Linux owns USB or GEM; never alters DDR/PLLs.
 * Keep the two source TCL hashes in the delivery manifest for review.
 */
static void prepare_peripherals(void)
{
    unsigned long pin;
    write(0xf8000008, 0xdf0d);
    checked(0xf800012c, 0x00400004, 0x00400004);
    for (pin=28; pin<=39; pin++) checked(0xf8000700+4*pin,0xe00,0x200);
    for (pin=48; pin<=49; pin++) checked(0xf8000700+4*pin,0xe00,0x200);
    checked(0xf80007b8,0xeff,0x200);
    checked(0xe000a244,0x4000,0x4000);
    checked(0xe000a248,0x4000,0x4000);
    checked(0xf8000810,0x4000,0);
    write(0xe000a008,0xbfff0000);
    mask_delay(0xf8f00200,50);
    if(rd(0xe000a064)&0x4000) hang();
    write(0xe000a008,0xbfff4000);
    mask_delay(0xf8f00200,10);
    if(!(rd(0xe000a064)&0x4000)) hang();
    if((rd(0xf8000108)&0x7f000)!=0x30000) hang();
    write(0xf8000008,0xdf0d);
    for(pin=16;pin<=27;pin++) checked(0xf8000700+4*pin,0x3fff,pin<=21?0x1202:0x1203);
    for(pin=52;pin<=53;pin++) checked(0xf8000700+4*pin,0x3fff,0x1280);
    checked(0xf800012c,0x40,0x40);
    checked(0xf8000138,0x11,1);
    checked(0xf8000140,0x03f03f71,0x00800801);
    checked(0xf8000214,0x11,0);
    /* R11 sheet10 U13 TXS02612: PS=A side 1.8V, TF=B0 side 3.3V.
     * Retain validated SD mux and clock; do not apply a default board preset.
     */
    for(pin=40;pin<=45;pin++) {
        if((rd(0xf8000700+4*pin)&0xff)!=0x80) hang();
        checked(0xf8000700+4*pin,0xe00,0x200);
    }
}
