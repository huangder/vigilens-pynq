# 2026-10-31 SD 冷启动镜像：内容核验与可复现 DTB

目标：把「无 JTAG 冷启动」从"没做"推进到"镜像已备好、只差物理上卡"。

## 1. 镜像事实【已验证】

| 项 | 值 |
|---|---|
| 文件 | `/mnt/d/w7/vigilens_sd.img`（768 MiB） |
| sha256（换 FSBL 后） | `43b2cffdd45513030eb7b7d5051656b22b7f5e5da59b38215b9bbc4441d43faa`（两次只读核验一致） |
| p1 | 64 MiB FAT32，label `boot` |
| p2 | 703 MiB ext4，label `rootfs`，UUID `fd23f3b1-6c5c-468b-8c7e-7b718e210850` |
| p1 内容 | `BOOT.BIN` 1,057,344 B、`zImage` 10,994,176 B、`system.dtb` 65,767 B、`boot.scr` 1,152 B |
| p2 内容 | BusyBox rootfs，`/sbin/init → ../bin/busybox`，`inittab` 有 `ttyPS0::respawn:/sbin/getty` |

## 2. 启动链核验

| 环节 | 证据 |
|---|---|
| `BOOT.BIN` | 含 `[mini_fsbl] VigiLens W7 first stage`、`ps7_init rc`、`jumping to U-Boot @`、`U-Boot generator` |
| `boot.scr` | `mkimage -l`：Image Name `VigiLens boot`，Type `ARM Linux Script (uncompressed)`，1088 B |
| U-Boot | 含 `bootcmd=run distro_bootcmd` 与 `mmc dev` 等命令 |
| boot.scr 内容 | `load mmc 0:1 ${kernel_addr_r} zImage`、`load ... system.dtb`、`bootz`；`bootargs root=/dev/mmcblk0p2 rw rootwait` |
| 设备树 | `/axi/mmc@e0100000`：`status=okay`，`compatible=arasan,sdhci-8.9a` |
| 内核 | `CONFIG_MMC_SDHCI=y`、`CONFIG_MMC_SDHCI_PLTFM=y`、`CONFIG_MMC_SDHCI_OF_ARASAN=y`、`CONFIG_MMC_BLOCK=y`、`CONFIG_EXT4_FS=y`、`CONFIG_ARCH_ZYNQ=y` |

~~结论：镜像内容齐全但不可启动（mini_fsbl 不搬 U-Boot）。~~
**2026-10-31 23:02 已修复**：BOOT.BIN 换为由 Vitis 2026.1 生成的 Xilinx FSBL（523,004 B）+ U-Boot 2024.10，
bootgen 打包 1,170,552 B；FSBL 会按 boot image 头表从 SD 读取 U-Boot 分区。
镜像 sha256 更新为 `43b2cffd…`；核验脚本新增"必须含 U-Boot、且不得含 mini_fsbl"的断言。

## 3. 可复现的 SD 设备树

JTAG 默认树故意关闭 sdhci0（空卡槽会刷中断风暴）；SD 启动必须打开并 `broken-cd`。
新增 `board/w7_buildroot/build_sd_dtb.sh`：

- 从 `mizar-z7.dts` + `system-user.dtsi` 生成；
- 只改 `&sdhci0` 节点：`disabled → okay` 并补 `broken-cd`；
- 编译后断言 `/axi/mmc@e0100000` 的 `status=okay`。

实测输出：size **65,767**，sha256 **8abbe98f7b58ebf1ffb7a970bac893686cf3ee4f13ecd33a93e7f33dbab241ef**，
与镜像内 `system.dtb` **完全一致**（已替换为同一份）。

## 4. 还差什么（物理步骤，软件侧无法代劳）

1. 用 `dd`/Win32DiskImager 把 `vigilens_sd.img` 写到 SD 卡（整盘写入，768 MiB）；
2. 把 Mizar-Z7 的 **BOOT_MODE 拨到 SD**（当前读回 `0xF800025C = 0x00000000`，即 JTAG）；
3. 插卡、断电重上电（不接 xsdb）；
4. 预期串口：mini_fsbl → U-Boot → `VigiLens boot` → 内核 → `login:`；
5. 验收命令：`cat /proc/cmdline`（应含 `root=/dev/mmcblk0p2`）、`ls /dev/mmcblk0*`。

若 U-Boot 未自动执行 `boot.scr`，可手动：

```text
load mmc 0:1 ${kernel_addr_r} zImage
load mmc 0:1 ${fdt_addr_r} system.dtb
bootz ${kernel_addr_r} - ${fdt_addr_r}
```

## 4.1 非破坏性核验脚本

`board/w7_buildroot/check_sd_image.sh <img>`：只读挂载 p1/p2，检查 boot 文件、`sdhci0=okay`、
`bootargs`、rootfs init/getty、boot.scr，并打印镜像 sha256。

实测：**PASS**，两次运行 sha256 一致 `56cb7248…`（只读挂载不改镜像）。

## 5. 边界

- **没有在 SD 卡上实启动过**：PC 当前只枚举到内置 NVMe（`Get-Disk` 无读卡器），也没有改 J1 拨码；
- U-Boot 的 distro boot 是否真会 source `boot.scr`，仍是【推测】（依据其默认环境串）；
- 镜像 rootfs 只到 BusyBox getty，**不自启**相机/服务/网络；冷启动验收只证明 Linux 用户态；
- 卡槽无 CD/WP，靠 `broken-cd` 轮询。
