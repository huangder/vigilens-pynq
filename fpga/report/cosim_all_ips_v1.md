# C3.5 报告：三个 IP 的 RTL 协同仿真（cosim）

> 项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
> 目的：在**不需要板卡**的前提下，验证 RTL 与 C 行为一致，并暴露 `csim` 查不出的**流深度死锁**
> 记录人：C 线（经 AI 协助整理；**所有数字均为真实运行输出**，外推值均已标注）
> 日期：2026-09-10

---

## 1. 结论

| IP | 测试尺寸 | Layer 1（内嵌用例） | Layer 2（**跨语言黄金参考**） | cosm 结论 |
|---|---|---|---|---|
| `roi_statistic` | 64×48 RGB | ✅ 28 / 28 | ✅ **45 / 45**，透传不一致 0 | ✅ **PASS** |
| `rgb2gray` v2 | 80×60 → 48×36 | ✅ 8 / 8 | ✅ **10 / 10**，不一致像素 0 | ✅ **PASS** |
| `motion_quality` v2 | 48×36 灰度 | ✅ 6 / 6 | ✅ **9 / 9**，流头错误 0 | ✅ **PASS** |

```
INFO: [COSIM 212-47] Using XSIM for RTL simulation.
INFO: [COSIM 212-1000] *** C/RTL co-simulation finished: PASS ***
```

> **三个 IP 全部通过 RTL 协同仿真，且没有一个死锁。**
> 「M3 上板前必过 cosim」这道关卡**已关闭**。

---

## 2. ⚠️ 本次最重要的发现：cosim 有个"假通过"陷阱

第一次跑 `motion_quality` 的 cosim 时，日志是这样的：

```
INFO: [COSIM 212-302] Starting C TB testing ...
  Layer 1: 6 passed, 0 failed          ← 只有 Layer 1！
==== RESULT: PASS ====
INFO: [COSIM 212-1000] *** C/RTL co-simulation finished: PASS ***
```

看起来"PASS"，但**Layer 2（跨语言黄金参考）根本没跑**。

**原因**：`run_hls.tcl` 原本只把数据目录传给了 `csim_design`：

```tcl
csim_design  -argv "$data_dir"          # ← 有 -argv
cosim_design -rtl verilog -tool auto    # ← 没有 -argv！测试台拿不到 data_dir，Layer 2 被静默跳过
```

`-argv` **不是自动继承的**，必须分别传给两个命令。不传的后果是：cosim 的测试台找不到
`golden_*.csv`，于是走"没有数据目录 → 跳过 Layer 2"的分支，**结果是拿 6 个玩具用例冒充了整个 RTL 正确性验证**。

**已修复**（`run_hls.tcl`）：

```tcl
cosim_design -rtl verilog -tool auto -argv "$data_dir"
```

修复后同一份日志变成：

```
INFO: [COSIM 212-302] Starting C TB testing ...
  Layer 1: 6 passed, 0 failed
  Layer 2: 9 passed, 0 failed  (stream header errors: 0)     ← 黄金参考进来了
==== RESULT: PASS ====
// RTL Simulation : 0 / 17 ... 17 / 17                        ← 17 个 RTL 事务
INFO: [COSIM 212-316] Starting C post checking ...
  Layer 1: 6 passed, 0 failed
  Layer 2: 9 passed, 0 failed
==== RESULT: PASS ====
INFO: [COSIM 212-1000] *** C/RTL co-simulation finished: PASS ***
```

> **教训（已写进 skill 第 22 条）**：cosim 的 `-argv` 必须显式传。
> 更一般的教训是：**看到 "PASS" 要确认它到底验了什么** ——
> 检查日志里 Layer 2 的行数，比相信那个 PASS 字样可靠。

---

## 3. 已排除的风险：`csim` 不建模流深度

这是 C3 起就挂着的**最大未知项**：`csim` 把 `hls::stream` 实现成**无界 `std::deque`**，
所以"先灌满整帧、再调用 IP、最后排空"的测试台写法在 `csim` 里永远看不出问题；
但 RTL 的 FIFO 深度只有 2~6，理论上会**溢出/死锁**。

**实测结论：没有死锁。** 三个 IP 全部 PASS，且 HLS 在 cosim 中主动编译进了死锁监控模块：

```
INFO: [VRFC 10-2263] Analyzing SystemVerilog file ".../AESL_deadlock_idx0_monitor.v"
INFO: [VRFC 10-2263] Analyzing SystemVerilog file ".../AESL_deadlock_idx1_monitor.v"
INFO: [VRFC 10-2263] Analyzing SystemVerilog file ".../AESL_deadlock_kernel_monitor_top.v"
```

这些监控器没有报错。即使在最"激进"的用例里也如此 —— 例如 `rgb2gray` 一次灌入
**4800 个像素**（输出 FIFO 综合深度仅 6），仍然正常完成。

> **结论**：本项目的测试台写法（灌满-调用-排空）在 cosim 下**可用**，
> 原因是 HLS 的 cosim 通道是事务级、会在 RTL 消费的同时推进，而不是真按 FIFO 深度阻塞。
> 但这**不代表上板也安全** —— 真实 DMA 场景下 PS 与 PL 的握手节奏不同，
> 故各 IP 的 `#pragma HLS STREAM` 深度仍需在 M3 用 `hls_exec = 2` 之外的实测确认。

---

## 4. RTL 实测时序（对 M4 的软硬件对比有用）

从 cosim 的 RTL 事务时间戳直接读出（100 MHz，1 拍 = 10 ns = 10,000 ps）：

| IP | 测试尺寸 | 稳态单次耗时 | 折算拍数 | 像素数 | **每调用固定开销** |
|---|---|---|---|---|---|
| `roi_statistic` | 64×48 | 30.96 µs | **3,096** 拍 | 3,072 | **≈ 24 拍**（240 ns） |
| `rgb2gray` v2 | 80×60 → 48×36 | 48.29 µs | **4,829** 拍 | 4,800 | **≈ 29 拍**（290 ns） |
| `motion_quality` v2 | 48×36 | 18.04 µs | **1,804** 拍 | 1,728 | **≈ 76 拍**（760 ns） |

**判读**：

- 三个 IP 的 `拍数 ≈ 像素数 + 固定开销`，即 **II=1 在 RTL 上成立**（与综合报告的 `Final II = 1` 互相印证）。
- `motion_quality` 的固定开销明显更大（76 拍 vs 24/29）—— 因为它每个像素循环外还有
  一次 **64 位除法**算 `motion_ratio_q16`。这是把它放在 IP 里的代价，已记录备查。
- `roi_statistic` / `rgb2gray` 的 ~25 拍开销主要是 `ap_start`/`ap_done` 握手。

### 4.1 折算到真实分辨率（**计算值，非实测**）

按"拍数 = 像素数 + 该 IP 的固定开销"线性外推：

| IP | 真实尺寸 | 折合拍数 | 折合耗时 @100 MHz | 30 fps 帧预算（33.3 ms）占用 |
|---|---|---|---|---|
| `roi_statistic` | 640×480 = 307,200 px | 307,224 | **≈ 3.07 ms** | ≈ 9.2% |
| `rgb2gray` | 640×480 = 307,200 px | 307,229 | **≈ 3.07 ms** | ≈ 9.2% |
| `motion_quality` | 384×288 = 110,592 px | 110,668 | **≈ 1.11 ms** | ≈ 3.3% |
| **三者串行合计** | | ~725,121 | **≈ 7.25 ms** | **≈ 21.8%** |

> ⚠️ **上表是外推计算，不是实测**。它给出的是"预算够不够"的结论：
> 即使三个 IP 串行跑完一帧，也只占 30 fps 帧预算的约 22%，**余量充足**。
> 真实数字要在 M3 上板后用 PYNQ 实测（这正是 M4 软硬件对比要拿的数据）。

---

## 5. 复现步骤

```bat
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga

:: 1) 生成 cosim 专用小向量（别用 640x480，RTL 仿真会跑到天荒地老）
python sim\gen_frames.py         --width 64 --height 48 --out-dir sim\data_small
python sim\gen_motion_vectors.py --width 80 --height 60 --out-dir sim\data_motion_small

:: 2) 逐个 IP 跑 cosim（HLS_EXEC=2 会额外执行 cosim_design）
set "HLS_EXEC=2"

set "HLS_IP=roi_statistic"
set "ROI_DATA_DIR=%CD%\sim\data_small"
vitis-run --mode hls --tcl run_hls.tcl

set "HLS_IP=rgb2gray"
set "ROI_DATA_DIR=%CD%\sim\data_motion_small"
vitis-run --mode hls --tcl run_hls.tcl

set "HLS_IP=motion_quality"
vitis-run --mode hls --tcl run_hls.tcl
```

**判据**：日志里必须同时出现 **Layer 1 与 Layer 2 的通过行**，且最后是
`C/RTL co-simulation finished: PASS`。**只见 Layer 1 就是没生效**（见第 2 节）。

三个 IP 的完整日志已归档：
- `report/logs/2026-09-10_roi_statistic_v2_cosim.log`
- `report/logs/2026-09-10_rgb2gray_v2_cosim.log`
- `report/logs/2026-09-10_motion_quality_v2_cosim.log`

---

## 6. 本次改动

| 文件 | 改动 |
|---|---|
| `fpga/run_hls.tcl` | ① `hls_exec` 支持环境变量 `HLS_EXEC` 覆盖（免手改文件）；② **修复** `cosim_design` 漏传 `-argv`；③ 明确 `-rtl verilog -tool auto` |
| `fpga/report/logs/*_cosim.log` | 新增 3 份 cosim 日志作为证据 |
| `skill/fpga_hls_c_line.md` | 追加常见坑 22：**cosim 的 `-argv` 不继承，漏传会静默只验 Layer 1** |

---

## 7. 待办（更新）

- [x] ~~三个 IP 各跑一次 cosim~~ → ✅ **本次完成，全部 PASS、无死锁**
- [ ] `fir_filter`（C5）—— 做完后**它也要补一次 cosim**
- [ ] `motion_thresh` 默认 16 待 A 线用其口径复核后冻结
- [ ] A 线按契约 4.4 节对拍灰度 + 缩放口径
- [ ] **待观察**：3/5 点采样的混叠（接真实视频后判断）
- [ ] M3 上板后用 PYNQ 实测每帧延迟，替换第 4.1 节的**外推值**

---

*本文件由 C 线维护。所有实测数字均为真实运行输出的抄录；4.1 节明确标注为外推计算值。*
