# board/ —— C 线后期：上板 / Overlay / DMA（M3 之后才需要）

> 依据《02》C8~C10 与《04》第 3 节。
> **M3 之前这个目录应该是空的** —— 这条原指"bitstream / 上板结论"。现已把**不依赖板卡的
> 脚本交付物**提前写好（纯 Python 部分可离线自检），但 **`bitstream/` 与任何"上板结论"仍为空**，
> 必须等板卡到手 + 完整权限终端跑 Vivado 才能产出。
> 接口契约见 **`docs/interface.md`**（改接口先改契约）。

## 目录

| 目录 / 文件 | 内容 | 现在能否验证 |
|---|---|---|
| `regmap.py` | 四个 IP 的 AXI-Lite 寄存器偏移（PS 侧单一来源，镜像 `docs/interface.md` §3） | ✅ 离线 `python board/regmap.py --selftest` |
| `overlay/load_overlay.py` | PYNQ Overlay 加载器 + HLS 控制/像素链/FIR 驱动 SDK | ⚠️ 仅 PC 上 py_compile；运行时依赖板卡 |
| `bringup_check.py` | C8：寄存器读写 + 计数器复位语义自检（门限 3/4） | ⚠️ 上板跑 |
| `dma_test.py` | C9：DMA 回环/缓存一致性 + 长跑不死锁 + 确定性（门限 5/6/7） | ⚠️ 上板跑 |
| `hw_sw_compare.py` | C10：PL vs 黄金参考逐点比对（容差 0，门限 8） | ⚠️ 上板跑 |
| `build_bd.tcl` | Block Design 构建脚本（Vivado batch） | ⚠️【未验证】需在 Vivado 2026.1 实跑迭代 |
| `bitstream/` | 导出的 `.bit` / `.hwh`（体积大，考虑 Releases/LFS） | ⏳ 空 |
| `overlay/` | PYNQ Overlay 封装（`.tcl`/`.xsa`、`*.py` 加载器） | ⏳ 见上 |

## 上板执行顺序（M3 板卡到手后，按序做）

```bat
:: 0) 导出四个 HLS IP 为 Vivado IP（fpga/ 目录，HLS_EXEC=3）
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga
set "HLS_EXEC=3" && set "HLS_IP=roi_statistic" && vitis-run --mode hls --tcl run_hls.tcl
:: （rgb2gray / motion_quality / fir_filter 同理）

:: 1) 建 Block Design + 综合 + 导出 bitstream（本目录）
call D:\Xilinx\2026.1\Vivado\settings64.bat
cd /d D:\Desktop\AMD\board
vivado -mode batch -source build_bd.tcl
::   产物 .bit/.hwh 拷到 board/bitstream/；真实资源/时序回填 m3_system_budget_v1.md 第 3 节

:: 2) 上板自检（PYNQ-Z2，Jupyter/命令行）
python board/bringup_check.py --bit system.bit --with-dma --frame fpga/sim/data/frames.bin
python board/dma_test.py --bit system.bit --frames 300
python board/hw_sw_compare.py --bit system.bit --data-root fpga/sim
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
- [ ] **C8** Overlay 加载器（PYNQ `Overlay()` + `register_map` 读写示例）
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

## 环境备忘

- PYNQ-Z2 / `xc7z020clg400-1`，Vitis HLS **2026.1** / Vivado **2026.1**（`D:\Xilinx\2026.1\`）。
- 板卡到手前，任何"上板结论"都不要写进报告 —— 只写"仿真结论"。
- 板卡晚到/损坏的预案见《02》第七节风险表最后一行（远程板卡或先用仿真结果撑住，M4 可延后）。

---

*本目录由 C 线维护；上板相关的踩坑请沉淀到 `skill/pynq_overlay_loader.md` 与 `skill/dma_buffer_debug.md`（见 `skill/README.md` 的待沉淀清单）。*
