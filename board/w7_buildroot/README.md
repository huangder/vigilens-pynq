# board/w7_buildroot —— W7 的 Buildroot 极简镜像脚手架

> **2026-11-02 SD自治冷启动交付候选**：[文档78](../../docs/usage.md)。
> 最新SPL/唯一Mizar FIT/官方Bootgen/PL/Linux及原生应用已封包；实际28文件与内核依赖核验通过。
> 当前入口`board/cold_boot/Build-Delivery.ps1`；物理SD启动仍待用户拷贝后断电验收，下方旧包只作历史记录。

> **2026-10-29 网络闭环【已验证】**：GEM0/RTL8211E、100Mbps/Full，板端同源RGB/PL结果
> 已接PC算法和本机网页。现行入口：[NETWORK_LOOP.md](NETWORK_LOOP.md)。30fps、独立启动与算法标定仍未完成。

> **2026-10-28 直连PL实验【已验证】**：真实USB相机帧已在Linux上解码并经DMA送入PL，
> 后续299帧灰度/ROI/有效运动量逐点对拍零差异。带CPU全量对拍约15.18fps，尚未达到30fps。
> 构建与实跑入口：[DIRECT_LOOP.md](DIRECT_LOOP.md)；原始证据：[技术记录](../../metrics/evidence/2026-10-28_direct_camera_pl.md)。

> **2026-10-28 USB 修复入口**：旧 README 下方的“未上板/没有串口”等是历史状态，
> Linux JTAG 启动已有证据。当前 USB 修复与真实验证状态见
> [排障记录](../../metrics/evidence/2026-10-28_usb_camera_repair.md)。
> 原理图 R11 第 2/9 页：MIO28..39=USB0，MIO46=RESETB，Bank501/VDDIO=1.8V，Y3=24MHz，J5=USB 5V 跳帽。
> 新入口 `boot_after_powercycle.ps1` 使用仓库内 `jtag_boot.tcl` 和串口脚本，
> 提供 `-ImageDir/-Port/-Out`；不再依赖仓库外 `reload5.tcl`、`serial_cap2.ps1`。
> `usb_phy.tcl` 仅在 OS 启动前操作寄存器；Linux 用 `reset-gpios` 管理复位。
> `S99vigi` 只读寄存器并运行不保存像素的采帧检查，不能在 Linux 运行时调用 `prepare`。
> `S98usb_host` 在 legacy PHY 就绪后恢复一次尚未绑定的 USB0 子驱动，已绑定时不操作。
> 2026-10-28 已自动枚举 DECXIN `0c45:64ab`，640×480/1280×720 各实采30帧；
> 此相机 MJPG 模式表只报60fps，严格30fps检查会返回非零，详见证据。
> 后续已完成3次物理冷启动（两档各30帧），3次摄像头拔插均自动重新识别。
> 冷启动验收加 `-RequireColdState`：在软件复位前检查USB/GPIO复位基线，防止USB线仍供电时误计为断电冷启动。

> **【未上板验证】** 离线链路已全部跑通（BOOT.BIN / zImage / system.dtb / boot.scr / rootfs.ext4 / SD 镜像都产出了，
> 见 docs/41 §23），但**没有一块板子真的从这张卡启动过**：本机没有读卡器（`Get-Disk` 只有一块 NVMe），
> 也没接串口。下面的"判据"都是待验证的预期，不是结论。
> 判据与推理全文：[docs/41_W7_Buildroot极简镜像方案_20261025.md](../../docs/README.md)
> **改 PS7 / 生成设备树前必读**：[docs/42_W7_PS7使能与设备树链路_20261025.md](../../docs/README.md)
> 验收判据沿用 [docs/37 §W7](../../docs/README.md)：板上 `v4l2-ctl --list-devices` 见相机。

## 文件

| 文件 | 作用 | 在哪跑 |
|---|---|---|
| `export_xsa.tcl` | 从 `board/vivado_project/vigilens_bd.xpr` 导 **XSA**（PetaLinux 与 DT 生成器的第 0 步） | Windows，Vivado 2026.1 |
| `ps7_w7.tcl` | 补 PS7 外设开关及 Bank501 的1.8V配置；**`PCW_EN_*` + `PCW_*_PERIPHERAL_ENABLE`+`*_IO` 两类都要写**。USB复位由PS GPIO/Linux管理。更新后的Vivado脚本尚未重新综合；本次上板用JTAG补丁与设备树 | Windows，Vivado 2026.1 |
| `system-user.dtsi` | 板级 DTS 覆盖：USB0为 **okay**，JTAG模式下SD0为 **disabled**；USB使用1.8V pinctrl和GPIO46低有效复位 | 与 sdtgen 生成的 `pcw.dtsi` 一起编进内核 |
| `check_dts.ps1` | **设备树预检**：Windows 上 3 秒把 DTS 编成 DTB 并断言关键项（**已实测 PASS**，见 docs/41 §17） | Windows，需 `gcc` + Vitis 自带 `dtc.exe` |
| `preflight.ps1` | **一条命令跑完全部离线自检**（工具存在性 / DTB / UART 仿真 / shell 语法 / XSA / FSBL / BOOT.BIN 头），只读仓库（**已实测 13 PASS / 0 WARN / 0 SKIP**，见 docs/41 §19、§21） | Windows |
| `mini_fsbl/` | **自己写的最小一级引导**：`ps7_init` → UART1 打印 → 跳 U-Boot，**不依赖 Vitis/BSP**，一条命令产出 `fsbl.elf` + `BOOT.BIN`（**已实测产出**，见 docs/41 §21） | Windows，Xilinx 自带 arm-gcc + bootgen |
| `w7env.ps1` | **点源一次**：把 `Vivado\bin`/`Vitis\bin` 排进 PATH、cd 到仓库根、打印各工具位置（解决 `vivado`/`bootgen` 找不到、`vitis.bat -s` 相对路径找不到；见 docs/41 §20） | Windows，普通 PowerShell（`. board\w7_buildroot\w7env.ps1`） |
| `sim/` | QEMU 上的裸机 **UART1 冒烟测试**（`start.S`、`link.ld`、`hello_uart1.c`、`run_sim.ps1`），证明 `0xE0001000` 的初始化序列真能吐字节（**已实测 PASS**，见 docs/41 §19） | Windows，需 Xilinx 自带 arm-gcc + QEMU |
| `dt/`（不入库） | 放 `sdtgen` 的 4 个产物：`system-top.dts` / `pcw.dtsi` / `pl.dtsi` / `zynq-7000.dtsi` | Windows（sdtgen） |
| `build_dtb.ps1` | **把 DTS 编成真正要用的 `system.dtb`**（cpp→dtc，和 check_dts 同一套步骤但保留产物；**已实测 65,767 B / FDT 魔数正确**） | Windows，需 `gcc` + Vitis 自带 `dtc.exe` |
| `wsl_build.sh` | WSL/Ubuntu 里一键编 rootfs + 内核 + U-Boot（**2026-10-25 实跑 exit 0**：`zImage` 10,994,176 B、`u-boot`(ELF)、`rootfs.ext2` 512 MiB；六个配置坑见 docs/41 §22） | Linux 主机 / WSL2 |
| `buildroot_w7.config` | Buildroot 增量配置（ext4 / 自定义 DTS / UVC fragment；内核 `multi_v7`、U-Boot `zynq_zed`） | 由 `wsl_build.sh` merge |
| `boot.bif` + `bootgen` | FSBL + U-Boot → `BOOT.BIN`（**不含 SPL**；实跑 1,056,384 B） | Windows，Vitis 2026.1 |
| `make_fsbl.py` | 用 Vitis 的 Python CLI 建 platform + FSBL 并编译，最后打印 `fsbl.elf` 路径（**在本沙箱里没跑通**，根因见 docs/41 §18.5；普通终端应可直接跑）。可选 `VIGILENS_VITIS_PORT=<端口>` 直连已启动的 Vitis Server，绕开 create_client 的代码页解码错（§20.7） | Windows，普通 PowerShell |
| `kernel_uvc.fragment` | 内核最小 USB/UVC 配置（`uvcvideo` 已实测编进 rootfs：`/lib/modules/6.6.158/.../uvcvideo.ko`） | Buildroot（Linux 主机） |
| `boot.cmd` | U-Boot 启动脚本源（生成 `boot.scr`）；**显式把 `fdt_addr_r` 挪到 `0x10000000`**，因为 U-Boot 默认 `kernel_addr_r=0x2000000` 与 `fdt_addr_r=0x1f00000` 只差 1 MiB（docs/41 §23.3） | SD p1 |
| `make_sd.sh` | 分区 + 拷 boot + dd rootfs，**直接写 `/dev/sdX`**（带二次确认；需主机能看到卡） | Linux 主机 |
| `make_sd_img.sh` | **不碰真盘**：在 WSL 里用 loop 设备拼出可烧的 `vigilens_sd.img`（64 MiB FAT32 + ext4 rest，**已实测 768 MiB / sha256 19f8749…**） | WSL（root） |

## 最短路径（2026-10-25 现状：离线全绿，只差上板）

```powershell
# 00) 每次开新窗口先修环境：PATH + 工作目录
. board\w7_buildroot\w7env.ps1

# 0) 离线自检（只读仓库；当前 13 PASS / 0 WARN / 0 SKIP）
powershell -NoProfile -ExecutionPolicy Bypass -File board\w7_buildroot\preflight.ps1

# 1) 一级引导 + BOOT.BIN —— 已完成，产物在 D:\w7\images\BOOT.BIN（1,056,384 B）
#    重做：powershell -NoProfile -ExecutionPolicy Bypass -File board\w7_buildroot\mini_fsbl\build_mini_fsbl.ps1 `
#              -Dt %TEMP%\dsh-EOCTrf\dt_ps8 -Out $env:TEMP\mini_fsbl -BootgenSwitch
#    说明：只用 ps7_init + 串口报活 + 跳 U-Boot，无 SD 分区解析/镜像校验（docs/41 §21）

# 2) 设备树 —— 已完成，产物 D:\w7\images\system.dtb（65,767 B）
powershell -NoProfile -ExecutionPolicy Bypass -File board\w7_buildroot\build_dtb.ps1 `
    -Dt %TEMP%\dsh-EOCTrf\dt_ps8 -Out D:\w7\images\system.dtb

# 3) 内核/U-Boot/rootfs —— 已在 WSL 里编完（exit 0），产物拷到了 D:\w7\images\
#    重编：wsl -d Ubuntu -e bash -lc 'cd /root/vigilens && bash wsl_build.sh'

# 4) 拼卡镜像（不碰真盘），然后用 Rufus(DD 模式)/balenaEtcher 烧到 TF 卡
wsl -d Ubuntu -e bash -lc 'bash /root/vigilens/make_sd_img.sh /mnt/d/w7/images /mnt/d/w7/vigilens_sd.img'
```

## 别踩的三个坑

1. **不要用 Buildroot 默认的 U-Boot SPL**：它带 ZedBoard/通用的 DDR 初始化，Mizar 是 1 GB DDR3，参数不同 ⇒ 不启动。
   真要免掉 FSBL，必须把 XSA 里的 `ps7_init_gpl.c/h` 覆盖进 U-Boot（docs/41 §18.3-2，**未验证**）。
2. **不要手写 MIO 分配**：`pcw.dtsi` 由 HDF 生成，手抄 MIO 是 W7 最常见的浪费半天。
3. **先 640×480@30**：MJPG 解码是唯一会卡住性能的地方；720p 只在实测 <33.3 ms/帧后才上。

## 止损

640×480@30 下若单帧解码 ≥33.3 ms，或 `uvcvideo` 报 URB 不足 —— **停手**，转 `docs/37` 备选（网络送帧 / DVP 直连），
调 Linux 不会变快。
