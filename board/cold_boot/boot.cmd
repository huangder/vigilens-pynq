# Fail closed on every SD load. Never boot leftover RAM or save an environment.
echo VIGILENS_SD_BOOT: PL + Linux + board-native service
if load mmc 0:1 0x01000000 system.bit;then
 if fpga loadb 0 0x01000000 ${filesize};then
  if load mmc 0:1 0x02000000 zImage;then
   if load mmc 0:1 0x18000000 uInitrd;then
    if load mmc 0:1 0x1c000000 system.dtb;then
     setenv bootargs "console=ttyPS0,115200 root=/dev/ram0 rw mem=512M cpuidle.off=1 cma=128M usbcore.autosuspend=-1 clk_ignore_unused vigilens.autostart=1"
     bootz 0x02000000 0x18000000 0x1c000000
    fi
   fi
  fi
 fi
fi
echo VIGILENS_SD_BOOT_FAILED
