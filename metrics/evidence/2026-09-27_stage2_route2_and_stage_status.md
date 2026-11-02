# 2026-09-27 阶段二「相机 → 网页」测试成果总结（真实运行输出 + 边界）

> **执行者**：AI（在用户机器上代跑）· **执行时间**：2026-09-27 12:3x ~ 19:0x
> **环境**：`D:\Desktop\AMD`，Python 3.12.13 @ `.venv`；相机 **OpenMV Cam H7 R2** 在 **COM10**（`OpenMV v5.0.0` / `MicroPython v1.28.0-49`）
> **判定基准**：`docs/15_功能测试契约.md` · **阶段二方案**：`docs/14` · **操作手册**：`docs/17` · **问题台账**：`docs/16`
> **口径**：本文所有数字都是**本次真实运行的输出**，一个字未改；**没跑的项明确写"未执行"**。
> 凡"用户口述但仓库里没有证据"的，一律标 **【口述·无证据】**，不写进结论。

---

## 0. 一页结论（先看这张表）

| 问题 | 结论 |
|---|---|
| 「相机 → 网页」这条**演示链路**通了吗？ | ✅ **通了**（2026-09-27 真机，一条命令，画面 + 指标 + 真 MediaPipe 同时成立） |
| 「视频链路打通」这句话能覆盖**契约像素源**吗？ | ❌ **不能**。演示链路是 **灰度 QQVGA 160×120 ≈10 fps**，与契约 §0 的 **640×480 RGB888@30fps** 是两件事 |
| 现在的问题是不是"只剩硬件制约"？ | ⚠️ **一半对**：OpenMV 受内存/带宽制约**确实**是硬件天花板（今天补了实证）；但**换树莓派摄像头不是"接上就行"** —— 它要新增 `MIPI CSI-2 RX + demosaic` 两个 PL 模块 + BD/bitstream + PS 软件，**这些一件都还没开始** |
| 阶段二整体完成了吗？ | ❌ **未完成**。见 §4 逐轨状态：A4 缺素材、B1~B4（UVC）未做、C 轨矩阵仍缺、**M3 板卡侧尚未开始** |
| 用户说的"用树莓派 Camera Module 2 连板卡、已在开发板跑完整项目测试"？ | ❓ **【口述·无证据】**。仓库里**没有**任何对应产物（无 bitstream、无 CSI-2/demosaic 源码、无 xdc、无上板日志）。**要记入成果必须先补证据**（见 §5 的四件套清单） |

**本次真正新增的确定性成果**只有三件（都有日志为证）：

1. **BUG-027 彻底解决**：相机侧不再调用 `img.compress()`（路线②：相机发原始灰度、JPEG 移到 PC 侧），
   5 分钟真机验收 **0 次 `Compression Failed!`**；
2. **顺手修掉一个 PC 侧瓶颈**：纯 PowerShell 逐位 CRC **93.5 ms/帧 → 0.91 ms/帧**（不修就比来帧间隔 77 ms 还慢，
   画面会成串更新、age 锯齿到 6 s）；
3. **"一条命令在网页看 OpenMV"打通**（含修掉 BUG-028 的启动顺序坑），并给出可复跑的验收脚本。

---

## 1. 今天跑了什么（逐条 + 真实输出）

### 1.1 相机探针（`docs/18` §1，补 `AGENTS.md` §7.7 的两个空缺）

```
UNAME (sysname='OpenMV4-H7', nodename='OpenMV4-H7', release='1.28.0',
       version='v1.28.0-49 on 2026-07-02', machine='OPENMV4 with STM32H743')
MEM 318992
ID 0x2481
LINK 1 12 2 3 True True
HWJPEG_ERR RuntimeError Sensor control failed.
GRAY_QVGA 320 240 76800 free 305888
GRAY_QQVGA 160 120 19200 free 305840
```

- `ID 0x2481` = **ON Semi MT9M114**（OpenMV 官方 changelog：*"MT9M114's ID was initially 0x81 and later
  corrected to **0x2481**"*）⇒ `docs/18` §0.1 的硬件结论**从"官方文档 + 实物"升级为真机寄存器实证**；
- `HWJPEG_ERR` ⇒ **BUG-017 根因结案**：MT9M114 是 raw Bayer，**物理上不输出 JPEG**，
  `set_pixformat(sensor.JPEG)` 必然失败，**不是固件配错、不是脚本 bug**；
- **GRAYSCALE 可用**且尺寸与预期逐字节吻合（QVGA 76800 B / QQVGA 19200 B）⇒ 路线② 的前提成立。

### 1.2 路线② 端到端（`docs/18` §3.1 / §3.5）

**验收脚本（已入库）**：`metrics/scripts/omv_accept_route2.ps1`
手工等价命令：`Send-OMVStreamer`（默认 `-Mode usb_gray`）→ `Send-OMVFramesToApi -TotalSeconds 300 -BurstSeconds 0.25`。

**5 分钟验收（修 CRC 之后，最终版）**

```
bursts=1048  frames=2988  jpeg=0  gray=2988  conv_fail=0  posted=2988  saved=0  crc_bad=0  elapsed=300.4s
jpeg bytes: min=2078 max=2186 mean=2119 B   frame_id: 2 -> 4032
gray bytes on the wire: min=19208 max=19208 mean=19208 B
SAMPLES=583 MAXAGE=0.29 FRESH=583 VISIBLE_PCT=100
```

| 判据（`docs/18` §3.1 路线①/② 行） | 实测 |
|---|---|
| 300 s 内 `Compression Failed!` **0 次** | ✅ 0 次 |
| 相机侧转录**无 Traceback** | ✅ 无 |
| `frames=` **持续增长** | ✅ 2988 帧 / 300.4 s = **9.95 fps 投递**；相机侧 `frame_id` 到 4032 ⇒ **≈13.4 fps** |
| 最大 age / 可见占比（原本是路线③ 的判据） | ✅ **0.29 s / 100%** |

**两条如实记录的取舍**（都来自真机实测）：
- 投递 9.95 fps < 相机 13.4 fps，差额 ≈1044 帧且 `crc_bad=0` —— 与"**每个攒批边界丢约 1 帧**"
  （1048 个 burst）吻合；对照 `-BurstSeconds 1.0`：**12.4 fps / MAXAGE 1.02 s / 可见 99.1%**（60 s 对照，非长稳）；
- 单帧线上恒为 **19208 B**（= 8 + 160×120），**没有**测 76800 B（QVGA）档位。

### 1.3 第一次验收暴露的 PC 侧瓶颈（已修，同轮复验）

第一次跑 5 分钟**根治判据全过**，但 age 是锯齿：`SAMPLES=586 MAXAGE=7.00 VISIBLE_PCT=52`。
按 age 时序定位（不是猜）：

```
41 帧 × 19208 B 载荷的解析：3833 ms → 93.5 ms/帧      单帧 CRC：112.6 ms
而路线②来帧间隔 = 1/13.4 fps ≈ 77 ms  ⇒ 解析器比来帧还慢
```

修法：给 CRC 加 **C# 表驱动快路径**（`Add-Type`，失败自动退回原 PowerShell 参考实现），
**成帧/重同步逻辑一字未动**。修后：

```
41 帧解析：37 ms → 0.91 ms/帧      单帧 CRC：0.18 ms
```

### 1.4 网页演示（一条命令，真机验证）

命令：`powershell -NoProfile -ExecutionPolicy Bypass -File metrics\scripts\omv_camera_demo.ps1 -WithMetrics`

| 通道 | 实测 |
|---|---|
| 页面 | `GET /` → **HTTP 200**（14003 B，含 `id="wsUrl"` 与 canvas）；WS 由 `app.js` 自动填成 `ws://127.0.0.1:8031/ws` |
| 画面（旁路） | `/video.mjpg` → **10 帧/秒**（10070 B/s）；`/api/video_status` → `has_video: true`、`age_s 0.055` |
| 指标（契约） | `/api/status` → `source=ingest`、**`published=111`**、`frame_id=894`、`status=unreliable` |
| WebSocket | 连上后收到合法契约帧，字段正好 9 个：`ts/frame_id/face/behavior/vital/quality/status/advice/reason` |
| 关键点来源 | `[face_landmark] 使用 MediaPipe FaceMesh（478 点）` ⇒ **不是 stub** |

**同一时刻画面的真实状态（必须记录）**：抓一帧量灰度统计 → **mean = 1、min = max = 1、std = 0**
⇒ **传感器当下看到的是一片黑**（镜头盖/光照）。所以网页显示黑画面 + `status: unreliable`，
理由是 `信号质量 0.30 低于门控阈值 0.60（最弱项：光照）` —— **这是质量门控在正确工作**，不是链路故障。

### 1.5 离线回归（改动后全量复跑，全绿）

```
python board/openmv/vigilens_link.py --selftest                 → RESULT: PASS (9/9)
python board/openmv/offline_stream_check.py                 → RESULT: PASS (17/17)     # 新增，假模块跑真 openmv_stream.py
powershell -File metrics\scripts\omv_stream_bridge.ps1 -SelfTest → RESULT: PASS
python board/openmv/offline_check.py                        → RESULT: PASS (28/28)
python metrics/scripts/check_all.py                         → ✅ 全部通过（PASS 24 / FAIL 0），工具自检 10/10
python -m pytest -q                                         → 88 passed
```

**反向实验（证明这些自检真的会红）**：灰度分支加回 `img.compress()` → `FAIL (16/17)`；
灰度调色板取反 → 桥接自检 `FAIL`（报左右像素反了）；参考 CRC 的 poly 改一位 → 桥接自检 `FAIL`。

### 1.6 顺带发现并修掉的两个"非硬件"缺陷

| 编号 | 症状 | 根因 | 状态 |
|---|---|---|---|
| [BUG-028](../../docs/16_测试问题台账.md#bug-028) | 网页**有画面但指标永远 0**（`published=0`） | 演示脚本把 A 线起在**相机出帧之前**；A 线是**拉流**模型（`--source mjpeg:`），拉不到就超时且**不重连** | ✅ 已修（先出帧再起 A 线；相机重启时一并重启 A 线），复跑 `published=111` |
| （同一处） | `"...at $ApiBase: ..."` 解析报错 | PowerShell 把 `$ApiBase:` 当"驱动器限定变量"，要写 `${ApiBase}` | ✅ 已修；教训记进卡片：改 `.ps1` 必须过 `[Parser]::ParseFile` |

---

## 2. 这次测试**证明了**什么（可以写进报告的部分）

1. **`OpenMV → 串口 → PC → 网页` 这条链路在真机上端到端成立**：画面（旁路 MJPEG）与指标（契约帧 + WS）
   同时在线，关键点来源是 **mediapipe**；
2. **BUG-027 的修法（路线②）在真机上成立**：相机侧一次都不调用编码器 ⇒ 那个"连续内存"失败模式**在原理上消失**，
   5 分钟 0 次、`crc_bad=0`；
3. **"先判断能不能测"这条产品承诺在真机上被观察到**：画面全黑时系统输出 `unreliable` + 原因，
   **没有报跳变数字**（`docs/15` O4）；
4. **OpenMV 不能作为契约像素源这一点，今天又多了一条硬件实证**（`0x2481` = MT9M114 = raw Bayer，
   内存 3.0 倍 / 带宽 29.5 倍的老结论不变）；
5. **离线回归可复现**：`check_all` PASS 24 / FAIL 0，四个 OpenMV 自检全过，且**自检本身会红**（反向实验）。

## 3. 这次测试**没有**证明什么（**不许引用为结论**）

| ❌ 不能说 | 为什么 |
|---|---|
| "契约 §0 的 640×480 RGB888@30fps 通了" | 全程用的是 QQVGA **灰度** 160×120，**没有任何一帧 RGB888** |
| "测量级数据可用" | 投递 ~10 fps ⇒ 最短可检出闭眼 ≈300 ms，正常眨眼会漏（`board/openmv/README.md` §3.4）；且画面全黑，本批 `status` 全是 `unreliable` |
| "A 线算法已验证" | 这批数据 `face.visible = 0`（无脸），EAR/PERCLOS 全是 0 —— 只证明了**链路**，没证明**算法**（`docs/14` §5 红线） |
| "树莓派摄像头 + MIPI 这条路通了" | 见 §5：仓库里**没有**任何对应产物，且它需要新增两个 PL 模块 |
| "板卡已经跑起完整项目" | `board/bitstream/` **空**、`build_bd.tcl` **从未跑通**、无上板日志/截图/串口转录 |

---

## 4. 阶段完成度（按 `docs/14` 自己的轨与判据逐条对）

| 轨 | 内容 | 判据 | 状态（2026-09-27） |
|---|---|---|---|
| **A0~A3 / A5** | 环境·合成源·网页·真实画面（零硬件）·M2 合体 | check_all 全绿 / 六态 / `has_video` / `mediapipe` | ✅ 已跑通（今天复跑全绿） |
| **A4** | **有意义的测算（真人脸）** | 四条判据全过 | ⏳ **缺素材**（仍无带脸的标准 mp4） |
| **B1~B4** | 刷 `uvc.bin` / UVC 真实能力 / 相机→网页 / 回滚 | `--list` 新序号 / `distinct_frames≈frames` / 四判据 / COM 口回来 | ❌ **未做**（**仍然未做**——今天走的是另一条路） |
| **B′（今天新增）** | **不刷固件的网页画面（`docs/18` 路线②）** | 画面 + 指标 + `mediapipe`；`frames=` 持续增长 | ✅ **已跑通**（2026-09-27 真机；见 §1.2/§1.4）<br>⚠️ 画面为灰度 QQVGA ~10 fps |
| **C0 / C 轨** | OpenMV 工具链自检 / **能力矩阵** | 9/9·10/10·18/18·28/28；**完整矩阵** | 🔶 **部分**：自检 ✅；矩阵 **仍缺 5 行**（JPEG 真实字节数、`GRAYSCALE/VGA` 等），今天只补了 `get_id`/固件串/`GRAYSCALE` 两档 |
| **M3 板卡侧** | PS bring-up → bitstream → DMA → 黄金回归 | 各门限 | ❌ **尚未开始**（bitstream 空 / BD 未跑通 / 无上板记录） |
| **M4 闭环** | 软硬件同屏 + 黄金回归 + 可复现脚本 | — | ❌ 未开始 |

> **所以对"这一阶段是否完成"的回答是：没有完成。**
> 今天完成的是**其中一条演示支路（B′）+ 一个 P1 缺陷的根治（BUG-027）**，不是阶段整体。
> `docs/14` §5 红线里那条 —— "把'演示通过'说成'相机测试完成'" —— 正好适用于当前这个判断。

---

## 5. 关于"树莓派 Camera Module 2 连板卡就能传彩色图像 / 已在开发板上完整测试"

**【口述·无证据】** 截至 2026-09-27，仓库里能核对的事实是：

| 该路线需要的东西 | 仓库现状（可复核） |
|---|---|
| `MIPI CSI-2 RX` 接收模块 | ❌ **不存在**（`fpga/src/` 只有 4 个 IP；全仓库 `.v/.sv/.cpp/.xdc/.tcl` grep `mipi/csi2/dphy` **零命中**） |
| **demosaic**（RAW10 Bayer → RGB888） | ❌ **不存在**，而 `docs/10` §12.2 明确写"Pi 摄像头出的是 RAW10，**必须新增这个 IP**" |
| MIPI 引脚约束（XDC） | ❌ 不存在（`board/openmv/mizar_z7_openmv_uart.xdc` 是 UART 那套，与 MIPI 无关） |
| Block Design / bitstream | ❌ `board/bitstream/` **只有 `.gitkeep`**；`build_bd.tcl` **从未跑通** |
| PS 侧软件（DMA / 搬运 / 契约帧） | ❌ 未跑过（`board/dma_test.py`、`hw_sw_compare.py` 都以上板为前提） |
| 板卡是否上过电 | ❌ 无任何记录；`AGENTS.md` §9 与 `docs/11` §"做不了" 均记 **Mizar-Z7020 尚未接过/从未上电** |
| 线（`MIPI CSI` 口是否给 3.3V / I2C(SCCB) / MCLK） | ❌ 【未验证】—— `docs/12` §4.3 第 9 条要求**万用表实测**，没做 |

**因此，即使你真的把 Camera Module 2 插上板子、在某个系统里看到了彩色图像，也不能推出**
"本项目链路通了" —— 那最多说明 **① 板子能跑起某个系统 + ② 摄像头能出图**，
而本项目要的是：`CSI-2 RX → demosaic → rgb2gray → motion_quality → DMA → PS → 契约帧 → 网页`。

**如果确实在板上跑过，请按 `docs/16` §5 的"四件套"给我，我就能把这笔账记成【已验证】：**

1. **命令原文**（含参数、你用的是 Vivado/Vitis 哪个版本、哪条 tcl / 哪个 python 脚本）；
2. **原始输出**（不许手改）：Vivado 综合/实现的 `util.rpt` / `timing.rpt` 摘要、Program Device 的结果、
   PS 侧串口 `dmesg`（看 `xilinx-csi2rxss` / `imx219` 是否 probe 成功）、以及**契约帧的原始 JSON**；
3. **你期望看到什么**（一句话）；
4. **实际看到什么**（一句话）。

**另外必须先说清是哪块板**（`AGENTS.md` §2.1 的板卡沿革要求）：契约上的生效板卡仍是 **PYNQ-Z2（v1.3 草案未会签）**，
实物是 **Mizar-Z7020**；写"上板结论"必须写明板名与器件串（`XC7Z020-1CLG400C` ≡ `xc7z020clg400-1`）。

---

## 6. 证据清单（本次新增/更新，可复核）

| 文件 | 内容 |
|---|---|
| `board/openmv/openmv_stream.py` | 相机侧灰度直发（`usb_gray`/`uart_gray`、`GRAY_FRAMESIZE`），`stream()` 改 payload 三选一 |
| `metrics/scripts/omv_stream_bridge.ps1` | PC 侧 `Convert-OMVGrayToJpeg` + `0x03` 分支 + `jpeg=/gray=/conv_fail=` 计数 + **C# 快 CRC** + 自检（GRAY 像素比对 / 锚点 / 快慢 CRC 一致） |
| `metrics/scripts/omv_accept_route2.ps1`（新） | 一条命令跑完 `docs/18` §3.1 的四件套（自起 api.py + 相机 + age 采样） |
| `metrics/scripts/omv_camera_demo.ps1` | 一键网页演示（`-WithMetrics`），含 BUG-028 的顺序修正 |
| `board/openmv/offline_stream_check.py`（新） | 假模块跑真推流脚本 17/17（钉死"灰度不调 compress"） |
| `docs/16` BUG-027 / BUG-017 / BUG-028 | 状态、证据四件套、反向实验、启动顺序坑 |
| `docs/18` §1/§2/§3.4/§3.5/§5/§7 | 探针结果、实现差异表（含快 CRC 第 10 行）、真机验收、诚实边界 |
| `docs/17` §5.5 | 不刷固件的网页画面路线（判据 5 条） |
| `AGENTS.md` §7.7 | 补固件串 / `get_id()` / `GRAYSCALE` 实测；路线② 真机验收记录 |

---

## 7. 诚实边界

- 本文所有数字来自 **2026-09-27 本机/真机真实运行**；**没跑的项一律写"未执行/未做"**，没有推算值冒充实测；
- **【口述·无证据】那一节不构成任何结论**，只是为了标出"要补什么证据才能记账"；
- 本次**没有**碰 `config.yaml`、`docs/interface.md`（契约仍是 **v1.1**，`CONTRACT_VERSION` 仍 `v1.1`），
  **没有**改任何 A/B 线算法文件；
- **A4（真人脸测算）与 M3（上板）仍未开始** —— 任何"完整项目已在板上跑通"的说法，
  在补上 §5 的四件套之前**都不成立**。
