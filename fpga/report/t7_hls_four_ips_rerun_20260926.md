# T7 四个 IP 的 csim + csynth 重跑 —— 补齐 v1.2（30 Hz）的未闭合项

> **日期**：2026-09-26 · **执行**：AI（用户机器上代跑，**非** C 线本人）· **工具链**：Vitis HLS **2026.1**（Build 6493734）
> **目的**：闭合 `AGENTS.md` §7.6 与 `fpga/report/c5_fir_filter_30hz_revert.md` §7 登记的欠账 ——
> **"v1.2（帧率 45→30 Hz）后 csim / csynth / cosim 未重跑"**
> **完整执行记录**：`metrics/evidence/2026-09-26_stage1_all_tests.md` §7
> ⚠️ **本文件需要 C 线本人复核后再采信**（执行者没有硬件背景，且未跑 cosim / export）。

---

## 1. 怎么跑（含一个**必须知道的坑**）

```powershell
cd D:\Desktop\AMD\fpga
foreach ($ip in 'fir_filter','roi_statistic','rgb2gray','motion_quality') {
  $env:HLS_IP = $ip
  cmd /c "call D:\Xilinx\2026.1\Vitis\settings64.bat >nul 2>&1 && vitis-run --mode hls --tcl run_hls.tcl"
}
```

> 🔴 **坑**：在**受限沙箱**里跑会失败 —— 报的是
> `cat.exe: *** fatal error - couldn't create signal pipe, Win32 error 5` → `ERROR: [SIM 211-100] 'csim_design' failed`。
> **这不是代码问题**（与 `AGENTS.md` §6.3 记载一致），**用一次提权重试即可跑通**。
> ⏱️ 四个 IP 合计约 **6 分钟**（单个 ~90 s），并不是文档里暗示的"数十分钟"。
> ⚠️ `run_hls.tcl` 的 `HLS_EXEC` 默认 = **1（csim + csynth）**，**不含 cosim**；cosim 要 `HLS_EXEC=2` 且**用小尺寸向量**。

---

## 2. 结果：**四个 IP 全 PASS，与归档逐位一致**

| IP | 本次 csim（Layer1 + Layer2） | 本次 II | 本次 Fmax | 归档（`fpga/report/`） | 判定 |
|---|---|---|---|---|---|
| **`fir_filter`** | **8/8 + 17/17**，0 errors | 1（Depth 9） | **146.97 MHz** | 8/8 + 16/16 · 146.97 MHz（`counters_reset_v1.md`） | ✅ 一致 ※ |
| **`roi_statistic`** | **28/28 + 45/45**，0 errors | 1（Depth 2） | **138.99 MHz** | 28/28 + 45/45 · 138.99 MHz（`c3_c7_roi_statistic_v1.md`） | ✅ **逐位一致** |
| **`rgb2gray` v2** | **8/8 + 10/10**，0 errors | 1（Depth 6） | **137.46 MHz** | 8/8 + 10/10 · 137.46 MHz（`c4_rgb2gray_motion_quality_v1.md`） | ✅ **逐位一致** |
| **`motion_quality` v2** | **6/6 + 9/9**，0 errors | 1（Depth 3） | **140.05 MHz** | 6/6 + 9/9 · 140.05 MHz（同上） | ✅ **逐位一致** |

### ※ 关于 `fir_filter` 的 `17/17` 与归档 `16/16`（**不是矛盾，别当 bug 报**）

`fpga/sim/tb_fir_filter.cpp:520`~`555` 在 16 段之外还做了一项**「跨段一致性」检查**：

```
同一串数据「分段调用(reset=1,0,0)」必须与「一次调用(reset=1)」逐样本相同
通过 → 第 546 行 pass++          （输出行：[跨段一致性] 分段(reset=1,0,0) == 一次调用 ✅）
```

⇒ **17 = 16 段 + 1 项跨段一致性**。归档里的 "16/16" 早于该检查加入。
数据源本身仍是 **16 段 / 4036 样本**（`fpga/sim/data_fir/meta.txt`：`segments=16`、`total_samples=4036`、`fs=30`）。

---

## 3. 资源（以 `roi_statistic` 为例，本次实测 `csynth.rpt`）

```
+-----------------+---------+-----+--------+-------+-----+
|       Name      | BRAM_18K| DSP |   FF   |  LUT  | URAM|
+-----------------+---------+-----+--------+-------+-----+
|Total            |        0|    1|     723|   1267|    0|
+-----------------+---------+-----+--------+-------+-----+
时序：Estimated 7.195 ns（Uncertainty 2.70 ns，Wire Delay Mode ON）< 目标 10 ns
   → **Estimated Fmax = 138.99 MHz**
```

与 `c3_c7_roi_statistic_v1.md` / `m3_system_budget_v1.md` 的 **1267 / 723 / 0 / 1** **逐位一致**。

> 这一并说明：**本机环境（Vitis 2026.1）对该设计是可复现的** —— 与 09-10 的两次运行（AI 提权沙箱 + C 线本人终端）得到同一组数字。

---

## 4. 本次闭合了什么、还有什么没闭合

| 项 | 状态 |
|---|---|
| `AGENTS.md` §7.6「v1.2 后 csim/csynth **未重跑**」 | ⚠️ **部分闭合**：`fir_filter`（**v1.2 改的正是它的采样率与系数**）@30 Hz 的 **csim + csynth 已重跑并 PASS**；其余三个 IP 与帧率无关，也一并重跑通过 |
| `AGENTS.md` §6.3「受限沙箱跑不了 csim」 | ✅ **已澄清**：提权重试一次即可（`docs/16` 卡片 **DOC-002**） |
| **cosim** | ❌ **仍未跑**（`HLS_EXEC=2`；`AGENTS.md` §L5 与 `run_hls.tcl:95` 都要求**用小尺寸向量**） |
| **export**（导出 IP 供 Block Design 用） | ❌ 未跑（`HLS_EXEC=3`） |
| 上板 / 时序实测（Vivado 实现 + 布线） | ❌ **不存在**（无 bitstream，板子从未上电） |

> ⚠️ **不要引用本文件去说"上板验证过了"**：这里全部是 **csim（C 仿真）+ csynth（C 综合估算）**，
> 与"综合/实现/上板"是四件不同的事。

---

## 5. 复现与证据

- **命令**：见 §1（四个 IP 循环，`HLS_IP` 切换）
- **原始输出**：`metrics/evidence/2026-09-26_stage1_all_tests.md` §7（含每个 IP 的 `RESULT: PASS` / `Layer` 行 / `Final II` / `Estimated Fmax`）
- **构建产物**：`fpga/component_{fir_filter,roi_statistic,rgb2gray,motion_quality}/`
  —— 已被 `.gitignore:28 fpga/component_*/` 覆盖，**未污染仓库**（本次实测 `git status` 干净）
- **若要归档原始日志**：建议把 `vitis-run` 的完整 stdout 存到 `fpga/report/logs/2026-09-26_hls_four_ips_csim_csynth.log`
  （本次执行者没有代做这一步 —— 那属于 C 线的证据归档动作）

---

## 6. 诚实边界

- **执行者（AI）没有跑过硬件**，也没有 C 线的设计背景；本文件只是**把真实输出如实对照归档**。
- 本次**只跑了 csim + csynth**（`HLS_EXEC=1`）；**cosim / export 未跑**。
- `Fmax` 是 **csynth 的估算值**（Wire Delay Mode ON），**不是** Vivado 实现后的时序结果。
- 所有数字均可由 §1 的命令复现；**跑出不同结果先报告事实，不要改期望值去凑绿**（`AGENTS.md` §6.1）。
