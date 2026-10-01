# `data_roi_720p/` 与 `data_roi_1080p/` —— `roi_statistic` 两档口径的黄金参考

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

## 怎么生成

```powershell
python fpga/sim/gen_frames.py --width 1280 --height 720  --out-dir fpga/sim/data_roi_720p
python fpga/sim/gen_frames.py --width 1920 --height 1080 --out-dir fpga/sim/data_roi_1080p
```

实测输出（2026-10-01）：内建 **numpy 独立对拍 PASS**（45 项）。

## 怎么用它跑仿真

```powershell
cd fpga
$env:HLS_IP='roi_statistic'
$env:ROI_DATA_DIR=(Resolve-Path sim\data_roi_720p)     # 或 data_roi_1080p
vitis-run --mode hls --tcl run_hls.tcl                  # csim + csynth
$env:HLS_EXEC='2'; vitis-run --mode hls --tcl run_hls.tcl   # 再加 cosim
```

⚠️ 本 IP **没有** `VIGILENS_TIER` 分支（档位只影响数据与输入宽高，IP 逻辑与帧尺寸无关），
所以**必须**用 `ROI_DATA_DIR` 指目录 —— 否则 `run_hls.tcl` 会退回 `sim/data`（640×480 那一套）。

## 已验证到什么程度（**别超范围引用**）

| 口径 | csim | csynth | 资源 | cosim |
|---|---|---|---|---|
| 640×480 | ✅ 28/28 + 45/45 | ✅ II=1 / 138.99 MHz | LUT 1267 / FF 723 / BRAM 0 / DSP 1 | ✅ PASS |
| **1280×720** | ✅ 28/28 + 45/45（0 不符） | ✅ II=1 / 138.99 MHz | **同上，逐项相同** | ❌ **未跑**（测试台含 **73 个事务**，是长作业；请用完整权限终端，给足时间） |
| **1920×1080** | ✅ 28/28 + 45/45（0 不符） | ✅ II=1 / 138.99 MHz | **同上，逐项相同** | ❌ 未跑 |

📌 **三种口径资源逐项相同**，因为该 IP 是纯流式逐像素统计、**逻辑路径与帧尺寸无关**
⇒ **档位化对它不增加硬件代价**，它只需"每档一套黄金参考"，**不需要每档一套资源/时序表**。

完整证据：`fpga/report/c6_seven_ips_csim_csynth_20260930.md` §5.4。
