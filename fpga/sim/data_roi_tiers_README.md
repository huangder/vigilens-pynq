# `data_roi_720p/`、`data_roi_1080p/`（5 帧）与 `data_roi_*_cosim/`（1 帧）—— `roi_statistic` 两档口径的黄金参考

> 契约依据：`docs/interface.md` §0 的 **v1.5 草案**（测量口径档位化，**待 A/B 会签**）。
> ⚠️ 会签前不得被当作已冻结接口的数据源引用。

## 为什么需要它们

`roi_statistic` 在链路**最前面**（吃的是原始 RGB），v1.5 把测量口径抬到 720p/1080p 之后，
它必须**按档位各有一套黄金参考**。补齐前的状态是"只有 640×480 一套"，
这是 v1.5 会签时最容易被问的缺口。

## 本目录应有的文件

| 目录 | `frames.bin`（实测） | `golden_roi.csv` |
|---|---|---|
| `data_roi_720p/` | 13,824,000 B（5 帧 × 1280×720×3） | 2345 B（45 行 = 9 用例 × 5 帧） |
| `data_roi_1080p/` | 31,104,000 B（5 帧 × 1920×1080×3） | 2392 B（45 行） |
| `data_roi_720p_cosim/` | 2,764,800 B（**1 帧** × 1280×720×3） | 705 B（**9 行** = 9 用例 × 1 帧） |
| `data_roi_1080p_cosim/` | 6,220,800 B（**1 帧** × 1920×1080×3） | 713 B（9 行） |

> **为什么另有一套 `_cosim` 目录**：cosim 的开销与"**帧数 × 用例数 × 每帧像素数**"成正比，
> 而档位 cosim 要覆盖的是"**单个事务就有整整一帧那么多像素**"，**不是**"帧数多"。
> 上一会话用 5 帧集跑 720p cosim，在 **40/73 个事务**处被 xsim 吃爆内存
> （`Out of memory on request for a fresh 8388608 bytes`，此时已耗时 1h17m）。
> 换成 1 帧集（9 个全尺寸事务）后，**两次档位 cosim 都跑通**（见下表）。
> ⚠️ **代价必须写清**：帧间覆盖由 **640×480 档的 5 帧 cosim** 承担，
> **"@720p/@1080p 的 5 帧 cosim"至今没跑过。**

## 怎么生成

```powershell
python fpga/sim/gen_frames.py --width 1280 --height 720  --out-dir fpga/sim/data_roi_720p
python fpga/sim/gen_frames.py --width 1920 --height 1080 --out-dir fpga/sim/data_roi_1080p
# 档位 cosim 用的小帧数集（全尺寸事务、帧数少）
python fpga/sim/gen_frames.py --width 1280 --height 720  --frames 1 --out-dir fpga/sim/data_roi_720p_cosim
python fpga/sim/gen_frames.py --width 1920 --height 1080 --frames 1 --out-dir fpga/sim/data_roi_1080p_cosim
```

实测输出（2026-10-01）：内建 **numpy 独立对拍 PASS**（5 帧集 45 项 / 1 帧集 9 项）。
同 seed 重生成的产物与上一会话留在仓库根的临时副本 **SHA256 逐文件一致**（9 对，含 `frames.bin`）。

## 怎么用它跑仿真

```powershell
cd fpga
$env:HLS_IP='roi_statistic'
$env:ROI_DATA_DIR=(Resolve-Path sim\data_roi_720p)     # 或 data_roi_1080p
vitis-run --mode hls --tcl run_hls.tcl                  # csim + csynth
$env:HLS_EXEC='2'; vitis-run --mode hls --tcl run_hls.tcl   # 再加 cosim（长作业，见下）
```

⚠️ 本 IP **没有** `VIGILENS_TIER` 分支（档位只影响数据与输入宽高，IP 逻辑与帧尺寸无关），
所以**必须**用 `ROI_DATA_DIR` 指目录 —— 否则 `run_hls.tcl` 会退回 `sim/data`（640×480 那一套）。

⚠️ **2026-10-01 起 `ROI_DATA_DIR` 是"硬约定"**：一旦设置而该目录里没有 `golden_*.csv`，
`run_hls.tcl` **直接 `exit 1`**（旧版会静默回退到默认档位，实测因此把一次"720p cosim"
跑成了 640×480 档，见台账 **BUG-034**）。可用时它会打印
`INFO: ROI_DATA_DIR -> <dir>  (explicit, verified)` —— **先看这一行再相信结论**。

## 已验证到什么程度（**别超范围引用**）

| 口径 | csim | csynth | 资源 | cosim |
|---|---|---|---|---|
| 640×480 | ✅ 28/28 + 45/45 | ✅ II=1 / 138.99 MHz | LUT 1267 / FF 723 / BRAM 0 / DSP 1 | ✅ PASS（5 帧） |
| **1280×720**（5 帧） | ✅ 28/28 + 45/45（0 不符） | ✅ II=1 / 138.99 MHz | **同上，逐项相同** | —（cosim 用下面那行） |
| **1920×1080**（5 帧） | ✅ 28/28 + 45/45（0 不符） | ✅ II=1 / 138.99 MHz | **同上，逐项相同** | —（同上） |
| **1280×720**（`_cosim` 目录，1 帧） | ✅ 28/28 + 9/9 | ✅ II=1 | 同上 | ✅ **PASS**（2026-10-01，1585 s，无死锁） |
| **1920×1080**（`_cosim` 目录，1 帧） | ✅ 28/28 + 9/9 | ✅ II=1 | 同上 | ✅ **PASS**（2026-10-01，无死锁） |

📌 **三种口径资源逐项相同**，因为该 IP 是纯流式逐像素统计、**逻辑路径与帧尺寸无关**
⇒ **档位化对它不增加硬件代价**，它只需"每档一套黄金参考"，**不需要每档一套资源/时序表**。

完整证据：`fpga/report/c6_seven_ips_csim_csynth_20260930.md` §5.4（csim/csynth）与
`fpga/report/c7_sim_closed_loop_20261001.md` §3（两档 cosim）。
