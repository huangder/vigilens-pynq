# fpga/ —— C 线（FPGA / Vitis HLS）

> 依据《02_三人分工与三线并行开发计划》《04_基础框架搭建指南》的目录约定。
> C 线里程碑驱动、全程可离线仿真验证（HLS C 仿真 + Python 黄金参考），**M3 前不需要板卡**。
> 接口契约见 **`docs/interface.md`**（改接口先改契约，再发公告）。

## 目录说明

| 目录 / 文件 | 内容 | 验收 |
|---|---|---|
| `src/` | HLS 源码（`roi_statistic` / `motion_quality` / `fir_filter`） | C 仿真与 Python 参考逐点一致 |
| `sim/tb_*.cpp` | C 测试台（内嵌边界用例 + 跨语言黄金比对） | 两层全 PASS |
| `sim/gen_frames.py` | 测试向量 + Python 黄金参考生成器（**只用标准库**，有 numpy 时自动对拍） | 同 seed 必得同产物 |
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
| **C2** 冻结 IP 接口 | ✅ v0.92（**待 A/B 会签**） | `docs/interface.md`；三个 IP 的寄存器映射均已与实综合**逐行核对** |
| **C3** `roi_statistic` | ✅ **完成** | csim 28/28 + 45/45、0 errors；II=1；Fmax 138.99 MHz；LUT 1267/FF 723/BRAM 0/DSP 1 |
| **C4** `rgb2gray` + `motion_quality` | ✅ **完成（功能层）** | `rgb2gray` 9/9 + 10/10，Fmax 151.98 MHz，BRAM 0；`motion_quality` 6/6 + 9/9，Fmax 140.05 MHz，**BRAM 256（91%）⚠️** |
| **C6** testbench + Python 黄金参考 | ✅ **完成（3 个 IP）** | 两层验证：内嵌边界用例 + 跨语言黄金参考 |
| **C7** 综合报告归档 | ✅ **完成（2 份）** | `report/c3_c7_roi_statistic_v1.md`、`report/c4_rgb2gray_motion_quality_v1.md` |
| **C5** `fir_filter` | ⏳ **下一步** | 形状已在契约冻结（3.5 节），系数/阶数待定 |
| **C8~C10** 上板 / Overlay / DMA | ⏳ M3 后 | 属 `board/`；**上板前必须先解决 motion_quality 的 BRAM 占用** |

### 三个 IP 汇总（全部真实运行数据）

| IP | csim | II | Fmax | LUT | FF | BRAM18 | DSP |
|---|---|---|---|---|---|---|---|
| `roi_statistic` | 28/28 + 45/45 | 1 | 138.99 MHz | 1267 | 723 | 0 | 1 |
| `rgb2gray` | 9/9 + 10/10 | 1 | 151.98 MHz | 927 | 763 | 0 | 3 |
| `motion_quality` | 6/6 + 9/9 | 1 | 140.05 MHz | 1503 | 1162 | **256** | 1 |
| **合计** | | | | **3697**（7%） | **2648**（2.5%） | **256（91%）** | **5**（2%） |

> 🚨 **瓶颈是 BRAM，不是逻辑资源**：`motion_quality` 的"上一帧"缓存占掉 91% 的 BRAM18，
> M3 还要放 AXI DMA 与互连，只剩 24 个极可能不够。**方案待决策**（降分辨率 / 双流 / 接受），
> 详见 `report/c4_rgb2gray_motion_quality_v1.md` 第 5 节。

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
```

| IP | 数据目录 | 生成器 |
|---|---|---|
| `roi_statistic` | `sim/data/` | `gen_frames.py` |
| `rgb2gray` / `motion_quality` | `sim/data_motion/` | `gen_motion_vectors.py` |

## 已冻结的接口要点（详见 `docs/interface.md`）

- 器件 **PYNQ-Z2 `xc7z020clg400-1`**，时钟 **10 ns（100 MHz）**；图像 **640×480 RGB888@30fps**。
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

## 跑 cosim（RTL 协同仿真，M3 前必做）

`csim` 查不出 `hls::stream` 的流深度死锁（见下节风险表第 1 条），**只能靠 cosim**。
但**别用 640×480 的向量跑 cosim**：45 用例 × 307200 像素 ≈ **1380 万拍 RTL 仿真**，会跑到天荒地老。

本次生成的脚本是**尺寸无关**的（测试台从 CSV 的 `# meta` 读宽高），所以换一份小向量即可，代码不用改：

```bat
:: 1) 生成小尺寸向量（64x48，5 帧 45 用例 = 约 13.8 万拍，RTL 仿真可接受）
python D:\Desktop\AMD\fpga\sim\gen_frames.py --width 64 --height 48 --out-dir D:\Desktop\AMD\fpga\sim\data_small

:: 2) 让 TCL 用这份小向量，并把 hls_exec 改成 2（csynth + cosim）
set ROI_DATA_DIR=D:\Desktop\AMD\fpga\sim\data_small
::   然后编辑 run_hls.tcl:  set hls_exec 2
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga
vitis-run --mode hls --tcl run_hls.tcl
```

> `run_hls.tcl` 会**优先**采用环境变量 `ROI_DATA_DIR`（已实现）。
> `data_small/` 已加入 `.gitignore`（一条命令即可重建，不必入库）。

## 本地快速自检（不跑 vitis-run，秒级）

改完 HLS 代码后，**先花 2 秒做语法/类型检查**，再去花几分钟跑 csim：

```powershell
$inc = 'D:\Xilinx\2026.1\Vitis\include'
g++ -std=c++17 -fsyntax-only -I $inc fpga/src/roi_statistic.cpp
g++ -std=c++17 -fsyntax-only -I $inc fpga/sim/tb_roi_statistic.cpp
```

> ⚠️ **只做 `-fsyntax-only`，不要试图用本机 g++ 运行**：
> 本机 MinGW 是 `win32` 线程模型，而 `hls::stream` 的 C 仿真模型依赖 `std::thread`/`std::mutex`，
> 链接后运行会直接以 `0xC0000139`（STATUS_ENTRYPOINT_NOT_FOUND，DLL 入口点缺失）退出。
> **真正执行仿真只能用 `vitis-run`**（详见下节风险表第 1 条）。

## 待验证假设 / 已知风险（按《05》：不确定的当假设，不当结论）

| # | 假设 / 风险 | 结论 |
|---|---|---|
| 1 | C 仿真不建模 `hls::stream` 的 FIFO 深度，因此测试台可"先灌满整帧、再调用、后排空" | ✅ **已验证**：实测日志 `The maximum depth reached by any hls::stream() instance in the design is 307200`（整帧堆在流里也没报错）。⚠️ 但**深度不足导致的死锁只有 cosim 才暴露**，M3 前务必跑一次 `hls_exec = 2` |
| 2 | 本机 MinGW 可运行 csim 模型 | ❌ **不成立**：`0xC0000139`（DLL 入口点缺失）。本机 g++ 仅用于 `-fsyntax-only` 自检，执行一律交给 `vitis-run` |
| 3 | `csim_design -argv` 能把数据目录传给测试台 | ✅ **已验证**：日志 `INFO: ROI data dir = D:/Desktop/AMD/fpga/sim/data` |
| 4 | `s_axilite` 的 `offset=` 按字节生效 | ✅ **已验证**：11 个数据寄存器偏移与契约逐一吻合。**但发现每个输出还多一个 `*_ctrl`(ap_vld) 寄存器**（`sum_r_ctrl=0x44` … `frame_id_ctrl=0x64`），已回填契约 |
| 5 | `#pragma HLS PIPELINE II=1` 可达 | ✅ **已验证**：`Target II = 1, Final II = 1, Depth = 2`，`All loop constraints were satisfied` |
| 6 | vitis-run 可直接调用 | ❌ **不成立**：Vitis 未写入用户 PATH，须先 `call D:\Xilinx\2026.1\Vitis\settings64.bat`（见 `report/environment.md` 1.1 节） |
| 7 | 受限沙箱能跑 csim | ❌ **不成立**：csim 需要 cygwin signal pipe（命名管道），受限沙箱禁止 → `Win32 error 5`。需完整权限终端或提权 |
| 8 | `static ap_uint<32> fid` 的复位行为 | ⚠️ **待定**：综合警告 `Register 'fid' is power-on initialization` —— 是**上电**初始化而非复位归零。M3 上板时按需决定是否改为复位归零 |
| 9 | `#pragma HLS BIND_STORAGE` 写在数组声明**之前**能被识别 | ❌ **不成立**：csynth 报 `[HLS 207-4637] use of undeclared identifier 'prev_buf'`。**pragma 必须写在变量声明之后**。⚠️ **csim 不检查这条 pragma，所以 csim 全绿 ≠ 综合能过** —— 这是本次最值得记住的教训 |
| 10 | 640×480 的片内"上一帧"缓存能装进 xc7z020 | ❌ **不成立**：实测 **256/280 = 91% BRAM**。**根因已用对照实验坐实**：片内数组按 **2 的幂地址空间**分配（`depth=307200` → 2¹⁹=524288 → 256 个），不是按真实深度（那样只需 150）。三组对照（320×240→**64**、512×512→**128**、640×480→**256**）全部吻合。⚠️ **成本是台阶式的**：像素数 ≤ 131072 的尺寸都只要 64 个（320×240 与 384×288 同价）。方案待决策 |
| 11 | 灰度公式与 OpenCV `cv2.cvtColor` 一致 | ⚠️ **大概率不一致**：本设计冻结式 `(77R+150G+29B+128)>>8` 给纯红 **77**，而浮点系数 0.299×255=76.245 → **76**，差 1 LSB。**已把自定义式冻结为唯一口径**，A 线须按 `docs/interface.md` 4.4 节对拍 |

## 任务清单（与《02》C1~C10 对应）

- [x] C1 锁定 Vitis HLS 2026.1 + 跑通官方最小 HLS 例程（仿真+综合）
- [x] C2 冻结 IP 接口 → `docs/interface.md`（**v0.92，待 A/B 会签转 v1.0**）
- [x] C3 `roi_statistic` C 仿真通过（28/28 + 45/45，0 errors）
- [x] C4 `rgb2gray` + `motion_quality` C 仿真通过（9/9+10/10、6/6+9/9，0 errors）——⚠️ **BRAM 91% 待解决**
- [ ] C5 `fir_filter` C 仿真通过 ← **下一步**
- [x] C6 每个 IP 的 testbench + Python 黄金参考就绪（3 个 IP 已就绪；C5 待补）
- [x] C7 综合报告（资源 + 时序）归档（2 份：`report/c3_c7_*.md`、`report/c4_*.md`）
- [ ] **BRAM 方案决策**（降分辨率 / 双流 / 接受）—— 上板前必做，见 C4 报告第 5 节
- [ ] **cosim**：对每个 IP 跑一次 `hls_exec = 2`（csim 查不出流深度死锁，M3 前必过）
- [ ] C8~C10 上板/Overlay/DMA（M3 后，属 `board/`）

## M0 欠账（《04》第 6 节 DoD 硬指标，尚缺）

- [ ] `git init` + 首次 commit（仓库根与仓库名待定，建议 `vigilens`）
- [ ] 仓库根 `README.md` / `LICENSE`
- [ ] `config.yaml`（阈值集中存放）

*本文件由 C 线维护；改动先过一遍 `skill/fpga_hls_c_line.md` 的纪律清单。*
