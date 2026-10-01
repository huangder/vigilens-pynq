# c7 档位仿真闭环（csim / csynth / cosim）—— 2026-10-01

> **证据文件**（A/B/C 三方共享）。本文所有数字来自 **2026-10-01 本机真实运行**，命令在 §7，
> 原始日志已归档到 `fpga/report/logs/2026-10-01_*`（**引用前请自己重跑一遍**）。
> **性质**：C 线把 `docs/26` §6.2 记的"仿真闭环还差 3 项"（U1/U2/U3）跑完的记录，
> **不是契约、不是判据**。契约以 `docs/interface.md` 为准。
>
> ⚠️ **全文纪律**：每条标 **【已验证】**（有命令与输出）/ **【推测】** / **【未验证】**。
> 上板相关结论**一律不存在**（`board/bitstream/` 仍为空、Mizar 从未上电）。

---

## 0. 一句话结论

`docs/26` §6.2 列的 U1/U2/U3 **三项全部关闭**；过程中**抓到并修掉 2 个真缺陷**
（`motion_quality` 片内缓存放不下 480×270；`run_hls.tcl` 显式数据目录被静默忽略），
并**纠正了 U1 原来的归因**（原文写"算力问题"，实测是越界）。

| 项 | 原状态（`docs/26` §3） | 现在 |
|---|---|---|
| **U1** `motion_quality` @480×270 全尺寸 csim | 【未验证】"算力问题，非缺陷" | ✅ **已闭环**，且**不是算力问题** —— 是**片内缓存越界**，已修（§2） |
| **U2** `roi_statistic` @720p cosim | 【未验证】"环境问题（两道关卡）" | ✅ **cosim PASS**、无死锁（§3.2） |
| **U3** `roi_statistic` @1080p cosim | 【未验证】同上 | ✅ **cosim PASS**（**9 用例集先失败**于 xsim 内存，裁到 4 用例后通过；见 §3.3） |
| 两档黄金参考入仓 | 【未验证】（文件在仓库根临时目录） | ✅ **已入仓**，且**重生成逐字节一致**（§4） |

---

## 1. 本轮覆盖矩阵（**只有这张表里的格子才是"跑过"的**）

| IP | 口径 / 数据目录 | csim | csynth | cosim |
|---|---|---|---|---|
| `motion_quality` | 640×480 → 384×288（`sim/data_motion`，既有档） | ✅ 6/6 + 9/9 | ✅ 64 BRAM18 / II=1 / 140.05 MHz | ✅ **PASS**（263 s，无死锁） |
| `motion_quality` | 720×60 → **480×270**（`sim/data_motion_720p60`） | ✅ 6/6 + 9/9 | ✅ **64 BRAM18** / II=1 / 140.05 MHz | ✅ **PASS**（整条 vitis-run 335 s，cosim 段 302 s，无死锁） |
| `motion_quality` | 1080p45 → **480×270**（`sim/data_motion_1080p45`） | ✅ 6/6 + 9/9 | ✅ II=1 | ✅ **PASS**（298 s，无死锁） |
| `roi_statistic` | 1280×720（`sim/data_roi_720p`，5 帧） | ✅ 28/28 + 45/45 | ✅ II=1 | —（cosim 用下面那行的小帧数集） |
| `roi_statistic` | 1920×1080（`sim/data_roi_1080p`，5 帧） | ✅ 28/28 + 45/45 | ✅ II=1 | — |
| `roi_statistic` | **1280×720 全尺寸事务**（`sim/data_roi_720p_cosim`，1 帧×9 用例） | ✅ 28/28 + 9/9 | ✅ II=1 | ✅ **PASS**（1585 s，无死锁） |
| `roi_statistic` | **1920×1080 全尺寸事务**（`sim/data_roi_1080p_cosim`，1 帧×**4 用例**） | ✅ 28/28 + 4/4 | ✅ II=1 | ✅ **PASS**（1745 s，无死锁；见 §3.3.3） |

> **其余 5 个 IP**（`rgb2gray` / `fir_filter` / `raw10_unpack` / `bayer_demosaic` / `frame_scale`）本轮**没有重跑**：
> 它们的源码一个字没动，最近一次 csim+csynth+cosim 结果仍在 `fpga/report/c6_seven_ips_csim_csynth_20260930.md`。

---

## 2. U1 —— 一个真缺陷：`motion_quality` 的片内缓存放不下 480×270

> 卡片：**BUG-033**（`docs/16`）。

### 2.1 现象【已验证】

在 v1.5 的 480×270 档位上，`motion_quality` 的 **csim 编译完成后不再前进**：

- `_u1_mq720full_b2.log`（vitis-run 会话 15:57:06 起）：编译告警打完即停，
  `15:57:04` 已生成 `csim.exe`、`temp0.log` **0 字节**；到 16:06:07（**>9 分钟**）日志**一字节没长**。
- 直接跑那个二进制也一样：`csim.exe D:\...\data_motion_720p60` → 输出**0 字节**。
  ⇒ **与 `vitis-run`、与命名管道都无关**，是 csim 自身。
- 对照组（**同一台机器、同一份源码**）：既有 **640×480 档**（工作尺寸 384×288）的 csim **约 1 秒**就跑完
  （`_u1_mq720full_before.log`：`Layer 1: 6 passed` / `Layer 2: 9 passed` / `RESULT: PASS`）。
  ⚠️ 那次"看起来在测 720p"其实是**跑错了数据目录**（见 **BUG-034**）—— 这一点本身也是证据。

### 2.2 根因【已验证 + 推测】

| 事实 | 证据 |
|---|---|
| 容量宏写死 `384×288`，数组 110592 格 | `fpga/src/motion_quality.cpp`（旧版）`#define MOTION_MAX_W 384/H 288` |
| v1.5 最大档是 **480×270 = 129600**，**越界 19008 格** | `fpga/sim/data_motion_720p60/meta.txt`：`out_width=480 out_height=270 gray_bytes=129600` |
| RTL 里那块 ram 的深度**就是 110592**，而地址口是 **17 bit**（可寻址到 131071） | 旧组件 RTL：`parameter AddressWidth = 17; parameter AddressRange = 110592; reg [7:0] ram[0:110591];` |
| 旧组件 csynth 同样记 `8, 110592, 1` | `component_u1_mq720full/hls/syn/report/csynth.rpt` |

- **csim 侧**：C 数组不设边界 ⇒ 越界写相邻内存。**【推测】**挂死的具体位置取决于 BSS 布局
  （`fid` 就紧跟在 `prev_buf` 后面），**换个编译器、加一个变量，症状就会变** —— 这正是它危险的地方。
- **RTL 侧**：`i` 走到 110592…129599 时地址越出 `ram[0:110591]` ⇒ 读出 X、写入被丢弃。
  **本轮没有拿到"RTL 真的算错"的 cosim 反例**，因为同一份测试台的 C 阶段就已经挂死（跑不到 RTL）。
  这一条按 **【已验证：数组深度/地址宽度】+【推测：RTL 行为后果】** 记。

### 2.3 改法

1. 把容量抽成**唯一来源** `fpga/src/motion_quality_cap.h`（IP 与测试台**共用一份**，
   写法照 `fir_coeffs_q15.h`）：`MOTION_MAX_W 480 / MOTION_MAX_H 270` ⇒ **129600 ≤ 2¹⁷**。
2. `tb_motion_quality.cpp` 增加**容量硬校验**：工作尺寸 > 容量时打印
   `FATAL: work size ... EXCEEDS on-chip capacity` 并让 `RESULT: FAIL`。
   —— 越界这件事**两边都不会自己报错**，所以判据必须写在测试台里。

### 2.4 修后实测【已验证】

| 指标 | 修前（384×288 档） | 修后（480×270 档） |
|---|---|---|
| csim | 6/6 + 9/9，~1 s | **6/6 + 9/9**，正常退出 |
| 片内缓存 | `8, 110592, 1` → **64 BRAM18** | `8, 129600, 1` → **64 BRAM18（22%）** |
| LUT / FF / DSP | 1501 / 1158 / 1 | **1501 / 1158 / 1（逐项相同）** |
| Final II / Fmax | 1 / 140.05 MHz | **1 / 140.05 MHz（相同）** |
| cosim | PASS | **PASS**（`C/RTL co-simulation finished: PASS`，cosim 段 5m02s） |
| 320 秒级回归（640×480 档） | —— | ✅ csim+cosim **PASS**（263 s） |

📌 **硬件代价为 0**：129600 与 110592 落在**同一个 2¹⁷ 台阶**内 —— 这就是 `AGENTS.md` §7.5
"片内数组按 2 的幂地址空间分配、成本台阶式"那条规律的应用，也是 v1.5 敢把两档都做成 480×270 的理由。

---

## 3. U2 / U3 —— `roi_statistic` 两档全尺寸 cosim

### 3.1 为什么要用"小帧数"向量集

上一会话的两次尝试都**死在 xsim 内存**上（`_xsim_roi720p_g.log`：
`Out of memory on request for a fresh 8388608 bytes / Total memory consumed so far :98001632 bytes`，
此时才走到 **40/73** 个事务、耗时已 **01:17:26**）。

本轮判断：**cosim 的开销与"帧数 × 用例数 × 每帧像素数"成正比**，而档位 cosim 要覆盖的是
"**单个事务就有整整一帧那么多像素**"，**不是**"帧数多"。所以：

- 给 `gen_frames.py` 增加 `--frames N`（默认 5，行为不变）；
- 档位 cosim 用 **1 帧 × 9 用例 = 9 个全尺寸事务**（`data_roi_720p_cosim` / `data_roi_1080p_cosim`）；
- 帧间覆盖（5 帧）仍由 **640×480 档**的既有 cosim 承担。

⚠️ **这是一次有意的覆盖权衡，不是"跑完了"**：见 §8。

### 3.2 `roi_statistic` @1280×720 —— cosim PASS【已验证】

```
===== tier run: roi_720p_cosim  ip=roi_statistic  exec=2  data=sim\data_roi_720p_cosim =====
      exit=0  elapsed=1585s
      Layer 1: 28 passed, 0 failed
      Layer 2: 9 passed, 0 failed  (pass-through mismatched pixels: 0)
      ==== RESULT: PASS ====
      INFO: [COSIM 212-1000] *** C/RTL co-simulation finished: PASS ***
```

**没有出现上一会话的内存失败** ⇒ 该失败与"全尺寸事务的**累计条数**"相关，减小帧数即可稳定复现通过。
⚠️ 但这也说明：**"5 帧 × 9 用例 @720p 的 cosim"目前仍然没有跑过**（§8）。

### 3.3 `roi_statistic` @1920×1080 —— 先失败一次（xsim 内存不够），裁用例后跑通

#### 3.3.1 第一次尝试：**FAIL**，但不是 IP 的问题【已验证】

先用与 720p 同构的向量集（1 帧 × **9 用例**）跑，**2658 s 后失败**：

```
// RTL Simulation :  ... 29/37  30/37  31/37  32/37  33/37  34/37
Out of memory on request for a fresh 8388608 bytes
Total memory consumed so far :2509307604 bytes          <- ≈ 2.34 GiB
Command failed: Simulator command interrupted.
ERROR: [COSIM 212-4] *** C/RTL co-simulation finished: FAIL ***
```

- 它跑到 **34 / 37** 个事务（28 个内嵌小用例 + **6 个**全尺寸用例），**在第 7 个全尺寸事务处**吃爆内存；
- 日志里**没有任何 `Layer 2: FAIL`**：C 侧的前置比对是 `Layer 1: 28 passed`、`Layer 2: 9 passed`（0 不符），
  所以**不是算错了**，是仿真器跑不到比对那一步。

#### 3.3.2 根因：xsim 的内存**按"总像素数"增长**【已验证现象 + 推测机制】

| 用例 | 总像素（Layer 2） | 结果 |
|---|---|---|
| `roi_statistic` @720p × 5 帧 × 9 用例 | 41.5 Mpx | ❌ 上一会话 **40/73 事务**处 OOM（`~98 MB` 那条上报） |
| `roi_statistic` @720p × 1 帧 × 9 用例 | 8.3 Mpx | ✅ **PASS**（1585 s，峰值内存未触顶） |
| `roi_statistic` @1080p × 1 帧 × 9 用例 | 18.7 Mpx | ❌ 本轮 **34/37 事务**处 OOM（**2.34 GiB**） |
| `roi_statistic` @1080p × 1 帧 × 4 用例 | 8.3 Mpx | ✅ 见 §3.3.3 |
| `motion_quality` @480×270 × 10 帧 | 1.2 Mpx | ✅ PASS（337 s） |

- 【推测】内存多半花在 cosim 的 SVR/AXI-VIP 事务基础设施上（**~130 B/像素**量级）；
  **不是波形记录** —— `cosim_design -help` 显示 `-trace_level` 的**默认已经是 `none`**。
- 【未试】`-disable_deadlock_detection` / `-disable_dependency_check` / `-disable_binary_tv`
  本轮**没有试**（会在"验证强度 vs 内存"之间做取舍）。这是**下一步能做的实验**（§8）。

#### 3.3.3 处置：把 1080p 的用例裁到 4 个（与已通过的 720p×9 同为 8.3 Mpx）

给 `gen_frames.py` 加 `--cases N`，生成 `sim/data_roi_1080p_cosim`（**1 帧 × 4 用例**：
`full_frame` / `center_roi` / `top_left_1x1` / `bottom_right_1x1`）。
这是**有意的覆盖权衡**，不是"跑完了"：见 §8。

实测（第二次运行，**1745 s**）：

```
===== tier run: roi_1080p_cosim  ip=roi_statistic  exec=2  data=sim\data_roi_1080p_cosim =====
      exit=0  elapsed=1745s
      Layer 1: 28 passed, 0 failed
      Layer 2: 4 passed, 0 failed  (pass-through mismatched pixels: 0)
      ==== RESULT: PASS ====
      INFO: [COSIM 212-1000] *** C/RTL co-simulation finished: PASS ***
```

⇒ **RTL 侧每像素逐字节 0 不符、无死锁**；覆盖的是 `full_frame` / `center_roi` /
`top_left_1x1` / `bottom_right_1x1` 四个用例在 **1920×1080 全尺寸事务**下的行为。
⚠️ 代价：`single_pixel` / `empty_roi` / `inverted_roi` / `odd_offset` / `oversized_roi`
这 5 个用例在 1080p 下**没有进 cosim**（它们已在 **csim** 的 5 帧 × 9 用例里覆盖，见 §1）。

---

## 4. 两档黄金参考：入仓 + 可复现性

- 仓库**已有**（并发会话的 `8047ef2` 已把它们提交）：`fpga/sim/data_motion_720p60/`、
  `fpga/sim/data_motion_1080p45/`、`fpga/sim/data_roi_720p/`、`fpga/sim/data_roi_1080p/`。
- 本轮另补：`fpga/sim/data_roi_720p_cosim/`、`fpga/sim/data_roi_1080p_cosim/`（§3.1 的小帧数集）。
- **可复现性【已验证】**：把两档**重新生成**到正式目录，与上一会话留在仓库根的临时目录逐文件
  **SHA256 完全一致**（9 对，含 `frames.bin` / `rgb_frames.bin` / `gray.bin` / `golden_*.csv` / `meta.txt`）：

```
identical=True   _roi720\golden_roi.csv      == fpga\sim\data_roi_720p\golden_roi.csv
identical=True   _roi720\frames.bin          == fpga\sim\data_roi_720p\frames.bin
identical=True   _roi1080\golden_roi.csv     == fpga\sim\data_roi_1080p\golden_roi.csv
identical=True   _roi1080\frames.bin         == fpga\sim\data_roi_1080p\frames.bin
identical=True   _golden1080\meta.txt        == fpga\sim\data_motion_1080p45\meta.txt
identical=True   _golden1080\golden_motion.csv == fpga\sim\data_motion_1080p45\golden_motion.csv
identical=True   _golden1080\gray.bin        == fpga\sim\data_motion_1080p45\gray.bin
identical=True   _golden720\meta.txt         == fpga\sim\data_motion_720p60\meta.txt
identical=True   _golden720\golden_motion.csv== fpga\sim\data_motion_720p60\golden_motion.csv
```

⇒ "同 seed 必得同产物"在**跨会话**尺度上成立，临时目录可以安全清掉。

---

## 5. 第二个真缺陷：`run_hls.tcl` 的 `ROI_DATA_DIR` 会被**静默忽略**

> 卡片：**BUG-034**（`docs/16`）。

### 5.1 现象【已验证】

显式把 `ROI_DATA_DIR` 指到 720p60 的目录，日志却显示它跑了**另一个档位**，且**没有任何警告**：

```
INFO: data dir  = D:/Desktop/AMD/fpga/sim/data_motion        <- 640x480 档
meta     : work 384x288 (RGB in 640x480), 10 frames, gray_bytes=110592, ...
```

而命令行给的是 `data_motion_720p60`。**后果**：一次 cosim 的结论被张冠李戴
（本轮真实发生过：第一次"720p 全尺寸 cosim"实际跑的是 384×288 档）。

### 5.2 根因【已验证】

旧逻辑把 `ROI_DATA_DIR` 当**候选之一**：目录里没有黄金参考文件就**静默跳到下一个候选**
（`run_hls.tcl` @ `2ada236` 第 141–156 行）。当时那个目录里**确实还没有** `golden_motion.csv`
（会话开始时 `git status` 里没有该目录的未跟踪文件、`git ls-files` 也只有它的 `README.md`；
数据是稍后才被搬进仓库的）—— 于是静默回退到默认档。

**A/B 反证【已验证】**：把旧版候选逻辑单独抽出来重跑（数据已就位后），它**能**正确选中 `data_motion_720p60`：

```
PROBE: ROI_DATA_DIR = 'D:\Desktop\AMD\fpga\sim\data_motion_720p60'
PROBE: candidate 'D:/Desktop/AMD/fpga/sim/data_motion_720p60'  golden_motion.csv exists = 1
PROBE: CHOSEN = 'D:/Desktop/AMD/fpga/sim/data_motion_720p60'
```

⇒ **候选逻辑本身没错，错在"不可用时静默降级"**。这也是本项目最忌讳的一类错：
不报错、看着自洽、结论却换了口径。

### 5.3 改法【已验证】

`run_hls.tcl` 现在分两条路：

1. `ROI_DATA_DIR` **一旦设置就必须可用**，否则 `exit 1` 并打印期望的文件名；
   可用时打印 `INFO: ROI_DATA_DIR -> <dir>  (explicit, verified)`。
2. 没设时按默认档位目录找，并打印**选中的**目录（不再只打印一行没有人会看的 `data dir =`）。

实测（同一条 `cmd /c` 调用形状）：

```
INFO: ROI_DATA_DIR -> D:/Desktop/AMD/fpga/sim/data_motion_720p60  (explicit, verified)
meta     : work 480x270 (RGB in 1280x720), 10 frames, gray_bytes=129600, ...
==== RESULT: PASS ====
```

---

## 6. 一条可复现入口

本轮新增 `fpga/run_tier_sim_all.ps1`（**ASCII-only**，理由同 `run_cosim_all.ps1`）：
把整张档位矩阵固化成一张表，**每项一条日志**，并显式设置/清除 `ROI_DATA_DIR` 与 `VIGILENS_TIER`
（避免 §5 那种"以为在跑 A 档"）。

```powershell
# 四项 csim+csynth（约 5 分钟）
powershell -NoProfile -ExecutionPolicy Bypass -File fpga\run_tier_sim_all.ps1

# 两档全尺寸 cosim（慢：720p 约 26 分钟，1080p 约 55 分钟）
powershell -NoProfile -ExecutionPolicy Bypass -File fpga\run_tier_sim_all.ps1 -WithCosim -Name 'roi_720p_cosim'
powershell -NoProfile -ExecutionPolicy Bypass -File fpga\run_tier_sim_all.ps1 -WithCosim -Name 'roi_1080p_cosim'
```

---

## 7. 怎么复现本文每个数字（命令 + 预期）

```powershell
# 0) 环境
. .\env.ps1
# 1) 重新生成两档黄金参考（同 seed 必得同产物）
python fpga/sim/gen_frames.py --width 1280 --height 720  --out-dir fpga/sim/data_roi_720p
python fpga/sim/gen_frames.py --width 1920 --height 1080 --out-dir fpga/sim/data_roi_1080p
python fpga/sim/gen_frames.py --width 1280 --height 720  --frames 1 --out-dir fpga/sim/data_roi_720p_cosim
python fpga/sim/gen_frames.py --width 1920 --height 1080 --frames 1 --cases 4 --out-dir fpga/sim/data_roi_1080p_cosim
python fpga/sim/gen_motion_vectors.py --width 1920 --height 1080 --decim 1 4 --out-dir fpga/sim/data_motion_1080p45

# 2) HLS（需完整权限终端；受限沙箱跑不了 csim，见 skill 坑 #15）
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga
powershell -NoProfile -ExecutionPolicy Bypass -File run_tier_sim_all.ps1
#    预期：3 项 RESULT: PASS（mq_1080p45 / roi_720p / roi_1080p）
powershell -NoProfile -ExecutionPolicy Bypass -File run_tier_sim_all.ps1 -WithCosim -Name 'mq_640'
#    预期：csim+cosim PASS（旧档无回归，263 s）
powershell -NoProfile -ExecutionPolicy Bypass -File run_tier_sim_all.ps1 -WithCosim -Name 'mq_1080p45_cosim'
#    预期：C/RTL co-simulation finished: PASS（298 s）
powershell -NoProfile -ExecutionPolicy Bypass -File run_tier_sim_all.ps1 -WithCosim -Name 'roi_720p_cosim'
#    预期：C/RTL co-simulation finished: PASS（约 26 分钟）
powershell -NoProfile -ExecutionPolicy Bypass -File run_tier_sim_all.ps1 -WithCosim -Name 'roi_1080p_cosim'
#    预期：C/RTL co-simulation finished: PASS（约 27 分钟；**9 用例集会在第 7 个事务处 OOM**，见 §3.3.1）

# 3) 那个"挂死"怎么复现（**需要把 motion_quality_cap.h 改回 384×288**，否则秒级通过）
#    改回后：set "VIGILENS_TIER=720p60" && vitis-run --mode hls --tcl run_hls.tcl
#    预期：csim 编译完成后不再前进（实测 >9 分钟零输出）

# 4) BUG-034 的 A/B 反证（旧候选逻辑 vs 显式目录）
#    旧版 run_hls.tcl 取自 git：git show 2ada236:fpga/run_hls.tcl
#    把它的候选定位段单独跑：set "ROI_DATA_DIR=...\data_motion_720p60" && vitis-run --mode hls --tcl <探针>
#    预期（数据就位后）：CHOSEN = 'D:/Desktop/AMD/fpga/sim/data_motion_720p60'
```

---

## 8. 本文的诚实边界（**别超范围引用**）

- **两档 cosim 的覆盖不对称，必须写清**：
  · `@1280×720`：**1 帧 × 9 用例**（9 个全尺寸事务）；
  · `@1920×1080`：**1 帧 × 4 用例**（4 个全尺寸事务）—— **9 用例那版被 xsim 内存挡掉了**（§3.3.1）。
  两者覆盖的都是"**单个事务 = 一整帧像素**"下的流协议、计数器宽度与死锁；
  **都不覆盖"连续多帧"**。多帧覆盖仍来自 640×480 档的 5 帧 cosim。
  **"@720p/@1080p 的 5 帧 cosim"至今没跑过**：上一会话在 40/73 事务处 OOM。
- **xsim 的内存上限是这台机器的硬约束，本轮没解决**：内存按总像素数增长（~130 B/px，【推测】）。
  能跑通的预算约 **8~9 Mpx**。**"给足时间"解决不了**，只能减工作量、或换仿真器、
  或试 `-disable_deadlock_detection` 之类开关（**本轮未试**，且会削弱"无死锁"这一条证据）。
- **`motion_quality` @480×270 的 RTL 越界后果是【推测】**，不是实测反例（C 阶段先挂死，跑不到 RTL）。
  能确定的是【已验证】：RTL 的 `ram` 深度 110592、地址口 17 bit，而循环要走到 129599。
- 本轮第一次"@1080p 的 9 用例 cosim"是 **FAIL**（§3.3.1），**按"失败"记录**，不要引用成"跑通过"。
- 本轮第一次 motion_quality 的 480×270 尝试**静默跑了 640×480 档**（BUG-034）——
  所以凡是引用"某档 PASS"的旧结论，**先确认当时的数据目录选对了**。
- 其余 5 个 IP 本轮未重跑（源码未动）。
- **上板相关结论一个都不存在**：`board/bitstream/` 为空、Mizar-Z7020 从未上电。
- 本文的 ✅ 都是**本机 2026-10-01** 的真实运行；**换机器/换工具链会变**，引用前请重跑。
