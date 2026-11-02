# 2026-10-26 W7 上板证据：Linux 6.6.158 起进用户态（到 login 提示符）

> **一句话**：JTAG 直启这条路上，内核完整起来了 —— `Run /init as init process` → 各服务 start OK → **`mizar login:`**。
> 全量原始串口日志：`metrics/evidence/2026-10-26_w7_linux_boot_console.log`（42,758 B，本文件所有行都出自它）。

## 1. 怎么跑出来的（一条命令）

```powershell
.\board\w7_buildroot\boot_after_powercycle.ps1 -CaptureSeconds 300 -WaitSeconds 130 -Out D:\w7\capD.txt
```

内部顺序：**先起串口抓包并确认 `[[open ok]]`** → `xsdb D:/w7/reload5.tcl`
（`rst -processor` → `ps7_init` → `dow -data` 五个产物 → `rwr pc 0x04000000` → `con`）→ 等 130 s → 打印日志尾。

## 2. 关键证据（逐行原样）

```text
U-Boot 2024.10 (Oct 25 2026 - 20:34:10 +0800)
CPU:   Zynq 7z020
Model: Avnet ZedBoard board
DRAM:  ECC disabled 512 MiB
Core:  22 devices, 16 uclasses, devicetree: board
MMC:   mmc@e0100000: 0
In/Out/Err: serial@e0001000
JTAG: Trying to boot script at 3000000
## Executing script at 03000000
VigiLens: JTAG script running
## Loading init Ramdisk from Legacy Image at 18000000 ...
   Image Name:   VigiLens initramfs
   Verifying Checksum ... OK
## Flattened Device Tree blob at 1c000000
   Loading Ramdisk to 1cfd6000, end 1dacc079 ... OK
   Loading Device Tree to 1cfc3000, end 1cfd5fb2 ... OK
Starting kernel ...

[    0.000000] Booting Linux on physical CPU 0x0
[    0.000000] Linux version 6.6.158 (root@dream) (arm-buildroot-linux-gnueabihf-gcc.br_real (Buildroot -g0a9b7567) 13.4.0 ...) #1 SMP Sun Oct 25 20:37:16 CST 2026
[    0.000000] OF: fdt: Machine model: MicroPhase Mizar-Z7 7020
[    0.000000] cma: Reserved 256 MiB at 0x0c800000 on node -1
[    0.000000] Kernel command line: console=ttyPS0,115200 root=/dev/ram0 rw mem=512M cpuidle.off=1
[    1.030504] Trying to unpack rootfs image as initramfs...
[    2.790430] Freeing unused kernel image (initmem) memory: 2048K
[    2.797136] Run /init as init process
Starting syslogd: OK
Starting klogd: OK
Running sysctl: OK
Starting network: OK
Starting crond: OK
Starting dropbear sshd: OK

VigiLens W7 (Mizar-Z7 7020)
mizar login:
```

- **`Kernel panic|Oops|Unable to handle` 命中数 = 0**（`grep -c` 实测），上一轮那个
  `Attempted to kill the idle task!` 没有再出现 ⇒ **`cpuidle.off=1` 这条修法有效**。
- `Machine model: MicroPhase Mizar-Z7 7020` 说明用的是**我们自己的 `system.dtb`**（不是 U-Boot 自带的 zed 板名）。
- 内存：`mem=512M` 生效（`node 0: [mem 0x0-0x1fffffff]`），与 U-Boot 报的 `DRAM: 512 MiB` 一致；
  **`system-top.dts` 里写的 1 GB 与实物不符**（待改，见 §4）。

## 3. 剩下的真问题：`mmc0` 中断风暴

`SD0` 使能了但卡槽空，`sdhci` 每 ~2.5 s 打一次寄存器 dump，把控制台刷满：

```text
[    4.764888] mmc0: Got command interrupt 0x00030000 even though no command operation was in progress.
[    4.774035] mmc0: sdhci: ============ SDHCI REGISTER DUMP ============
[    4.780465] mmc0: sdhci: Sys addr:  0x00000000 | Version:  0x00008901
[    4.806192] mmc0: sdhci: Present:   0x01ff0000 | Host ctl: 0x00000001
```

`Present: 0x01ff0000` 的 bit16（CARD_INSERTED）为 0 ⇒ **确实没插卡**。这条**不影响启动**，
但会把串口刷满、也让登录交互变难。可选修法（按优先级）：

1. 插一张卡再启动（最省事，且本来就要验证 SD 启动）；
2. `system-user.dtsi` 里 `&sdhci0` 改成 `status = "disabled"`（JTAG 直启用不上卡）——但这会**掩盖** SD 问题，
   只在"先看 Linux 别的功能"时用；
3. 保留节点但加 `no-1-8-v` / 检卡脚 `broken-cd` 的组合，减少轮询。

## 4. 顺带确认的硬件事实

| 项 | 实测 | 备注 |
|---|---|---|
| DDR | 512 MiB | 与 `system.dtb` 的 1 GB 矛盾 ⇒ dts 要改 |
| MMC | `mmc@e0100000: 0`，无卡 | SD0 已使能（`ps7_init` 写了 MIO40..45） |
| 以太网 | `Could not get PHY for eth0: addr 0` / `No ethernet found` | GEM0 在 dts 里 disabled，符合预期 |
| USB | U-Boot 阶段没有出现 `ULPI request timed out` | 这次没走到 USB 枚举；内核侧 `uvcvideo.ko` 在 rootfs 里但没插设备 |
| console | `serial@e0001000`（UART1, MIO48/49） | 串口链路稳定，全程 42 KB 无丢字 |

## 5. 这一轮踩的坑（避免重复）

1. **抓包必须"先开、后启动"**，且同一时刻只能有一个抓包进程占 `COM11`；否则得到
   `OPEN_FAIL: 对端口“COM11”的访问被拒绝`，会被误读成"板子没输出"。
2. **`nohup` 在 git-bash 里不存在**（`nohup: command not found`），后台抓包要用
   `Start-Process powershell -WindowStyle Hidden -ArgumentList ...`。
3. **同一通电周期里反复 `rst -processor` 后再起 U-Boot 会挂在 `DRAM:` 之后**（`pc≈0x1FEF5010`）；
   这次是先用 `rst -system`（在 A9 #0 目标上执行，返回 0）把 PS 拉回干净状态才通过的。
   ⇒ 结论：**"断电重上电"仍是最可靠的重启手段**，脚本已按这个顺序写。
4. 直跳内核（`r0=0,r1=0,r2=dtb,pc=0x02000000`）**没成功**：CPU 最后落在 `0xffff000c`（BootROM）。
   走 U-Boot 的 `bootz` 这条已知路径即可，不必再试。

## 6. 未验证的部分：键盘输入能不能进到板子（PC → PS 方向）

- 板子→PC 方向**确凿可用**：42 KB 内核日志完整收下，无丢字。
- PC→PS 方向**没有验证成功**：用 `serial_login2.ps1` 往 `COM11` 发 `root` + `uname -a` 等命令，
  串口抓到的只有 `mmc0` 中断风暴的碎片，**没有 shell 回显、没有 `VIGILENS_SHELL_OK`**。
  这与 2026-10-25 深夜"从 PC 发 `BOARD_RX_TEST_12345`、JTAG 读 UART1 `RX FIFO` 仍为空"的观测一致。
- **推论（【不确定】）**：`CP210x` 的 TX 可能没接到 `MIO49`（PS 的 UART1_RX），或接线只做了一半。
  要确认只有两条路：① 看原理图/量线；② 保持 CPU halted 时从 PC 发字节、JTAG 读 `SR(0xE000102C)` 的
  `RXEMPTY` 位 —— 本次尝试因 CPU 正在跑 Linux（读寄存器报 `MMU section translation fault`）没能做成。
- **影响**：不影响"内核能起来"这个结论，但**影响交互**：现在能看日志、不能敲命令。
  登录 shell 这条路要等串口 RX 线确认后再验。
