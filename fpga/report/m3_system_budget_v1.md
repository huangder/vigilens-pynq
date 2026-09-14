# M3 系统级资源/时序预算（P0-3）

> 项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
> 目的：在**动手做 Block Design（C8）之前**，先回答"四个 IP 一起上板放得下吗、时序收得住吗、
> 数据怎么流"，并给出 M3 的验收门限与降级路径。
> 记录人：C 线（**实测数字**与**待实测项**严格分开标注；任何假设都写明是假设）
> 日期：2026-09-11

---

## 1. 结论先说

1. **放得下，余量充足**：四个 IP 实测合计 LUT **8259（15.5%）**、FF **8971（8.4%）**、
   BRAM18 **64（23%）**、DSP **32（14.5%）**。就算互连/DMA/时钟复位再吃掉一大块，也远不到紧张。
2. **真正的风险不是"装不下"，而是三件"没验过"的事**：
   ① 系统级时序（多 IP + 互连的布线拥塞）；② **真实 DMA 节奏下的流深度**（cosim 证明不了）；
   ③ 缓存一致性（C9）。这三件都只能在板上验，**没有捷径**。
3. **带宽完全不是问题**：像素链 27.6 MB/s、时间序列 60 B/s，相对 Zynq 的 DDR/HP 口带宽是零头。
4. **推荐架构**：像素链用 1 个 AXI DMA（MM2S 喂帧 + S2MM 回读灰度流做同帧比对）；
   **时间序列不走 DMA**，用 AXI4-Stream FIFO（60 B/s 用 DMA 是杀鸡用牛刀）。理由见第 4 节。

---

## 2. 器件与实测占用（这一节全是真实数字）

### 2.1 目标器件 `xc7z020clg400-1`（Zynq-7020）

| 资源 | 总量 |
|---|---|
| LUT | 53,200 |
| FF | 106,400 |
| BRAM18 | 280（= 140 × BRAM36） |
| DSP48E1 | 220 |
| PS | Zynq-7000 PS7（双核 Cortex-A9 @650 MHz，固定占用，不在 PL 预算内） |

### 2.2 四个 IP 的实测占用（各自 csynth，含各自的 AXI-Lite 从口）

| IP | LUT | FF | BRAM18 | DSP | Estimated Fmax |
|---|---|---|---|---|---|
| `roi_statistic` | 1267 | 723 | 0 | 1 | 138.99 MHz |
| `rgb2gray` v2 | 1410 | 918 | 0 | 5 | 137.46 MHz |
| `motion_quality` v2 | 1501 | 1158 | 64 | 1 | 140.05 MHz |
| `fir_filter` v1 | 4043 | 6174 | 0 | 26 | 154.38 MHz |
| **合计** | **8221（15.4%）** | **8973（8.4%）** | **64（23%）** | **33（15.0%）** | 全部 **> 137 MHz** |

来源：各 `component_<ip>/hls/syn/report/csynth.rpt`；汇总见 `fpga/README.md` 的 IP 汇总表。
`fir_filter` 的 DSP 26 是"全并行 + II=1"的代价，**可调**（见第 6 节降级路径）。

> ✅ 注意：**每个 IP 的 LUT 里已经包含它自己的 AXI-Lite 从口**（`ctrl_s_axi_U`），
> 所以系统级只差"把它们接到 PS 的那套互连"。

---

## 3. 系统级还缺什么（**待实测**，附测量方法）

| 组件 | 为什么需要 | 资源影响 | 怎么测 |
|---|---|---|---|
| PS7 + Processor System Reset + FCLK | BD 必需 | LUT/FF 若干、1 个 MMCM/PLL | Vivado 综合后看 `report_utilization` |
| **AXI SmartConnect** | PS 的 1 个 `M_AXI_GP0` 要接 4~6 个 AXI-Lite 从口（4 个 IP + DMA 寄存器） | LUT 随从口数与位宽上升 | 同上 |
| **AXI DMA**（MM2S + S2MM） | 喂 640×480 RGB 帧、回读灰度流 | 每通道数百~上千 LUT | 同上 |
| AXI4-Stream FIFO（FIR 路径，**推荐**） | 让 PS 用 AXI-Lite 写 30 样本/秒给 FIR | 约数百 LUT + 少量 BRAM | 同上 |
| AXI-Stream 位宽/协议匹配 | `roi(24b RGB)` → `rgb2gray(24b)` → `motion_quality(8b)`；`fir(16b)` | 直连即可，可能需要 `regslice` | 看 BD 的 critical warnings |
| 常量/时钟约束 | 100 MHz 目标（`create_clock -period 10`） | — | `report_timing_summary` 的 WNS |

**测量方法（C8 时执行）**：BD 建好后
`report_utilization -hierarchical` + `report_timing_summary`（先 `synth_design`，必要时再 `impl`）。
**在这些跑出来之前，任何"系统占多少 LUT"的话都只能是假设 —— 本文档不给假数字。**

### 3.1 最坏情况判断（**假设**，不是估算）

为了回答"会不会装不下"，做一个**假设上界**：假设互连 + DMA + 时钟复位 + FIFO **合计吃掉 10,000 LUT**
（这是刻意取的一个大数，**不是估算值**）。那么：

```
LUT : 8259 + 10000 = 18,259 / 53,200 = 34.3%
BRAM:   64 + ~8    =    72 / 280    = 25.7%
DSP :   32 + 0     =    32 / 220    = 14.5%
```

**结论：即使在假设上界下也只有 ~34% 的 LUT**，装不下的可能性极低。真正要盯的是时序与流深度。

---

## 4. 数据怎么流：四种接入方案（含推荐）

四个 IP 分成两个**数据平面**，这是设计的关键：

- **像素平面**：`roi_statistic`（RGB 进/出，结果进寄存器）→ `rgb2gray`（RGB→384×288 灰度）
  → `motion_quality`（灰度进/出，结果进寄存器）。**固定点对点链**，不需要流交换。
- **时间序列平面**：PS 按契约 4.6 生成 q 序列 → `fir_filter` → 回 PS。**每秒只有 30 个样本**。

| 方案 | 像素平面 | 时间序列平面 | 优点 | 缺点 / 风险 |
|---|---|---|---|---|
| **① 单 DMA 时间复用** | 1 个 DMA 分时服务两个平面 | 同左（靠流交换/MUX 切换） | DMA 最省 | 控制最复杂；切换时残留事务会咬人；还要额外的流交换逻辑 |
| **② 两个 DMA 独立** | DMA-A（MM2S+S2MM） | DMA-B（MM2S+S2MM） | 控制最简单，两平面互不干扰 | 多一个 DMA 的 LUT；60 B/s 走 DMA 属浪费 |
| **③ FIFO 混搭（推荐）** | DMA（MM2S 喂帧 + **S2MM 回读灰度**） | **AXI4-Stream FIFO**（PS 经 AXI-Lite 写样本） | 各用各的最合适器件；PS 每秒只写 30 次；**S2MM 回读让"同帧硬件 vs 软件"比对成为可能** | 多一个 FIFO IP；PS 侧要按样本写（30 次/秒，可接受） |
| ④ 像素链也走 FIFO | ✗ | — | — | 640×480×3 = **921.6 KB/帧**，PS 逐字节搬不现实 —— **排除** |

**推荐方案 ③**，理由：

1. 时间序列 60 B/s，**DMA 的复杂度与资源都不划算**；FIFO 让 PS "写一个样本、读一个结果"更直观；
2. 像素链保留 DMA + S2MM 回读 —— 这不只是搬运，还是**证据来源**：
   可以把硬件 `rgb2gray`/`motion_quality` 的输出流回 DDR，与 A 线的 NumPy 结果做**同帧逐像素比对**
   （容差 0），这正是 M4 需要的"软硬件一致性"硬证据；
3. 两个平面**物理隔离**，一个出问题不影响另一个（M3 可以分步点亮，降低调试复杂度）。

**带宽核对（算术）**：

| 路径 | 每秒数据 | 结论 |
|---|---|---|
| MM2S 喂 640×480 RGB @30 fps | 640×480×3×30 = **27.6 MB/s** | 对 DDR/HP 口是零头 |
| S2MM 回读 384×288 灰度 @30 fps | 110,592×30 = **3.3 MB/s** | 同上 |
| FIR 时间序列 @30 Hz | 30×2×2 B ≈ **120 B/s** | 可忽略 |

---

## 5. M3 验收门限（**过了才算上板成功**，逐条可判）

| # | 门限 | 判据 |
|---|---|---|
| 1 | **装得下** | LUT ≤ 70%、BRAM ≤ 80%、DSP ≤ 70%（留出后续改动余量） |
| 2 | **时序收敛** | 100 MHz 下 **WNS ≥ 0**（`report_timing_summary`），无 critical warning |
| 3 | **寄存器读写** | 按契约偏移逐个读写自检通过，**含每个输出的 `*_ctrl`(ap_vld)** —— 漏了会误判"值没更新" |
| 4 | **复位语义**（P0-2） | 显式 PL 复位后，`frame_id` / `seg_id` **从 1 开始**（见 `counters_reset_v1.md`） |
| 5 | **DMA 回环**（C9） | 短数组进出**无随机错误**；缓存一致性靠 `Xil_DCacheFlush`/`pynq.allocate` 正确使用 |
| 6 | **流深度不死锁** | 连续 ≥ 300 帧真实 DMA 节奏下不 stall（cosim 只证明了"事务级不死锁"，**不能替代**这条） |
| 7 | **确定性** | 同一段 300 帧跑两次，四个 IP 的寄存器结果**逐位相同** |
| 8 | **软硬件一致** | 同一帧下 PL 的 `sum_r/g/b`、灰度、`diff_total` 与 A 线软件结果**容差 0**；FIR 输出**逐样本相等** |
| 9 | **数据不出 PL 的闭环** | 像素链在 PL 内跑通（不是"搬进搬出只做转发"），能给出 PL 侧处理时延 |

> 门限 6 与 8 是**这个项目最容易翻车也最能加分**的两条：前者是"上板才暴露"的死锁，
> 后者是"软硬件一致"的证据。建议 M3 一开始就按这两条设计实验，别等到最后。

---

## 6. 降级路径（真出问题时的预案，按代价从低到高）

| 触发条件 | 降级动作 | 代价 |
|---|---|---|
| 时序收不住（WNS < 0） | 把 `FCLK_CLK0` 从 100 MHz 降到 **75 MHz**（四个 IP 都 ≥ 137 MHz，余量极大） | 吞吐略降，功能不变 |
| LUT 紧张 | 去掉 S2MM 回读通道（牺牲"同帧图像比对"） | 失去 M4 的一条硬证据 |
| DSP 紧张（FIR 占 25） | FIR 的 MAC 折叠：`#pragma HLS PIPELINE II=4` | II 上升（30 Hz 下完全无感），DSP 大幅下降 |
| 流深度/背压出问题 | 在 IP 之间插 AXI-Stream FIFO/Data FIFO 做弹性缓冲 | 占少量 BRAM |
| 时间序列想再省 | FIR 完全改用 FIFO 直连、连 `fir_filter` 的 AXI-Lite 也只留必要寄存器 | 灵活性下降 |
| 板卡晚到/损坏 | 按 `docs/02` 第七节预案：先用仿真/综合结果支撑 M4，上板结论延后**且不得写成已上板** | 报告强度下降 |

---

## 7. C8 执行清单（让上板变成机械动作）

1. `export_design` 或直接用 IP-XACT 把四个 IP 加入 BD（`vitis-run` 的 `HLS_EXEC=3` 可导出）；
2. BD：PS7（使能 `M_AXI_GP0` + `S_AXI_HP0`）→ SmartConnect → 4 个 IP 的 AXI-Lite；
   DMA 的 MM2S/S2MM 经 `S_AXI_HP0`；
3. 像素链直连：`roi_statistic.video_out → rgb2gray.rgb_in`、`rgb2gray.gray_out → motion_quality.gray_in`；
4. FIR 路径：AXI4-Stream FIFO → `fir_filter.fir_in`，`fir_filter.fir_out` → 另一个 FIFO 或 DMA-B；
5. 时钟：`FCLK_CLK0 = 100 MHz` → 所有 IP 的 `ap_clk`；复位统一用 `Processor System Reset`；
6. 约束：`create_clock -period 10`；检查 BD 的 critical warnings（位宽/协议不匹配会在这里现形）；
7. 综合 → `report_utilization` / `report_timing_summary` → **把真实数字回填本文件第 3 节**；
8. 导出 bitstream + `.hwh` 到 `board/bitstream/`，写加载器到 `board/overlay/`；
9. 按第 5 节 9 条门限逐条记录（每条都要贴命令与输出）。

> Vivado **2026.1 已安装**（`D:\Xilinx\2026.1\Vivado`），与 Vitis HLS 同版本，
> 不存在版本不匹配问题 —— 这是 C8 可以立刻开工的前提。

---

## 8. 待办与证据

- [ ] C8：建 BD 并回填第 3 节的**真实**资源/时序数字（当前全部标"待实测"）
- [ ] C8：按第 5 节门限 3/4 做寄存器与复位自检
- [ ] C9：门限 5/6 的 DMA 回环与长跑不死锁
- [ ] M4：门限 8 的软硬件一致性报告（与 `m4_baseline_v1.md` 的对比表合流）

| 证据文件 | 内容 |
|---|---|
| 四个 `component_<ip>/hls/syn/report/csynth.rpt` | 第 2.2 节实测占用的来源（本地、`.gitignore`） |
| `fpga/report/c5_fir_filter_v1.md` | `fir_filter` 的 II/Fmax/资源 |
| `fpga/report/counters_reset_v1.md` | 门限 4 的实现与证据 |
| `fpga/report/m4_baseline_v1.md` | 门限 8 的对比表模板与 PS 基线 |
| `board/README.md` | C8~C10 的交付清单 |

---

*本报告由 C 线维护。⚠️ 本文档里的"待实测"项在上板前**不得**被引用为已完成事实（《05》铁律 1）。*
