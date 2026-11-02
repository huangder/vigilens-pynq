# board/sim —— PL 数据通路 RTL 仿真（XSim，非上板）

> 状态：✅ 3 帧 VGA 测试图案 + 3 帧真实视频对拍 PASS（Vivado Simulator v2026.1）
> 说明文档：[`docs/46`](../docs/46_XSim_PL数据通路仿真_20261026.md)

---

## 这里有什么

| 文件 | 作用 |
|---|---|
| `tb_pl_chain.v` | 测试台：直接例化 `roi_statistic` → `rgb2gray` → `motion_quality`，不走 PS7/DMA/BD |
| `gen_sim_frames.py` | 生成 N 帧（默认 3）640×480 RGB clip + `expected.json` + `sim_config.vh`；默认测试图案，`--source` 可换真实视频/摄像头（复用 `board/video_link` 的打包与 PC 模型） |
| `check_xsim_log.py` | 解析 `SIM f=...` 行，与 `expected.json` 逐帧对拍；支持 UTF-16 LE / UTF-8 BOM |
| `run_xsim_chain.ps1` | 一键：生成帧 → `xvlog` → `xelab` → `xsim` → 对拍；纯 ASCII，避免 PS5.1 的 GBK 解码坑 |

---

## 一键跑

在仓库根、普通 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\board\sim\run_xsim_chain.ps1
```

指定输出目录与帧数：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\board\sim\run_xsim_chain.ps1 `
  -WorkDir "$env:TEMP\vigilens_xsim" -Frames 3

# 真实视频片段（前 3 帧；视频不入库）
powershell -NoProfile -ExecutionPolicy Bypass -File .\board\sim\run_xsim_chain.ps1 `
  -WorkDir "$env:TEMP\vigilens_xsim_real" -Frames 3 -Source "data\raw\blink.mp4"
```

源尺寸不是 640×480 时加 `-AllowResize`。`-Frames` 可改（已实测 `-Frames 2` 通过）；
帧数与尺寸由脚本生成的 `clip\sim_config.vh` 传给测试台。

通过判据：退出码 0，且末行是

```text
XSim 对拍：PASS（N 帧；ROI/灰度 sum/尺寸/motion/frame_id 全对）
```

产物（默认 `%TEMP%\vigilens_xsim\`）：

```text
clip\frames.bin        2,764,800 B（3 帧 640×480 RGB888）
clip\expected.json     ROI/灰度/motion 的 Python 黄金值
work\                  xvlog/xelab/xsim 工作目录
sim.log                真实仿真日志（含 SIM f=... 行）
```

---

## 实测基线（2026-10-26）

| 项 | 值 |
|---|---|
| clip sha256 | `e589dd6cc96f98132fcce62e6968fff5f755f4b5fbc8f44c81a5adeaaf36d5c2` |
| 帧数 | 3（VGA 640×480） |
| ROI count | 307200 / 帧 |
| 灰度像素 | 110592 / 帧 |
| 灰度 sum | 17055721 / 17055814 / 17055959 |
| motion（f=2/3） | pixels=1728，ratio_q16=1024 |
| 对拍 | **PASS** |
| 仿真结束 | 9,219,275 ns |
| 原始日志 | `metrics/evidence/2026-10-26_xsim_pl_chain.log` |

真实视频（`data/raw/blink.mp4` 前 3 帧，640×480）：

| 项 | 值 |
|---|---|
| clip sha256 | `82834f823bf06912bcbae9764a98f6fcf27048a5eb4c2ee0764a2542c12025cf` |
| 灰度 sum | 11474081 / 11482420 / 11478778 |
| motion（f=2/3） | pixels=2202/386，ratio_q16=1304/228 |
| 对拍 | **PASS** |
| 原始日志 | `metrics/evidence/2026-10-26_xsim_pl_chain_real_video.log` |

---

## 边界（写报告时必须带上）

- **是 RTL 仿真，不是上板**：不含 DMA、AXI-Stream 互联、PS7、DDR、JTAG 带宽。
- 只跑了 **VGA**；720p 未跑（当前 `motion_quality` 的 `prev_buf` 深度只够 384×288）。
- 真实视频只跑了 **`blink.mp4` 前 3 帧**，不是整段视频，也不是实时 30 fps。
- `motion_quality` 的 **第 1 帧无效**（`prev_buf` 上电未定义），对拍从第 2 帧开始比 motion。
- 不复现“实时 30 fps”；XSim 的 elapsed 时间不是板端性能。
