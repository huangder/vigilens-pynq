# `board/s4_bench/` —— 裸机上板自检（S4）的唯一可复现入口

本目录是 **M3/M4 硬件证据的产生器**。板子（Mizar-Z7020）上没有 Linux、没有 PYNQ、
串口也没有输出，`board/bringup_check.py` / `dma_test.py` / `hw_sw_compare.py` 三个脚本
都 `import pynq` 跑不起来 —— 所以"PL 到底算没算对"只能由 **Cortex-A9 自己**读寄存器取证，
JTAG 只负责下载与取回结果块。这套东西就是 S4 那 6 次取证用的同一份代码。

## 一条命令跑一档

```bat
rem ① 第一次用（或改了 s4_bench.c）：编固件
bash board/s4_bench/build.sh

rem ② 640×480 档（契约 v1.1 的档位，3/5 抽取 → 384×288 灰度）
python board\s4_bench\s4_run.py recm

rem ③ 720p60 档（v1.5 草案主档，3/8 抽取 → 480×270 灰度）
python board\s4_bench\s4_720.py recm
```

判据（`recm` = 相位 7 = BUG-037 的"长度留 384 B 余量 + 帧末软件停 S2MM"接法）：

| 看什么 | 期望 |
|---|---|
| `magic=` | `0x600D600D`（固件跑到了 `finish()`） |
| `ROI/RGB/MOT` 三行 | 像素计数与当前档位一致（640 档 `307200 / 110592 / 110592`；720 档 `921600 / 129600 / 129600`） |
| `post_*_ctrl` | `0x00000004`（`ap_done=1`、`ap_idle=1`） |
| `CMP mismatch` | `0`（ARM 把硬件灰度与 `fpga/sim/data_motion*/gray.bin` 逐字节比） |
| `s2mm_stopped_cr` | 低两位 `0b10`（RS 已清、通道 Halted） |

## 文件

| 文件 | 作用 |
|---|---|
| `s4_bench.c` | 板上取证固件（A9 直接驱动 axi_dma + roi/rgb2gray/motion），几何档位是编译期宏 |
| `start.S` | 裸机入口：设栈、清 `.bss`、跳 `main`（`-nostdlib` 没有 CRT0） |
| `link.ld` | 链接脚本，`. = 0x00100000`（xsdb 按这个地址跳） |
| `build.sh` | 编译两档固件 |
| `s4_run.py` | 驱动：`xsdb` 编程 PL → `ps7_init` → 灌帧/黄金 → 跑相位 → 取回结果块 |
| `s4_720.py` | 只换常量（bitstream/固件/向量/几何）的 720p60 档驱动 |

## 四个坑（都真踩过，别再踩）

1. **`--build-id=none` 必须有**：不加的话镜像最前面多 36 字节 `.note.gnu.build-id`，
   CPU 把它当指令执行 ⇒ 数据中止到 `0x10`，结果块 `magic` 全 0（2026-10-23）。
2. **DAP sticky error 只能断电**：`TARGETS0= DAP (APB AP transaction error, DAP status 0x30000021)`
   出现后 `rst -dap` / `rst -processor` 都清不掉，只能物理断电重上电（`docs/36` §5）。
3. **HLS 重跑会毁掉导出 IP**：`component_<ip>/` 是同一个目录，重跑 csim/csynth 会把
   `hls/impl/ip/` 里的 `component.xml` 冲掉，`build_bd.tcl` 随即报"找不到导出 IP"。
   改完 HLS 一定要补一次 `vitis-run --mode hls --tcl export_ip.tcl`。
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
