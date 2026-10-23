# S4 上板闭环（干净 bitstream + 门限 7 跨启动）—— 2026-10-23

> 对应 `docs/38` §3.1 的 **C1 / C2 / C4 / C5**，`docs/36` §4.2 的三件收尾事。
> 板卡：**Mizar-Z7020**（`xc7z020clg400-1`），JTAG = Digilent JTAG-HS2（`210241631901`）。
> 所有数字来自本机真实运行；本文每个结论后的文件名都是**仓库里能点开的那一份**。

## 0. 结论先说

| 项 | 结果 | 一句话依据 |
|---|---|---|
| 摘取证核后的 BD | ✅ **ERROR=0 / CRITICAL_WARNING=0** | `logs/2026-10-23_s4_clean_bd_only_validate.log` |
| 重综合 + 实现 + bitstream | ✅ 综合布通，**时序全通**（WNS=0.727 ns / WHS=0.014 ns） | `logs/2026-10-23_s4_clean_bitstream_timing_impl.rpt` |
| **C1** 门限 7 跨启动确定性 | ✅ **两次各 300/300、`fails=0`、`fid 1→301`，两次 `gt_ticks` 相差 7 tick** | `logs/2026-10-23_s4_long_clean_bitstream_run1.log`、`run2.log` |
| **C2** BUG-037 接法落到脚本 | ✅ 脚本已改；两脚本 `--selftest` PASS；板上按同一配方复跑三个 IP 全 `ap_done` | `board/overlay/load_overlay.py`；`logs/2026-10-23_s4_recm_clean_bitstream.log` |
| **C4** 证据归档 | ✅ 见 §6（**没有 ILA 波形**，原因见 §6.3） | 本目录 `logs/2026-10-23_s4_*` |
| **C5** overlay 的 PS 侧交付 | 🟡 **文档已交付；"一条真跑通的命令"做不到**（W7 无 PetaLinux） | `board/overlay/README.md` |
| **C3** M4 完整闭环 | ❌ 未做（依赖 §3.2 像素源） | —— |

⚠️ **必须先读的一条诚实声明**：板上**没有 Linux/PYNQ**（`docs/36` §3：COM11 无输出），
所以本节的门限 5/6/7 **是用裸机固件 `.tmp/s4/s4_run.py` 跑的**，**不是** `board/dma_test.py` 的正文。
两者的**判据等价**（同一份 bitstream、同一条像素链、同一个"余量 + 软件停"配方、同一组寄存器读数），
但**脚本本体仍未在板上执行** —— 这一条不得被表述成"`dma_test.py` 上板跑通了"。

---

## 1. 干净 bitstream：把取证核摘掉

| 动作 | 内容 |
|---|---|
| 删 `ila_tap` | `xilinx.com:ip:ila:6.2`（`C_NUM_OF_PROBES 5` / `C_DATA_DEPTH 1024`）整段 |
| 删 `mm2s_tap` / `mm2s_axi_tap` | `axis_tap.v` / `axi_tap.v` 的 `add_files`、`create_bd_cell`、全部时钟复位与 probe 连线 |
| 恢复直连 | `M_AXIS_MM2S → conv_rgb/S_AXIS`、`M_AXI_MM2S → axi_smc_dma/S00_AXI` |
| 地址 | 删掉 `mm2s_axi_tap` 的 HP0 段与 `exclude_bd_addr_seg`；`axi_dma_0/Data_MM2S` 显式分配到 `HP0_DDR_LOWOCM`（0x00000000 / 0x40000000） |
| 白名单 | `set n_crit_allowed 1` → **0**（不再允许任何 CRITICAL WARNING） |

- 【已验证】`BD_ONLY=1`：`== validate_bd_design 完成：return=''，ERROR=0，CRITICAL_WARNING=0 ==`
  （`logs/2026-10-23_s4_clean_bd_only_validate.log`）。日志里 `mm2s_tap` / `axi_tap` / `ila_tap` 只剩注释文字，无功能引用。
- 【已验证】全量 build（synth → impl → `write_bitstream`）退出码 0；
  `All user specified timing constraints are met.`，`WNS=0.727 ns`、`WHS=0.014 ns`、`TNS=0`、`THS=0`
  （`logs/2026-10-23_s4_clean_bitstream_timing_impl.rpt` 第 139–141 行；综合/实现全过程见
  `logs/2026-10-23_s4_clean_bitstream_build.log`）。
- 【已验证】资源（`report_utilization -hierarchical`，顶层 `system_wrapper`）：
  **Total LUTs 8161**（Logic 7473 / LUTRAM 532 / SRL 156）、**FF 15084**、**RAMB36 38**、**DSP 33**、RAMB18 0。
  其中 `roi_statistic_0` 402 LUT / 765 FF，`axi_dma_0` 1300 LUT / 1863 FF。
  ⚠️ 这是**绝对数量**；本报告的 `util_impl.rpt` 只有"按层次"表，没有器件百分比列，故**不报百分比**。
- 【已验证】产物：`board/bitstream/system_wrapper.bit`，**4,045,769 B**，SHA-256
  `A6035B42C5632B58846CB21D71C21914058BE26902F2A96CD312565D6E44751E`（2026-10-23 14:56:56）。
- 【已验证】**PS 侧没有被这次改动碰到**：从新 `system.hdf` 解出的 `ps7_init.tcl` 与旧版**逐字节相同**
  （两者 SHA-256 均为 `49e8f817…b35fd0b`，26,573 B）。⇒ 摘 tap 只动了 PL 织物。

---

## 2. C2 / BUG-037：接法落到脚本

### 2.1 改了什么（`board/overlay/load_overlay.py`）

```python
GRAY_SINK_MARGIN = 384            # 一拍 64bit×16 的 SmartConnect burst 的 3 倍
out_buf = allocate(shape=(R.GRAY_PIXELS + GRAY_SINK_MARGIN,), dtype="u1")
recv.transfer(out_buf); send.transfer(in_buf)
send.wait()                       # S2MM 有余量 ⇒ 不会自己收满 ⇒ 不能 recv.wait()
all(ap_wait_done(ip, timeout_s) for ip in (roi_ip, rgb_ip, mot_ip))
stop_channel(recv)                # = 写 S2MM_CR.RS=0（PYNQ 的 _SDMAChannel.stop()）
gray_out = bytes(out_buf[:R.GRAY_PIXELS].tobytes())
```

### 2.2 为什么只改一处就够（**已核实，不是推断**）

`board/dma_test.py` 与 `board/hw_sw_compare.py` **自己都不碰 DMA**：
它们 `from overlay.load_overlay import load, run_pixel_chain(, run_fir_segment)`，
`grep -n "transfer\(|allocate|recvchannel|LENGTH"` 在两个文件里**零命中** ⇒ 配方改在 `run_pixel_chain` 里即两处生效。

### 2.3 验证

| 验什么 | 命令 | 结果 |
|---|---|---|
| 测试图/黄金档位配套 | `python board/dma_test.py --selftest` | `RESULT: PASS`，退出码 0 |
| 同上 | `python board/hw_sw_compare.py --selftest` | `RESULT: PASS`，退出码 0 |
| 板上等价复跑（裸机，同一配方） | `python .tmp/s4/s4_run.py recm` | 三个 IP `post_*_ctrl=0x00000004`（`ap_done`+`ap_idle`）、`fid=1`、灰度 `CMP mismatch=0`、`s2mm_stopped_cr=0x00010002`（RS 已清）、`s2mm_stopped_sr=0` |

原始输出：`logs/2026-10-23_s4_recm_clean_bitstream.log`：

```text
ROI 39235654 39134742 39146537 307200 1
RGB 384 288 110592 14112238 1
MOT 14112238 110313 65370 110592 1
post_roi_ctrl=0x00000004  post_rgb_ctrl=0x00000004  post_mot_ctrl=0x00000004
post_roi_fid=1  post_rgb_fid=1  post_mot_fid=1
s2mm_stopped_cr=0x00010002  s2mm_stopped_sr=0x00000000
CMP mismatch=0 first_idx=4294967295
```

> `s2mm_stopped_cr=0x00010002` = `RS=0`、`Halted=1`（bit1）⇒ 通道确实被软件停住，而**不是**"收满自然停"。

---

## 3. C1 / 门限 7：跨启动确定性

一次 `long` = 300 帧；固件每帧都做"软复位两通道 → W1C 清 SR → 启动三个 IP →
S2MM(RS→DA→LEN=110592+384) → MM2S(RS→SA→LEN=921600) → 等 MM2S IOC → 等三个 `ap_done` →
软件停 S2MM → 校验三个 `frame_id` 逐帧 +1"。

**每一次运行都重新烧了 bitstream**（`s4_run.py` 的 PREAMBLE 每次都 `fpga -file`），
判据是 **`long_fid0=1`** —— `frame_id` 只在重烧 bitstream 时归零，所以 `fid0=1` 就是"这是一次全新启动"的板上证据。

| # | 日期 | 日志文件 | `fid0` | `ok_frames` | `fails` | 末帧 `fid` | `gt_ticks` |
|---|---|---|---|---|---|---|---|
| 1 | 10-19 | （未归档，`docs/36` §2 转录） | 1 | 300 | 0 | 301 | 922,187,841 |
| 2 | 10-19 | 同上（**同一上电周期**） | 2 | 300 | 0 | 302 | 922,187,882 |
| 3 | 10-23 | （本机控制台，未归档） | 1 | 300 | 0 | 301 | 922,188,102 |
| 4 | 10-23 | （本机控制台，未归档） | 1 | 300 | 0 | 301 | 922,187,869 |
| 5 | 10-23 | `logs/2026-10-23_s4_long_clean_bitstream_run1.log` | 1 | 300 | 0 | 301 | **922,187,840** |
| 6 | 10-23 | `logs/2026-10-23_s4_long_clean_bitstream_run2.log` | 1 | 300 | 0 | 301 | **922,187,847** |

- **门限 7 的判据（跨启动）**：第 5、6 行是**两次独立启动**（各 300 帧），`ok_frames` 都 300、`fails` 都 0、
  `fid 1→301` 连续，且两次的**同量** `long_roi_sum_r=39235654`、`long_roi_cnt=307200`、
  `long_rgb_pc=110592`、`long_mot_cnt=110592` 完全一致，`long_s2mm_sr=0`（软件停后无残留错误位）。
- **`gt_ticks` 是什么**：固件取 Cortex-A9 global timer（`0xF8F00200`）在 300 帧前后的**差值**
  （`s4_bench.c`：`t1 - t0`），**不是**绝对时刻，也**不换算 fps**（计数器频率没有板上证据）。
  所以"跨启动可比"的含义是：**两次独立启动跑同一段 300 帧，耗时差 7 tick**，
  本日四次运行极差 **262 tick**（922,187,840 ~ 922,188,102，相对差 ~2.8×10⁻⁷）。
- 【已验证】两帧之间没有任何"第一次慢、第二次快"的趋势 ⇒ 不存在随启动次数漂移的隐藏状态。

> ⚠️ 不要把这六行读成"六次都跨启动"：第 1/2 行是**同一上电周期**（第 2 行 `fid0=2`），
> 只有第 3~6 行各自是独立启动。真正的跨启动对比成立在**第 5 与第 6 行**（都有日志文件）。

---

## 4. 与门限表的关系（哪些门由这次收尾覆盖）

| 门限 | 这次是否新增证据 | 说明 |
|---|---|---|
| 3 寄存器读写 | ❌ | `bringup_check.py` 正文要在 PYNQ 里跑；本次只间接覆盖（`post_*_ctrl` 全部读到 `0x4`） |
| 4 复位语义 | ❌ | 同上（`fid0=1` 是"重烧后从 1 开始"，属同类证据但**不是**该脚本） |
| 5 DMA 回环 | ✅ 间接 | `recm` 的 `CMP mismatch=0`（110592 B 与黄金 `gray.bin` 逐字节相等） |
| 6 流深度不死锁 | ✅ 间接 | 两次 `long` 各 300/300、`fails=0` |
| 7 确定性 | ✅ **直接** | §3 第 5/6 行（跨启动） |
| 8 软硬件一致 | ❌ | `hw_sw_compare.py` 正文未跑；本次只有灰度一项逐字节相等 |
| 9 数据不出 PL | ❌ | 需要综合实验与实测时延，本轮没做 |

---

## 5. C5：PS 侧交付（文档已写，"真跑通的命令"没有）

`board/overlay/README.md`（2026-10-23 新建）把四条 PS 侧路径写成一张表：
**裸机 + `xsdb`（唯一已验证）/ PYNQ `Overlay()` / 普通 Linux + `/dev/mem` / PetaLinux + `fpga_manager`**，
并给出各自的命令、判据与"待验清单"。

补齐的两条容易踩的事实：

- 【已验证】PYNQ 需要 `.hwh` 与 `.bit` 同名同目录；本仓库的 `system.hwh` 就在 `system.hdf`（zip）里，
  成员清单实测含 `system.hwh` / `ps7_init.tcl` / `hwdef.xml` / 四个 HLS 的 `*_linux.c`。
- 【已验证】`board/overlay/mmap_overlay.py --selftest` = **16/16 PASS**（纯离线，匿名 mmap 模拟）。
  但它**只覆盖 AXI-Lite**；`bringup_check.py` 的 `load()` 仍走 PYNQ，**没有**接到 mmap 分支 ⇒
  "普通 Linux 上跑寄存器 smoke"现在还做不到（已写进 README 的待补项）。

`docs/38` 给 C5 的判据是"文档 + **一条真跑通的命令**"——**后者做不了**：仓库里没有
`BOOT.BIN` / `image.ub` / `.dts`，MicroPhase 是否给 Mizar-Z7 提供 PetaLinux BSP 仍【未知】（`docs/37` R9）。

---

## 6. C4：证据清单（每个数字点到文件）

### 6.1 文件

| 文件 | 内容 |
|---|---|
| `fpga/report/logs/2026-10-23_s4_clean_bd_only_validate.log` | `BD_ONLY=1` 的 validate：ERROR=0 / CRITICAL_WARNING=0，且跳过综合 |
| `fpga/report/logs/2026-10-23_s4_clean_bitstream_build.log` | 全量 synth → impl → `write_bitstream completed successfully`（含各 IP 子 run） |
| `fpga/report/logs/2026-10-23_s4_clean_bitstream_timing_impl.rpt` | 布线后时序：`All user specified timing constraints are met.`、WNS/WHS 明细 |
| `fpga/report/logs/2026-10-23_s4_recm_clean_bitstream.log` | 单帧推荐配方：三个 IP `ap_done`、灰度逐字节相等、S2MM 被软件停 |
| `fpga/report/logs/2026-10-23_s4_long_clean_bitstream_run1.log` | 跨启动 #1：300/300 |
| `fpga/report/logs/2026-10-23_s4_long_clean_bitstream_run2.log` | 跨启动 #2：300/300 |

### 6.2 不能入库、但可复现的东西

- `board/bitstream/system_wrapper.bit`（4,045,769 B）与 `system.hdf`：**不入库**（体积；与 `docs/36` 的既有约定一致）。
  可核对的锚点是 **SHA-256 与字节数**（见 §1）。
- `.tmp/s4/`（`s4_bench.c` / `s4_run.py` / `run_*.tcl`）：一次性取证工具，按既有约定**不入库**。
  所以"S4 的 xsdb 原始 transcript"**没有**作为文件保存下来 —— 保存下来的是驱动脚本解析后的结果块
  （上面那六份 `.log`），报告引用的是这些行。

### 6.3 关于"ILA/CSV"

`docs/38` C4 原文提到"ILA/CSV"。**本次没有 ILA 证据**，原因有两层：
① 这一轮**把 ILA 连同三个 tap 一起摘了**（它本来就是取证件，留着会占 BRAM 且要允许一条 CRITICAL WARNING）；
② 上一轮的取证也不是 ILA 波形，而是**裸机固件把关键寄存器写进 DDR 结果块**再由 xsdb 取回（`res.bin`）。
⇒ 需要波形时得另开一个"带 ILA 的取证版本"，**那不属于 S4 闭环**。

---

## 7. 仍然没做的事（防"已完成幻觉"）

- ❌ **`board/dma_test.py` / `hw_sw_compare.py` / `bringup_check.py` 的正文没有在板上跑过**（板上没 Linux/PYNQ）。
  这次覆盖的是**同一接法在裸机下的等价实验**。
- ❌ **门限 3 / 4 / 8 / 9** 未新增板上证据（§4）。
- ❌ **C3（M4 完整闭环）**：需要真实像素源（§3.2 W1~W7），现在没有。
- ❌ **C5 的"一条真跑通的命令"**：等 W7 的 PetaLinux 镜像。
- ❌ `docs/38` §3.2 的 8 个工作包一个都没开始。

---

## 8. 复现命令（本机实测顺序）

```powershell
. .\env.ps1                                       # 切到仓库根 + .venv 进 PATH

# ① 干净 BD 校验（不综合）
cd board; $env:BD_ONLY="1"; vivado.bat -mode batch -source build_bd.tcl -nojournal -notrace
#    期望：ERROR=0 / CRITICAL_WARNING=0

# ② 全量重综合（约 20 min）——去掉 BD_ONLY 后重跑同一条命令
Remove-Item Env:BD_ONLY; vivado.bat -mode batch -source build_bd.tcl -nojournal -notrace
#    期望：write_bitstream completed successfully；timing_impl.rpt 里 "All user specified timing constraints are met."

# ③ 板上：单帧推荐配方 + 两次 300 帧
cd D:\Desktop\AMD
python .tmp\s4\s4_run.py recm
python .tmp\s4\s4_run.py long
python .tmp\s4\s4_run.py long
#    期望：post_*_ctrl=0x4 / CMP mismatch=0 / long_ok_frames=300 / long_fails=0 / long_fid0=1

# ④ 脚本侧离线自检（不需要板卡）
python board/dma_test.py --selftest
python board/hw_sw_compare.py --selftest
python board/overlay/mmap_overlay.py --selftest
```

---

## 变更记录

| 日期 | 改了什么 | 依据 |
|---|---|---|
| 2026-10-23 | 新建：S4 闭环（干净 bitstream + 门限 7 跨启动 + C2 脚本落地 + C4 归档 + C5 文档） | 本机实跑，日志见 §6.1 |
