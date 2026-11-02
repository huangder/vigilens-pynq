# U-Boot 启动脚本（源文件）。make_sd.sh 会用 mkimage 生成 boot.scr 放到 SD 的 p1。
#
# 事实依据（2026-10-25 实测，不是猜）：
#   * 编出来的 U-Boot 2024.10 默认 `bootcmd=run distro_bootcmd`，
#     `boot_scripts=boot.scr.uimg boot.scr` ⇒ distro boot 会自己找 p1 上的 boot.scr 并 source。
#   * U-Boot 自带默认地址 `kernel_addr_r=0x2000000` / `fdt_addr_r=0x1f00000`（两者只差 1 MB）。
#     zImage 自解压到 0x8000 后正文可能长到 20 MB+，会把 31 MB 处的 dtb 覆盖掉，
#     所以这里把 dtb 显式挪到 0x10000000（256 MB），不复用那个过近的默认值。
#
# 【未上板验证】以上是"读 U-Boot 默认环境串"得出的结论，真实板上是否 source 成功仍未验证。

setenv bootargs "console=ttyPS0,115200 root=/dev/mmcblk0p2 rw rootwait cma=256M usbcore.autosuspend=-1"
setenv fdt_addr_r 0x10000000
echo "VigiLens: loading zImage + system.dtb from mmc 0:1"
load mmc 0:1 ${kernel_addr_r} zImage
load mmc 0:1 ${fdt_addr_r} system.dtb
bootz ${kernel_addr_r} - ${fdt_addr_r}
