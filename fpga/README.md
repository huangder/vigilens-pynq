# fpga/ —— C 线（FPGA / Vitis HLS）

> 依据《02_三人分工与三线并行开发计划》《04_基础框架搭建指南》的目录约定。
> C 线里程碑驱动、全程可离线仿真验证（HLS C 仿真 + Python 黄金参考），**M3 前不需要板卡**。
> 接口契约见 **`docs/interface.md`**（改接口先改契约，再发公告）。

## 目录说明

| 目录 / 文件 | 内容 | 验收 |
|---|---|---|
| `src/` | HLS 源码（`roi_statistic` / `rgb2gray` / `motion_quality` / `fir_filter`）+ `fir_coeffs_q15.h`（自动生成的冻结系数） | C 仿真与 Python 参考逐点一致（容差 0） |
| `sim/tb_*.cpp` | C 测试台（内嵌边界用例 + 跨语言黄金比对） | 两层全 PASS |
| `sim/gen_frames.py` | 测试向量 + Python 黄金参考生成器（**只用标准库**，有 numpy 时自动对拍） | 同 seed 必得同产物 |
| `sim/design_fir_coeffs.py` | **fir_filter 系数设计器**（纯标准库：响应评估 / −3dB 搜索 / d 扫描 / 呼吸带可行性） | 同一条命令必得同一张系数表 |
| `sim/host_model_fir.cpp` | **主机端算术模型**（秒级自检，本机 g++ 可运行；**不是** HLS 证据） | 与黄金参考逐样本相等 + 折叠逐位相同 |
| `sim/q15_ref.py` | **Q1.15 量化参考实现**（契约 4.6 节，P0-1）：ROI 均值 → Q1.15，含 5 条性质自检与 CSV 批量转换 | `--selftest` 必须 PASS |
| `report/m3_system_budget_v1.md` | **M3 系统级预算**（P0-3）：四 IP 实测占用、四种接入方案、9 条上板验收门限、降级路径 | 上板前**待实测**项不得当既成事实引用 |
| `report/m4_baseline_v1.md` | **M4 软硬件对比基线**（P0-4）：PS 侧实测基线 + PL 延迟口径 + 待填对比表 | 与 `metrics/scripts/bench_filter_ps.py` 配套 |
| `report/counters_reset_v1.md` | **计数器复位改造**（P0-2）：四个 IP 加 `HLS RESET`，含 RTL 复位证据与 +2 LUT 代价 | 关闭风险表第 8 条 |
| `sim/data/golden_roi.csv` | **黄金参考（入库）** | 后续改动不得破坏 |
| `sim/data/frames.bin` | 生成的测试向量（**.gitignore，不入库**） | 由 seed 重建 |
| `report/` | 综合报告（LUT/FF/BRAM/DSP/时钟/WNS）+ `environment.md` | 无 ERROR、时序收敛 |
| `run_hls.tcl` | 一键 C 仿真 + C 综合 | 见下 |

## 怎么跑

```bat
:: 0) 必须先加载 Vitis 环境，否则 vitis-run 不在 PATH（见 report/environment.md 第 1.1 节）
call D:\Xilinx\2026.1\Vitis\settings64.bat

:: 1) 生成测试向量 + Python 黄金参考（数据变化时才需要重跑）
python D:\Desktop\AMD\fpga\sim\gen_frames.py

:: 2) C 仿真 + C 综合
cd /d D:\Desktop\AMD\fpga
vitis-run --mode hls --tcl run_hls.tcl
```

> ⚠️ PowerShell 里 `& '...\settings64.bat'` **不生效**（子进程环境变量留不下来），
> 用 cmd，或按 `report/environment.md` 第 1.1 节的 PowerShell 写法导入环境变量。
> ⚠️ **受限沙箱跑不了 csim**：HLS 的 C 仿真用 cygwin/MSYS2，需要创建 signal pipe（命名管道），
> 受限沙箱禁止命名管道 → 报 `cat.exe ... couldn't create signal pipe, Win32 error 5`。
> **这不是 Vitis 或代码的问题**，请在完整权限终端跑。

### 实测输出（2026-09-10 真实运行，非预期值）

```text
==== tb_roi_statistic: contract docs/interface.md (tolerance = 0) ====
---- [Layer 1] embedded boundary cases (8x6) ----
  Layer 1: 28 passed, 0 failed
---- [Layer 2] cross-language golden reference ----
  data dir : D:/Desktop/AMD/fpga/sim/data
  meta     : 640x480, 5 frames, 921600 bytes/frame, 45 golden rows
  Layer 2: 45 passed, 0 failed  (pass-through mismatched pixels: 0)

==== RESULT: PASS ====
INFO [HLS SIM]: The maximum depth reached by any hls::stream() instance in the design is 307200
INFO: [SIM 211-1] CSim done with 0 errors.
```

综合实测：`Final II = 1`、`Estimated Fmax = 138.99 MHz`（目标 100 MHz）、
LUT **1267** / FF **723** / BRAM **0** / DSP **1**（LUT 占 2%）、无 ERROR。
完整记录（含寄存器映射核对、逐条警告判读、复现步骤）：**`report/c3_c7_roi_statistic_v1.md`**。

> **两次独立运行结果逐项一致**（AI 提权沙箱内一次 + C 线本人在自己终端一次，
> 时间 09-10 22:17 / 22:29，资源数字逐位相同）。
> 本人那次运行的完整日志已归档为证据：
> `report/logs/2026-09-10_roi_statistic_v1_csim_csynth.log`。

## 当前进度

| 任务 | 状态 | 说明 |
|---|---|---|
| **C1** 锁工具链 + 官方最小例程 | ✅ 完成 | 见 `report/environment.md` |
| **C2** 冻结 IP 接口 | ✅ **v1.1 已会签完成**（v1.0 于 2026-09-11 冻结；v1.1 的 45 fps 于 2026-09-16 三方会签完成，A/B 补签见 §5.2/§5.4） | `docs/interface.md`；四个 IP 的寄存器映射均已与实综合**逐行核对** |
| **C3** `roi_statistic` | ✅ **完成** | csim 28/28 + 45/45、0 errors；II=1；Fmax 138.99 MHz；LUT 1267/FF 723/BRAM 0/DSP 1 |
| **C3.5** RTL 协同仿真（cosim） | ✅ **完成（4 个 IP）** | 全部 PASS、Layer 2 黄金参考也跑到了、**无死锁**；见 `report/cosim_all_ips_v1.md`（三个图像 IP）与 `report/c5_fir_filter_v1.md` 第 7 节（`fir_filter`） |
| **C4** `rgb2gray` + `motion_quality` | ✅ **完成** | `rgb2gray` v2 8/8+10/10，Fmax 137.46 MHz，BRAM 0；`motion_quality` v2 6/6+9/9，Fmax 140.05 MHz，**BRAM 64（23%）** |
| **C5** `fir_filter` | ✅ **完成** | N=63 Q15 带通 @45fps；csim **8/8 + 17/17**、**容差 0**、cosim **PASS**、II=1、Fmax **154.38 MHz**、LUT 4043/FF 6174/**BRAM 0**/DSP 26（2026-09-13 @45fps 实测）；报告 `report/c5_fir_filter_v1.md`、`report/c5_fir_filter_45hz_reserved.md` |
| **C6** testbench + Python 黄金参考 | ✅ **完成（4 个 IP）** | 两层验证：内嵌边界用例 + 跨语言黄金参考 |
| **C7** 综合报告归档 | ✅ **完成（4 份）** | `report/c3_c7_roi_statistic_v1.md`、`report/c4_rgb2gray_motion_quality_v1.md`、`report/cosim_all_ips_v1.md`、`report/c5_fir_filter_v1.md` |
| **C8~C10** 上板 / Overlay / DMA | ⏳ M3 后 | 属 `board/`；BRAM 已留出 216 个 |

### 四个 IP 汇总（全部真实运行数据）

| IP | 工作尺寸 | csim | II | Fmax | LUT | FF | BRAM18 | DSP |
|---|---|---|---|---|---|---|---|---|
| `roi_statistic` | 640×480 RGB | 28/28 + 45/45 | 1 | 138.99 MHz | 1267 | 723 | 0 | 1 |
| `rgb2gray` **v2** | 640×480 → 384×288 | 8/8 + 10/10 | 1 | 137.46 MHz | 1410 | 918 | 0 | 5 |
| `motion_quality` **v2** | 384×288 灰度 | 6/6 + 9/9 | 1 | 140.05 MHz | 1501 | 1158 | **64** | 1 |
| `fir_filter` **v1** | 时间序列（63 阶 Q15） | **8/8 + 17/17** | 1 | **154.38 MHz** | **4043** | **6174** | **0** | **26** |
| **合计** | | | | | **8221**（15.4%） | **8973**（8.4%） | **64（23%）** | **33**（15.0%） |

> ⚠️ `fir_filter` 的 DSP 是四个里最高的（26），这是**全并行 + II=1** 的代价；
> 时间序列只需 45 Hz，若 M3 发现 DSP 紧张，可把 MAC 折叠（`PIPELINE II=4`）换 DSP。
> 它 **不占 BRAM**（延迟线 63×16bit 被完全分区成寄存器）。

> ✅ **BRAM 问题已闭环**：`motion_quality` 初版在 640×480 上工作，占 **256 个 BRAM18 = 91%**（上板必炸）；
> 根因是**片内数组按 2 的幂地址空间分配**（三组对照实验坐实：320×240→64、512×512→128、640×480→256）。
> 据此让 `rgb2gray` 增加 **3/5 缩放**（640×480→384×288），`motion_quality` 工作尺寸随之降到 384×288，
> BRAM 降到 **64（23%）**，**给 M3 留出 216 个 BRAM18**。详见 `report/c4_rgb2gray_motion_quality_v1.md` 第 5 节。
>
> 💡 **可复用的设计规律**：BRAM 成本是**台阶式**的（跨过 2 的幂就翻倍）；
> 像素数 ≤ 131072（2¹⁷）的尺寸都只要 64 个 —— 所以选了 384×288 而不是 320×240：**同价、更清晰**。

> M1 的"计数+累加"玩具版 IP 已被 C2/C3 的真接口版**替换**（C1 的环境验证结论仍然有效）。

## 怎么选要跑的 IP（`run_hls.tcl` 已改为多 IP 通用）

```bat
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga

:: 默认跑 roi_statistic
vitis-run --mode hls --tcl run_hls.tcl

:: 跑别的 IP（注意加引号，否则 cmd 会把尾随空格写进变量值）
set "HLS_IP=rgb2gray"
vitis-run --mode hls --tcl run_hls.tcl

set "HLS_IP=motion_quality"
vitis-run --mode hls --tcl run_hls.tcl

set "HLS_IP=fir_filter"
vitis-run --mode hls --tcl run_hls.tcl
```

| IP | 数据目录 | 生成器 |
|---|---|---|
| `roi_statistic` | `sim/data/` | `gen_frames.py` |
| `rgb2gray` / `motion_quality` | `sim/data_motion/` | `gen_motion_vectors.py` |
| `fir_filter` | `sim/data_fir/` | `gen_fir_vectors.py`（系数来自 `src/fir_coeffs_q15.h`） |

## 已冻结的接口要点（详见 `docs/interface.md`）

- 器件 **PYNQ-Z2 `xc7z020clg400-1`**，时钟 **10 ns（100 MHz）**；图像 **640×480 RGB888@45fps**。
- 三个必须记住的坑：
  1. 通道顺序 **R-G-B**（OpenCV 默认 BGR，A 线比对前要转）；
  2. ROI 是**半开区间** `[x0,x1)`，等价于 `img[y0:y1, x0:x1]`；
  3. `TUSER` = 帧首像素，`TLAST` = **行**末像素（不是帧末）。
- `roi_statistic` 全整数运算，单通道累加上限 78,336,000 < 2³²，**比对容差 = 0**。

## 环境速查

- 赛制指定 **Vitis HLS 2026.1**：已装于 `D:\Xilinx\2026.1\Vitis`，入口 `vitis-run --mode hls --tcl <脚本>`。
- Python：系统 3.14.7（**有 numpy 2.5.2**）；项目 venv 在 `.venv`（有 cocotb 2.1.0 / pytest 9.1.1，**无 numpy**）。
  `gen_frames.py` 只用标准库，两个解释器都能跑。
- 便携 git：`D:\Git\cmd\git.exe`（已加入用户 PATH，新终端生效）。
- 详细流程与避坑：见 `skill/fpga_hls_c_line.md`。

## 跑 cosim（RTL 协同仿真 —— ✅ 四个 IP 已完成，全部 PASS）

**结果**：`roi_statistic` / `rgb2gray` / `motion_quality` 三个图像 IP 见 `report/cosim_all_ips_v1.md`；
`fir_filter` 见 `report/c5_fir_filter_v1.md` 第 7 节。四个都是 Layer 1 与 **Layer 2 黄金参考**都跑了、
**无一死锁**。

`csim` 把 `hls::stream` 实现成无界 `std::deque`，查不出流深度死锁，**只能靠 cosim**。
但**别用 640×480 的向量跑 cosim**：45 用例 × 307200 像素 ≈ **1380 万拍 RTL 仿真**，会跑到天荒地老。
测试台是**尺寸无关**的（从 `meta` 读宽高），换一份小向量即可，代码不用改：

```bat
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga

:: 1) 生成 cosim 专用小向量（几万拍量级，RTL 仿真可接受）
python sim\gen_frames.py         --width 64 --height 48 --out-dir sim\data_small
python sim\gen_motion_vectors.py --width 80 --height 60 --out-dir sim\data_motion_small

:: 2) HLS_EXEC=2 让脚本额外执行 cosim_design
set "HLS_EXEC=2"

set "HLS_IP=roi_statistic"
set "ROI_DATA_DIR=%CD%\sim\data_small"
vitis-run --mode hls --tcl run_hls.tcl

set "HLS_IP=rgb2gray"
set "ROI_DATA_DIR=%CD%\sim\data_motion_small"
vitis-run --mode hls --tcl run_hls.tcl

set "HLS_IP=motion_quality"
vitis-run --mode hls --tcl run_hls.tcl

:: fir_filter —— 它没有"图像尺寸"，小向量只缩样本数（--scale），段结构不变
python sim\gen_fir_vectors.py --out-dir sim\data_fir_small --scale 0.25
set "HLS_IP=fir_filter"
set "ROI_DATA_DIR=%CD%\sim\data_fir_small"
vitis-run --mode hls --tcl run_hls.tcl
```

> ⚠️ **判据：日志里必须同时出现 Layer 1 与 Layer 2 的通过行**，且最后是
> `C/RTL co-simulation finished: PASS`。**只见 Layer 1 就是没生效** ——
> `cosim_design` 的 `-argv` **不会**从 `csim_design` 继承，漏传会让 Layer 2 被静默跳过，
> 于是拿几个玩具用例冒充整个 RTL 验证（本项目踩过，见 `report/cosim_all_ips_v1.md` 第 2 节）。
>
> `run_hls.tcl` 已修复并优先采用环境变量 `ROI_DATA_DIR`；`data_small/`、`data_motion_small/`
> 已加入 `.gitignore`（一条命令即可重建，不必入库）。

## `fir_filter` 的系数从哪来（C5 专用）

```bat
:: 设计/复现系数（纯标准库；会重写 src/fir_coeffs_q15.h）
python fpga\sim\design_fir_coeffs.py                 :: 默认 N=63、0.7~3.5 Hz @45fps
python fpga\sim\design_fir_coeffs.py --scan --d 0.0  :: 打印 d 扫描表（-6dB vs -3dB 口径的取舍）
python fpga\sim\design_fir_coeffs.py --band 0.1 0.5  :: 附呼吸带可行性评估（结论：63 阶做不到）
```

- **系数只有一处来源**：`src/fir_coeffs_q15.h`（自动生成，请勿手改）。
  `gen_fir_vectors.py` 与 `tb_fir_filter.cpp` **都解析/包含这同一个头文件**，不存在"两边各一份系数"。
- 改系数 = 重跑 `design_fir_coeffs.py` → **必须**重跑 `gen_fir_vectors.py`（否则测试台以
  `meta.taps/shift 与工程不一致` 硬失败，这是刻意设计）。
- 设计口径（−6 dB = 0.70/3.50 Hz）与"为什么不用 −3 dB 口径"的实测权衡表见
  `report/c5_fir_filter_v1.md` 第 3 节。

## 本地快速自检（不跑 vitis-run，秒级）

改完 HLS 代码后，**先花 2 秒做语法/类型检查**，再去花几分钟跑 csim：

```powershell
$inc = 'D:\Xilinx\2026.1\Vitis\include'
g++ -std=c++17 -fsyntax-only -I $inc fpga/src/roi_statistic.cpp
g++ -std=c++17 -fsyntax-only -I $inc -I fpga/src fpga/sim/tb_roi_statistic.cpp

# fir_filter 还能更进一步：主机端算术模型可以**真的运行**（不含 hls::stream，故不受 win32 线程模型限制）
g++ -O2 -std=c++17 -I fpga/src fpga/sim/host_model_fir.cpp -o host_model_fir.exe
host_model_fir.exe fpga/sim/data_fir     # 逐样本对黄金参考 + 折叠 vs 朴素累加 + 溢出界
```

> `host_model_fir.cpp` 把 IP 的内层算式原样转写，1 秒内就能告诉你"算术对不对"。
> ⚠️ 它是**主机端模型，不是仿真/综合证据**；权威证据只能是 `vitis-run` 的输出。

> ⚠️ **只做 `-fsyntax-only`，不要试图用本机 g++ 运行**：
> 本机 MinGW 是 `win32` 线程模型，而 `hls::stream` 的 C 仿真模型依赖 `std::thread`/`std::mutex`，
> 链接后运行会直接以 `0xC0000139`（STATUS_ENTRYPOINT_NOT_FOUND，DLL 入口点缺失）退出。
> **真正执行仿真只能用 `vitis-run`**（详见下节风险表第 1 条）。

## 待验证假设 / 已知风险（按《05》：不确定的当假设，不当结论）

| # | 假设 / 风险 | 结论 |
|---|---|---|
| 1 | C 仿真不建模 `hls::stream` 的 FIFO 深度，因此测试台可"先灌满整帧、再调用、后排空" | ✅ **已验证**：实测日志 `The maximum depth reached by any hls::stream() instance in the design is 307200`（整帧堆在流里也没报错）。**cosim 侧三个图像 IP 全部 PASS、无一死锁**（HLS 自带的 `AESL_deadlock_*_monitor` 未报警，即使一次灌 4800 像素而输出 FIFO 深度只有 6）→ 详见 `report/cosim_all_ips_v1.md`；`fir_filter` 同样"先灌整段（最长 300 样本）再调用"，**PASS 且不死锁** → `report/c5_fir_filter_v1.md` 第 7 节。⚠️ 上板仍需确认流深度（DMA 握手节奏不同） |
| 2 | 本机 MinGW 可运行 csim 模型 | ❌ **不成立**：`0xC0000139`（DLL 入口点缺失）。本机 g++ 仅用于 `-fsyntax-only` 自检，执行一律交给 `vitis-run` |
| 3 | `csim_design -argv` 能把数据目录传给测试台 | ✅ **已验证**：日志 `INFO: ROI data dir = D:/Desktop/AMD/fpga/sim/data` |
| 4 | `s_axilite` 的 `offset=` 按字节生效 | ✅ **已验证**：11 个数据寄存器偏移与契约逐一吻合。**但发现每个输出还多一个 `*_ctrl`(ap_vld) 寄存器**（`sum_r_ctrl=0x44` … `frame_id_ctrl=0x64`），已回填契约 |
| 5 | `#pragma HLS PIPELINE II=1` 可达 | ✅ **已验证**：`Target II = 1, Final II = 1, Depth = 2`，`All loop constraints were satisfied` |
| 6 | vitis-run 可直接调用 | ❌ **不成立**：Vitis 未写入用户 PATH，须先 `call D:\Xilinx\2026.1\Vitis\settings64.bat`（见 `report/environment.md` 1.1 节） |
| 7 | 受限沙箱能跑 csim | ❌ **不成立**：csim 需要 cygwin signal pipe（命名管道），受限沙箱禁止 → `Win32 error 5`。需完整权限终端或提权 |
| 8 | `static ap_uint<32> fid` 的复位行为 | ✅ **已闭环（2026-09-11，P0-2）**：确认默认行为是"**上电初始化**而非复位归零"，会让 PS 在复位后按"frame_id 从 1 开始"同步时失配。**四个 IP 已各加一行 `#pragma HLS RESET variable=<计数器>`**，计数器随 `ap_rst_n` 清零；四个 IP csim 回归全过、代价 **+2 LUT**、Fmax 不变。证据 `report/counters_reset_v1.md`。⚠️ **判据陷阱**：加复位后 `Register 'fid' is power-on initialization` 警告**不会消失**（上电值仍在）—— 要判断是否真加了复位，**必须看生成的 RTL 里计数器是否处于 `ap_rst_n_inv` 分支**（坑 #31） |
| 9 | `#pragma HLS BIND_STORAGE` 写在数组声明**之前**能被识别 | ❌ **不成立**：csynth 报 `[HLS 207-4637] use of undeclared identifier 'prev_buf'`。**pragma 必须写在变量声明之后**。⚠️ **csim 不检查这条 pragma，所以 csim 全绿 ≠ 综合能过** —— 这是本次最值得记住的教训 |
| 10 | 640×480 的片内"上一帧"缓存能装进 xc7z020 | ❌ **不成立**（但已解决）：实测 **256/280 = 91% BRAM**。**根因已用对照实验坐实**：片内数组按 **2 的幂地址空间**分配（`depth=307200` → 2¹⁹=524288 → 256 个），不是按真实深度（那样只需 150）。三组对照（320×240→**64**、512×512→**128**、640×480→**256**）全部吻合。✅ **已按该规律解决**：`rgb2gray` 加 3/5 缩放，把工作尺寸压到 384×288，BRAM 降到 **64（23%）** |
| 12 | 3/5 点采样缩放的混叠会不会让运动量偏噪 | ⚠️ **待观察**：点采样保留原始灰度值、不降噪，运动检测可能偏"敏感"。接真实视频后才能判断；若偏噪可改块均值或退回 320×240 的 2×2 均值（黄金参考需同步改） |
| 11 | 灰度公式与 OpenCV `cv2.cvtColor` 一致 | ⚠️ **大概率不一致**：本设计冻结式 `(77R+150G+29B+128)>>8` 给纯红 **77**，而浮点系数 0.299×255=76.245 → **76**，差 1 LSB。**已把自定义式冻结为唯一口径**，A 线须按 `docs/interface.md` 4.4 节对拍 |
| 13 | `fir_filter` 的"容差 ±1 LSB"是否真的需要 | ✅ **不需要，容差 = 0**（2026-09-11 实测）：全整数运算 + **唯一**舍入点（`>>15`）+ 可证不溢出，Python 与 C 逐样本相等（3940/3940）；契约 4.3 已把该项从 ±1 LSB **收紧为 0** |
| 14 | 对称折叠（63 → 32 个乘法器）会不会改变结果 | ✅ **不会**：预加是精确整数运算、无中间舍入。主机端模型实测"折叠 vs 朴素 long long 累加**逐位相同**"（`host_model_fir.cpp`），csim 侧同样 0 不一致 |
| 15 | `static` 延迟线的初值可否依赖 | ❌ **不可以**：C++ 层面 static 是零初始化，但 HLS 把它实现为**上电初始化**（综合日志里每个 bit 一条 `Register '...delay_line...' is power-on initialization`），**复位不清零**。故本 IP 提供 `reset` 寄存器；**PS 与测试台都必须"先 reset 再喂数据"**（坑 #17 的又一次落地） |
| 16 | 63 阶能否顺带做呼吸带（0.1~0.5 Hz） | ❌ **不能**（2026-09-11 实测 + 公式）：Hamming 过渡带 `3.3/(2πN)·fs` = 0.250 Hz，已与整个呼吸带（0.4 Hz）同量级；按 0.1/0.5 Hz 设计时 −3 dB 边沿在 ±0.3 Hz 内找不到。压到 0.15 Hz 需 **N ≳ 105**。→ 呼吸带须 **PS 侧先降采样**（见契约 3.5 节末） |
| 17 | `#pragma HLS ARRAY_PARTITION variable=FIR_COEFF_Q15 ...` 有意义吗 | ❌ **没有**：`static const` 系数表会被**常量折叠**，首版综合把它标为 `Not implemented`。已删除该 pragma（资源与 Fmax 复跑后无变化），避免综合报告里留一条误导记录 |

## 任务清单（与《02》C1~C10 对应）

- [x] C1 锁定 Vitis HLS 2026.1 + 跑通官方最小 HLS 例程（仿真+综合）
- [x] C2 冻结 IP 接口 → `docs/interface.md`（🔒 **v1.1 已会签完成**，2026-09-16）
- [x] C3 `roi_statistic` C 仿真通过（28/28 + 45/45，0 errors）
- [x] C4 `rgb2gray` + `motion_quality` C 仿真通过（8/8+10/10、6/6+9/9，0 errors）——BRAM 已从 91% 降到 **23%**
- [x] C5 `fir_filter` C 仿真通过（**8/8 + 17/17，0 errors**）—— N=63 Q15 带通，容差 **0**（@45fps，2026-09-13）
- [x] C6 每个 IP 的 testbench + Python 黄金参考就绪（**4 个 IP 已就绪**）
- [x] C7 综合报告（资源 + 时序）归档（**4 份**：`report/c3_c7_*.md`、`report/c4_*.md`、`report/c5_fir_filter_v1.md`）
- [x] **BRAM 问题闭环**：根因坐实 + 方案 A′（384×288）已实现验证，留出 216 个 BRAM18
- [x] **cosim**：**四个 IP** 全部 RTL 协同仿真 PASS（Layer1 28/8/6/8 + Layer2 **45/10/9/16**），**无一死锁**
- [x] **C5 `fir_filter` 补跑 cosim**（小向量 985 样本，RTL 侧两层同样全过）
- [x] **P0-1** 契约补「ROI 均值 → Q1.15 量化口径」（契约 4.6 节）+ 参考实现 `sim/q15_ref.py`（自检 PASS）
- [x] **P0-2** 四个 IP 计数器改为随块复位清零（`#pragma HLS RESET`，+2 LUT）—— 风险表第 8 条关闭
- [x] **P0-3** `report/m3_system_budget_v1.md`：系统级资源/时序预算 + 四种接入方案 + 9 条 M3 验收门限
- [x] **P0-4** `report/m4_baseline_v1.md` + `metrics/scripts/bench_filter_ps.py`：PS 侧基线（实测）与 PL 延迟口径
- [ ] C8~C10 上板/Overlay/DMA（M3 后，属 `board/`）

## M0 欠账（《04》第 6 节 DoD 硬指标）

> ✅ **已于 2026-09-10 全部闭环**（`git init` + 首次 commit、仓库根 `README.md` / `LICENSE`、`config.yaml` 均已就位）。
> 本条原先列在本文件末尾，属**已过期的欠账清单**，2026-09-11 更正 —— 引用前请以 `git log` / 仓库根为准。

*本文件由 C 线维护；改动先过一遍 `skill/fpga_hls_c_line.md` 的纪律清单。*
