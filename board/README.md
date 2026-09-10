# board/ —— C 线后期：上板 / Overlay / DMA（M3 之后才需要）

> 依据《02》C8~C10 与《04》第 3 节。
> **M3 之前这个目录应该是空的** —— 这不是欠账，是计划：C 线的验证手段是
> "HLS C 仿真 + Python 黄金参考"，全程不需要板卡（《02》第六节）。

## 目录

| 目录 | 放什么 |
|---|---|
| `bitstream/` | 导出的 `.bit` / `.hwh`（体积大，考虑 Releases 或 LFS） |
| `overlay/` | PYNQ Overlay 封装（`.tcl` / `.xsa` 生成脚本、`*.py` 加载器） |
| （本层） | 上板自检脚本（如 `bringup_check.py`） |

## 到 M3 时要交付的东西（先列清楚，避免临时抓瞎）

- [ ] **C8** Block Design：`roi_statistic` / `motion_quality` / `fir_filter` 接入 AXI DMA，
      寄存器读写自检通过（偏移量以 `docs/interface.md` 3.2 为准，
      **注意每个输出还多一个 `*_ctrl`(ap_vld) 寄存器**，漏了会误判"值没更新"）
- [ ] **C8** Overlay 加载器（PYNQ `Overlay()` + `register_map` 读写示例）
- [ ] **C9** DMA 回环测试：短数组进出，验证**缓存一致性**（无随机错误）
- [ ] **C10** 软硬件一致性：同一输入下 PL 结果 vs A 线软件结果的比对报告
      （`roi_statistic` 容差 **0**，`motion_quality` 容差 **0**，`fir_filter` ±1 LSB 待确认）
- [ ] C 线在 M3 前必须先跑一次 `hls_exec = 2` 的 **RTL 协同仿真**：
      csim 查不出 `hls::stream` 深度不足导致的死锁，只有 cosim 才暴露
      （见 `fpga/README.md` 风险表第 1 条与本目录上方说明）

## 环境备忘

- PYNQ-Z2 / `xc7z020clg400-1`，Vitis HLS **2026.1**（`D:\Xilinx\2026.1\Vitis`）。
- 板卡到手前，任何"上板结论"都不要写进报告 —— 只写"仿真结论"。
- 板卡晚到/损坏的预案见《02》第七节风险表最后一行（远程板卡或先用仿真结果撑住，M4 可延后）。

---

*本目录由 C 线维护；上板相关的踩坑请沉淀到 `skill/pynq_overlay_loader.md` 与 `skill/dma_buffer_debug.md`（见 `skill/README.md` 的待沉淀清单）。*
