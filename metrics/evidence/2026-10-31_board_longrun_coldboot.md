# 2026-10-31 板端长跑与冷启动可行性

## 1. 长跑：3000 帧相机 + PL + JPEG 发送

配置：`VIGI_PIPELINE=threaded`、2 解码 worker、`PL_WAIT=yield`、审计每 30 帧、
JPEG 透传、30 fps 分析（相机协商 1/60）。

| 项 | 结果 |
|---|---|
| 输入帧 | 3000（相机 59.39 fps） |
| 交付帧 | **1515**（30 fps 分析取一半） |
| 交付 fps（板端时间戳） | **29.9998** |
| 帧间隔 p95（PC 接收） | **36.93 ms** |
| 驱动丢帧（sequence gaps） | **0** |
| 空缓冲（empty slots） | **0** |
| 审计 | 51 次，**0 失败** |
| 发送 | 1515 帧，`BOARD_STREAM_PASS` |
| 内存（长跑前后） | 14.7 MB → 14.8 MB used |

时长约 **50.5 s**（1515 / 30）。这只能说明 50 秒级稳定；分钟级/小时级仍需更长时间。
日志：`metrics/logs/board_longrun.log`；PC 接收：`metrics/logs/board_longrun_20261031.json`。

## 2. 冷启动可行性：当前硬件配置下不可行

板上探测（`metrics/logs/board_boot_probe.log`）：

| 检查 | 结果 | 含义 |
|---|---|---|
| `/dev/mmcblk*` | 不存在 | 没有可用 SD 卡设备 |
| `/proc/mtd` | 空 | 没有 QSPI MTD 分区 |
| `devmem 0xF800025C` | `0x00000000` | Zynq BOOT_MODE = JTAG |
| `/proc/cmdline` | `root=/dev/ram0` | RAM rootfs，由 JTAG 灌入 |
| uptime | 8034 s | 本次会话已运行 2.2 h |

**结论**：这台板当前拨码/跳线是 JTAG 启动，没有 SD/QSPI 介质，所以“无 JTAG 冷启动”
不是软件没写，而是**硬件启动方式未配置**。要验收冷启动至少需要：

1. 把 BOOT_MODE 拨到 SD（或 QSPI）并准备 `BOOT.BIN`（FSBL + bitstream + U-Boot）；
2. 一张可写的 SD 卡（或 QSPI 烧写流程）与 rootfs 镜像；
3. 断电重启后不接 xsdb，验证网络/相机/服务自启。

在这些条件具备前，报告中只能写“JTAG 启动 + RAM rootfs”，不能写“已独立冷启动”。

## 3. 结论

- 长跑：30 fps 相机管线在 50 s 内稳定、零审计失败、零驱动丢帧。
- 冷启动：**硬件条件不具备**，需要改拨码并准备启动介质；这是当前唯一无法在软件侧闭环的验收项。
