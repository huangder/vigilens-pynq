# 像素源升级方案：Camera Module 2 + MIPI CSI-2 → PL → DMA → DDR → OpenCV

> 项目：知倦 / VigiLens（AMD 2026 嵌入式芯片与系统设计竞赛 · 赛道 3.3，自主选题初级组）
> 文档性质：**方案与分工**（不是已完成的成果）。所有"已做/未做"逐条标注证据。
> 日期：2026-09-28　发起：C 线　状态：🚧 **契约提案，未会签**
> 相关：`docs/interface.md`（契约，**本方案不改它**）、`docs/10` §12（摄像头选型）、
> `docs/12` §6（硬件清单）、`docs/14`（PC 模拟测试）、`docs/18`（旧相机链路的教训）、
> `fpga/src/raw10_unpack.cpp`、`fpga/src/bayer_demosaic.cpp`、`fpga/sim/host_model_mipi.cpp`

---

## 0. 先回答那个问题：**"直接插排线就行吗？"**

**分两半，答案相反。**

| 问题 | 答案 |
|---|---|
| **物理连接**上，是不是只要把排线插到板子上？ | ✅ **是**。Mizar-Z7020 自带一个 15-pin 1 mm **top-contact FPC** MIPI CSI 口，厂商手册原文写明 *"The MIPI FPC connector pinout is compatible with Raspberry Pi cameras"*，而树莓派 Camera Module 2 的接口就是 **15 × 1 mm FPC**。插上、卡扣压下，硬件连接就完成了（⚠️ top-contact：金手指朝向插反 = 不工作）。 |
| **插上就能用 / 就能 `import cv2`** 吗？ | ❌ **不是**。差 5 样东西，其中 3 样是"没有就完全不出图"的硬缺口。 |

**插上排线之后仍然缺的 5 样**：

| # | 缺什么 | 现状 | 谁做 |
|---|---|---|---|
| 1 | **PL 里的 MIPI CSI-2 RX**（D-PHY 接收 + CSI-2 解包子系统） | ❌ **零行代码**。全仓库 grep `mipi` / `csi2` / `dphy` 在 `.v/.sv/.cpp/.xdc` 里**零命中** | C 线（用 Xilinx IP，不写 RTL，但要配 BD） |
| 2 | **RAW10 解包 IP** | ✅ **本次新增**（`fpga/src/raw10_unpack.cpp`，已过语法自检 + 主机模型逐位对拍） | C 线（已完成到"离线可验证"这一层） |
| 3 | **Bayer → RGB888 去马赛克 IP** | ✅ **本次新增**（`fpga/src/bayer_demosaic.cpp`，同上） | C 线（同上） |
| 4 | **Block Design + MIPI 差分对 XDC + bitstream** | ❌ `board/bitstream/` **为空**；`build_bd.tcl` **从未跑通**；Mizar 的 1 GB DDR3 PS7 参数**未落实**（脚本会主动 `exit 1`，这是刻意设计） | C 线 + 人类（要板子在手） |
| 5 | **PS 侧软件**：DMA 搬运、把帧交给 OpenCV 的内存映射、传感器 I2C(SCCB) 初始化 | ❌ 未开始 | C 线 |

**还有 3 个"可能让你插上也不出图"的前置**（都【未验证】，采购/上电前必须先确认）：

1. **Mizar 的 15-pin 口是否真的给出 3.3V / I2C(SCCB) / MCLK** —— 厂商只说"引脚兼容"，**本项目从未量过**。缺任何一个，树莓派相机根本不会启动。查 [Mizar-Z7_R11 原理图](https://github.com/MicroPhase/fpga-docs/blob/master/schematic/Mizar-Z7_R11.pdf) + 万用表量（`docs/12` §7 第 9 条）。
2. **板子必须是 7020 版**：7010 版**没有** MIPI CSI 口（厂商手册 Key Features 原文 "MIPI CSI: 1 MIPI CSI for camera applications. **(7020 Version Only)**"）。
3. **Zynq-7000 的 PS 没有 MIPI 硬核** —— 这个口是**接在 PL IO 上**的。所以相机**不会**变成 Linux 的 `/dev/video0`，`cv2.VideoCapture(0)` 那条路**不存在**。"用 OpenCV"必须理解为：**帧由 PL 处理完、经 DMA 写进 DDR，再由你的程序（`/dev/mem` mmap 或 VDMA 驱动）交给 OpenCV 当图像处理**。

---

## 1. 选定链路与"1080p + 60fps"的硬账

**选定标准（用户 2026-09-28 确认）**：Camera Module 2（IMX219，15-pin）/ 1080p RAW10 /
`MIPI → PL → DMA → DDR → OpenCV`。

### 1.1 先把带宽算清楚（【算术，非实测】）

Mizar 手册："tested to operate at up to **672 Mbps per channel**"（芯片规格上限 950 Mbps/lane）。
15-pin 树莓派相机接口是 **2 lane + 1 clock lane** ⇒ 可用线速 ≈ **2 × 672 = 1344 Mbps**。

| 分辨率 | 格式 | 每帧载荷 | 30 fps | 45 fps | **60 fps** |
|---|---|---|---|---|---|
| 640×480 | RAW10 | 3.072 Mbit | 92 Mbit/s（7%） | 138（10%） | **184（14%）** ✅ |
| 640×480 | RGB888 | 7.373 Mbit | 221（16%） | 332（25%） | **442（33%）** ✅ |
| 1280×720 | RAW10 | 9.216 Mbit | 277（21%） | 415（31%） | **553（41%）** ✅ |
| 1920×1080 | RAW10 | 20.736 Mbit | 622（46%） | **933（69%）** ✅ | 1244（**93%**）❌ |
| 1920×1080 | RGB888 | 49.766 Mbit | 1493（**111%**）❌ | — | — ❌ |

> 括号内为占 1344 Mbps 的比例。**未含** CSI-2 协议开销（包头/ECC/CRC/行消隐）。
> 工程上一般要求留出 ≥20% 裕量 —— **这条 20% 是【推测】，不是厂商给的数**。

### 1.2 由此得到三条结论（本方案的全部依据）

1. **1080p 与 60 fps 在 2-lane 上互斥**：1080p RAW10 @60 已占 93%，加上协议开销**装不下**。
   ⇒ 60 fps 只能落在 **≤ 720p RAW10**。
2. **1080p 的天花板是 45 fps**（69%）—— 而这正好是 **Camera Module 2 官方列出的模式 "1080p45"**（树莓派产品页原文）。这不是巧合，是同一个链路预算的两面。
3. **契约 §0 的 640×480 RGB888 在 60 fps 下只占 33%**，非常宽裕。所以"60 fps 主档"与"保住 640×480 契约、四个 IP 一行不改"**可以同时成立**。

### 1.3 因此"1080p"的定位（这一点必须说清，否则会误解成"整条流水线跑 1080p"）

| 层面 | 分辨率 | 理由 |
|---|---|---|
| **传感器采集模式** | **1920×1080 RAW10**（30/45 fps 档）；60 fps 档降到 1280×720 或 640×480 RAW10 | 用户选定标准；也是带宽允许的最大值 |
| **PL 流水线工作尺寸** | **保持 640×480 RGB888**（契约 §0 不动） | 四个 HLS IP 的工作尺寸是**写死**的（`roi_statistic` 640×480、`rgb2gray` 640×480→384×288、`motion_quality` 384×288），且黄金参考是冻结的。抬到 1080p = 四个 IP 全部重做 + 全部黄金参考重生成 + 片内 BRAM 预算重做（2 的幂台阶规则） |

> 也就是说：**"1080p 采集" 是给 SNR / 取景 / 裁剪余量用的，"640×480 测量" 是契约要的**。
> 想要"整条流水线 1080p"的人，请先读 §7 的备选方案 B —— 那是另一个量级的工作。

---

## 2. 目标链路

```
 Raspberry Pi Camera Module 2 (IMX219, 15-pin FPC)
        │  MIPI CSI-2, 2 lane, RAW10（每 5 字节装 4 像素）
        ▼
 ┌──────────────────────── Mizar-Z7020 · PL ────────────────────────┐
 │  [1] MIPI CSI-2 RX 子系统（Xilinx IP, AXI4-Stream 输出）           │
 │        │                                                          │
 │  [2] raw10_unpack      ← 本次新增：5 字节 -> 4 个 10 bit 像素       │
 │        │                                                          │
 │  [3] bayer_demosaic    ← 本次新增：RGGB -> RGB888（整数双线性）     │
 │        │                                                          │
 │  [4] （缩放，可选）     ← 若传感器不直接出 640×480 则需要           │
 │        ▼                                                          │
 │  既有四 IP（契约 §3，**一行不改**）：                                │
 │      roi_statistic → rgb2gray → motion_quality → fir_filter        │
 └───────────────────────────────┬──────────────────────────────────┘
                                 │ AXI DMA / VDMA
                                 ▼
                        PS：DDR3（1 GB）
                                 │  /dev/mem mmap 或 VDMA 驱动
                                 ▼
                        OpenCV（Python / C++，PS 侧 Linux）
```

---

## 3. 30 / 45 / 60 三档备选方案（**主开发 60 fps**）

三档**共用**同一条链路与同一套 IP，只有三处参数不同：**传感器模式、FIR 采样率、契约 §0 的帧率值**。

| 档位 | 主/备 | 传感器模式（IMX219） | PL 流水线 | MIPI 占用 | FIR `fs` | 系数状态 | 用途 |
|---|---|---|---|---|---|---|---|
| **60 fps** | 🥇 **主档（现在就做）** | 1280×720 RAW10 **或** 640×480 RAW10（1080p@60 装不下，见 §1.2） | 640×480 RGB888 | 41% / 14% | **60 Hz** | ❌ **未设计**（现有只有 45 Hz 与 30 Hz 两套） | 采集侧高帧率：眨眼/闭眼时长的时间分辨率最好；rPPG 带内样本更密 |
| **45 fps** | 🥈 备选（承接 v1.1） | **1920×1080 RAW10**（官方模式 1080p45） | 640×480 RGB888 | 69% | **45 Hz** | ✅ 已有（`fpga/report/c5_fir_filter_45hz_reserved.md`） | 1080p 与高帧率的折中；**与已会签的 v1.1 口径一致**，回退成本最低 |
| **30 fps** | 🥉 备选（承接 v1.2 草案） | 1920×1080 RAW10 | 640×480 RGB888 | 46% | **30 Hz** | ✅ 已有（`fpga/report/c5_fir_filter_30hz_revert.md`） | 最稳、最省；与 `docs/14` 阶段二的低帧率采集源兼容 |

**为什么 60 fps 档不能沿用 45 Hz 的系数**：`fir_filter` 是**按采样率设计**的（`design_fir_coeffs.py --fs`）。
把 fs 从 45 改到 60 而不换系数，滤波器的通带/阻带会整体错位（心率 1~3 Hz 带会漂）。

> ⚠️ **60 fps 档的第一件事就是重新设计系数**：
> `python fpga/sim/design_fir_coeffs.py --fs 60 ...` → 新 `fir_coeffs_q15.h` → 重生成
> `data_fir/` 黄金参考 → 重跑 csim。**这一步没做之前，"60 fps 方案"是不完整的。**
> 本方案**不擅自生成这套系数**（要按 `skill/fpga_hls_c_line.md` 的流程走并与 A 线对拍）。

### 3.1 切换成本

| 改动 | 60 fps（主） | 45 fps | 30 fps |
|---|---|---|---|
| 传感器模式 | 改 `SCCB` 寄存器组（IMX219 mode 表） | 同左 | 同左 |
| PL 四 IP | 不动 | 不动 | 不动 |
| `fir_coeffs_q15.h` | **需新设计 + 重跑 csim** | 已有 | 已有 |
| `data_fir/` 黄金参考 | **需重生成** | 已有 | 已有 |
| 契约 §0 帧率行 | 需会签 | 需会签（v1.1 已是 45） | 需会签（v1.2 草案） |
| `config.yaml` `fps_nominal` | 60 | 45 | 30 |

---

## 4. 三线开发方案

### 4.1 C 线（FPGA/HLS）—— 本轮主攻

**本轮已完成（离线可验证，见 §5 的真实输出）**

| 项 | 产物 | 验证到哪一层 |
|---|---|---|
| `raw10_unpack` IP | `fpga/src/raw10_unpack.cpp` | 语法自检 ✅ / 主机模型逐位对拍 ✅ / csim ❌ |
| `bayer_demosaic` IP | `fpga/src/bayer_demosaic.cpp` | 同上 |
| 主机端算术模型 | `fpga/sim/host_model_mipi.cpp` | **本机真跑，RESULT: PASS** ✅ |
| 测试向量 + 黄金参考生成器 | `fpga/sim/gen_mipi_vectors.py` | **本机真跑**，含 numpy 独立对拍 ✅ |
| 两层测试台 | `fpga/sim/tb_raw10_unpack.cpp`、`tb_bayer_demosaic.cpp` | 语法自检 ✅ / csim ❌ |
| `run_hls.tcl` 接线 | 两个新 IP + `sim/data_mipi` | 静态检查 ✅ |

**后续（按依赖顺序）**

1. **设计 60 Hz FIR 系数**（`--fs 60`）并重生成 `data_fir/` 黄金参考；
2. 在**完整权限终端**跑 csim/csynth：`set HLS_IP=raw10_unpack` → `vitis-run --mode hls --tcl run_hls.tcl`（本沙箱跑不了 HLS，见 `docs/11`）；
3. 补 `(可选) frame_scale` IP（若传感器不直接出 640×480）；
4. **MIPI 差分对 XDC**：15-pin FPC 的 2 lane + clock 要按原理图落成 `LVDS` 差分约束（**引脚号必须来自原理图，不得猜**）；
5. 落实 Mizar 的 **PS7 1 GB DDR3 参数**（`build_bd.tcl` 现在会主动 `exit 1`，这是防止套用 PYNQ-Z2 预设做出起不来的板子）；
6. BD：`CSI-2 RX → raw10_unpack → bayer_demosaic →（scale）→ 四 IP → VDMA → DDR`；
7. bitstream 归档到 `board/bitstream/`，再跑 cosim（用**小尺寸向量**）；
8. 上板：`Mizar 从未上电` —— 这是**第一件要做的硬件事**，且要写明是**哪块板**。

### 4.2 A 线（算法/后端）

1. **帧源后端**：`backend/capture` 增加一个"PL/DMA 帧源"，与现有 `--source synthetic` / `--source 0` 并列；契约帧字段**不改**。
2. **去马赛克口径对拍**（关键）：PL 出的是本项目**自研**的整数双线性结果，**不等于** `cv2.cvtColor(bayer, COLOR_BayerRG2RGB)`。A 线必须：
   - 用 `fpga/sim/gen_mipi_vectors.py` 的同一套口径做一次对拍，把差异量化并**记录**（就像 `docs/interface.md` §4.4 对灰度的处理）；
   - **不得**默认 OpenCV 的结果等于 PL 的结果 —— 这是新的"通道顺序/口径"级踩坑点。
3. **60 fps 的时间序列口径**：契约要求 `ts` / `frame_id` 单调递增，且**时间戳用 `frame_id / fps` 而非墙上时钟**（确定性纪律）。fps 换了，`ts` 的换算必须跟着换。
4. **重新标定**：`ear_close_threshold` / `mar_threshold` / `pose_*` / `quality_weights` 都是**占位值**；帧率变了，PERCLOS 滑窗与眨眼计数的等效时长也会变，60 fps 下必须重新标定并写依据。
5. **rPPG**：60 fps 对 rPPG 是**好消息**（带内样本更密），但 `fir_filter` 的 fs 必须与采集率一致，否则心率估计整体偏移。

### 4.3 B 线（前端/服务）

1. **契约帧率不直接进 JSON schema**（`frame_id`/`ts` 已在），所以 B 线**基本不用改**；
2. 但要在界面**标注当前帧率档位**（30/45/60），因为用户看到的是"趋势"，档位会影响趋势的平滑度；
3. **旁路画面**（`POST /api/frame` → `/video.mjpg`）的 `video_push_hz` 在 60 fps 下要重新定：现在的默认值是按低帧率源调的，60 fps 若不压会白吃 CPU。参数只在 `config.yaml`（`run_demo.py --video-hz` 可临时覆盖，**不必动三人共用文件**）；
4. 画面链路**不得**把图像塞进契约帧（`check_video_bypass.py` 的 T9/T10 会红）。

---

## 5. 本轮 C 线的真实验证输出（**可复现，命令与原文都在**）

> 前置：`. .\env.ps1`（切到仓库根 + 把 `.venv` 排到 PATH 最前）。

```powershell
# ① 生成向量与 Python/NumPy 黄金参考（5 帧 640x480：random / all_zero / all_max / col_stripes / impulse）
.\.venv\Scripts\python.exe fpga\sim\gen_mipi_vectors.py

# ② 语法自检（本机 MinGW 能做的极限）
$inc = "D:\Xilinx\2026.1\Vitis\include"
g++ -std=c++17 -fsyntax-only -I $inc fpga/src/*.cpp
g++ -std=c++17 -fsyntax-only -I $inc -I fpga/src fpga/sim/tb_raw10_unpack.cpp
g++ -std=c++17 -fsyntax-only -I $inc -I fpga/src fpga/sim/tb_bayer_demosaic.cpp

# ③ 主机端算术模型（**本机真跑，秒级**）
g++ -O2 -std=c++17 -I fpga/src fpga/sim/host_model_mipi.cpp -o host_model_mipi.exe
.\host_model_mipi.exe fpga/sim/data_mipi
```

**① 的真实输出（末行）**：`[numpy 对拍] PASS —— 打包/解包/去马赛克三项均与 numpy 独立实现一致（5 帧）`

**② 的真实输出**：`src` 下 6 个 IP 全部 `OK`；`tb_raw10_unpack` / `tb_bayer_demosaic` / `tb_rgb2gray` / `tb_motion_quality` 全部 `OK`。

**③ 的真实输出（摘录，末行 RESULT: PASS）**：

```
---- [1] embedded cases (hand-checkable) ----
  OK   to8 saturation (10 cases, bad=0)
  OK   RAW10 packing (0,1,2,1023) -> 0,1,2,1023  (bytes 00 00 00 FF E4)
  OK   flat 2x2 field v=   0 -> all channels ==   0
  OK   flat 2x2 field v= 511 -> all channels == 128
  OK   flat 2x2 field v=1023 -> all channels == 255
  OK   corner clamp (4x4, only (0,0)=1023) -> R=255 G=128 B=64 (expect 255,128,64)
  OK   G phase direction probe: Gr(3,2)=(38,0,13) exp (38,0,13) | Gb(2,3)=(25,0,25) exp (25,0,25)
---- [2] stream (rolling 3-row) vs naive (full-frame) ----
  OK   640x480 random field: 921600 bytes compared, mismatches=0
---- [3] cross-language golden reference (Python/NumPy) ----
  OK   frame 0..4: unpack_bad=0 naive_bad=0 stream_bad=0
  ==> all 5 frames bit-exact vs Python/NumPy golden
---- [4] committed golden CSV re-check (golden_mipi.csv) ----
  OK   CSV rows=60, mismatches=0

==== RESULT: PASS ====
```

### 5.1 自我纠错轨迹（**必须记下来**：AI 写的用例错了三次，是"跑一遍"抓出来的）

| # | 我最初写的期望 | 实际 | 谁错了 | 正确算法 |
|---|---|---|---|---|
| 1 | 角点 (0,0) 的 `B = 255` | `64` | **用例写错** | B 的四对角里**只有左上**钳位到自身(1023)，其余是 0 → `B=(1023+2)>>2=256` → `to8(256)=64` |
| 2 | Gr(1,0) 的 `B = to8(200)` | `13` | **用例写错** | 下邻是**第 1 行**（未赋值=0），不是第 2 行 → `B=(100+0+1)>>1=50` → `13` |
| 3 | Gb(0,1) 的 `B = to8(600)` | `88` | **用例写错** | 左/右邻在**同一行**，右邻是 `row1[1]=300` 而非 col2 的 800 → `B=(400+300+1)>>1=350` → `88` |

**结论**：三次都是**测试期望算错**，IP 口径本身没改；改用"方向探针"（R 位随列变、B 位随行变）后手算不再依赖易错的邻域拼接，并显式打印"若两相位写反会读到什么"。**这正是 `host_model_mipi.cpp` 存在的理由**。

### 5.2 未验证项（**不得当作结论引用**）

| 项 | 状态 | 怎么补 |
|---|---|---|
| `raw10_unpack` / `bayer_demosaic` 的 **csim / csynth** | ❌ 未跑（受限沙箱跑不了 HLS：需命名管道 → Win32 error 5） | 完整权限终端 + `call D:\Xilinx\2026.1\Vitis\settings64.bat` |
| II / Fmax / LUT / FF / BRAM 占用 | ❌ **一个数都没有** | 同上，csynth 后归档 `fpga/report/` |
| 与 **Xilinx MIPI CSI-2 RX** 的真实握手（beat 宽度、打包形式） | ❌ **本 IP 唯一的集成假设**：假定"1 beat = 5 字节组、装在 64 bit 低 40 位、`tkeep=0b11111`" | 上板用 ILA 抓一次；若 RX 直接按"1 beat = 1 像素"输出，则本 IP 退化为直通 |
| 相机在 Mizar 上**是否出图** | ❌ 相机未买、板子未上电 | §0 的 3 个前置先做 |
| 60 Hz FIR 系数 | ❌ 未设计 | `design_fir_coeffs.py --fs 60` + 与 A 线对拍 |
| MIPI CSI-2 RX IP 的**授权** | ⚠️ **说法冲突**（见 §8 风险 1） | 在 Vivado 2026.1 的 IP Catalog 里查 |

---

## 6. 契约变更提案（**本方案不修改 `docs/interface.md`**）

按 `AGENTS.md` §4，改契约要"先改契约文档 → 同步两套实现 → 跑跨语言检查 → 群公告 → 会签"。
**本次没有会签，故不落地。** 下面是**要改哪几行、改成什么**的完整提案，供会签时逐条执行：

| # | 位置 | 现在 | 拟改为 |
|---|---|---|---|
| 1 | §0 「图像尺寸 / 格式 / 帧率」 | 640 × 480，RGB888，30 fps（🚧 v1.2 草案） | **像素源**：CM2(IMX219) 经 MIPI CSI-2 出 RAW10；**PL 内 demosaic 为 RGB888**；**流水线尺寸保持 640 × 480**；**帧率档位 30 / 45 / 60 fps（主用 60）** |
| 2 | §0 新增一行 | — | **像素源链路**：`MIPI CSI-2 (2-lane) → raw10_unpack → bayer_demosaic →（可选 scale）→ 四 IP → DMA → DDR → PS` |
| 3 | §3 新增 3.8 | — | `raw10_unpack` 接口与寄存器表（初稿见下表） |
| 4 | §3 新增 3.9 | — | `bayer_demosaic` 接口与寄存器表（初稿见下表） |
| 5 | §3.5 `fir_filter` 采样率 | 45 → 30（v1.2 草案） | **提供 30 / 45 / 60 三套系数**，默认 60；群延迟、通带/阻带指标**逐档实测回填** |
| 6 | §4 新增 4.7 | — | 新测试向量格式：`sim/data_mipi/{raw10.bin, golden_bayer16.bin, golden_rgb.bin, golden_mipi.csv, meta.txt}` |
| 7 | §6 变更记录 | — | 新增 **v1.4 草案**：像素源升级 + 帧率档位化；**与 v1.2 冲突**（v1.2 若会签，其"30 Hz"将被本档位方案取代） |

**接口初稿（待会签冻结；偏移量在冻结前都可能变）**

`raw10_unpack`

| 端口 | 方向 | 类型 | 偏移 | 语义 |
|---|---|---|---|---|
| `raw_in` | in | AXI4-Stream 64 bit（`tkeep=0b11111`） | — | 每 beat = 1 个 5 字节组 = 4 像素 |
| `pix_out` | out | AXI4-Stream 16 bit | — | 每 beat = 1 像素（低 10 bit 有效） |
| `width` / `height` | in | u16 | 0x10 / 0x18 | 行像素数（**须 4 的整数倍**）/ 行数 |
| `words_per_line` | out | u16 | 0x20 | `width/4` |
| `pixel_count` | out | u32 | 0x28 | 本帧像素数 |
| `frame_id` | out | u32 | 0x30 | 从 1 开始，随 `ap_rst_n` 归零 |

`bayer_demosaic`

| 端口 | 方向 | 类型 | 偏移 | 语义 |
|---|---|---|---|---|
| `bayer_in` | in | AXI4-Stream 16 bit | — | 每 beat = 1 个 Bayer 像素（低 10 bit 有效） |
| `rgb_out` | out | AXI4-Stream 24 bit | — | RGB888，**byte0=R**（不是 BGR） |
| `width` / `height` | in | u16 | 0x10 / 0x18 | 行像素数 / 行数（**须 ≥2**） |
| `pixel_count` | out | u32 | 0x20 | 本帧像素数 |
| `frame_id` | out | u32 | 0x28 | 从 1 开始，随 `ap_rst_n` 归零 |

**新冻结口径（三条，容差 = 0）**

```
RAW10 打包：每 5 字节一组含 4 像素
            P0=(b0<<2)|((b4>>0)&3)  P1=(b1<<2)|((b4>>2)&3)
            P2=(b2<<2)|((b4>>4)&3)  P3=(b3<<2)|((b4>>6)&3)     行内独立、不跨行
Bayer 相位：RGGB —— (0,0)=R (1,0)=G (0,1)=G (1,1)=B
去马赛克：  整数双线性 + 四舍五入（(+2)>>2 / (+1)>>1），边界**坐标钳位**（replicate）；
            Gr 的 R 取同一行左右、B 取同一列上下；Gb 反之；
            10->8 bit：v8 = min(255, (v10+2)>>2)
```

---

## 7. 备选方案（以及为什么**不**选它们）

| 方案 | 内容 | 判定 |
|---|---|---|
| **A（本方案）** | CM2 + MIPI CSI-2 → PL 新增 2 个 IP → 契约 640×480 保持 | ✅ **推荐**：保住四个 IP 与全部黄金参考，PL 承担真实算法（demosaic 是确定性整数任务，正合 HLS） |
| **B（全链路 1080p）** | 契约 §0 抬到 1920×1080，四个 IP 全部重做 | ❌ **本期不做**：四个 IP + 全部黄金参考 + BRAM 预算（2 的幂台阶）全废重来；且 1080p 下 `motion_quality` 存"上一帧"按 2^21 地址空间 → BRAM 需求是器件的数倍，**放不下** |
| **C（DVP 并行模块，如 OV5640）** | 接 JP2 40-pin，模块内置 ISP 可直出 **RGB565**，**免 demosaic** | ➡️ **MIPI 走不通时的备选**：省掉一个 IP，代价是要飞 ~22 根线 + 新增 DVP 采集 IP + 40-pin 上易接触不良 |
| **D（USB / UVC 摄像头）** | 插板载 USB Host，Linux 直接 `/dev/video0`，`cv2.VideoCapture(0)` 开箱可用 | ❌ **直接违背比赛主题**：像素只到 PS，**PL 根本看不到画面**，正中"FPGA 不是转发器"的反面 |
| **E（继续用 OpenMV）** | 现有链路 | ❌ 已被实测否决：**片上内存**（640×480 RGB888 需 921600 B > 可用 308944 B，差 3.0 倍）与**带宽**两条独立证据 |

---

## 8. 风险与未闭合项

| # | 风险 | 依据 | 处置 |
|---|---|---|---|
| 1 | **MIPI CSI-2 RX IP 的授权说法冲突** | 厂商手册原文：*"The MIPI CSI-2 receiver IP core is available from Xilinx … **It requires a licence to use**, but it is possible to obtain an evaluation licence … at no cost."*（该页标注开发环境 Vivado **2018.3**）；而 `docs/10` §12.2 记【已验证】"自 **Vivado 2020.1** 起包含在免费标准授权内" | ⚠️ **两条冲突，按 `AGENTS.md` §3 如实上报，不自行拍板**。在 Vivado 2026.1 里核：IP Catalog 搜 `MIPI CSI-2 Receiver Subsystem`，或 Tcl `get_ipdefs -filter {NAME =~ "*mipi_csi2_rx*"}` |
| 2 | **Mizar 的 15-pin 口是否供 3.3V / I2C / MCLK** | 官方只说"引脚兼容"，**本站从未量过** | 查原理图 + 万用表（`docs/12` §7 #9）。缺一个 → 相机不启动 |
| 3 | **本 IP 的输入打包假设**（beat 宽度/打包形式） | Xilinx RX 的实际输出未实测 | 上板 ILA 抓一次；错了就改对齐层，不改算法 |
| 4 | **1 GB DDR3 的 PS7 参数未落实** | `build_bd.tcl` 未落实时**主动 `exit 1`**（刻意设计） | 必须取自 MicroPhase 参考设计，**不得猜**（猜错 → PS 起不来） |
| 5 | **FPC 排线是 top-contact**，插反不工作 | 厂商手册写明确 | 装机时按手册核金手指朝向 |
| 6 | **不要加长 MIPI 排线** | D-PHY 高速差分，加长/劣质排线会误码 | 用官方 200 mm 排线 |
| 7 | **10 bit → 8 bit 的降位是不可逆的信息损失** | 契约要 RGB888 | 已口径化（`to8`）。若将来要 10/12 bit 精度，属契约级改动 |
| 8 | **60 fps 档的 FIR 系数尚未设计** | §3 | 会签前必须补：`--fs 60` + 重生成黄金参考 + csim |
| 9 | **本方案的 demosaic 是"够用且可验证"，不是"画质最好"** | 整数双线性在彩色高频区会有伪彩/拉链 | 若画质成为瓶颈，可换边缘自适应算法 —— 但**那要换黄金参考**，属契约级改动 |

---

## 9. 结论标注（区分已验证 / 推测 / 不确定）

- 【已验证】Mizar-Z7 7020 版有 15-pin 1 mm top-contact FPC MIPI CSI 口、**引脚兼容树莓派相机**、实测 672 Mbps/channel —— **来源：MicroPhase《Mizar-Z7 Reference Manual》MIPI CSI 节（2026-09-28 实读原文）**
- 【已验证】Camera Module 2 = IMX219 8MP，官方模式 **1080p45 / 480p100**，接口 **15 × 1 mm FPC** —— **来源：树莓派 Camera Module 2 产品页**
- 【已验证】`raw10_unpack` / `bayer_demosaic` 的**算法**与 Python/NumPy 黄金参考**逐位相同**（5 帧 640×480，本机真跑，§5）
- 【已验证】本机 MinGW **可以**编译运行不含 `hls::stream` 的主机模型（`host_model_mipi.exe` 实测 exit 0）；**不能**运行 `hls::stream` 模型
- 【算术，非实测】§1.1 的全部带宽数字（基于厂商给的 672 Mbps/lane 与像素格式算得，未含协议开销）
- 【推测】"协议开销需留 ≥20% 裕量"—— 工程经验值，非厂商数据
- 【未验证】Mizar 的 MIPI 口供电/时钟；Xilinx RX 的实际 beat 形式；两个新 IP 的 csim/csynth/cosim/资源/时序；相机能否在 Mizar 上出图；MIPI CSI-2 RX IP 的授权
- 【不确定】IMX219 是否有可用的 **720p60** 档（官方产品页只列了 1080p45 与 480p100）→ 用 `rpicam` 实际枚举
- 【未做】60 Hz FIR 系数、PS 侧 DMA/mmap 软件、BD、bitstream、上板

---

*本文件由 C 线发起，供 A/B/C 三方评审。按 `AGENTS.md` 与 `docs/05_AI使用约束.md`：
本文档里所有"已验证"都附了可复现命令与真实输出；所有"未验证"请勿当结论引用。
人类需自行核对、理解、记录真实协作过程并决定是否采用；涉及冻结接口的改动走 `docs/interface.md` §4 流程。*
