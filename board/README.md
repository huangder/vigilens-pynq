# board/ —— C 线后期：上板 / 板级加载 / DMA（M3 之后才需要）

> 依据《02》C8~C10 与《04》第 3 节。
> **M3 之前这个目录应该是空的** —— 这条原指"bitstream / 上板结论"。现已把**不依赖板卡的
> 脚本交付物**提前写好（纯 Python 部分可离线自检），但 **`bitstream/` 与任何"上板结论"仍为空**，
> 必须等板卡到手 + 完整权限终端跑 Vivado 才能产出。
> 接口契约见 **`docs/interface.md`**（改接口先改契约）。

---

## 🚧 板卡变更：目标板 = Mizar-Z7020（v1.3 草案，2026-09-23，待 A/B 会签）

**实物板卡是 MicroPhase Mizar-Z7 的 7020 版，不是原定的 PYNQ-Z2。** 依据 `docs/interface.md` §0 与 §6。

### 好消息：器件相同，HLS 成果全部复用

| 项 | PYNQ-Z2（原定） | Mizar-Z7020（实物） | 判定 |
|---|---|---|---|
| 芯片 | XC7Z020-1CLG400C | **XC7Z020-1CLG400C** | ✅ **同一颗** |
| Vivado part | `xc7z020clg400-1` | `xc7z020clg400-1` | ✅ **同一个字符串，不用改** |
| PL 资源 | 85K LC / 53200 LUT / 220 DSP / 4.9 Mb BRAM | 同 | ✅ |

→ **四个 HLS IP、全部黄金参考、容差表、寄存器偏移一律不用重做。**

### 要重做的只有板级三件事

| # | 项 | 差异 | 影响 |
|---|---|---|---|
| 1 | **PS 配置** | DDR **1 GB**（PYNQ-Z2 是 512 MB） | `build_bd.tcl` 的板级 preset **已参数化并加了停止守卫**，见下 |
| 2 | **引脚约束** | PL 晶振 **50 MHz @ H16**；LED/KEY/40-pin 扩展口引脚号**全不同** | 需要新的 XDC（`board/openmv/mizar_z7_openmv_uart.xdc` 是一份最小样例） |
| 3 | **bitstream** | `bitstream/` 本就为空 | 同上，仍待产出 |

### ⚠️ 两个必须先确认的前提（否则 M3 会卡住）

1. **MicroPhase 是否为 Mizar-Z7 提供 Vivado board file？** 官方手册"Related Documents"只列了原理图/尺寸，**未见 board file**
   （同厂 Z7-Lite 有，Mizar 未确认）。
   - **没有** → `build_bd.tcl` 里 `USE_BOARD_PRESET=0`，PS7 的 **DDR 参数必须取自 MicroPhase 参考设计**。
     **不允许猜** —— 猜错会得到一块 PS 起不来的板子，症状是"完全没反应"，极易误判为板子坏了。
     脚本会在 PS7 之后**主动 `exit 1`** 并打印该做什么，这是刻意设计。
2. **板子上有没有 PYNQ 环境？** 官方手册没有 Mizar 的 PYNQ 镜像记载。
   - 板上跑 `python3 -c "import pynq; print(pynq.__version__)"` 判定。
   - **没有** → `overlay/load_overlay.py` 的 `Overlay()` 路线不可用，改走 `overlay/mmap_overlay.py`
     （普通 Linux + `/dev/mem` mmap 读 AXI-Lite 寄存器，**已实现**，含 16 项离线自检）。
     ⚠️ DMA 数据通路（`run_pixel_chain` / `run_fir_segment`）仍需 UIO + CMA，另行补。

> 📌 另注：**§3.5 的 100 MHz 目标时钟不受影响** —— 它来自 PS **FCLK0**（`build_bd.tcl` 的 `PCW_FPGA0_PERIPHERAL_FREQMHZ`），
> 由 PS 33.333 MHz 经 PLL 产生，与 PL 侧那颗 50 MHz 晶振无关。

---

## 目录

| 目录 / 文件 | 内容 | 现在能否验证 |
|---|---|---|
| `regmap.py` | 四个 IP 的 AXI-Lite 寄存器偏移（PS 侧单一来源，镜像 `docs/interface.md` §3） | ✅ 离线 `python board/regmap.py --selftest` |
| `overlay/load_overlay.py` | 板级加载器 + HLS 控制/像素链/FIR 驱动 SDK（**PYNQ 路线**，见上方 ⚠️ 第 2 条） | ⚠️ 仅 PC 上 py_compile；运行时依赖板卡 |
| `overlay/mmap_overlay.py` | 🆕 **无 PYNQ 回退路线**：`/dev/mem` mmap 读 AXI-Lite（`load_mmap`/`MmapMMIO`/`.hwh` 解析 + 地址校验） | ✅ 离线 `python board/overlay/mmap_overlay.py --selftest`（**16/16**）；⚠️ `/dev/mem` 与真实地址需上板验 |
| `bringup_check.py` | C8：寄存器读写 + 计数器复位语义自检（门限 3/4） | ⚠️ 上板跑 |
| `dma_test.py` | C9：DMA 回环/缓存一致性 + 长跑不死锁 + 确定性（门限 5/6/7） | ⚠️ 上板跑 |
| `hw_sw_compare.py` | C10：PL vs 黄金参考逐点比对（容差 0，门限 8） | ⚠️ 上板跑 |
| `imx219_driver.py` + `imx219_sccb_check.py` | 🆕 **IMX219（Camera Module 2）SCCB 驱动与三档模式表**：外部证据台账 + 真机读出模式 + 三档口径 + SCCB 线上层 + **安全阀**（寄存器表含未核实项即**拒绝下装**）。依据/未闭合项见 `docs/27` | ✅ 离线 `python board/imx219_sccb_check.py`（**110/110**，已纳入 `check_all.py`）；✅ 寄存器初始化序列已按 linux imx219.c 补齐（`docs/27` §1.4），但出图仍需硬件（B3~B6） |
| `build_bd.tcl` | Block Design 构建脚本（Vivado batch；**板级 preset 已参数化 + 有停止守卫**） | ⚠️【未验证】需在 Vivado 2026.1 实跑迭代 |
| `mipi_csi2_rx_bd.tcl` | 🆕 **MIPI CSI-2 RX 接入 system BD 的脚手架**（实例化 + 参数 + 数据通路 + 200 MHz 时钟；**未验证**） | ❌ 需 Vivado + IP 授权 + DDR 参数 + D-PHY XDC，本机验不了 |
| `bitstream/` | 导出的 `.bit` / `.hwdef`（体积大，考虑 Releases/LFS） | ⏳ 空 |
| `overlay/` | Overlay 封装（`.tcl`/`.xsa`、`*.py` 加载器） | ⏳ 见上 |
| `openmv/` | **OpenMV 首次测试包**：串口帧协议 / 上位机采集测试 / OpenMV 侧采集测试 / PL 最小回环 / 接线与分阶段测试方案 | ✅ 协议与上位机工具可离线自检（含 `offline_check.py` = 54/54，已纳入 `check_all.py`）；🧪 **OpenMV 已接入过 1 次**，但采集矩阵只回来 2 行 → `fpga/report/t6_openmv_capture_matrix_v1.md` |

## ✅ 上板前检查单（**2026-10-01 新增，按序勾**）

> 这一节是**动手前**的清单，来自 2026-09-30~10-01 那轮 C 线工作里**真实踩过的坑**。
> 目的：别把"环境/口径问题"当成"板子坏了"或"IP 算错了"，浪费一整天。
> 与之配套的**离线自检**（不需要板卡，先全跑绿再上板）：
> ```bat
> python metrics\scripts\check_all.py                 :: 期望 [工具自检] 13/13
> python board\regmap.py --selftest                   :: 档位自洽
> python board\bringup_check.py --selftest            :: 帧/灰度尺寸与单帧字节数自洽
> python board\dma_test.py --selftest                 :: 确定性测试图 + 黄金参考配套
> python board\hw_sw_compare.py --selftest            :: 黄金参考是否与当前档位配套
> ```

- [ ] **① 档位必须三处一致**（最容易错，且症状像"IP 算错"）
      `VIGILENS_TIER` 决定帧尺寸/抽取比/灰度尺寸，它必须与**实际综合进 bitstream 的那一档**一致：
      | 档位 | 测量口径 | 抽取比 | 灰度 | 用哪套黄金参考 |
      |---|---|---|---|---|
      | （不设） | 640×480 | 3/5 | 384×288 | `fpga/sim/data` + `data_motion` |
      | `720p60` | 1280×720 | 3/8 | 480×270 | `data_roi_720p` + `data_motion_720p60` |
      | `1080p45` | 1920×1080 | 1/4 | 480×270 | `data_roi_1080p` + `data_motion_1080p45` |
- [ ] **② 黄金参考要在正式目录里、且与本档配套**
      跑 `python board\hw_sw_compare.py --selftest` 确认（它会报出
      "`gray.bin` 能被本档 GRAY_PIXELS 整除"这类配错）。若目录为空，先跑
      `fpga\promote_tier_data.ps1` 把两档数据归位，或按各目录 `README.md` 里的命令重新生成。
- [ ] **③ 上板脚本从 `board/` 目录调用**
      它们 `import regmap`，必须在 `board/` 下运行（或在 `sys.path` 里放 `board/`）。
      `hw_sw_compare.py --data-root` 现在会**锚到仓库根**解析，但脚本本身仍要在 `board/`。
- [ ] **④ 跑 HLS 仿真（csim/csynth/cosim）时注意两件环境事**（2026-10-01 实测）
      1. **`vitis-run` 长作业容易被外层超时连带杀掉** —— 改动/重跑时**直接写日志文件**
         （`> x.log 2>&1`）再看文件增长；**不要**用 `| Select-Object -Last N` 看进度
         （它要等命令结束才输出，会把"正在跑十几分钟"显示成"卡住"）。
      2. **cosim 若报 `Simulation engine failed to start` / `shut down unexpectedly during
         initialization`，先试 `-mt off`**：`xelab` 默认开 22 线程，在本机会以访问违例崩。
         做法是在 `component_*/hls/sim/verilog` 下重建快照：
         `xelab <...> -mt off -s <snapshot>` 再 `xsim <snapshot> -tclbatch <tb>.tcl`。
         ⚠️ `-mt 1` 会报错，合法值是 `auto | off | >1`。
      （另：`roi_statistic` 的 cosim 测试台含 **73 个事务**，是**长作业**，别当成死锁。）
- [ ] **⑤ cosim 失败时先数 `hls/sim/tv/` 的文件数**
      为 0 = C 测试台**还没写出 HDL 测试向量**（xsim 会用 `$fscanf` 读它们，读不到就
      `File descriptor (0) passed to $fscanf is not valid`）。成功过的 IP 那里有 58~68 个文件。
- [ ] **⑥ `fpga/` 子目录在受限沙箱里会拒绝进程写入**
      症状：`Could not open file xsim.dir/.../xsim.type for writing`、
      或 `gen_motion_vectors.py` 报 `PermissionError ... rgb_frames.bin`。
      这不是代码问题 —— 需要完整权限终端，或把产物先落到可写目录再搬。
- [ ] **⑦ 相机侧的掉线是"拔线"不是"流卡"**
      跑长时间演示时若 `omv_camera_demo.ps1` 停止，先看它有没有打印
      `camera is NOT on USB any more (GetPortNames() returned EMPTY)` ——
      那是**相机从 USB 消失**，重启推流救不回来，需要重新插拔（BUG-031，已修成第 1 轮报清）。

## 上板执行顺序（M3 板卡到手后，按序做）

```bat
:: 0) 导出四个 HLS IP 为 Vivado IP（fpga/ 目录，HLS_EXEC=3）—— 换板卡**不需要**重跑这一步
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga
set "HLS_EXEC=3" && set "HLS_IP=roi_statistic" && vitis-run --mode hls --tcl run_hls.tcl
:: （rgb2gray / motion_quality / fir_filter 同理）

:: 0.5) 【新增，Mizar 专属】先做最小上板：PL 字节回环 + 1kHz 测频
::      加 board/openmv/pl_uart_echo.v + board/openmv/mizar_z7_openmv_uart.xdc 建最小工程，
::      综合→实现→生成 bitstream→Hardware Manager 经板载 USB-JTAG 下载。
::      这一步不需要 PS、不需要 DDR，用来单独验证 JTAG/时钟/引脚/IO 电平。
::      见 board/openmv/README.md 的 T3。

:: 1) 建 Block Design + 综合 + 导出 bitstream（本目录）
::    ⚠️ Mizar 的 PS7 DDR 配置未落实时，脚本会主动 exit 1（这是刻意设计，别绕过）
call D:\Xilinx\2026.1\Vivado\settings64.bat
cd /d D:\Desktop\AMD\board
vivado -mode batch -source build_bd.tcl
::   产物 .bit/.hwh 拷到 board/bitstream/；真实资源/时序回填 m3_system_budget_v1.md 第 3 节

:: 2) 上板自检（Mizar-Z7020；命令行，或 Jupyter —— 但 PYNQ 环境需先确认）
::    ⚠️ 先把档位设成与 bitstream 一致的那一档（见上面检查单 ①）：
::       不设 = 640x480/3,5；   set "VIGILENS_TIER=720p60" = 1280x720/3,8；   =1080p45 = 1920x1080/1,4
set "VIGILENS_TIER=720p60"
python board/bringup_check.py --bit system.bit --with-dma --frame fpga/sim/data_roi_720p/frames.bin
python board/dma_test.py --bit system.bit --frames 300 --golden-gray fpga/sim/data_motion_720p60/gray.bin
python board/hw_sw_compare.py --bit system.bit --data-root fpga/sim
::    📌 hw_sw_compare 的 --data-root 现在锚到仓库根；但换成 720p/1080p 档时
::       roi 与 motion 的目录名不是默认的 data/data_motion，需显式指到 data_roi_* / data_motion_*。
```

**门限对照**（`fpga/report/m3_system_budget_v1.md` 第 5 节，9 条）：
门限 3/4 → `bringup_check.py`；门限 5/6/7 → `dma_test.py`；门限 8 → `hw_sw_compare.py`；
门限 1/2（装得下/时序收敛）→ `build_bd.tcl` 综合出的 `util.rpt`/`timing.rpt`。

## 到 M3 时要交付的东西（先列清楚，避免临时抓瞎）

> 📋 **动手前先读 `fpga/report/m3_system_budget_v1.md`（P0-3）**：那里有四个 IP 的实测占用、
> 四种接入方案的取舍（**推荐"像素链 DMA + 时间序列走 AXI4-Stream FIFO"**）、
> **9 条上板验收门限**，以及时序/资源吃紧时的降级路径。
> 本目录的清单是"交付物视角"，那份文档是"预算与判据视角"，两份配套看。

- [ ] **C8** Block Design：`roi_statistic` / `motion_quality` / `fir_filter` 接入 AXI DMA，
      寄存器读写自检通过（偏移量以 `docs/interface.md` 3.2 为准，
      **注意每个输出还多一个 `*_ctrl`(ap_vld) 寄存器**，漏了会误判"值没更新"）
- [ ] **C8** **复位语义自检**（P0-2）：显式 PL 复位后 `frame_id` / `seg_id` **从 1 开始**
      （四个 IP 已加 `#pragma HLS RESET`，见 `fpga/report/counters_reset_v1.md`）——
      PS 侧因此可以放心用"id 从 1 开始"做同步，但**前提是先复位一次**
- [ ] **C8** 板级加载器（**PYNQ `Overlay()` + `register_map`** 路线；**若板上无 `pynq` 模块**则改走 `/dev/mem` mmap + UIO，见上方 ⚠️ 第 2 条）
- [ ] **C9** DMA 回环测试：短数组进出，验证**缓存一致性**（无随机错误）
- [ ] **C9** **真实 DMA 节奏下的长跑**：连续 ≥300 帧不 stall ——
      cosim 只证明了"事务级不死锁"，**这一条只能在板上验**（门限 6）
- [ ] **C10** 软硬件一致性：同一输入下 PL 结果 vs A 线软件结果的比对报告
      （`roi_statistic` 容差 **0**，`motion_quality` 容差 **0**，`fir_filter` **容差 0**）
- [ ] **C10** 软硬件对比表：按 `fpga/report/m4_baseline_v1.md` 第 5 节的模板填，
      软件侧基线用 `metrics/scripts/bench_filter_ps.py`（**同一脚本拷到板上跑**）
- [ ] C 线在 M3 前必须先跑一次 `hls_exec = 2` 的 **RTL 协同仿真**：
      csim 查不出 `hls::stream` 深度不足导致的死锁，只有 cosim 才暴露
      （见 `fpga/README.md` 风险表第 1 条与本目录上方说明）

## ⚠️ 本目录脚本的诚实标注

- `regmap.py` 的偏移量**已离线自检通过**（与契约 §3 逐一核对）。
- `bringup_check.py` / `dma_test.py` / `hw_sw_compare.py` 的**寄存器偏移与黄金格式是确定的**，
  但 DMA 握手顺序、`axi_fifo_mm_s` 寄存器语义、PL 复位源、BD 接口 pin 名标有【未验证】，
  **必须上板逐条核到通过为止**，不得当既成事实引用（《05》铁律 1）。
- `build_bd.tcl` 是**照做脚手架**，`[TODO-verify]` 处需按 Vivado 2026.1 实际 IP catalog 就地修正。
  **换到 Mizar 后新增两条硬约束**：① 板级 preset 由 `USE_BOARD_PRESET` 控制，**默认 0**；
  ② 未落实 PS7 配置时脚本会 `exit 1`（**这是刻意设计**，防止拿 PYNQ-Z2 的 DDR 预设生成一块起不来的板子）。
- 🆕 `imx219_driver.py` 现覆盖**离线能验的那一半 + 寄存器表**（几何 / 链路预算 / SCCB 线上字节序 / 安全阀 / 寄存器初始化序列）。
  寄存器初始化序列**已按 linux imx219.c 补齐**（`docs/27` §1.4）；写错不报错的风险仍在，故保留安全阀。
  引用本文件时**仍不得**表述成"IMX219 驱动已完成 / 相机能起来了"—— 出图还需硬件（B3~B6）。
- 🆕 `dma_test.py` 与 `hw_sw_compare.py` 在 **GBK 代码页的控制台**里，收尾打印 `⚠️` 会抛
  `UnicodeEncodeError` —— 而它发生在**已经打印完 `RESULT: PASS` 之后**，
  于是"人眼看着 PASS、退出码却是 1"。2026-10-02 已给两者加 `console_utf8()` 修掉
  （与 `backend/console.py` 同一做法）。**判据：这两个脚本 `--selftest` 的退出码才是结论，别只看末行。**

## 环境备忘

- **Mizar-Z7020**（MicroPhase Mizar-Z7 7020 版）/ `xc7z020clg400-1`，
  Vitis HLS **2026.1** / Vivado **2026.1**（`D:\Xilinx\2026.1\`）。
  🚧 原定 PYNQ-Z2；v1.3 草案（2026-09-23）按实物改指本板，待 A/B 会签。
- 板卡到手前，任何"上板结论"都不要写进报告 —— 只写"仿真结论"。
- 板卡晚到/损坏的预案见《02》第七节风险表最后一行（远程板卡或先用仿真结果撑住，M4 可延后）。

## ⚠️ 已知的板卡相关风险（Mizar 专属）

| 风险 | 症状 | 先查什么 |
|---|---|---|
| 板级 preset 缺失导致 DDR 配错 | 板子**完全没反应**，串口无输出 | 别急着怀疑板子坏 —— 先确认 `build_bd.tcl` 的 PS7 DDR 参数来源 |
| 板上无 PYNQ 环境 | `import pynq` → `ModuleNotFoundError` | 改走 mmap 路线（M3 待补工作） |
| 扩展口 IO 电压不是 3.3V | 外设通信乱码 / IO 损坏风险 | 板上 BANK34 的 VCCIO 由 R208/R209/R210 决定，出厂焊 R208=3.3V，**上表测一次** |
| 以太网速率手册自相矛盾 | 网络行为与预期不符 | 手册 Key Features 写 10/100、"Giga ETH" 节写 10/100/1000 → 以 `ethtool` 实测为准 |

---

*本目录由 C 线维护；上板相关的踩坑请沉淀到 `skill/pynq_overlay_loader.md` 与 `skill/dma_buffer_debug.md`（见 `skill/README.md` 的待沉淀清单）。
换到 Mizar 后新增一条**已落地**：`overlay/mmap_overlay.py`（无 PYNQ 环境 `/dev/mem` mmap 驱动 AXI-Lite 的通用做法；待沉淀成 `skill/zynq_mmap_overlay.md`）。*
