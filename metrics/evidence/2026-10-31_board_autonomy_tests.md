# 2026-10-31 板端自治核心测试（Mizar-Z7020 真板）

板卡已连接，本轮通过既有 JTAG 流程启动 Buildroot Linux，并在板上运行 2026-10-30/31 新增的 C 代码：
`vg_measure`、`vg_vital`、`vg_contract_write`，以及既有的 900 帧回放。

## 板端环境【已验证】

| 项 | 值 |
|---|---|
| 主机名 / 内核 | `mizar` / `6.6.158` |
| CPU / 内存 | 2 核 / 476868 kB |
| 启动方式 | `board/w7_buildroot/boot_after_powercycle.ps1` + `network_jtag_boot.tcl` |
| 摄像头 | 启动日志：`usb 1-1: Found UVC 1.00 device DECXIN (0c45:64ab)` |
| 以太网 | `macb e000b000.ethernet eth0: Link is Up - 100Mbps/Full` |
| 网络地址 | 本次启动**未自动配置**；串口执行 `ip addr add 169.254.73.29/16 dev eth0` 后 PC→板 ping 通、板端 wget 取文件成功 |

## 1. 自治核心自检 `vg_board_check`

静态交叉编译（Buildroot `arm-linux-gcc`，`-static`），sha256 在板上核对：
`7efaeb6d25b0fe14f4ed047a044394c2a3e8458849812f82edd7802e02dd9093`。

| 项 | 板端结果 | 主机结果 |
|---|---|---|
| `vg_vital` 合成 1000 帧（1.2 Hz 色度脉冲） | `present=1 hr=72.000000 conf=1.000000` | 同 |
| `vg_measure` + `vg_contract_write` 8 帧 | 8 行契约，全部合法 | 同 |
| 契约字节 | **5098 B**，与主机输出 `diff` 完全一致（逐字节） | 5098 B |

板端契约行经串口 `gzip+base64` 回传、PC 解码后：
`backend/contract.validate_frame` **8/8 通过**，中文 `advice`/`reason` 正确（`状态正常`），
状态均为 `normal`。

## 2. 900 帧 C 核心回放（既有基线复跑）

板端运行 `vg_measure_replay /tmp/synthetic_replay.txt`：

| 项 | 结果 |
|---|---|
| 输出大小 | **964663 B** |
| sha256 | **`f16d1d99516d1d28fecfdd4d03811ae2379dbe53ce34078cf5a31dadba26ae40`** |
| 对照 | 与 `docs/54` 记录的主机/板端相同散列一致；与 `.tmp/autonomy/synthetic_replay_host.jsonl` 相同 |

## 3. 关键点模型真板复测（MNN 压缩 Peppa，本次启动）

同一台板、本次启动、`vg_mnn_probe`（sha256 在板上核对通过，三个文件均 `OK`）：

| 项 | 本次结果 | 2026-10-30 历史 |
|---|---:|---:|
| 线程 / 次数 | 2 / 300 | 2 / 300 |
| mean_ms | **27.2714** | 27.5942 |
| p50_ms | 27.2700 | 27.5913 |
| p95_ms | 27.3686 | 27.6998 |
| 峰值 RSS | 7040 KiB | 7040 KiB |
| 输出 sha256 | **`cde263832e4b17ea1f477df2d32b7799d1b047309a8382205a4979881a4996f7`** | 同 |

输出与 10-30 两次板端记录**逐字节相同**；说明该模型在本板的计算结果可复现。
仍只计 `runSession`，不含相机、检测、裁剪、PL 与服务。

## 4. 摄像头与并发实测（本次启动）

**摄像头**：`/usr/bin/uvc_probe /dev/video0 1280 720 300` 成功取到 **300 帧**
（每帧约 69–70 KB MJPG，时间戳间隔约 16 ms，即协商 1/60），结尾
`CAPTURE_FRAMES_OK … requested_mode=unmet`。

同一程序**加第 5 个参数（analysis-fps 30 或 60）就失败**：
`CAPTURE_MODE_MISMATCH … CAPTURE_FAIL errno=22 (Invalid argument)`，一帧也拿不到。
设备只给出 `1/60`，请求 30 fps 时该路径直接拒绝 —— 这正是"原生 30 fps 未过"的一个具体现场。

**并发（缩小版）**：300 帧 1280×720 采集与 `vg_mnn_probe` 300 次双线程同时运行：

| 项 | 结果 |
|---|---|
| 采集 | 300/300 帧，`CAM_RC=2`（程序以非零退出但帧完整） |
| 推理 mean | **29.4048 ms** |
| 推理 p50 / p95 | 27.4731 / **33.9675 ms** |
| 对照（无采集） | 27.2714 / p95 27.3686 ms |

这**不是** 10-30 的 88.46 ms 场景：这里没有 JPEG 解码、没有 PL、没有网口发送，
只证明"UVC 采集 + 推理"这一层的代价约 +2.1 ms（p95 +6.6 ms）。
旧场景的退化仍需在解码/PL/发送齐全时复现。

## 5. 并行失败现场

`/tmp/autonomy_camera_concurrent.log` **已随本次 JTAG 启动丢失**（RAM rootfs）：
板上 `ls` 返回 `No such file or directory`。

【已验证】现场丢失；【待办】复现该实验时，脚本必须在相机退出后无条件输出日志与退出码，
并把日志写到串口或持久介质，不能再依赖 `/tmp`。

## 复现命令（MNN / 摄像头）

```powershell
# 1) 启动（已执行，日志 metrics/logs/autonomy_reboot_20261031.log）
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/boot_after_powercycle.ps1 `
  -ImageDir .tmp/net_loop -Tcl board/w7_buildroot/network_jtag_boot.tcl `
  -Bitstream board/bitstream/system_wrapper_720p60.bit -Port COM11 `
  -Out metrics/logs/autonomy_reboot_20261031.log
# 2) PC 起文件服务（169.254.73.28:8093），板上 wget 后运行
```

原始串口日志：`metrics/logs/board_check3_20261031.log`、`board_replay_20261031.log`、
`board_sha_20261031.log`、`board_net_20261031.log`。

## 边界

- 只证明这些 C 模块**能在 Mizar 上运行且与主机逐字节一致**；不代表完整相机→指标链路、30 fps 或准确率。
- 网络地址是串口手工配置的；本次启动脚本没有自动配网，不能写成"已自动联网"。
- 未跑相机+推理并发；失败现场已丢失，需重新设计取证脚本后复现。
