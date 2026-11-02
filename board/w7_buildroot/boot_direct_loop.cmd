# RAM-only JTAG boot, with the PL DMA carveout supplied by direct_loop.dtsi.
setenv bootargs "console=ttyPS0,115200 root=/dev/ram0 rw mem=512M cpuidle.off=1 cma=128M usbcore.autosuspend=-1 clk_ignore_unused"
bootz 0x02000000 0x18000000 0x1c000000
