# `data_motion_720p60/` —— 720p60 档（v1.5 主档）的 rgb2gray / motion_quality 黄金参考

> 契约依据：`docs/interface.md` §0 的 **v1.5 草案**（测量口径档位化，**待 A/B 会签**）
> + `docs/20`（720p60 落地）。档位定案与排期见 `docs/24_C线执行记录_20260930.md` §8。
> ⚠️ **v1.5 会签前，本目录不得被 A/B 线当作已冻结接口的数据源引用。**

## 本目录应有的文件

| 文件 | 大小（实测） | 说明 |
|---|---|---|
| `rgb_frames.bin` | 27,648,000 B | 10 帧 × 1280×720×3（RGB888 输入，喂 `rgb2gray`） |
| `gray.bin` | 1,296,000 B | 10 帧 × **480×270**（3/8 抽取，= `motion_quality` 的输入 = `rgb2gray` 的期望输出） |
| `golden_motion.csv` | 559 B | `motion_quality` 黄金参考（帧 1..9） |
| `meta.txt` | 201 B | 含 `in 1280x720 -> out 480x270`、`decim=3/8 keep_x%8<3 keep_y%8<3` |

## 怎么生成（本机真跑过；确定性：同 seed 必得同产物）

```powershell
python fpga/sim/gen_motion_vectors.py --width 1280 --height 720 --decim 3 8 `
       --out-dir fpga/sim/data_motion_720p60
```

实测输出（2026-10-01）：内建 **numpy 独立对拍 PASS**（灰度/缩放/运动量，9 帧对），
`motion_pixels=129600`（= 480×270，与 `docs/interface.md` §0 的推导一致）。

## 怎么用它跑 IP 仿真

```powershell
cd fpga
$env:HLS_IP='rgb2gray'; $env:VIGILENS_TIER='720p60'
$env:ROI_DATA_DIR=(Resolve-Path sim\data_motion_720p60)
vitis-run --mode hls --tcl run_hls.tcl
```

`run_hls.tcl` 在 `VIGILENS_TIER=720p60` 时**默认**就指向本目录；
显式给 `ROI_DATA_DIR` 只是为了在数据放在别处（如仓库根临时目录）时覆盖。

## 已验证到什么程度（**别超范围引用**）

| 项 | 状态 |
|---|---|
| `rgb2gray` @720p60 | ✅ **480×270 全尺寸** csim+csynth PASS（`Layer 1 8/8`、`Layer 2 10/10`、0 不符、II=1、Fmax 140.81 MHz） |
| `motion_quality` @720p60 | ✅ csim+csynth PASS，但**比对尺寸是缩小的**（60×33）。⚠️ **480×270 全尺寸的 csim 在本会话算不完**（该 IP 要建模整帧缓存，C 仿真代价高）—— 需在完整权限终端给足时间 |
| 两档 cosim | ✅ 7 个 IP 全部 PASS、无死锁（见 `fpga/report/c6_*` §5） |

完整证据与命令：`fpga/report/c6_seven_ips_csim_csynth_20260930.md`。
