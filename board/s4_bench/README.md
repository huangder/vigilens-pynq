# `board/s4_bench/` —— 裸机上板自检（S4）的唯一可复现入口

本目录是 **M3/M4 硬件证据的产生器**。板子（Mizar-Z7020）上**没有 PYNQ**，本目录的取证路径
也不依赖板上 Linux；`board/bringup_check.py` / `dma_test.py` / `hw_sw_compare.py` 三个脚本
都 `import pynq` 跑不起来 —— 所以"PL 到底算没算对"只能由 **Cortex-A9 自己**读寄存器取证，
JTAG 只负责下载与取回结果块。这套东西就是 S4 那 6 次取证用的同一份代码。

> 🆕 **2026-10-26：新增 phase 10（视频转运上板）**。板载 USB 相机因 ULPI PHY 不应答退场，
> 像素源改为**主机采集 → `board/video_link/` 打包 → JTAG 写入 DDR → A9 逐帧驱动 PL**。
> 详见 [`docs/44`](../../docs/README.md)。phase 10 **已编译、未上板**。

## 一条命令跑一档

```bat
rem ① 第一次用（或改了 s4_bench.c）：编固件
powershell -ExecutionPolicy Bypass -File board\s4_bench\build.ps1

rem ② 640×480 档（契约 v1.1 的档位，3/5 抽取 → 384×288 灰度）
python board\s4_bench\s4_run.py recm

rem ③ 720p60 档（v1.5 草案主档，3/8 抽取 → 480×270 灰度）
python board\s4_bench\s4_720.py recm
```

> ⚠️ `build.sh` 在本机不可用：`Get-Command bash` 指向 STM32CubeCLT 的 bash，
> 不支持 `FLAGS=(...)` 数组。`build.ps1` 是等价实现（已在临时副本实测 exit 0，产出 9,354 / 9,458 B）。

判据（`recm` = 相位 7 = BUG-037 的"长度留 384 B 余量 + 帧末软件停 S2MM"接法）：

| 看什么 | 期望 |
|---|---|
| `magic=` | `0x600D600D`（固件跑到了 `finish()`） |
| `ROI/RGB/MOT` 三行 | 像素计数与当前档位一致（640 档 `307200 / 110592 / 110592`；720 档 `921600 / 129600 / 129600`） |
| `post_*_ctrl` | `0x00000004`（`ap_done=1`、`ap_idle=1`） |
| `CMP mismatch` | `0`（ARM 把硬件灰度与 `fpga/sim/data_motion*/gray.bin` 逐字节比） |
| `s2mm_stopped_cr` | 低两位 `0b10`（RS 已清、通道 Halted） |

## 视频转运（phase 10）

```bat
rem ① 主机摄像头/视频 → clip（输出在 metrics\logs\video_clips\，不入库）
python board\video_link\make_clip.py --source 0 --tier 720p --frames 10

rem ② 只生成 Tcl，不碰板子
python board\video_link\run_video.py --clip metrics\logs\video_clips\720p_0 --dry-run

rem ③ 真跑：JTAG 把 frames.bin 写 0x12000000，跑 phase 10，回传逐帧 sum/FNV
python board\video_link\run_video.py --clip metrics\logs\video_clips\720p_0
```

判据：日志里有 `vid f=… sum=… hash=0x… fid=… mot_diff=… mot_pix=… mot_ratio=…`，
且 `vid_fails=0`、`done_video`；主机 `s4_run.py` 的 `_check_video_log()` 会与
`clip_digests()` 逐帧比 sum/hash，并把 MOT 结果写成 `pl_motion.json`（第 1 帧 `valid=false`）。
`run_pipeline.py --pl-motion-json <pl_motion.json>` 可用它覆盖 `quality.motion_score`，
形成“视频 → PL 运动量 → 契约帧”的闭环。
⚠️ phase 10 **尚未上板**；`mwr -bin -file` 只确认了 xsdb 语法，吞吐与稳定性未知。

**板子不在手边时**：用 `board/video_link/run_pc_sim.py --clip <clip> --pipeline` 在 PC 上复现
phase 10 的灰度/sum/FNV/帧差语义，并自动把 `motion_ratio` 覆盖进 A 线质量分；输出带
`pc_sim` / `is_board_run=false` 标记。它不替代上板，见
[`docs/45`](../../docs/README.md)。

## 文件

| 文件 | 作用 |
|---|---|
| `s4_bench.c` | 板上取证固件（A9 直接驱动 axi_dma + roi/rgb2gray/motion），几何档位是编译期宏 |
| `start.S` | 裸机入口：设栈、清 `.bss`、跳 `main`（`-nostdlib` 没有 CRT0） |
| `link.ld` | 链接脚本，`. = 0x00100000`（xsdb 按这个地址跳） |
| `build.sh` | 编译两档固件（本机不可用，见上） |
| `build.ps1` | 等价的 Windows 编译入口，输出两档 `.bin` |
| `s4_run.py` | 驱动：`xsdb` 编程 PL → `ps7_init` → 灌帧/黄金 → 跑相位 → 取回结果块；`video` 阶段走 phase 10 |
| `s4_720.py` | 只换常量（bitstream/固件/向量/几何）的 720p60 档驱动 |
| `../video_link/` | 主机侧 clip 打包 / 校验 / 上板入口（`make_clip.py`、`run_video.py`、`video_clip.py`） |

## 四个坑（都真踩过，别再踩）

1. **`--build-id=none` 必须有**：不加的话镜像最前面多 36 字节 `.note.gnu.build-id`，
   CPU 把它当指令执行 ⇒ 数据中止到 `0x10`，结果块 `magic` 全 0（2026-10-23）。
2. **DAP sticky error 只能断电**：`TARGETS0= DAP (APB AP transaction error, DAP status 0x30000021)`
   出现后 `rst -dap` / `rst -processor` 都清不掉，只能物理断电重上电（`docs/36` §5）。
3. **HLS 重跑会毁掉导出 IP**：`component_<ip>/` 是同一个目录，重跑 csim/csynth 会把
   `hls/impl/ip/` 里的 `component.xml` 冲掉，`build_bd.tcl` 随即报"找不到导出 IP"。
   改完 HLS 一定要补一次 `vitis-run --mode hls --tcl export_ip.tcl`（**用 `vitis-run`，
   不是 `vivado -mode batch`** —— 它调的是 Vitis HLS 的 `open_component`）。
   另：**换档必须同时设 `HLS_COMPONENT`**，否则 `open_component -reset component_rgb2gray`
   会把 640 档正在用的默认组件重建成 3/8（`fpga/run_hls.tcl` 已加安全阀，设了 TIER 没设 COMPONENT 直接退出）。
4. **`stream`（phase 9）必须配 26 位长度的 bitstream**：`c_sg_length_width=23` 时单次传输上限
   8,388,607 B，装不下"300 帧连续写"，固件在**第 33 帧重新 arm 时** DMA 报 `DMAIntErr` 自停
   （`stream_ok_frames=32`、`S2MM_SR=0x5011`，见 docs/16 BUG-040）。判据看**最后几帧**，不是前几帧。

## 一个额外的诊断阶段：`stream`（phase 9）

```bat
python board\s4_bench\s4_720.py stream
```

它测的不是"对不对"（对不对比 `CMP` 已经钉住了），而是**每帧要多久**：
通道只在开头复位一次、S2MM 整段 arm 一次，300 帧跑完看 `stream_gt_ticks / 300`。
2026-10-24 用它判定 720p 到不了 60 fps（每帧 9,217,369 ticks，与逐帧停通道只差 0.0064%），
见 [`fpga/report/t11_streaming_720p_20261024.md`](../../fpga/report/t11_streaming_720p_20261024.md)。

## 产物

`*.bin` / `*.elf` **不入库**（`.gitignore` 已挡），`board/bitstream/*.bit` 同样不入库 ——
bitstream 由 `board/build_bd.tcl` 重建，日志与报告在 `fpga/report/`。
