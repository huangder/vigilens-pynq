# OpenMV 首次测试包 —— 判断 / 接线 / 测试方案

> **结论先说（TL;DR）**
>
> **能做第一次测试，但"第一次测试"必须是"OpenMV → 笔记本"这条链路，不是"OpenMV → FPGA → 笔记本"。**
>
> - ✅ **可以立即做**：OpenMV 采集链路 + A 线指标跑通（T0/T1，不需要板卡，今天就能做）
> - ✅ **可以立即做**：Z7020 PS 侧 bring-up 排查（T2，只需要板子能上电/接串口）
> - ⚠️ **做不了、也不该现在做**：OpenMV 把像素送进 PL（T4）。原因有两条硬约束，见 §3
> - ❌ **不存在的东西不要假装有**：`board/bitstream/` 是空的，`build_bd.tcl` 从未跑过 →
>   任何"PL 上板已验证"的结论目前**都不存在**
>
> 一句话：**OpenMV 能当 A 线的采集源，当不了 C 线的像素源。** 这不是配线问题，是带宽问题。

> 🧪 **2026-09-24 真机首测（部分完成）** —— 上面那句话现在有**两条**硬证据了：
> 实测 `gc.mem_free()` = **308944 B**，而契约帧 640×480 RGB888 = **921600 B = 3.0 倍**；
> 连 VGA 的 **RGB565**（614400 B）都直接抛 `Frame buffer overflow`。
> **带宽差 29 倍之外，又多了"片上内存差 3 倍"** —— 而内存是换接口也解决不了的。
> 实测能用的最强组合：`RGB565 / QVGA / fb=1` = **39.76 fps 均值、153600 B/帧**。
> 📄 完整数据、逐项算术、未闭合项见 **`fpga/report/t6_openmv_capture_matrix_v1.md`**。
> ⚠️ 那次只回帖了矩阵的 **2 行**（7 组里），**JPEG 实际字节数等还没拿到** ——
> 不要把"矩阵已跑完"当成既有事实（本文件 §5 与那份报告的 §5 列了缺什么）。

> 📌 **板卡变更已落实（🚧 v1.3 草案，2026-09-23，待 A/B 会签）**
> 目标板卡已由 PYNQ-Z2 改指 **Mizar-Z7020**，落在 `docs/interface.md` §0/§6、`config.yaml` 的 `fpga.board`、
> `AGENTS.md` §2.1/§7.1/§7.6、根 `README.md`、`docs/00`、`board/README.md`、`board/build_bd.tcl`（板级 preset 已参数化 + 加停止守卫）。
> **器件字符串不变**，所以本文的结论（OpenMV 不能当 PL 像素源、带宽差 29 倍）**全部不受影响**。
> ⚠️ 会签前 PYNQ-Z2 仍是契约上的生效板卡，但实物已是 Mizar —— **写"上板结论"时务必写明是哪块板**。

---

## 0. 我实际核对了什么（以及没核对成的）

| 项目 | 方式 | 结果 |
|---|---|---|
| GitHub 网页 | `web_fetch` / `read_page` | ❌ **打不开**：本机 DNS 把 `github.com` 解析到 `127.0.0.1`（fake-IP 代理的典型症状）。所以**我没有看过 GitHub 的网页渲染结果**。 |
| GitHub 远端真实状态 | `git ls-remote origin` | ✅ `refs/heads/main = ad9ad54` |
| 本地 main | `git rev-parse main` | ✅ `ad9ad54` → **与远端 main 同步** |
| 当前分支 | `git rev-parse --abbrev-ref HEAD` | ⚠️ **不在 main**，在 `c-line/fs30` @ `bc4c4fb` |
| 该提交是否已推远端 | `git branch -r --contains HEAD` | ⚠️ **输出为空 → 这个提交还没推到任何远端** |
| 仓库自检 | `python -m pytest -q` | ✅ `78 passed`（与 AGENTS.md 基线一致） |
| A 线链路 | `run_pipeline.py --source synthetic --pattern blink --seconds 5 --stub` | ✅ 处理 150 帧，末帧 `normal`，退出码 0 |
| 本机依赖 | 逐个 import | ✅ `cv2 4.11.0` / `numpy 1.26.4` / `mediapipe 0.10.21`；❌ **缺 `pyserial`** |
| Mizar-Z7 硬件 | MicroPhase 官方《Mizar-Z7 Reference Manual》 | ✅ 拿到**完整 40-pin 引脚表**、芯片型号、外设清单 |
| OpenMV Cam H7 规格 | openmv.io 产品页 | ✅ 拿到官方规格；⚠️ 官方页面**自相矛盾**（见 §2.2） |

**你现在最该知道的两件事**：

1. **分支不对**。`c-line/fs30` 上有 1 个未推送的提交（`contract: 采样率 45→30 Hz 回退（v1.2 草案，待 A/B 会签）`）。
   这个 v1.2 草案**还没有 A/B 会签**，按 `AGENTS.md` §4 它**不生效**，
   `docs/interface.md` 头部自己也这么写。别拿它当"契约已改"去写报告。
2. **`board/regmap.py` 里 `FPS = 45`，而 `config.yaml` 是 30** —— 真实存在的不一致，见 §6。

---

## 1. 一条好消息：板子的芯片和契约冻结的是同一颗

| 项 | 契约冻结（`config.yaml` `fpga.device` / `docs/interface.md` §0） | Mizar-Z7 7020 版（官方手册） | 判定 |
|---|---|---|---|
| 器件 | `xc7z020clg400-1` | **XC7Z020-1CLG400C** | ✅ **同一颗器件**（`C` 只是商用温度等级后缀） |
| PL 逻辑 | Zynq-7020 | 85K LC / 53200 LUT / 220 DSP / 4.9 Mb BRAM | ✅ 与 PYNQ-Z2 同级 |
| PL 时钟 | 目标 100 MHz（HLS `create_clock -period 10`） | PL 晶振 **50 MHz**（H16） | ✅ **不是阻塞项**：HLS IP 的 100 MHz 来自 **PS FCLK0**，而 `board/build_bd.tcl` 里已写明 `CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ = 100`（由 PS 的 33.333 MHz 时钟经 PLL 产生）。H16 的 50 MHz 只在做**独立 PL 设计**（如 T3 的字节回环，不启 PS）时才用 |
| PS 时钟 | — | 33.333 MHz（E7） | — |
| DDR3 | — | **1 GB**（7020 版） | ⚠️ 与 PYNQ-Z2 的 512 MB **不同** → PS 的 DDR 配置不能照抄 |
| 以太网 | — | 手册"Key Features"写 **10/100**，但 "Giga ETH" 节写 RTL8211E **10/100/1000** | ⚠️ **手册自相矛盾**，需实测确认 |
| MIPI CSI | — | **7020 版有**，15-pin 1 mm FPC，**兼容树莓派摄像头**，实测 672 Mbps/lane | 💡 这是 M3 正解，见 §3.3 |
| 扩展口 | — | JP1/JP2 两个 40-pin，共 72 IO，**默认 3.3V** | ✅ 正好接 OpenMV |
| 器件本体相同 | | | ✅ **HLS IP 源码与黄金参考全部可复用，只需换引脚约束** |

> 结论：换板子**不需要重做 HLS**，需要重做的是 **PS 配置（DDR / 板级 preset）+ 引脚约束 + bitstream**。
> 器件层面 `board/build_bd.tcl` 里的 `set PART "xc7z020clg400-1"` **与实物一致，不用改**；
> 真正要改的是下一节 §6 第 7 条那条硬编码的 PYNQ-Z2 板级 preset。

---

## 2. 硬件事实（带出处，便于你复核）

### 2.1 Mizar-Z7 JP2 扩展口（**接线只用这张表**）

> 📖 **引脚表的权威来源是 `docs/12_硬件清单与接线文档.md` §3**（那里有 JP1 + JP2 的**完整** 80 脚，
> 以及 LED/KEY/时钟/USB-UART/MIPI 全套）。本节只是**本项目当前用到的子集**，与那份文档同源
> （都出自 MicroPhase 官方《Mizar-Z7 Reference Manual》）。**改引脚请改 docs/12，再回来同步这里。**

来源：MicroPhase 官方《Mizar-Z7 Reference Manual》GPIO 节。

| JP2 脚 | 信号名 | FPGA 引脚 | 本文用它做 |
|---|---|---|---|
| 1 | GPIO2_0P | J18 | — |
| 2 | GPIO2_0N | H18 | — |
| **3** | **GPIO2_1P** | **G17** | **`clk_test`（PL 分频方波，测频反证时钟）** |
| 4 | GPIO2_1N | G18 | — |
| **5** | **GPIO2_2P** | **K14** | **`uart_rx` ← OpenMV P4 (TX)** |
| 6 | GPIO2_2N | J14 | — |
| **7** | **GPIO2_3P** | **H15** | **`uart_tx` → OpenMV P5 (RX)** |
| 8 | GPIO2_3N | G15 | — |
| 9 | GPIO2_4P | J20 | — |
| 10 | GPIO2_4N | H20 | — |
| 11 | **VCC_5V** | — | ⛔ **绝对不要接 OpenMV 信号脚** |
| **12** | **GND** | — | ✅ **共地（必接）** |
| 29 | VCC_3V3 | — | 可选用；建议**先不要**用板子给 OpenMV 供电 |
| 30 | GND | — | 备用共地 |

JP1（另 40 脚，GPIO1_xP/N，引脚号见官方手册）本文不用，留给后续 MIPI/并行口方案。

### 2.2 OpenMV Cam H7 的关键规格（官方产品页，**注意这里有矛盾**）

| 项 | 官方说法 |
|---|---|
| MCU | STM32H743VI，480 MHz，1 MB SRAM |
| **USB** | **full-speed 12 Mb/s**（不是高速） |
| **UART** | 最高 **7.5 Mb/s** |
| SPI | 最高 80 Mb/s |
| IO | **3.3V 输出、5V 容忍**；单脚 25 mA |
| 最大分辨率（描述段） | OV7725 "可拍 **640×480 8-bit 灰度** 或 **640×480 16-bit RGB565 @ 75 fps**" |
| 最大分辨率（Specs 表） | Grayscale ≤ 640×480；**RGB565 ≤ 320×240**；Grayscale JPEG ≤ 640×480；**RGB565 JPEG ≤ 640×480** |

> ✅ **2026-09-24 真机实测裁决（部分）**：同一页自相矛盾的两处，在**我们这台 + v5 的 `csi` 分支**上
> 实测结果是 —— **Specs 表那边对**：`RGB565 / VGA` 直接抛
> `RuntimeError: Frame buffer overflow, try reducing the frame size.`，
> 而 `RGB565 / QVGA`（153600 B）成功、**39.76 fps 均值**。
> 原因也一并实测到了：`gc.mem_free()` = **308944 B**，而 VGA RGB565 要 **614400 B**（1.99 倍）。
> ⚠️ 但**不要**据此说"OpenMV 官方文档错了"：描述段讲的可能是 OV7725 + 别的固件/模式下的情形，
> 我们**没有**条件复现那个上下文。**只能说"我们这台在 v5 分支上 VGA RGB565 不可用"。**
> 📄 数据：`fpga/report/t6_openmv_capture_matrix_v1.md`。
>
> 依据（仓库自己的记录）：`fpga/report/c5_fir_filter_30hz_revert.md` §9 已记录实测
> "送 PC（USB VCP，JPEG）：VGA **11.7 fps**（官方示例）→ 定时器双缓冲 **20 fps**；QVGA 约 25~32 fps"。
> 也就是说 **640×480 的彩色只能走 JPEG**（有损）。
>
> 📌 **本次首测还没拿到的那一行**：`GRAYSCALE / VGA` = **307200 B**，比可用内存 308944 B
> **只小 1744 B** —— 这是"擦边"里最有信息量的一组（成 → VGA 灰度可用；崩 → 说明擦边请求会搞死相机），
> 见 `fpga/report/t6_openmv_capture_matrix_v1.md` §5。

### 2.3 OpenMV H7 的 UART 引脚（固定）

| OpenMV 脚 | 功能 | 接到 Mizar |
|---|---|---|
| **P4** | UART3 **TX** | → JP2 pin 5 (K14) |
| **P5** | UART3 **RX** | ← JP2 pin 7 (H15) |
| GND | 地 | → JP2 pin 12 |

---

## 3. 核心判断：为什么 OpenMV 当不了 PL 的像素源

### 3.1 带宽算术（这是全文最关键的一段）

契约 §0 冻结口径：**640 × 480、RGB888、30 fps**。

```
单帧字节数 = 640 × 480 × 3            = 921,600 B
所需带宽   = 921,600 × 30             = 27,648,000 B/s = 27.648 MB/s = 221.2 Mbps
```

| OpenMV H7 可用链路 | 物理上限 | 换算 | 相对 27.648 MB/s |
|---|---|---|---|
| UART | 7.5 Mb/s | 0.9375 MB/s | **差 29.5 倍** |
| USB full-speed | 12 Mb/s | 1.5 MB/s（理论）/ ~1.0 MB/s（实用） | **差 18~27 倍** |
| SPI（master） | 80 Mb/s | 10 MB/s | **仍差 2.8 倍** |

**结论：OpenMV Cam H7 的每一条链路都装不下契约要求的无损 RGB888 视频流。**
唯一能塞进去的是 **JPEG 压缩**（VGA 约 11.7~20 fps），而 JPEG 是有损的 ——
它直接违反契约 §4.3 的 **"容差 0、逐点严格相等"**。

### 3.2 所以能力边界是这样切的

| 用途 | 需要什么 | OpenMV H7 能否胜任 |
|---|---|---|
| **A 线**：眨眼 / 闭眼时长 / PERCLOS / 哈欠 / 头姿 | 能看清脸的 RGB 图，几十 fps 更好 | ✅ **可以**（用 JPEG 或 QVGA），但这些指标**对帧率敏感**，见 §3.4 |
| **A 线**：rPPG 心率趋势 | 稳定的 ROI 均值时间序列 | ⚠️ **勉强**：需要 ≥30 fps 才够采样率，且 JPEG 有损会污染极微弱的交流分量 |
| **C 线**：`roi_statistic` 黄金参考逐点比对 | **无损** RGB888 640×480 | ❌ **不行**（JPEG 有损；无损又带宽不够 29 倍） |
| **C 线**：上板 DMA 回放 | 离线 `frames.bin` | ✅ 根本不需要相机 —— 板子从 DDR 里回放黄金向量即可 |

> 💡 **重要推论**：C 线上板的第一次测试**本来就不该接相机**。
> `board/dma_test.py` / `board/hw_sw_compare.py` 设计的输入是 `fpga/sim/data/frames.bin`。
> 先用黄金向量验 PL 算得对不对，再谈接真实相机 —— 这样"算法错"和"相机链路错"不会混在一起。

### 3.3 M3 要真实像素时，正确的路是 MIPI CSI，不是 OpenMV

Mizar-Z7 **7020 版自带 MIPI CSI 口，且官方写明"FPC 引脚兼容树莓派摄像头"**，实测 672 Mbps/lane。
这是唯一能满足 27.6 MB/s 且**无损**的路线（树莓派摄像头 + Xilinx MIPI CSI-2 RX IP）。
OpenMV 没有 MIPI 输出，走不到这条路上。

**建议的定位**（这条要 A/B 两线一起拍板，不是我能替你定的）：
- OpenMV = **A 线的开发/演示采集源**（行为指标、算法调参、真实视频回填）
- 树莓派摄像头 + MIPI CSI = **M3/M4 的正式像素源**
- 离线 `frames.bin` = **C 线上板的黄金输入**

### 3.4 ⚠️ 一个必须现在就知道的坑：低帧率会**系统性低估疲劳**

这条是我读 `config.yaml` 推出来的，不是猜的：

```yaml
min_close_frames: 3      # 连续 N 帧低于阈值才记为"闭眼"，抗单帧抖动
long_close_ms: 500       # 单次闭眼 ≥ 此毫秒数记"长闭眼"而非"眨眼"
```

`min_close_frames=3` 意味着**最短可检出的闭眼时长 = 3 / 帧率**：

| 实际帧率 | 最短可检出闭眼 | 对照：正常眨眼 100~400 ms |
|---|---|---|
| **10 fps**（OpenMV VGA JPEG 实测下限） | **300 ms** | ❌ 大量正常眨眼被整段漏掉 |
| **20 fps**（OpenMV VGA JPEG 上限） | **150 ms** | ⚠️ 短的仍会漏 |
| 30 fps（契约） | 100 ms | ✅ 基本覆盖 |

后果链：**漏掉眨眼 → 眨眼率偏低 → PERCLOS 偏低 → 疲劳漏报**。
而 `config.yaml` 里 `fatigue_blink_rate_low: 10.0` 又是"眨眼率**低于**此值判疲劳" ——
系统把"相机太慢"误读成"眨眼太少"完全可能。

> **这是本次调研最有价值的一条发现**，而且**不需要板卡就能验证**：
> T1 跑出真实帧率后，你就知道该不该动 `min_close_frames`。
> ⚠️ 但 `config.yaml` 是三人共用文件，**改它必须先说一声并写明依据**（`AGENTS.md` §5）。

---

## 4. 详细接线

### 4.0 三条通路，分清哪条是今天要接的

```
通路 A（今天做，不需要板卡）          通路 B（验证 PS 就绪，不需要 bitstream）
┌──────────┐  USB  ┌──────────┐      ┌──────────┐  USB  ┌──────────────┐
│ OpenMV   │──────►│ 笔记本   │      │ OpenMV   │──────►│ Mizar USB-Host│
│ Cam H7   │       │ (上位机) │      │ Cam H7   │       │  → /dev/ttyACM*│
└──────────┘       └──────────┘      └──────────┘       └──────────────┘

通路 C（第一次上板，需要先出 bitstream）
┌──────────┐ UART(P4/P5) ┌────────────────────┐
│ OpenMV   │────────────►│ Mizar JP2 (PL)     │
│ Cam H7   │◄────────────│ pl_uart_echo.v     │
└──────────┘             └────────────────────┘
```

### 4.1 通路 A：OpenMV ↔ 笔记本（**第一次测试就走这条**）

**方式 A1：UVC 摄像头模式（最省事，推荐先试）**

| 步骤 | 操作 |
|---|---|
| 1 | OpenMV 用 micro-USB 线接笔记本（**要数据线，不是充电线**） |
| 2 | 用 OpenMV IDE 刷入 **`uvc.bin`** 固件（OpenMV 官方提供；刷完 OpenMV 变成一个标准 UVC 摄像头） |
| 3 | 拔插一次，等系统识别 |
| 4 | 跑 `--list` 找到它的序号 |

> ⚠️ **`uvc.bin` 会替换掉固件**，刷了它就跑不了 `openmv_stream.py` 的 MicroPython 脚本；
> 要回去跑脚本得重新刷 `firmware.bin`。**两者不可兼得**，先想好这次要哪个。

**方式 A2：USB 虚拟串口（VCP）模式**

OpenMV 插上就是虚拟串口。用 `openmv_stream.py` 的 `MODE="usb_jpeg"` 往外送帧，
笔记本侧用 `--serial` 收。这条不需要刷固件，但**只有 OpenMV 自己的协议**，OpenCV 认不出来。

**方式 A3：UART + USB-TTL 转接板**（通路 C 的桌面演练，不接板子也能先练）

```
OpenMV P4 (TX) ──► USB-TTL 的 RX
OpenMV P5 (RX) ◄── USB-TTL 的 TX
OpenMV GND     ─── USB-TTL 的 GND        ← 必须共地
```
Windows 上会出现一个 COM 口，用 `--serial COMx` 收。

> ⚠️ **`pyserial` 目前没装**。先装：
> ```
> python -m pip install pyserial
> ```

### 4.2 通路 B：OpenMV ↔ Mizar-Z7020 的 USB Host 口

Mizar-Z7 有 **USB Host（USB3320C-EZK，ULPI，USB 2.0 高速）**。
OpenMV 作 USB CDC 设备插上去，板子的 Linux 里会出现 `/dev/ttyACM0`。
**这条路不需要 bitstream**，只要板子能起来 Linux —— 是验证"PS 侧就绪"最便宜的入口。

| 注意 | 说明 |
|---|---|
| 供电 | Mizar 的 USB Host 口能否给 OpenMV 足够电流**未确认**；OpenMV 活动时约 160~170 mA @3.3V。建议**先只插 USB、观察是否掉电重启**；不稳就给 OpenMV 单独供电 |
| 设备名 | 通常是 `/dev/ttyACM0`；用 `dmesg | tail` 确认 |
| 权限 | 可能要 `sudo usermod -aG dialout $USER` 或直接 `sudo` |

### 4.3 通路 C：OpenMV ↔ Mizar JP2（PL 引脚，**需要先出 bitstream**）

```
       OpenMV Cam H7                          Mizar-Z7  JP2
    ┌───────────────┐                     ┌──────────────────────┐
    │  P4 (UART3 TX)├────────────────────►│ pin 5  K14  uart_rx  │
    │  P5 (UART3 RX)◄────────────────────┤ pin 7  H15  uart_tx  │
    │  GND          ├────────────────────┤ pin 12 GND           │
    │               │                     │                      │
    │  (可选) 任一输入脚 ◄────────────────┤ pin 3  G17  clk_test │ ← 1 kHz 方波，测频反证 50MHz
    └───────────────┘                     └──────────────────────┘
         USB 单独供电                       5V DC 供电 + USB-JTAG
```

**上电前必须确认的 5 件事**：

1. ☑ **共地**（JP2 pin 12 ↔ OpenMV GND）。不共地是最常见的"完全收不到数据"原因。
2. ☑ **TX/RX 交叉**：OpenMV 的 TX 接板子的 `uart_rx`(K14)，**不要** TX 接 TX。
3. ☑ **电平**：两边都是 3.3V，**直连、不需要电平转换**。
   但**要在板上确认扩展口 IO 电压是 3.3V** —— 官方手册说 BANK34 的 VCCIO 由 R208/R209/R210 决定，
   出厂焊 R208 = 3.3V。**用万用表量一下 JP2 pin 29(VCC_3V3) 与 pin 11(VCC_5V) 的区别**，
   顺手确认 R208 在位、R209/R210 不在位。
4. ☑ **绝不要把 JP2 pin 11（VCC_5V）接到 OpenMV 的信号脚**（会打坏 IO）。
5. ☑ **先不要用板子的 3V3 给 OpenMV 供电**，让它用自己的 USB —— 少一个变量，故障好定位。

**要下载的 bitstream 需要先自己做**（目前不存在）：

```
board/openmv/pl_uart_echo.v              ← PL 侧最小回环设计（UART 字节回环 + 心跳 + 1kHz 测频）
board/openmv/mizar_z7_openmv_uart.xdc    ← 上面那张表的引脚约束
```
用 Vivado 建一个最小工程（加这两个文件 + 一个 top 就是全部），综合、实现、生成 `.bit`，
然后用 **Vivado Hardware Manager 通过板载 USB-JTAG 直接 Program Device**（易失，重上电即失效）。

> ⚠️ 这两个文件**都没有综合过、没有上板过**（我手上没板子）。
> 按 `AGENTS.md` 铁律 2，**"能跑"一律以你本地的综合与上板实测为准**。

### 4.4 连不上相机（"VSCode 里的 OpenMV 扩展没有响应"）—— 按这个顺序排查

> 📌 **先分清是哪一类**，否则会在错的方向上浪费时间：
>
> - **A. 扩展连不上相机**：点连接/运行没反应、或报串口超时 —— 多半是"**相机侧**"的问题
> - **B. VSCode 本身卡死/无响应**：OpenMV 扩展的 code intelligence **与 `ms-python`（含 Pylance）冲突**，
>   官方要求用之前先**禁用 `ms-python`**；禁用后 `Ctrl+Shift+P → Developer: Reload Window`
> - **C. 与连接无关的干扰项**：脚本里 `csi/sensor/omv/machine/pyb` 的"**无法解析导入**"告警
>   （Pylance 假警报，见 **`AGENTS.md` §6.4 环境坑表**）—— **它不影响连接，别拿它当线索**

**判据 0（最省事，先做这个）**：**用官方 OpenMV IDE 连一下。**

| 结果 | 说明 |
|---|---|
| IDE 能连 | 问题在 **VSCode 侧** → 查 `ms-python` 是否禁用、串口是否被别的窗口占、扩展是否需要重载 |
| IDE 也连不上 | 问题在 **相机侧** → 继续往下看 LED 与那 6 条 |

**相机侧：先看 LED**（OpenMV 板上那颗 RGB）：

| LED 现象 | 含义 | 处理 |
|---|---|---|
| 白 / 蓝常亮 | 固件在跑，只是 PC 侧没连上 | 查下面第 4、5 条 |
| **绿灯呼吸或闪烁** | **停在 bootloader 里**（**没在跑固件**） | 十有八九是刷过 `uvc.bin` 或固件刷坏了 → 见第 2 条 |
| 全灭 | 没供电 | 换**数据线**、换 USB 口（直插主机，不要 hub） |
| 周期性重新闪烁 / 反复重启 | 供电不足，或被脚本带崩 | 同上；也可先不接任何外设只插 USB |

**相机侧 6 个高频原因（按顺序查，越靠前越可能）**：

1. **相机被上一轮脚本搞挂了**（**这一次最怀疑的就是它**）。
   `matrix` 那次**只回来 2 行**，完全符合"某个大内存组合把相机卡死"：
   **真卡死时 USB CDC 会一起失联**，PC 侧症状正是"**连接没有响应**"。
   → **处理：拔掉 micro-USB 再插上**（OpenMV 由 USB 供电，拔插 = 整机上电复位）。
   恢复后重跑 `matrix`，**务必用 `MATRIX_STAGE` 分段**（先 `"small"`，再 `"mid"`，最后 `"big"`），
   脚本现在会在有风险的组合前打印 `⚠️ 内存余量：…` 预警 —— 见 §5 的 T1.0。
2. **刷成了 `uvc.bin`**。那固件把相机变成**标准 UVC 摄像头**，**没有 MicroPython REPL**，
   所以任何"连相机跑脚本"的工具**永远连不上**（这跟故障看起来一模一样）。
   判据：Windows「相机」应用里能看到它，或设备管理器里它是**摄像头**而不是 **COM 口**。
   → 处理：按住/进入 **bootloader 模式（绿灯呼吸）**，用 IDE 刷回 **`firmware.bin`**。
   ⚠️ **UVC 与跑脚本不可兼得**：所有 MicroPython 侧的测试做完再刷它。
3. **相机盘上留着 `main.py`**。OpenMV **上电会自动执行 `main.py`**，如果它是个死循环，
   REPL 就被占住 —— 表现也是"连不上"。⚠️ 这跟我们之前提醒的"板载盘极小、别在上面存东西"
   是同一片盘，之前踩过 `Not enough disk space`，**很可能真留下了文件**。
   → 处理：拔插后趁 `main.py` 起来之前连（IDE 有停止/中断按钮）；实在不行**格式化相机盘**
   （⚠️ **会丢掉相机盘上的所有文件**，先想清楚）。
4. **串口被别的程序占着**。**同一时刻只能有一个程序占用那个 COM 口**：官方 OpenMV IDE 还开着、
   另一个 VSCode 窗口、串口助手、`host_capture_test.py --serial COMx`……
   → 处理：全部关掉 → `设备管理器 → 端口(COM 和 LPT)` 里确认它还在、**记下 COM 号** → 再拔插一次。
5. **`ms-python` 扩展冲突**（同上面的 B 类）：禁用 `ms-python` → 重载窗口 → 再连。
6. **线/口问题**：micro-USB 必须是**数据线**（不是纯充电线）；换一根、直插主机、换一个 USB 口。

**要判定到底是哪一条，请回帖这 3 条证据**（比描述"连不上"有用得多）：

1. 相机现在的 **LED 现象**（常亮什么颜色 / 是否呼吸绿 / 是否反复重启）；
2. **设备管理器**里插拔 USB 时的变化：出现/消失的是 **COM 口**还是**摄像头**？有没有黄色感叹号？
3. **官方 OpenMV IDE 能不能连上**；若 VSCode 扩展有报错，把**报错原文**贴回来。

---

## 5. 详细测试方案

### 阶段总览

| 阶段 | 名称 | 需要什么 | 预计耗时 | 核心判据 |
|---|---|---|---|---|
| **T0** | 环境自检 | 只需笔记本 | 2 min | 三个 `--selftest` 全 PASS + `pytest 78 passed` |
| **T1** | **OpenMV 采集链路 + A 线指标**（← 你选的目标） | OpenMV + 笔记本 | 40 min | 拿到真实分辨率/帧率表；A 线产出契约合法 JSON |
| **T2** | Z7020 PS 侧排查 | 板子 + 5V 电源 + micro-USB | 30~90 min | 能进串口 console / 能 ping 通 |
| **T3** | PL 首次上板（字节回环） | + bitstream | 2~4 h | `echo` 模式多数轮次逐字节一致；`clk_test` 实测 1.000 kHz |
| **T4** | PL 像素链 | + Block Design + DMA | 数天 | **现在做不了**（无 bitstream） |

---

### T0 —— 环境自检（2 分钟，先做，别跳）

```powershell
# 在仓库根执行
python board/openmv/vigilens_link.py --selftest        # 期望 RESULT: PASS (9/9)
python board/openmv/host_capture_test.py --selftest    # 期望 RESULT: PASS (10/10)
python board/openmv/raw_to_contract.py --selftest      # 期望 RESULT: PASS (18/18)
python board/openmv/offline_check.py                   # 期望 RESULT: PASS (28/28) —— 见下
python -m pytest -q                                    # 期望 78 passed
python -m pip install pyserial                         # 串口模式需要
```

> `offline_check.py` 是**在 PC 上用假模块跑真脚本**（`openmv_capture_test.py` 只能在相机上跑）：
> 假的 `csi`（v5）/ 假的 `sensor`（v4）各 14 项，**两条分支都验**。
> 它已经真抓到过一个真机也会犯的 bug（`main()` 里 `_CAM_API` 漏 `global`，
> 被自己的 `except` 吞掉、打印成假的"相机 API 探测失败"）—— 见 §6 第 9 条。
> 它也已纳入 `metrics/scripts/check_all.py` 的工具自检，所以**每次跑总入口都会顺带验它**。

**判据**：9/9、10/10、18/18、28/28、78 passed。**任何一条红，先解决它再往下走。**

> 本节 9/9、10/10、18/18、28/28 与 78 passed 都是我**在本机真实跑出来的**（2026-09-23 / 09-24）。
> `raw_to_contract.py` 另外还跑过一次**端到端**：合成 dump → 转换 → 逐像素抽查通道顺序（见 §7 末尾）。
> 硬件相关的数字目前只有 `matrix` 的前 2 行（`fpga/report/t6_openmv_capture_matrix_v1.md`）。

---

### T1 —— OpenMV 采集链路 + A 线指标（**今天的重点**）

#### T1.0 OpenMV 图像采集测试（**必须先做这一步**）

> ⚠️ **第 0 步：确认固件版本 ≥ 4.5.6**（实测踩到：2026-09-24 相机上是 **4.5.3**，
> VSCode 的 OpenMV 扩展直接拒绝连接 —— `Firmware version 4.5.3 is too old, requires >= 4.5.6`）。
> **VSCode 的 OpenMV 扩展自带固件升级**（功能清单里的 `☁️ Upgrade firmware`）：
> 连接设备后它会自动检测最新版并提示升级；也可以 `Ctrl+Shift+P` 输入 `OpenMV` 找升级命令。
> 手动退路：从 <https://github.com/openmv/openmv/releases> 下载固件 zip，用 **`OPENMV4/firmware.bin`**
> （OpenMV Cam H7 就是 `OPENMV4`），在 **bootloader 模式（绿灯呼吸）** 下刷入。
> ⚠️ 刷固件前先把相机盘上你自己的文件拷出来备份。
> ⚠️ 该扩展的 code intelligence **与 `ms-python` 扩展冲突** —— 官方要求用之前先禁用 `ms-python`。

用专门的采集测试程序 **`board/openmv/openmv_capture_test.py`**（不是联调用的 `openmv_stream.py`）。

**准备**：`openmv_capture_test.py` **自包含**，**不需要** `vigilens_link.py`（只有 `openmv_stream.py` 才需要）。
用 OpenMV IDE 或 **VSCode 的 OpenMV 扩展**直接打开它、把 `MODE` 改成 `"matrix"`、点运行。

> ⚠️ **不要把脚本存到 OpenMV 的 U 盘上再编辑。** OpenMV 官方开发者原话：
> *"the onboard flash on the H7 is **extremely small** ... I do not recommend opening and editing
> the script directly on device."* 社区实测 **`main.py` 涨到 43 KB 就报 `Not enough disk space`**；
> 更糟的是 **FAT 挂载失败时相机会自动格式化整个盘**，你之前存的文件会全部丢失。
> **正确做法**：在 PC 上编辑 → 点 Run（脚本经 USB 推给相机、在 RAM 里执行），**不往相机盘写任何东西**。

> 📌 **`dump` 模式必须先插一张 micro SD 卡**，否则会拒绝执行并说明原因。
> 因为一帧 320×240 RGB565 = 153,600 B，而 H7 的板载盘只有 43 KB 级 —— 一帧都放不下。
> **只要 `matrix` 模式的数据的话，不需要 SD 卡**（它纯内存、不落盘）。

> ℹ️ **固件 v5.0.0 有两处破坏性变化，脚本已内置兼容层**（实测两条路都通）：
> ① **`sensor` 被 `csi` 取代**（changelog 标记为 *major*）——v5 用 `csi.CSI()` 类 API，
>    v4 用 `sensor` 模块级 API。脚本会**自动探测**并打印实际走的那条。
> ② **SD 卡挂载点从 `/sd` 变成 `/sdcard`** —— 脚本两个都试。
> 脚本开头会打印 **固件/板子标识 + 走哪套 API**，并写进 `report.json`，
> 这样数据可追溯到具体固件。
>
> 🔴 **但兼容层只加在了 `openmv_capture_test.py` 上，`openmv_stream.py` 还没有。**
> 2026-09-24 实测确认相机走的是 **`csi`（v5.x）** 分支，而 `openmv_stream.py` 用的是
> **v4 的 `sensor` 模块级 API**。官方说 `sensor` 这个 qstr 在 v5 上
> "is still wired up ... for backwards-compatible firmware builds" ——
> **【不确定】它在这台相机上到底还能不能 import 成功**。
> 上板前先跑一次 `python board/openmv/offline_check.py`（那套假模块装置**可以**扩展成
> 覆盖 `openmv_stream.py`），或直接在相机上试 `MODE="probe"`；**报错就把原文贴回来**。
> ⚠️ 这条**没有**被任何本机自检覆盖，是当前已知的空白。

**第 1 步：能力矩阵**（改顶部 `MODE = "matrix"`，在 OpenMV IDE 里运行）

它会逐个试「像素格式 × 分辨率 × 帧缓冲数」共 **10 组**，每组实测 2 秒，打印：

```
# 先打印「本轮按什么顺序跑」以及每一组的预计占用与风险，再逐行出结果
本轮 MATRIX_STAGE=auto → 10 组。**下面的顺序就是执行顺序**：
    1. JPEG      QVGA     fb=1 [small] 预计整帧 ~12800 B
    ...
   10. RGB565    VGA      fb=1 [big  ] 预计整帧 614400 B   ← 比可用内存大，**预期失败**

pixformat  size     fb    fps均值    fps中位   最慢帧fps        B/帧        理论B     mem可用  备注
RGB565     VGA       1        -        -        -          -     614400    307136  RuntimeError: Frame buffer overflow, ...
RGB565     QVGA      1    39.76    40.01    16.08     153600     153600    307136  csi API     ← 真机实测
```

> ⚠️ **顺序是刻意的，别随手重排**：2026-09-24 第一次真机运行**只回来了 2 行**（7 组里），
> 分不清是"没跑到"还是"某组把相机搞死了"（`RGB565/VGA` 已经证明内存会被请求爆掉，
> 紧随其后的 `GRAYSCALE/VGA` 离可用内存只差 1744 B，是最容易拖死相机的擦边请求）。
> 所以现在**小的、要 JPEG 字节数的在前面，已知会炸的大组合放最后** ——
> 就算炸，也只剩它自己没跑完。
>
> 🔧 **万一还是没跑完**：把 `MATRIX_STAGE` 改成 `"small"` / `"mid"` / `"big"` 分段跑，
> 并注意每组开跑前那行 `---- [k/N] … 开始（此刻 gc.mem_free() = … B）----`：
> **最后一次出现的组号，就是把相机搞死的那一组。**
> 脚本还会在有风险的组合前打印一行 `⚠️ 内存余量：…`（装不下 / 擦边），
> 提示"**这一组有可能把相机卡死，卡死就得拔插 micro-USB 复位**" ——
> 真卡死的 PC 侧症状就是"**扩展连接没有响应**"，排查见 **§4.4**。

**判据 / 这一步买到什么**：

| 列 | 它能定论什么 |
|---|---|
| `fps均值` / `fps中位` | **契约 §0 要 30 fps** —— 一眼看出差多少（已实测 `RGB565/QVGA` = 39.76 fps） |
| **`最慢帧fps`** | 最坏情况的采样间隔。运动/眨眼检测关心的是它，不是平均值。<br>⚠️ **它是帧率不是毫秒**（16.08 = 62.2 ms/帧），第一行失败时最容易看错 |
| `B/帧` vs `理论B` | 若两者差很多 → **实际生效的分辨率/格式与请求的不是一回事**（工具会打警告）。<br>`理论B` 前带 `~` 的是压缩格式（JPEG）的**名义值**，那种格式只看 `B/帧` |
| **`mem可用`** | `gc.mem_free()` 实测值。契约要 640×480 **RGB888** = **921600 B/帧**，实测可用只有 **308944 B** → **装不下，不需要推算** |
| `备注` | 双缓冲是否真的生效（老固件没有 `set_framebuffers()` 时工具会明说，不静默忽略） |
| 表末的**「契约可行性小结」** | 工具**自动**折算出来的：装得下契约帧的组合、≥30fps 的组合、最坏单帧、按 `min_close_frames` 折算的最短可检出闭眼。**只用实测数字，不引用任何预期值** |

**第 2 步：无损落盘**（改 `MODE = "dump"`，并把 `CHOSEN_*` 填成第 1 步里可用的最好组合）

采 10 帧原样写入 SD，并**逐帧回读校验**；同时记录 `pixel_samples`（见 §7.1）。产物：

```
/sd/vigilens_frames/frame_0000.raw ...   # img.bytearray() 原样字节
/sd/vigilens_frames/meta.json            # 布局/宽高/对拍样本/校验结果
/sd/vigilens_report.json                 # 本次测试的完整报告
```

**第 3 步：PC 侧转成契约布局**（把整个 dump 目录拷回 PC）

```powershell
# 先看这份 dump 是什么、有没有行填充
python board/openmv/raw_to_contract.py metrics\logs\openmv_dump --info
# 用板上的对拍样本判定 RGB565 扩展公式（**别跳过**）
python board/openmv/raw_to_contract.py metrics\logs\openmv_dump --calibrate
# 转成契约 §4.1 的 RGB888
python board/openmv/raw_to_contract.py metrics\logs\openmv_dump `
    --out metrics/logs/openmv_frames.bin --report metrics/logs/openmv_convert.json
```

**把这整张矩阵表和转换报告原样贴进 `report/llm_log/` 的记录里**（不许手改数字）。

> 📊 **已实测回填（2026-09-24，只回来 2 行）**：`RGB565/VGA` **确实失败**（overflow，与 Specs 表一致）；
> `RGB565/QVGA` **成功且 39.76 fps**（比预期好，超过契约的 30 fps）。
> **还没拿到的**：`GRAYSCALE/VGA`（擦边 1744 B）、四组 JPEG 的真实字节数
> （A 线旁路画面的带宽预算就等这个）、`omv.version` 与传感器 `get_id()`。
> 报错原文与全部算术见 `fpga/report/t6_openmv_capture_matrix_v1.md`。

> ⚠️ **JPEG 是死路，别在这上面花时间**：即使 VGA JPEG 能到 20 fps，它也是**有损**的，
> 转出的 RGB888 **不能**作为 C 线黄金参考（契约 §4.3 要求容差 0），
> `raw_to_contract.py` 会对 JPEG 帧明确拒绝转换。JPEG 只能喂 A 线的行为指标。

#### T1.1 让 A 线消费真实帧（UVC 路线，最直接）

```powershell
# 1) 找到 OpenMV 的摄像头序号（它会把序号直接打印出来）
python board/openmv/host_capture_test.py --list

# 2) 按契约口径测它的真实能力（这一步产出证据）
#    把下面的 0 换成上一步打印的序号 —— PowerShell 里 < 和 > 是保留运算符，
#    带尖括号的占位符**没法直接复制粘贴**。
python board/openmv/host_capture_test.py `
    --device 0 --seconds 10 `
    --probe 640x480,320x240 `
    --dump-rgb-bin metrics/logs/openmv_vga.bin --dump-frames 30

# 3) 让 A 线管线吃真实相机（同样把 0 换成实际序号）
python backend/run_pipeline.py `
    --source 0 --seconds 30 `
    --json metrics/logs/last.json `
    --jsonl metrics/logs/openmv_stream.jsonl `
    --csv metrics/csv/openmv_metrics.csv `
    --summary metrics/logs/openmv_summary.json `
    --print-every 30
```

**判据**：
1. `--list` 能看到 OpenMV（**看不到就说明没刷 `uvc.bin`**，不是故障）；
2. 第 2 步的 JSON 里有 `actual_width/height`、`fps`、`distinct_frames`、`first_frame_latency_ms`；
3. **`distinct_frames` 必须 ≈ `frames`** —— 如果 `distinct_frames == 1`，说明拿到的是**冻结画面**，
   这个"帧率"是假的（工具会打 ⚠️ 警告）；
4. 第 3 步产出契约合法 JSON，且 **`landmark_source` 字段必须是 `mediapipe`**。

> ⚠️⚠️ **第 4 条是最容易犯错的地方**：`AGENTS.md` 明确写 ——
> 若 `landmark_source` 是 `"stub"`，**这次运行的 EAR/MAR 是占位几何量，不得作为算法结果引用**。
> 真实相机 + mediapipe 装好的情况下它应该是 `mediapipe`；**如果是 `stub`，说明关键点没跑起来，
> 这批数据只能证明"链路通"，不能写进任何指标结论。**

#### T1.2 用真实视频回填契约的"待真实视频"两项

`docs/interface.md` §5.2 里有两项明确挂着 **"待真实视频"**：

| # | 事项 | 需要什么数据 |
|---|---|---|
| 7 | 3/5 点采样缩放的**混叠**是否让运动量偏噪 | 真实视频下 `motion_quality` 的运动量分布 |
| 12 | **rPPG 端到端**（ROI → Q1.15 → FIR → BPM） | 真实视频的 q 序列 |

用 T1.1 导出的 `metrics/logs/openmv_vga.bin` 就能开始做（它已是契约 §4.1 的 RGB888 布局，**通道顺序 R-G-B 已用自检钉过**）。

#### T1.3 低帧率影响评估（基于 T1.1 的实测帧率）

拿到真实帧率 F 后，回答这一个问题：

```
最短可检出闭眼 = min_close_frames / F = 3 / F
  F=10 → 300 ms   （严重：正常眨眼 100~400ms，会漏掉一大截）
  F=20 → 150 ms   （仍偏漏）
  F=30 → 100 ms   （合格）
```

**判据**：若 F < 20，则 §3.4 的偏差是真实的，**必须**在报告里写明，
并在"改用 `config.yaml` 的 `min_close_frames`"与"接受偏差"之间做一次**有依据的**选择
（改 `config.yaml` 要先公告，它是三人共用文件）。

---

### T2 —— Z7020 PS 侧排查（你说"不清楚状态"，按下面顺序逐条判定）

#### T2.0 上电前的物理检查（断电做）

| 检查 | 怎么看 | 不对会怎样 |
|---|---|---|
| 电源 | +5V DC 圆孔（不是 micro-USB 供电！） | 很多板子 USB 供电不足以让 PL 起来 |
| 启动模式跳线 | 手册说由 **J1 (MODE)** 决定 JTAG / QSPI / SD | 模式错 → 完全没反应 |
| SD 卡 | 有没有卡、卡里有没有厂商镜像 | 无卡 → 只能 JTAG 或 QSPI |
| 跳线状态 | 对照官方手册的 BOOTMODE 图 | 猜不得，必须看图 |

#### T2.1 三盏"活过来"的证据（按顺序，逐条判定）

**判据 1 —— 电源灯**：上电后板上有电源指示灯亮。
不亮 → 换电源/查开关，别往下做。

**判据 2 —— 串口 console**（最有用的一条）：
```
Mizar 的 USB-UART 是 CH340，接 PS 的 MIO14/MIO15（C8/C5）
```
用 micro-USB 接板子的 **USB-UART 口**（不是 JTAG 口），Windows 出现新 COM 口，用串口工具开 **115200 8N1**：

```powershell
# 没有串口工具就用 python（装 pyserial 后）
python -c "import serial,time; s=serial.Serial('COMx',115200,timeout=1); t=time.time(); [print(s.readline()) for _ in range(100) if time.time()-t<5]"
```

- 有 U-Boot / Linux 日志滚动 → ✅ **PS 活着，镜像在跑**，跳到 T2.2
- 完全无输出 → 可能是：没镜像 / 启动模式错 / 波特率不是 115200 / 这其实是 JTAG 口

**判据 3 —— JTAG 链**：
用 **Vivado Hardware Manager → Open Target → Auto Connect**。
- 能看到 `xc7z020` → ✅ JTAG 通，可以下载 bitstream（T3 的前提）
- 看不到 → 装 Digilent/Cable 驱动；确认接的是 **USB-JTAG** 口而不是 UART 口

#### T2.2 网络（若 console 能进 Linux）

```bash
ip addr                      # 看有没有 eth0 和 IP
ping -c 3 192.168.1.100      # 换成笔记本的 IP；两边必须在同一网段
```
⚠️ 手册对以太网速率自相矛盾（10/100 vs 10/100/1000），**以 `ethtool eth0` 的实测为准**。

#### T2.3 ⚠️ 关于 PYNQ 的重要提醒

`board/overlay/load_overlay.py` 是按 **PYNQ 的 `Overlay()` API** 写的。但是：

- MicroPhase 官方手册里**没有提到 PYNQ 镜像**；开发环境写的是 **Vivado 2018.3**；
- 卖家的商品页宣传"PYNQ, AI & Python-friendly"，但那**不是**官方支持承诺。

**所以：不要假设这块板子有 PYNQ 镜像。** 进 T3 前先确认：

```bash
python3 -c "import pynq; print(pynq.__version__)"    # 板上执行
```
- 有 → ✅ 可以复用 `load_overlay.py` 的 PYNQ 路线
- `ModuleNotFoundError` → ⚠️ **M3 要改路线**：用普通 Linux + `/dev/mem` 的 mmap
  （或 UIO/uio_pdrv_genirq）来读写 AXI-Lite + 跑 DMA。**这是一处对 `board/` 的实质性返工，
  必须在 M3 计划里体现出来**，别到上板当天才发现。

---

### T3 —— PL 首次上板：字节回环（需要先做出 bitstream）

**目的**：一次只验证一件事，且每件都能单独定位故障。

| 验什么 | 怎么看 | 失败说明什么 |
|---|---|---|
| bitstream 能下载 | Vivado 报 Program successful | JTAG/驱动/器件型号 |
| **PL 时钟真的是 50 MHz** | OpenMV 侧测 `clk_test` 频率，应 **1.000 kHz ±1%** | 晶振 / 约束 / 引脚错（该设计直接用 H16 的 50MHz，无 MMCM） |
| IO 电平与引脚对 | OpenMV 字节能进 PL（`led[1]` 闪） | XDC 引脚错 / VCCIO 不是 3.3V / 没共地 |
| 双向通路通 | `MODE="echo"` 多数轮次逐字节一致 | TX/RX 接反 / 波特率 / 线太长 |

**操作**：
1. Vivado 建最小工程（`pl_uart_echo.v` + `mizar_z7_openmv_uart.xdc`），综合→实现→生成 bitstream；
   **先把 `util.rpt` / `timing.rpt` 存档**（门限 1/2 要回填 `fpga/report/m3_system_budget_v1.md` 第 3 节）。
2. Hardware Manager 里 Program Device（易失）。
3. OpenMV 侧改 `MODE = "echo"` 运行。

**判据**：
- `clk_test` 实测 **1.000 kHz**（这是"时钟真的对"的硬证据，比"LED 亮了"强得多）
- `echo` 结果：**多数轮次逐字节一致**。
  ⚠️ **不是要求 100%** —— `pl_uart_echo.v` 刻意只做了 1 字节暂存、没有 FIFO，
  连续满速发送时**预期会丢字节**（文件头已写明）。这正好用来验证上位机协议里的丢帧统计
  是不是真的在工作：`--serial` 模式的输出里应能看到 `crc_errors` / `lost_frames` 有反应。

**这一步产出的证据要归档到** `fpga/report/`（C 线目录），并回填 `m3_system_budget_v1.md`。

---

### T4 —— PL 像素链（`roi_statistic` / `rgb2gray` / `motion_quality` / `fir_filter`）

**现在做不了**，缺的是：

| 缺什么 | 现状 |
|---|---|
| bitstream | `board/bitstream/` **是空的**（只有 `.gitkeep`） |
| Block Design 脚本 | `board/build_bd.tcl` 存在，但文件头自己写着 `[TODO-verify]`、**从未在 Vivado 里跑过** |
| PS 配置 | ✅ **已改**：`board/build_bd.tcl` 的板级 preset 已参数化（`USE_BOARD_PRESET`，**默认 0 = 不套用 PYNQ-Z2 preset**），并在未落实 PS7 配置时**主动 `exit 1`**。⚠️ 但 Mizar 的 **1 GB DDR3** 参数本身仍待按 MicroPhase 参考设计填入 —— **不允许猜** |
| PYNQ 路线是否可用 | **未知**（见 T2.3） |

**正确的顺序**（做完 T3 之后）：
```
build_bd.tcl（把 BOARD_PART 换成 Mizar-Z7 的板级文件，或手写 PS7；L29 的 PART 与 FCLK0=100MHz 都不用改）
    → 综合/实现/导出 bitstream  →  归档 m3_system_budget_v1.md
    → bringup_check.py （寄存器读写 + 复位语义，门限 3/4）
    → dma_test.py      （DMA 回环 + ≥300 帧长跑不死锁，门限 5/6/7）
    → hw_sw_compare.py （PL vs 黄金参考，容差 0，门限 8）
```
⚠️ **这三条都不要接相机**。输入用 `fpga/sim/data/frames.bin`。
先把"PL 算得对不对"钉死，再接相机 —— 否则"算法错"和"链路错"会混在一起无法定位。

---

## 6. 我发现的不一致 / 风险（如实列出，未擅自修改）

| # | 事项 | 证据 | 我的建议 | 我是否动了它 |
|---|---|---|---|---|
| 1 | **`board/regmap.py` 的 `FPS = 45`，而 `config.yaml` 的 `fps_nominal` 是 30** | `board/regmap.py:24` vs `config.yaml:12` | 在 `c-line/fs30` 分支上这是**滞后值**。但 v1.2 草案未会签，**不能先把代码改成 30**（那等于代码跑到契约前面）。建议**要么等会签、要么加注释说明它对应 v1.1**。属 C 线文件 | ❌ 没动 |
| 2 | 当前分支 `c-line/fs30` 有 1 个**未推送**提交，且 v1.2 草案**未会签** | `git log main..HEAD`；`docs/interface.md` 头部自述 | 推之前先确认 A/B 是否已回复"收到、不冲突" | ❌ 没动 |
| 3 | `board/README.md` 开头说"M3 之前这个目录应该是空的"，但目录里已有 5 个脚本 | `board/README.md:4` vs 实际文件 | 该 README 自己下面已经打了补丁说明，属**已自认**的滞后，可接受 | ❌ 没动 |
| 4 | **Mizar-Z7 手册自相矛盾**：以太网 Key Features 写 10/100，Giga ETH 节写 10/100/1000 | 官方手册两处 | 以 `ethtool` 实测为准 | — |
| 5 | **OpenMV 官方页面自相矛盾**：描述说 VGA RGB565@75fps，规格表说 RGB565 ≤ 320×240 | openmv.io 产品页 | ✅ **2026-09-24 实测裁决（部分）**：本机固件/传感器下 `RGB565/VGA` 抛 `Frame buffer overflow` → 与 Specs 表一致（**不下"官方文档错了"的结论**） | — |
| 6 | 契约 §0 冻结的板卡是 **PYNQ-Z2**，实物是 **Mizar-Z7** | `config.yaml` `fpga.device` vs 实物 | **芯片相同（XC7Z020-1CLG400C），但板卡不同** → DDR 配置、引脚约束、bitstream 全部要重做。这是**契约层面的变更**，按 `AGENTS.md` §4 该走 `contract:` 流程，且 `config.yaml` 的 `device:` 注释 `# PYNQ-Z2` 已不准确 | ❌ 没动 |
| 7 | **`board/build_bd.tcl` 曾硬编码 PYNQ-Z2 的板级 preset** | 原 `board/build_bd.tcl:30` = `set BOARD_PART "tul.com.tw:pynq-z2:part0:1.0"` | ✅ **2026-09-23 已改**：改为 `USE_BOARD_PRESET` 开关（默认 0）+ `catch` + 缺 preset 时 `exit 1`。同文件 L29 的 `set PART "xc7z020clg400-1"` **与实物一致，未动** ✅ | ✅ 已动 |
| 7b | MicroPhase 是否为 **Mizar-Z7** 提供 Vivado board files | 官方手册"Related Documents"只列了原理图/尺寸/dxf，**没有 board file**（同厂的 Z7-Lite 有，Mizar 未确认） | 若没有 → 得手工配 PS7（**1GB DDR**、UART、Ethernet、SD）+ 自己写 XDC。这是对 `build_bd.tcl` 的**实质性返工**，要提前排进 M3 | — |
| 8 | `board/overlay/load_overlay.py` 走 PYNQ `Overlay()`，但 Mizar 是否有 PYNQ 镜像**未确认** | 官方手册无 PYNQ 记载 | T2.3 的判定命令；若没有则 M3 要改成 `/dev/mem` mmap 路线 | ❌ 没动 |
| 9 | **旧版 `openmv_capture_test.py` 的 `main()` 漏写 `global _CAM_API`** → 相机探测**成功**时也会打印 `相机 API : **探测失败**`（`UnboundLocalError` 被同一段 `except Exception` 吞掉），并让 `report.json` 的 `camera_api` 变成 `null` | 2026-09-24 本机假模块自检复现：把 `HEAD` 版脚本喂给 `offline_check.py`，输出确认含该行假故障（旧版 4/14，新版 14/14） | ✅ **2026-09-24 已修**（加 `global` + 说明注释），并把 `offline_check.py` 纳入 `check_all.py` 回归，防止复发 | ✅ 已动 |
| 10 | 旧版把 `uname.release`（**MicroPython 版本**）标成"固件版本"，又把 `sys.version` 首段标成"MicroPython 版本" | 真机回帖那行：`OPENMV4 with STM32H743 1.28.0 \| MicroPython 3.4.0` —— **两个标签都不准** | ✅ **2026-09-24 已修**：标签写准，并新增 `omv.version` / `os.uname().version` / 传感器 `get_id()` 探测。**完整固件串仍待下次跑出来** | ✅ 已动 |

> ⚠️ **第 6 条最需要你注意**：把目标板从 PYNQ-Z2 换成 Mizar-Z7 是一次**契约级变更**。
> 我没有替你改 `config.yaml` 或 `docs/interface.md` —— 按规矩这要先公告、再改文档、再同步实现。

---

## 7. 本目录文件清单

| 文件 | 作用 | 能否离线验证 |
|---|---|---|
| `vigilens_link.py` | **串口帧协议唯一实现**（12B 头 + CRC-16/CCITT-FALSE）。**刻意不用类型注解**，为了能在 MicroPython 里跑同一份 | ✅ `--selftest` **9/9 PASS**（已跑） |
| `host_capture_test.py` | 上位机：列摄像头 / 测真实分辨率与帧率 / 判契约 §0 / 导契约布局 RGB888 / 收串口帧 | ✅ `--selftest` **10/10 PASS**（已跑） |
| `openmv_stream.py` | OpenMV 侧（联调/演示用）：链路握手 / 字节回环 / 统计量流 / JPEG 流 | ⚠️ **语法与 API 引用已检，未在真机运行**；🔴 **且它只用 v4 的 `sensor` 模块 API，没有 v5 兼容层** —— 相机实测走 `csi`，它能不能跑【不确定】（见 T1.0 的红字注） |
| **`openmv_capture_test.py`** | **OpenMV 上运行的图像采集测试**：能力矩阵（格式×分辨率×帧缓冲数，按风险排序 + 可分段）+ 长跑帧率统计（含抖动/最慢帧）+ 无损落盘 + **pixel_samples 对拍样本** + 表末**契约可行性小结** | 🧪 **真机跑过一次**（2026-09-24，只回来 2 行 → `fpga/report/t6_openmv_capture_matrix_v1.md`）；本机逻辑自检见下一行 |
| **`offline_check.py`** | **PC 上用假模块跑真脚本**（v5 `csi` / v4 `sensor` 两条分支，各 14 项）：验组合顺序、失败行的内存算术、契约小结、`_CAM_API` 作用域…… | ✅ `RESULT: PASS (28/28)`（已跑，且已纳入 `check_all.py` 回归） |
| **`raw_to_contract.py`** | PC 侧：原始 dump → **契约 §4.1 布局 RGB888**；`--calibrate` 用对拍样本**反推** RGB565 位扩展公式；含 stride/行填充推断 | ✅ `--selftest` **18/18 PASS** + 合成 dump **端到端实测通过**（均已跑） |
| `pl_uart_echo.v` | PL 侧最小回环（UART 回环 + 心跳 + 1 kHz 测频） | ⚠️ **未综合、未上板** |
| `mizar_z7_openmv_uart.xdc` | Mizar-Z7 引脚约束（含与 PYNQ-Z2 的关键差异说明） | ⚠️ **未在 Vivado 里验证** |
| `README.md` | 本文 | — |

### 7.1 为什么 RGB565→RGB888 的转换放在 PC，而且"不猜公式"

`openmv_capture_test.py` 的 `dump` 模式**只写原始字节**（`img.bytearray()` 原样）加一份 `meta.json`，
**不在板上做任何像素转换**。三个理由：

1. **口径只能有一处实现**。板上转一份、PC 上转一份，迟早不一致；
   放在 `raw_to_contract.py` 里，它**能被 `--selftest` 真跑到**（18 项），板上那份跑不了。
2. MicroPython 逐像素循环极慢，而板上写 SD 卡本来就慢，不该再叠 CPU 转换。
3. **RGB565 → RGB888 的位扩展有两种常见变体，选错会让每个像素系统性偏色**，而且肉眼很难发现：

   | 变体 | R（5bit→8bit） | 白点结果 |
   |---|---|---|
   | `replicate` | `(r5<<3) \| (r5>>2)` | **255** |
   | `shift` | `r5<<3` | 248（**到不了 255**） |

   到底 OpenMV 固件用哪种，**我不猜**。方法是让相机自己说：
   `openmv_capture_test.py` 落盘时同时记录若干像素的
   `raw16 = img.get_pixel(x,y)` 与 `(r,g,b) = img.get_pixel(x,y,rgbtuple=True)`，
   写进 `meta.json` 的 `pixel_samples`；PC 侧 `--calibrate` 用它们判断哪个公式能**逐像素精确复现**驱动输出。

   **对拍样本缺失或两种公式都对不上时，`raw_to_contract.py` 会拒绝自动转换**（要求显式 `--expand`）——
   这是刻意设计：宁可不转，也不要悄悄给出一份偏色的"黄金参考"。

> 📌 顺带：`dump` 模式会**逐帧回读校验**写入字节数。SD 卡写失败/截断很常见，
> 不校验就会得到"N 帧都写成功了"的假象，然后在 PC 侧才发现文件残缺。

**已验证 / 未验证的边界（按 `AGENTS.md` 的自检三条，必须分清）**：

- 【已验证】五个程序的离线自检（本机真实输出）：`vigilens_link` **9/9**、`host_capture_test` **10/10**、
  `raw_to_contract` **18/18**、`offline_check` **28/28**（v5/v4 两条分支各 14）；
  `openmv_capture_test.py` 本体是**在相机上跑**的，其逻辑由 `offline_check` 在 PC 上代为验证
- 【已验证】**真机硬件事实（2026-09-24）**：`gc.mem_free()` = **308944 B**；`RGB565/VGA` 抛
  `Frame buffer overflow`；`RGB565/QVGA` = **39.76 fps 均值 / 153600 B/帧**；该固件走 **`csi` API**。
  证据与逐项算术：`fpga/report/t6_openmv_capture_matrix_v1.md`
- 【已验证】`raw_to_contract.py` 的**端到端**：合成一份 320×240 RGB565 × 3 帧的 dump（含 `meta.json`
  与 `pixel_samples`）→ `--info` / `--calibrate` / 转换全部跑通；输出 691200 B = 320×240×3×3，
  逐像素抽查 `c=0→R` 正确（纯红落在 (255,0,0)、纯蓝落在 (0,0,255)）
- 【已验证】导出帧的通道顺序 `c=0→R`、总字节 = 帧数 × 921600（用生成的测试视频实测）
- 【已验证】`pytest 78 passed`、A 线 synthetic 链路退出码 0、`check_all.py` **PASS 22 / FAIL 0**
- 【未验证/未拿到】**JPEG 的真实压缩后字节数**（A 线旁路画面的带宽预算等它）、
  **`GRAYSCALE/VGA`（307200 B，离可用内存只差 1744 B）能否成功**、
  **完整固件版本串与传感器 `get_id()`** —— 重跑 `matrix` 即可
- 【不确定】第一次真机的矩阵**只回来 2 行**：是相机在某组卡死，还是回帖被截断 —— 见报告中 §4.2
- 【未验证】OpenMV 实际采用哪种 RGB565 位扩展 —— 由板上 `pixel_samples` 对拍确定（工具会拒绝猜）
- 【未验证】`pl_uart_echo.v` 的可综合性、时序、上板行为
- 【未验证】`mizar_z7_openmv_uart.xdc` 的引脚正确性（引脚号来自官方手册，但没在 Vivado 里跑）
- 【未验证】Mizar 是否有 PYNQ 镜像、以太网实际速率

---

## 8. 与项目规范的衔接（提交前请照做）

**建议的提交信息**（`AGENTS.md` §10.1 格式，本目录是新增且跨 A/C 两线 → 建议先群里说一声）：

```text
C: 新增 OpenMV 首次测试包（协议/上位机工具/PL 最小回环/接线与测试方案）

为什么改：
  首次联调 OpenMV 采集链路。原 board/ 只有 PYNQ-Z2 路线的上板脚本，
  实物改成了 Mizar-Z7，且 OpenMV 能不能当像素源一直没有实测依据。

改了什么：
  新增 board/openmv/：串口帧协议（唯一实现）、上位机采集测试工具、
  OpenMV 侧程序、PL 最小字节回环与 Mizar 引脚约束、接线与分阶段测试方案。

怎么验证的：
  python board/openmv/vigilens_link.py --selftest      → RESULT: PASS (9/9)
  python board/openmv/host_capture_test.py --selftest  → RESULT: PASS (10/10)
  python -m pytest -q                                  → 78 passed
  （硬件相关结论均为【未验证】，待上板；本提交不含任何实测性能数字）
```

**还必须做的两件事**：

1. **写 `report/llm_log/`** —— 按《05》第五节模板，**必须你亲手写**（AI 代写即违规）。
   把 T1.0 那张探测表、T1.1 的 JSON、以及 §3.4 那个"低帧率低估疲劳"的推理链都记进去。
2. **公告契约变更**（§6 第 6 条）：目标板从 PYNQ-Z2 → Mizar-Z7 属契约级变更，
   要走 `contract:` 流程先改 `docs/interface.md` + `config.yaml`，再同步实现。

---

## 9. 我明确做不到的事（避免"已完成幻觉"）

1. **我没看过 GitHub 网页** —— DNS 把 `github.com` 指到 `127.0.0.1`。上面关于远端的结论全部来自 `git ls-remote`。
2. **我没跑过任何硬件** —— OpenMV、Mizar-Z7 都不在这台机器上。本文件里的硬件数字
   **全部来自人类回帖的真机输出**（2026-09-24 那 2 行，见 `fpga/report/t6_openmv_capture_matrix_v1.md`），
   我做的只有**算术与判读**；JPEG 字节数、`GRAYSCALE/VGA`、固件串、传感器型号**都还没拿到**。
3. **我没跑过 Vivado / Vitis** —— `pl_uart_echo.v` 与 XDC 未综合、未实现、未上板。
4. **我改过哪些既有文件**（分两轮，都在下方列清）：
   - 板卡 v1.3 那一轮：`docs/interface.md`（v1.3 草案：§0 + 文件头 + §6 变更记录）、
     `config.yaml`（新增 `fpga.board`，`device` **未动**）、`AGENTS.md`（§2.1 / §5.1 / §7.1 / §7.6 / §9）、
     根 `README.md`、`docs/00`、`board/README.md`、`board/build_bd.tcl`、`board/bringup_check.py`、
     `board/dma_test.py`、`board/hw_sw_compare.py`、`board/overlay/load_overlay.py`。
   - **真机首测（2026-09-24）这一轮**：`board/openmv/openmv_capture_test.py`（修 `_CAM_API` bug +
     组合重排 + `MATRIX_STAGE` + 环境探测 + 契约小结）、**新增 `board/openmv/offline_check.py`**、
     `metrics/scripts/check_all.py`（工具自检 +1 → 基线 21→22）、
     `board/openmv/README.md`、`docs/10`、`docs/11`、`docs/12`、`AGENTS.md`，
     以及**新增证据文档 `fpga/report/t6_openmv_capture_matrix_v1.md`**。
   **但 `CONTRACT_VERSION` 仍是 `v1.1`**（两个草案未会签，按 `AGENTS.md` §4 不升级），
   且 **`backend/` 与 `frontend/` 一个字没动**（A/B 线领地）。
5. **历史记录一律没有改写**：`fpga/report/` 的既有测量报告、`report/llm_log/`、`metrics/evidence/`
   全部原样 —— 它们记录的是"当时那块板（PYNQ-Z2）当时的事实"，改写它们等于篡改历史。
   只有**活规格**（契约/config/AGENTS/README/board 脚本）才随板卡更新。
6. **我没有替决策** —— "OpenMV 定位为 A 线采集源 / MIPI CSI 作为 M3 正式像素源"是**建议**，
   需要 A/B/C 一起拍板；`min_close_frames` 要不要改也需要依据 + 公告。
   **板卡变更虽然已落实，但版本号没升、会签没做** —— 那两步必须由人类走完（`AGENTS.md` §4 第 4、5 步）。

---

*本目录由 C 线维护（`board/`）。接线表来自 MicroPhase 官方手册，协议口径对齐 `docs/interface.md`。*
