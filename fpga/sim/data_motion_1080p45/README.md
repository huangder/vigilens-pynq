# `data_motion_1080p45/` —— 1080p45 档（v1.5 备档）的 rgb2gray / motion_quality 黄金参考

> 契约依据：`docs/interface.md` §0 的 **v1.5 草案**（测量口径档位化，**待 A/B 会签**）。
> ⚠️ **v1.5 会签前，本目录不得被 A/B 线当作已冻结接口的数据源引用。**

## 本目录应有的文件

| 文件 | 大小（实测） | 说明 |
|---|---|---|
| `rgb_frames.bin` | 62,208,000 B | 10 帧 × 1920×1080×3（RGB888 输入，喂 `rgb2gray`） |
| `gray.bin` | 1,296,000 B | 10 帧 × **480×270**（**1/4** 抽取） |
| `golden_motion.csv` | 559 B | `motion_quality` 黄金参考（帧 1..9） |
| `meta.txt` | 202 B | 含 `in 1920x1080 -> out 480x270`、`decim=1/4 keep_x%4<1 keep_y%4<1` |

## 怎么生成

```powershell
python fpga/sim/gen_motion_vectors.py --width 1920 --height 1080 --decim 1 4 `
       --out-dir fpga/sim/data_motion_1080p45
```

实测输出（2026-10-01）：内建 **numpy 独立对拍 PASS**（9 帧对），
`motion_pixels=129600` —— **与 720p60 档完全相同**，这正是 v1.5 能成立的核心理由
（两档落在同一个 2¹⁷ 台阶内 ⇒ `motion_quality` 片内缓存仍是 64 个 BRAM18）。

## 怎么用它跑 IP 仿真

```powershell
cd fpga
$env:HLS_IP='rgb2gray'; $env:VIGILENS_TIER='1080p45'
$env:ROI_DATA_DIR=(Resolve-Path sim\data_motion_1080p45)
vitis-run --mode hls --tcl run_hls.tcl
```

## 已验证到什么程度（**别超范围引用**）

| 项 | 状态 |
|---|---|
| `rgb2gray` @1080p45 | ✅ **480×270 全尺寸** csim+csynth PASS（`Layer 1 8/8`、`Layer 2 10/10`、0 不符、II=1、Fmax 140.81 MHz，54 s） |
| `motion_quality` @1080p45 | ✅ **480×270 全尺寸** csim+csynth PASS（2026-10-01：`Layer 1 6/6`、`Layer 2 9/9`、0 不符、II=1、Fmax 140.05 MHz，40 s）。⚠️ **cosim 未单独跑** —— 本档与 720p60 的**工作尺寸同为 480×270、RTL 完全相同**，cosim 见 `data_motion_720p60/README.md` |
| `roi_statistic` @1920×1080 | ✅ 28/28 + 45/45、0 不符、II=1、Fmax 138.99 MHz（**资源与 640×480/720p 逐项相同**）；黄金参考另见 `data_roi_1080p/` |

> ⚠️ 上面 `motion_quality` 那行在 2026-10-01 之前写的是"480×270 全尺寸 csim 未跑（算不完）"——
> **那是误判**：真因是片内"上一帧"缓存只有 110592 格 < 129600 像素（越界），已修，
> 见台账 **BUG-033** 与 `fpga/report/c7_sim_closed_loop_20261001.md` §2。

完整证据：`fpga/report/c6_seven_ips_csim_csynth_20260930.md`、
`fpga/report/c7_sim_closed_loop_20261001.md`。
