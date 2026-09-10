# C5 报告：`fir_filter` v1 —— C 仿真 + C 综合 + RTL 协同仿真（cosim）

> 项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
> 契约：`docs/interface.md` 第 3.5 / 4.5 节
> 记录人：C 线（经 AI 协助整理；**所有数字均为真实运行输出**，未估算、未代填；属推算的地方已显式标注）
> 日期：2026-09-11（本机时钟；HLS 会话跨过 00:00）

---

## 1. 结论

| 验收项 | `fir_filter` v1 |
|---|---|
| C 仿真结果 | ✅ **PASS**（`CSim done with 0 errors`） |
| 第 1 层（内嵌边界用例） | ✅ **8 / 8** |
| 第 2 层（跨语言黄金参考） | ✅ **16 / 16**（15 段 + 1 项跨段一致性），样本不一致 **0**，流头错误 **0** |
| 比对容差 | ✅ **0（逐样本严格相等）** —— 契约原定 ±1 LSB，实测**不需要**容差 |
| 流水线 II | ✅ **Final II = 1**（Iteration Latency 9，Depth 9） |
| 时序 | ✅ Estimated **6.80 ns** < 10 ns → **146.97 MHz**（目标 100 MHz） |
| 综合 | ✅ 无 ERROR，`All loop constraints were satisfied` |
| 资源 | LUT **4077**（7%）/ FF **6172**（5%）/ **BRAM 0** / DSP **25**（11%） |
| RTL 协同仿真 | ✅ **PASS**，**无死锁**（Layer 1 8/8 + Layer 2 16/16 在 RTL 侧同样通过） |
| 滤波器规格 | 63 阶、线性相位 I 型、int16 Q15、Hamming 窗带通；设计边缘（-6 dB）**0.70 / 3.50 Hz** @30 fps |

> **C5 任务达成。** 至此 C 线的四个自研 IP（`roi_statistic` / `rgb2gray` / `motion_quality` / `fir_filter`）
> 全部完成「C 仿真 + C 综合 + cosim」三件套，且四个 IP 的黄金参考比对**容差全为 0**。
> M3 上板前 C 线**不再有未实现的离线任务**（只剩 `board/` 的上板工作与两项待观察项，见第 8 节）。

---

## 2. 本次新增/改动

| 文件 | 说明 |
|---|---|
| `fpga/src/fir_filter.cpp` | **新增 IP**：时间序列带通 FIR（对称折叠、II=1、含复位与饱和计数） |
| `fpga/src/fir_coeffs_q15.h` | **新增（自动生成）**：63 个冻结 Q15 系数，是系数的**唯一来源** |
| `fpga/sim/design_fir_coeffs.py` | **新增**：系数设计器（纯标准库；含响应评估、-3dB 搜索、d 扫描、呼吸带可行性对照） |
| `fpga/sim/gen_fir_vectors.py` | **新增**：测试向量 + Python 黄金参考生成器（**解析 C 头文件拿系数**） |
| `fpga/sim/tb_fir_filter.cpp` | **新增**：两层测试台 + 跨段一致性检查 |
| `fpga/sim/host_model_fir.cpp` | **新增**：主机端算术模型（秒级自检，本机 g++ 可直接运行，**不是** HLS 证据） |
| `fpga/run_hls.tcl` | 支持 `HLS_IP=fir_filter`（数据目录 `sim/data_fir`） |
| `.gitignore` | 新增 `fpga/sim/data_fir/series.bin` 与 `fpga/sim/data_fir_small/` |
| `docs/interface.md` | 3.5 节定稿（阶数/系数/寄存器/语义）、4.5 节新增向量格式、§5 第 8 项核销 |

**为什么需要这个 IP**：`docs/03` 的 IP-3 与 `docs/00` 都写明"PL 端承担 FIR 带通滤波"。
在此之前的 `vital.*` 只能靠 PS 侧 `scipy.lfilter`，PL 少一条"确定性时序处理"的能力；
补上它之后，从**像素流（roi_statistic / rgb2gray / motion_quality）**到**时间序列流（fir_filter）**，
PL 侧的处理链才完整，M4 的"软硬件延迟/CPU 对比"才有第二个可比对象。

---

## 3. 滤波器设计（系数从哪来、为什么是这个口径）

### 3.1 方法与冻结结果

| 项 | 冻结值 | 依据 |
|---|---|---|
| 采样率 | **30 Hz** | 契约第 0 节（图像 30 fps，时间序列与之同频） |
| 阶数 | **N = 63**（I 型，奇数，群延迟 31 样本 = 1033 ms） | 契约 `N ≤ 64`；奇数阶群延迟为整数，最简单也最好测 |
| 设计法 | **Hamming 窗理想带通**（`h = (hi·sinc(hi·t) − lo·sinc(lo·t)) · w[n]`） | 纯标准库可实现；Blackman 过渡带 5.5/N 对本通带太宽 |
| 通带口径 | **设计边缘 = −6 dB 点 = 0.70 / 3.50 Hz** | 窗函数法固有口径；实测 −6 dB 0.704 / 3.496 Hz |
| 归一化 | 通带峰值增益归一到 1.0（Q15 = 32768） | 输出与输入同量纲，便于 PS 侧直接画图 |
| 量化 | int16 Q15，四舍五入远离零 | 契约 3.5 节 |
| 运算 | int32 精确累加 → **一次**算术右移 15 → 饱和到 int16 | 契约 3.5 节 |

**实测幅频响应（对量化后的整型系数计算）**：

| 频率 (Hz) | 0.1 | 0.3 | 0.5 | 0.7 | 1.0 | 1.5 | 2.0 | 2.5 | 3.0 | 3.5 | 4.0 | 5.0 | 8.0 | 12.0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 增益 (dB) | −28.4 | −18.3 | −11.0 | −6.1 | −1.9 | −0.1 | −0.0 | −0.0 | −0.6 | −6.1 | −22.9 | −68.0 | −80.2 | −94.5 |

- 实测 **−3 dB 点：0.895 / 3.305 Hz**；−6 dB 点：0.704 / 3.496 Hz
- 1.0~3.0 Hz 平台起伏：**1.90 dB**
- `Σh = 897`（DC 增益 −31.25 dB，即带通确实把直流压掉）
- `Σ|h| = 55073`，峰值系数 6101

### 3.2 一个必须交代的设计权衡（−6 dB vs −3 dB 口径）

带通是"低频抑制 → 上升 → 平台 → 下降 → 高频抑制"。窗函数法下 **−6 dB 点落在设计边缘**，
而 **−3 dB 点会往里缩**。两种口径给出的滤波器完全不同，`design_fir_coeffs.py --scan` 实测：

| 设计边缘外扩 d | −3dB 低/高 (Hz) | 0.35 Hz 抑制 | 0.5 Hz 抑制 | 4.5 Hz 抑制 | 1~3 Hz 起伏 |
|---|---|---|---|---|---|
| **0.00（本设计选定）** | 0.895 / 3.305 | **−16.2 dB** | **−11.0 dB** | −49.8 dB | 1.90 dB |
| 0.10 | 0.796 / 3.405 | −12.5 dB | −8.2 dB | −53.9 dB | 1.17 dB |
| 0.20 | 0.698 / 3.504 | −9.5 dB | −6.1 dB | −51.1 dB | 0.70 dB |

**选定 d = 0**，理由：rPPG 最大的干扰源是**呼吸与基线漂移（0.1~0.5 Hz）**，
而 0.7~0.9 Hz 对应 42~54 bpm、在"长时间学习/值守"场景里并非关注区间。
所以宁可牺牲 0.7 Hz 附近的通带完整度，也要把呼吸带压下去（0.35 Hz 从 −9.5 dB 提升到 −16.2 dB）。
**这是一个有依据的取舍，不是一个随手定的数**；若 A 线实测认为 0.7~0.9 Hz 必须保住，
改 `--d 0.20` 重新生成头文件即可（一行命令，黄金参考会同步重建）。

### 3.3 已知限制：63 阶做不了呼吸带（契约级）

契约 3.5 节原写"呼吸 0.1~0.5 Hz（待定用哪个实例）"。实测结论是**不能**用同一阶数：

- Hamming 过渡带宽 `Δf ≈ 3.3/(2πN)·fs` = **0.250 Hz**（N=63、fs=30），而呼吸带总宽只有 0.4 Hz；
- 直接按 0.1/0.5 Hz 设计时，**−3 dB 边沿在 ±0.3 Hz 范围内根本找不到**（通带已被过渡带吃掉）：
  实测 0.1 Hz 处 0.0 dB、0.5 Hz 处 −3.6 dB，但 1.0 Hz 处仍有 −18.9 dB —— 这不是带通，是低通；
- 要把过渡带压到 0.15 Hz 需 **N ≳ 105**，压到 0.10 Hz 需 **N ≳ 158**，都远超契约的 `N ≤ 64`。

**给 A/B 线的建议**：呼吸带由 **PS 侧先降采样**（例如降到 2 Hz 采样，同阶数过渡带降到 0.017 Hz），
再调用同名 IP 的第二组系数；或对呼吸单独增加阶数。**不要**指望 30 fps 下的 63 阶同时覆盖两个带。

---

## 4. 接口（已与实综合报告逐行核对）

寄存器映射（`docs/interface.md` 3.5 节）与 `csynth.rpt` 的 `* S_AXILITE Registers` 表**逐行核对一致**：

| 偏移 | 名称 | 方向 | 说明 |
|---|---|---|---|
| 0x00 | `CTRL` | W | ap_start / ap_done / ap_idle / ap_ready（HLS 自动） |
| 0x04 / 0x08 / 0x0c | `GIER` / `IP_IER` / `IP_ISR` | RW | 中断相关（HLS 自动，本 IP 未用中断） |
| 0x10 | `n_samples` | W | 本段样本数（1..65535） |
| 0x18 | `reset` | W | 1 = 读取样本前清空延迟线与饱和计数 |
| 0x20 | `out_count` | R | 本段输出样本数（== `n_samples`） |
| **0x24** | `out_count_ctrl` | R | ap_vld（坑 #16：每个输出后面多一个 valid 寄存器） |
| 0x28 | `saturation_count` | R | 本段饱和样本数（PS 可当质量判据：饱和多 = 输入过载） |
| **0x2c** | `saturation_count_ctrl` | R | ap_vld |
| 0x30 | `seg_id` | R | **自 IP 上电以来的调用序号**，每次调用 +1，从 1 开始 |
| **0x34** | `seg_id_ctrl` | R | ap_vld |

**AXI-Stream 语义**（本 IP 是时间序列，与图像 IP 不同，契约 3.5 节已单列）：

- `TDATA = 16`（有符号 Q1.15 样本，位级重解释）；`TKEEP/TSTRB = 0b11`；`TID/TDEST` 透传；
- `TUSER = 1` 标记**一次调用的第一个样本**；`TLAST = 1` 标记**一次调用的最后一个样本**；
- 输入输出**一一对应、同序、等长**，**输出包含启动瞬态**（不丢前 N−1 个样本）——
  这样"输入第 i 个 <-> 输出第 i 个"最直观，黄金参考可以逐样本严格比对；
- 延迟线是 static：**段间保持**（连续流语义）；`reset=1` 的段之间互不影响。

> ⚠️ **`reset` 不是可选项**：延迟线是 static，HLS 把它实现为**上电初始化**
> （综合日志里每个 bit 一条 `Register '...delay_line...' is power-on initialization`），
> **复位不会把它清零**（skill 坑 #17，`motion_quality` 的 `fid` 同款）。
> 所以"上电后是 0"这个假设在 RTL 上**没有被保证**：测试台与 PS 都必须靠 `reset` 建立确定性起点。

---

## 5. 验证（两层 + 主机端模型，容差 0）

### 5.1 第 1 层：内嵌边界用例（8 项，全过）

| # | 用例 | 判据 | 结果 |
|---|---|---|---|
| 1 | 单位脉冲（δ=32767） | 峰值下标 = 群延迟 **31**、偶对称 `out[k]==out[62−k]`、且与朴素参考逐样本相同 | ✅ |
| 2 | 全零输入 | 输出全零、饱和 0（复位后状态干净） | ✅ |
| 3/4 | 全幅直流 +32767 / −32768 | 数值与朴素参考相同，且稳态峰值 ≤ 1000（DC 被抑制） | ✅ |
| 5 | 奈奎斯特（交替满幅） | 与朴素参考相同（高阻带强抑制） | ✅ |
| 6 | `n_samples = 1` | 单样本输出 = `(h[0]·x)>>15`，`out_count=1`，TUSER/TLAST 同时为 1 | ✅ |
| 7 | **复位语义** | 7a 同段连跑两次结果相同；**7b 分段(reset=1,0) == 一次调用**；7c reset=0 的结果**不同于** reset=1（证明状态真的被继承） | ✅ |
| 8 | 饱和路径 | 满幅 1 Hz 方波 → `saturation_count` 与朴素参考一致且 > 0，输出被夹在 ±32767 | ✅ |

### 5.2 第 2 层：跨语言黄金参考（15 段 + 1 项跨段一致性，全过）

`gen_fir_vectors.py` 生成 15 段共 3940 个样本（`data_fir/`），Python 侧用**精确整数**实现同一算式；
C 侧逐样本比对，**不一致 0 个、流头错误 0 个**。各段实测增益（整数实现、跳过前 62 个瞬态样本）：

| 段 | 类型 | 实测增益 | 饱和样本 | 说明 |
|---|---|---|---|---|
| `impulse` | δ | — | 0 | 结构自检（见 5.1） |
| `dc_pos` / `dc_neg` | 满幅直流 | **−31.3 dB** | 0 | 与 `Σh` 的理论 DC 增益吻合 |
| `nyquist_alt` | 交替满幅 | **−87.3 dB** | 0 | 高阻带 |
| `sine_1p5hz` | 1.5 Hz, A=20000 | **−0.1 dB** | 0 | 通带中心，增益 ≈ 1 |
| `sine_0p8hz` | 0.8 Hz | −4.5 dB | 0 | 通带下沿（−3 dB 点 0.895 Hz 之外） |
| `sine_3p2hz` | 3.2 Hz | −1.9 dB | 0 | 通带上沿 |
| `sine_0p2hz` | 0.2 Hz | **−22.8 dB** | 0 | **呼吸/漂移带被压住**（本设计的主要目的） |
| `sine_8hz` | 8 Hz | **−79.4 dB** | 0 | 运动伪影/噪声 |
| `square_1hz_full` | 满幅 1 Hz 方波 | −2.2 dB | **33** | **饱和路径**（振铃 + 基波接近满幅） |
| `sine_small_amp` | 1.5 Hz, A=100 | −0.1 dB | **0** | 小信号不饱和 |
| `random_full` | 满幅随机 512 | −8.3 dB | 0 | 通用覆盖 |
| `split_part1/2/3` | 同 `random_full` 数据切 3 段（reset=1,0,0） | −7.7 / −9.3 / −7.3 dB | 0 | 段间状态保持 |

**跨段一致性（独立于黄金参考的检查）**：三段拼接的输出与 `random_full` 一次调用的输出**逐样本相同** ✅
（512 样本）—— 这是"连续流语义正确"的直接证据，且与"黄金参考算得对"互相独立。

`gen_fir_vectors.py` 还用 `np.convolve`（int64 全精度 + 插入零历史）**独立复算**：
**3940 个样本逐样本相等 ✅**。

### 5.3 为什么容差可以是 0（而契约原定 ±1 LSB）

三条都成立，所以两侧必然逐位相同：

1. 定点运算只有**一个**舍入点（最后的 `>>15`），累加全程精确；
2. C++ 的 `>>` 与 Python 的 `>>` 对负数**都是向下取整**（算术右移）。**绝不能用 `/32768`**，
   因为整数除法是向零取整，负数会差 1；
3. 溢出界可证：`|acc| ≤ 32768 · Σ|h| = 1,804,632,064 < 2³¹−1 = 2,147,483,647`
   —— 生成器与主机端模型都**断言**了这条，故 int32 不会溢出、不会因回绕产生分歧。

### 5.4 主机端算术模型（秒级，不是 HLS 证据）

`fpga/sim/host_model_fir.cpp` 把 IP 的内层算式原样转写为可用本机 g++ 运行的程序
（不含 `hls::stream`，所以不受"MinGW 跑不了 HLS 流模型"的限制），实测输出：

```
int32+对称折叠 vs Python 黄金参考：逐样本相等 ✅（不一致 0 个样本）
对称折叠 vs 朴素长整型累加   ：逐位相同 ✅（不一致 0 个样本）
段：15 passed, 0 failed；饱和样本合计 33
饱和路径覆盖：有饱和=1 无饱和=1
```

**它证明了"对称折叠（63 → 32 个乘法）不改变任何一位"**，也让后续改动能在 1 秒内发现问题，
避免每次改一行都去等 Vitis。**注意：它是主机端模型，不是仿真/综合证据**（文件头已写明）。

---

## 6. C 综合（真实运行输出）

```
INFO: [HLS 200-1470] Pipelining result : Target II = 1, Final II = 1, Depth = 9, loop 'VITIS_LOOP_101_2'
INFO: [HLS 200-790] **** Loop Constraint Status: All loop constraints were satisfied.
INFO: [HLS 200-789] **** Estimated Fmax: 146.97 MHz
```

| 模块 | Iteration Latency | Interval | Final II | Slack | BRAM | DSP | FF | LUT |
|---|---|---|---|---|---|---|---|---|
| `fir_filter`（顶层） | — | — | — | 0.50 | 0 | **25 (11%)** | **6172 (5%)** | **4077 (7%)** |
| `..._Pipeline_VITIS_LOOP_101_2` | 9 | 1 | **1** | 0.50 | 0 | 25 (11%) | 2955 (2%) | 1726 (3%) |

- **Estimated Fmax = 146.97 MHz**（≈ 6.80 ns < 10 ns 目标）—— 同一 IP 的既有写法，取自日志原文
- `Storage Report`：顶层 BRAM **0**、URAM **0** —— 延迟线（63×16 bit）被完全分区成**寄存器**（约 1008 FF）
- **对称折叠节省乘法器**：不折叠需 63 个乘法器（其中系数对称），折叠后按 32 个算
  （实测 DSP 25，说明部分乘法被常量优化成了移位加法）

**四个 IP 合计**（xc7z020：LUT 53200 / FF 106400 / BRAM18 280 / DSP48 220）：

| IP | LUT | FF | BRAM18 | DSP |
|---|---|---|---|---|
| `roi_statistic` | 1267 | 723 | 0 | 1 |
| `rgb2gray` v2 | 1410 | 918 | 0 | 5 |
| `motion_quality` v2 | 1501 | 1158 | 64 | 1 |
| **`fir_filter` v1** | **4077** | **6172** | **0** | **25** |
| **合计** | **8255（15.5%）** | **8971（8.4%）** | **64（23%）** | **32（14.5%）** |

> BRAM 仍只占 23%，DSP 从 3% 升到 14.5% —— 这是"全并行 FIR"的必然代价，
> 换来的是 **II=1**（每拍一个样本）。若 M3 发现 DSP 不够，可把 MAC 部分折叠
> （`#pragma HLS PIPELINE II=4`）换 DSP，代价是 II 上升 —— 但时间序列只需 30 Hz，II=4 也绰绰有余。

---

## 7. RTL 协同仿真（cosim）

小向量（`--scale 0.25`，985 样本 / 15 段）跑 `HLS_EXEC=2`：

```
INFO: [COSIM 212-47] Using XSIM for RTL simulation.
Layer 1: 8 passed, 0 failed
Layer 2: 16 passed, 0 failed  (样本不一致 0, 流头错误 0)
INFO: [COSIM 212-1000] *** C/RTL co-simulation finished: PASS ***
// RTL Simulation : 28 / 28 [100.00%] @ "40045000"
INFO [HLS SIM]: The maximum depth reached by any hls::stream() instance in the design is 300
```

- **RTL 侧同样跑了两层**（Layer 1 + Layer 2 都出现），不是"只见 Layer 1 的假通过"（坑 #22）；
- **无死锁**：HLS 自带的 `AESL_deadlock_idx0/idx1_monitor.v` 未报警，28 次调用全部 100%；
- 流深度：测试台"先灌整段再调用"的写法在 cosim 下达到 300（段最长 300 样本），**不死锁**；
- RTL 仿真总时长 40,045,000 ps = **4,004.5 拍 @100 MHz**，覆盖 985 个样本 + 13 次复位 + 28 次调用的全部开销
  （≈ 4.1 拍/样本，**含 AXI-Lite 事务与复位开销**；纯滤波部分是 II=1，见第 6 节）。
- ⚠️ 与图像 IP 一样：**这不等于上板也安全**。真实 DMA 的握手节奏不同，流深度仍需 M3 实测（坑 #23）。

---

## 8. 综合警告判读（逐条给结论，不回避）

| 警告 | 数量/范围 | 判读 |
|---|---|---|
| `Register '...delay_line...' is power-on initialization` | 63 bit × N 条 | **已知且被设计吸收**：延迟线用 static + 显式 `reset` 寄存器，正是为了不依赖上电值（坑 #17）。**上板后必须"先 reset 再喂数据"**。 |
| `Register 'sid' is power-on initialization` | 1 | 与 `roi_statistic` 的 `fid` 同款（风险表第 8 条）。`seg_id` 只用于诊断"有没有丢调用"，不参与任何计算；若 M3 要求严格从 1 开始，改为显式复位。 |
| `UNROLL ... Not implemented`（2 条） | 移位循环、MAC 循环 | **冗余 pragma**：`PIPELINE II=1` 已经把循环体完全展开，故工具报"未实现"。保留是为了表达意图；报告里如实记录。 |
| `ARRAY_PARTITION variable=FIR_COEFF_Q15` | — | **首版有、现已删除**：`static const` 系数表会被常量折叠，工具首轮把它标为 `Not implemented`。删掉后综合报告不再有这条误导性记录（资源与 Fmax 无变化，已复跑对比）。 |
| `flow_target` / `open_component -flow_target` 弃用 | — | 工具链版本问题，与代码无关（另三个 IP 同样出现）。 |

---

## 9. 复现步骤（陌生机器，从零到 PASS）

```bat
:: 0) 环境
call D:\Xilinx\2026.1\Vitis\settings64.bat

:: 1) 重新设计系数（可选；默认已冻结在 fpga/src/fir_coeffs_q15.h）
python D:\Desktop\AMD\fpga\sim\design_fir_coeffs.py --scan --d 0.0

:: 2) 生成测试向量 + Python 黄金参考（必须）
python D:\Desktop\AMD\fpga\sim\gen_fir_vectors.py

:: 3) 秒级自检（无需 Vitis；本机 g++ 可运行）
g++ -O2 -std=c++17 -I fpga/src fpga/sim/host_model_fir.cpp -o host_model_fir.exe
host_model_fir.exe fpga/sim/data_fir

:: 4) HLS C 仿真 + C 综合
cd /d D:\Desktop\AMD\fpga
set "HLS_IP=fir_filter"
vitis-run --mode hls --tcl run_hls.tcl

:: 5) RTL 协同仿真（小向量）
python sim\gen_fir_vectors.py --out-dir sim\data_fir_small --scale 0.25
set "HLS_EXEC=2"
set "ROI_DATA_DIR=%CD%\sim\data_fir_small"
vitis-run --mode hls --tcl run_hls.tcl
```

**判据**：第 3 步 `== PASS ==`；第 4 步日志出现 `Layer 1: 8 passed, 0 failed` +
`Layer 2: 16 passed, 0 failed` + `==== RESULT: PASS ====` + `CSim done with 0 errors`；
第 5 步除上述外还必须出现 **`C/RTL co-simulation finished: PASS`**（只见 Layer 1 就是没生效）。

> ⚠️ 受限沙箱跑不了 csim/cosim（cygwin 命名管道被拒 → `Win32 error 5`，坑 #15）；
> 需在完整权限终端执行，或按第 5 步原样提权。

---

## 10. 证据文件

| 文件 | 内容 |
|---|---|
| `fpga/report/logs/2026-09-11_fir_filter_v1_csim_csynth.log` | csim + csynth 完整原始日志（含两层通过行、II、Fmax、资源） |
| `fpga/report/logs/2026-09-11_fir_filter_v1_cosim.log` | cosim 完整原始日志（含 RTL 侧两层通过行、28/28 事务、PASS） |
| `fpga/sim/data_fir/golden_fir.csv` | 每段小结（n_samples / reset / sat_count），**入库** |
| `fpga/sim/data_fir/golden_fir_out.csv` | **逐样本**期望输出 3940 行，**入库可人工评审** |
| `fpga/sim/data_fir/meta.txt` | 采样率 / 阶数 / 系数移位 / 带通口径 / 段数 |
| `fpga/src/fir_coeffs_q15.h` | 冻结系数（唯一来源，自动生成） |
| `fpga/component_fir_filter/hls/syn/report/csynth.rpt` | 综合报告（本地、`.gitignore`；含 S_AXILITE 寄存器表与 Bind Op 表） |

*本报告由 C 线维护；改动先过一遍 `skill/fpga_hls_c_line.md` 的纪律清单。*
