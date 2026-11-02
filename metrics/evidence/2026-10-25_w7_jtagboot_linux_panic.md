# 2026-10-25 W7 上板证据：JTAG 直启 Linux 6.6.158（在 zynq cpuidle 处 panic）

> **运行方式**：Windows 侧 `xsdb` + Digilent JTAG-HS2，**不用 SD 卡、不用串口交互输入**。
> 全部步骤都是本机真实执行，下面每一行日志都是 `COM11`（115200，Silicon Labs CP210x）抓下来的原样字节。

## 1. 复现命令（真实执行的顺序）

```powershell
# 0) 环境
. .\board\w7_buildroot\w7env.ps1        # 把 Vivado/Vitis 的 bin 加进 PATH、cd 到仓库根
# 1) 把 5 个产物一次性灌进 DDR，最后 PC=0x04000000 起 U-Boot
xsdb D:/w7/reload4.tcl
# 2) 抓串口（另开一个后台任务，110 s，边收边写盘）
powershell -File D:\w7\serial_cap_inc.ps1 -Seconds 110 -Out D:/w7/cap_linux6.txt
# 3) 让 U-Boot 从头再跑一次（不清 DDR）
xsdb D:/w7/rerun2.tcl
```

`reload4.tcl` 里的装载地址（**逐个都实测过 DDR 内容**：zImage 的 `0x016F2818`、dtb 的 `d0 0d fe ed`、
uInitrd 的 `27 05 19 56`、U-Boot 的 `EA0000B8` 全部读回正确）：

| 产物 | 地址 | 说明 |
|---|---|---|
| `zImage`（10,994,176 B） | `0x02000000` | Buildroot 6.6.158，`multi_v7` |
| `uInitrd`（11,493,561 B） | `0x18000000` | `mkimage -T ramdisk -C gzip`，ramdisk 必须是 uImage，裸 `.cpio.gz` 会被判 `Wrong Ramdisk Image Format` |
| `system.dtb`（65,767 B） | `0x1C000000` | sdtgen + dtc 产出（`serial0 = &uart1`） |
| `boot_jtag.scr`（202 B） | `0x03000000` | U-Boot 的 `jtag` boot target 会在这里找脚本 |
| `u-boot-dtb.bin`（1,056,440 B） | `0x04000000` | **必须用 `.bin`**：`u-boot.elf` 不含 appended DTB，`fdtdec_setup` 会 hang（见 `docs/42` §12.2） |

脚本内容（`boot_jtag.scr` 的源）：

```text
echo VigiLens: JTAG script running
setenv bootargs console=ttyPS0,115200 root=/dev/ram0 rw mem=512M cpuidle.off=1
bootz 0x02000000 0x18000000 0x1C000000
```

## 2. U-Boot 侧的前半段（真实输出，来自更早一次同样流程的抓包）

```text
U-Boot 2024.10 (Oct 25 2026 - 20:34:10 +0800)
CPU:   Zynq 7z020
Silicon: v3.1
Model: Avnet ZedBoard board
DRAM:  ECC disabled 512 MiB
MMC:   mmc@e0100000: 0
In:    serial@e0001000
Out:   serial@e0001000
JTAG: Trying to boot script at 3000000
## Executing script at 03000000
VigiLens: JTAG script running
Kernel image @ 0x008000 [ 0x000000 - 0xa7c200 ]
## Loading init Ramdisk from Legacy Image at 02000000 ...
   Image Name:   VigiLens initramfs
   Image Type:   ARM Linux RAMDisk Image (gzip compressed)
   Data Size:    11493497 Bytes = 11 MiB
   Verifying Checksum ... OK
## Flattened Device Tree blob at 01000000
   Booting using the fdt blob at 0x1000000
```

## 3. Linux 侧：内核真的起来了，但在 `zynq_enter_idle` 撞上 Zynq cpuidle 的坑

抓包窗口从内核启动后第 28 s 开始（前面的启动日志被 UART FIFO 冲掉，**这段缺失要如实记着**），之后是：

```text
[   28.065368] 8<--- cut here ---
[   28.068430] Unable to handle kernel paging request at virtual address 04000000 when execute
[   28.076777] [04000000] *pgd=00000000
[   28.080356] Internal error: Oops: 80000005 [#1] SMP ARM
[   28.088627] CPU: 0 PID: 0 Comm: swapper/0 Tainted: G        W          6.6.158 #1
[   28.096107] Hardware name: Xilinx Zynq Platform
[   28.100630] PC is at 0x4000000
[   28.103685] LR is at zynq_enter_idle+0x18/0x20
[   28.108138] pc : [<04000000>]    lr : [<c0e9e4e4>]    psr: 00000193
[   28.288748]  zynq_enter_idle from cpuidle_enter_state+0x94/0x4c8
[   28.294778]  cpuidle_enter_state from cpuidle_enter+0x50/0x54
[   28.300532]  cpuidle_enter from do_idle+0x168/0x2f0
[   28.305428]  do_idle from cpu_startup_entry+0x28/0x2c
[   28.310495]  cpu_startup_entry from rest_init+0xac/0xb0
[   28.315738]  rest_init from arch_post_acpi_subsys_init+0x0/0x8
[   28.321590] Code: bad PC value
[   28.329245] Kernel panic - not syncing: Attempted to kill the idle task!
[   28.335944] CPU1: stopping
[   28.468895] ---[ end Kernel panic - not syncing: Attempted to kill the idle task! ]---
```

## 4. 怎么读这段日志（结论 + 边界）

- **结论 1（确凿）**：内核跑起来了 —— `6.6.158 #1`、`SMP ARM`、`Hardware name: Xilinx Zynq Platform`、
  `rest_init → cpu_startup_entry → do_idle`（这是内核第一个用户态进程起来之后的路径）。**不是"没启动"**。
- **结论 2（确凿）**：崩在 `zynq_enter_idle+0x18`，`PC=0x04000000` 说明 idle 例程把 CPU 送回了
  **U-Boot 的入口地址**（那 4 字节 `EA0000B8` 是 `b` 指令，不是合法内核代码）——
  这是 Zynq 的 cpuidle/OCM 老问题：`mem=` 缩小了内存、加上 DDR 自刷新路径，返回地址被踩。
- **修法**：`cpuidle.off=1`（已写进 `boot_jtag.scr`）。**这次没能复验**：重载脚本时 JTAG 链掉了
  （所有访问报 `MMU section translation fault`，最后 `targets` 一个目标都没有）⇒ **需要给板子断电重上电**。
- **不能证明的**：这条证据**不能**说明"Linux 能稳定跑"或者"USB/摄像头能用"——
  只证明"JTAG 能把 6.6.158 引导到 idle"，且暴露了一个已知的 cpuidle 坑。

## 5. 顺带记录的硬件事实（同一轮实测）

| 项 | 实测值 | 意义 |
|---|---|---|
| `DRAM` | `ECC disabled 512 MiB`（U-Boot 自己 DT 报的） | 板上 DDR **可能只有 512 MiB**（`MT41K256M16` = 4 Gb = 512 MB）。所以 bootargs 加 `mem=512M`；我们的 `system.dtb` 里 `memory` 写的是 `0x3FF00000`（1 GB），**两者矛盾，待查** |
| `MMC` | `mmc@e0100000: 0` | SD 控制器在、卡不在（没有卡插入/没有读卡器） |
| `USB` | `ULPI request timed out` / `zynq ULPI viewport init failed` / `No USB controllers found` | **USB0 的 PHY 复位没生效**（`PCW_USB0_RESET_*` 在 Tcl 里写不进去，见 `docs/42` §4） |
| `Net` | `Could not get PHY for eth0: addr 0` | GEM0 在 BD 里仍是 disabled（`system-user.dtsi` 里也 `disabled`） |
| `In/Out/Err` | `serial@e0001000` | console 就是 UART1（MIO48/49），**串口本身一直是好的** |

## 6. 与其它证据的关系

- `docs/42_W7_PS7使能与设备树链路_20261025.md` §11 的"串口 0 字节"结论**已被推翻**（那两次是抓包窗口没和 poke 重叠），
  正确结论见 §11.5 与本文件 §5 最后一行。
- 本文件只覆盖"JTAG 直启"这一条路；**SD 卡启动仍未验证**（没有读卡器、`BOOT.BIN` 里 mini_fsbl 也不 load u-boot 分区）。
