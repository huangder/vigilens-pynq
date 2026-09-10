# C3 + C7 报告：`roi_statistic` v1 —— C 仿真 + C 综合

> 项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
> 契约：`docs/interface.md` v0.91 第 3.2 / 4.2 节
> 记录人：C 线（经 AI 协助整理；**所有数字均来自本文件第 2 节命令的真实运行输出**，未做任何估算或代填）
> 日期：2026-09-10

---

## 1. 结论

| 验收项 | 结论 |
|---|---|
| C 仿真（两层验证） | ✅ **PASS**，`CSim done with 0 errors` |
| 第 1 层：内嵌边界用例 | ✅ **28 / 28 通过** |
| 第 2 层：跨语言黄金参考 | ✅ **45 / 45 通过**，透传不一致像素 **0** |
| C 综合 | ✅ 无 ERROR |
| 流水线 II | ✅ **Target II = 1 → Final II = 1**（Depth = 2） |
| 时序 | ✅ **Estimated 7.195 ns** < 目标 10.00 ns（Uncertainty 2.70 ns，Wire Delay Mode ON）→ **Estimated Fmax = 138.99 MHz** |
| 循环约束 | ✅ `All loop constraints were satisfied` |
| **可复现性** | ✅ **两次独立运行逐项一致**（见 1.1 节） |

> 「C3 `roi_statistic` C 仿真通过」与「C7 综合报告归档」两项任务**达成**。

### 1.1 可复现性：两次独立运行结果一致

| 运行 | 时间 | 执行者 | csim | II | Fmax | LUT | FF | BRAM | DSP |
|---|---|---|---|---|---|---|---|---|---|
| #1 | 09-10 22:17 | AI（提权沙箱内） | 28/28 + 45/45 | 1 | 138.99 MHz | 1267 | 723 | 0 | 1 |
| #2 | 09-10 22:29 | **C 线本人（自己的终端）** | 28/28 + 45/45 | 1 | 138.99 MHz | 1267 | 723 | 0 | 1 |

**两次结果完全一致**（资源数字逐位相同），说明流程不依赖执行环境与执行者。
运行 #2 的完整日志已归档：`fpga/report/logs/2026-09-10_roi_statistic_v1_csim_csynth.log`
（SHA256 `D9B91200B9BE71BFAFCD24B51ED7EECD980C75A88C1DFFD23D1FB9E50977EAAA`）。

> ⚠️ **一个需要理解的观察**：`csynth.rpt` 的 Latency Summary 显示 `?`（未知）。
> 这是**预期的**，不是缺陷 —— 本 IP 的循环次数由**运行时**的 `width × height` 决定，
> 综合期无法静态推断总延迟。设计上我们只约束"每拍一个像素"（II=1，已达成）；
> 一帧的绝对延迟 ≈ `width × height` 拍 + 少量开销，软件侧按此计算即可。

---

## 2. 运行环境与命令

```powershell
# 终端需先加载 Vitis 环境（否则 vitis-run 不在 PATH，见 environment.md 第 1.1 节）
call D:\Xilinx\2026.1\Vitis\settings64.bat

# 1) 生成测试向量 + Python 黄金参考
python D:\Desktop\AMD\fpga\sim\gen_frames.py

# 2) C 仿真 + C 综合
cd /d D:\Desktop\AMD\fpga
vitis-run --mode hls --tcl run_hls.tcl
```

| 项 | 值 |
|---|---|
| 工具 | vitis-run v2026.1 (64-bit), SW Build 6511674, HLS Build v2026.1 6493734 |
| 器件 | `xc7z020-clg400-1`（PYNQ-Z2） |
| 目标时钟 | 10 ns（100 MHz） |
| C 仿真编译器 | clang-16（HLS 内置，非本机 g++） |
| 总耗时 | 50 s（csim 23 s + csynth 24 s） |

**测试向量指纹（可复现性凭据）**

| 文件 | SHA256 |
|---|---|
| `fpga/sim/data/golden_roi.csv` | `8C4DAEDABE87BAA5A678EAFB9627C2110C84DEA3EEDF1DFC164A577F69CE40E2` |

> 该 CSV 重跑 `gen_frames.py` 后逐字节一致（已实测两次哈希相同）。

---

## 3. C 仿真输出（原文摘录）

```text
==== tb_roi_statistic: contract docs/interface.md (tolerance = 0) ====
---- [Layer 1] embedded boundary cases (8x6) ----
  Layer 1: 28 passed, 0 failed
---- [Layer 2] cross-language golden reference ----
  data dir : D:/Desktop/AMD/fpga/sim/data
  meta     : 640x480, 5 frames, 921600 bytes/frame, 45 golden rows
  Layer 2: 45 passed, 0 failed  (pass-through mismatched pixels: 0)

==== RESULT: PASS ====
INFO [HLS SIM]: The maximum depth reached by any hls::stream() instance in the design is 307200
INFO: [SIM 211-1] CSim done with 0 errors.
```

**两点值得记录：**

1. `The maximum depth reached by any hls::stream() instance in the design is 307200`
   —— 证实了 C 仿真**不建模 FIFO 深度**（整帧 307200 像素都堆在流里也没报错）。
   ⚠️ 代价是：**深度不足导致的死锁 csim 查不出来**，只能在 cosim 暴露。M3 前必须跑一次 `hls_exec = 2`。
2. 第 2 层 45 行覆盖了 `docs/interface.md` 第 4.1 节的 9 个用例 × 5 帧，其中包含
   全零 / 全满 / 单点变化 / 随机四类边界（《skill》硬要求），以及 `x0==x1`、`x0>x1`、越界裁剪三种异常。

---

## 4. C 综合结果（原文摘录）

```text
INFO: [SCHED 204-61] Pipelining loop 'VITIS_LOOP_76_1'.
INFO: [HLS 200-1470] Pipelining result : Target II = 1, Final II = 1, Depth = 2, loop 'VITIS_LOOP_76_1'
INFO: [HLS 200-790] **** Loop Constraint Status: All loop constraints were satisfied.
INFO: [HLS 200-789] **** Estimated Fmax: 138.99 MHz
```

### 4.1 资源占用（`roi_statistic_csynth.rpt`）

| 资源 | 用量 | 可用 | 占比 |
|---|---|---|---|
| BRAM_18K | **0** | 280 | 0% |
| DSP | **1** | 220 | ~0% |
| FF | **723** | 106400 | ~0% |
| LUT | **1267** | 53200 | 2% |
| URAM | **0** | 0 | 0% |

明细：

| 实例 | 模块 | BRAM | DSP | FF | LUT | 说明 |
|---|---|---|---|---|---|---|
| `ctrl_s_axi_U` | `ctrl_s_axi` | 0 | 0 | 338 | 512 | AXI-Lite 寄存器组（11 个寄存器 + 握手） |
| `mul_16ns_16ns_32_1_1_U24` | 乘法器 | 0 | 1 | 0 | 6 | `width × height` 的动态帧长计算 |
| `grp_..._VITIS_LOOP_76_1` | 流水线主体 | 0 | 0 | 238 | 611 | 逐像素累加 + 透传 |
| **合计** | | **0** | **1** | **723** | **1267** | |

> **0 BRAM** 符合预期：本 IP 是纯流式、逐像素处理，无需帧缓存（帧缓存是 `motion_quality` 才需要的）。
> **那 1 个 DSP** 用于 `width × height`。若想把这点也省掉，可把帧长改为编译期常量或走移位，
> 但占比 ~0%，**当前不值得优化**（记录备查）。

### 4.2 接口（`csynth.rpt` → HW Interfaces）

**AXIS**（与契约 `ap_axiu<24,1,1,1>` 完全一致）

| 接口 | 方向 | TDATA | TKEEP | TSTRB | TUSER | TLAST | TID | TDEST |
|---|---|---|---|---|---|---|---|---|
| `video_in` | in | 24 | 3 | 3 | 1 | 1 | 1 | 1 |
| `video_out` | out | 24 | 3 | 3 | 1 | 1 | 1 | 1 |

**S_AXILITE**：`s_axi_ctrl`，Data Width 32，**Address Width 7**（128 B 地址空间）。

### 4.3 寄存器映射（综合实测 —— **已核对 `docs/interface.md`**）

| 偏移 | Register | 访问 | 说明 |
|---|---|---|---|
| `0x00` | `CTRL` | RW | `0=AP_START 1=AP_DONE 2=AP_IDLE 3=AP_READY 7=AUTO_RESTART 9=INTERRUPT` |
| `0x04` | `GIER` | RW | 全局中断使能（`0=Enable`） |
| `0x08` | `IP_IER` | RW | IP 中断使能 |
| `0x0c` | `IP_ISR` | RW | IP 中断状态 |
| `0x10` | `roi_x0` | W | ROI 左边界（含） |
| `0x18` | `roi_y0` | W | ROI 上边界（含） |
| `0x20` | `roi_x1` | W | ROI 右边界（不含） |
| `0x28` | `roi_y1` | W | ROI 下边界（不含） |
| `0x30` | `width` | W | 图像宽（640） |
| `0x38` | `height` | W | 图像高（480） |
| `0x40` | `sum_r` | R | ROI 内 R 累加和 |
| `0x44` | `sum_r_ctrl` | R | `bit0 = sum_r_ap_vld` ← **新增记录** |
| `0x48` | `sum_g` | R | ROI 内 G 累加和 |
| `0x4c` | `sum_g_ctrl` | R | `bit0 = sum_g_ap_vld` ← **新增记录** |
| `0x50` | `sum_b` | R | ROI 内 B 累加和 |
| `0x54` | `sum_b_ctrl` | R | `bit0 = sum_b_ap_vld` ← **新增记录** |
| `0x58` | `count` | R | ROI 内像素个数 |
| `0x5c` | `count_ctrl` | R | `bit0 = count_ap_vld` ← **新增记录** |
| `0x60` | `frame_id` | R | 已处理帧计数，从 1 开始 |
| `0x64` | `frame_id_ctrl` | R | `bit0 = frame_id_ap_vld` ← **新增记录** |

> ✅ **契约核对结果**：11 个数据寄存器的偏移与 `docs/interface.md` v0.9 第 3.2 节**逐一吻合**，无需修正。
> ⚠️ **但契约漏记了 5 个 `*_ctrl` 寄存器**：因为输出端口被综合成 `s_axilite & ap_vld`
> （原因：变量只在函数末尾赋值），HLS 为每个输出额外生成一个 +4 字节的 valid 寄存器。
> **对 M3 的影响**：PYNQ 侧 `register_map` 读 `sum_r` 时若要确认"值已刷新"，应查 `sum_r_ctrl` 的 bit0；
> 这也正好可以用来判断"新一帧统计是否已就绪"（配合 `frame_id`）。已回填进契约。

### 4.4 综合警告（逐条判读，不忽略）

| 警告 | 判读 | 处置 |
|---|---|---|
| `[RTGEN 206-101] Design contains AXI ports. Reset is fixed to synchronous and active low.` | 有 AXI 端口时 HLS 强制同步低有效复位 | 正常，Block Design 里按此接复位即可 |
| `[RTGEN 206-101] Register 'fid' is power-on initialization.` | `static ap_uint<32> fid` 是**上电初始化**，不是复位初始化 | 上电后 `frame_id` 从 0 起算（首次调用后为 1）。若要求复位后归零，需改为显式复位逻辑 —— 待 M3 按需处理 |
| `[RTGEN 206-500] ... 's_axilite & ap_vld'` | 输出带 valid 握手 | 见 4.3，已记入契约 |
| `[HLS 200-484] open_component -flow_target 已弃用` | 不影响功能 | 后续可删掉 `-flow_target vivado` |
| `[HLS 200-2149] flow_target 选项将移除` | 同上 | 同上 |
| `__GMP_LIBGMP_DLL macro redefined` | HLS 自带头文件之间的宏重定义 | 与本次设计无关，忽略 |

---

## 5. 复现步骤（陌生人照着做）

1. 装 Vitis 2026.1，`call D:\Xilinx\2026.1\Vitis\settings64.bat`
2. `python fpga/sim/gen_frames.py`（应得到 SHA256 = `8C4DAEDA...CE40E2` 的 `golden_roi.csv`）
3. `cd fpga && vitis-run --mode hls --tcl run_hls.tcl`
4. 期望：`Layer 1: 28 passed, 0 failed` + `Layer 2: 45 passed, 0 failed` + `RESULT: PASS` + csynth 无 ERROR

---

## 6. 待办（承接下一步）

- [ ] `hls_exec = 2` 跑一次 **RTL 协同仿真（cosim）**：csim 不查流深度死锁，上板前必须过这一关
- [ ] C4 `motion_quality`（需一帧 BRAM 缓存，预期 BRAM ≠ 0）
- [ ] C5 `fir_filter`
- [ ] `frame_id` 复位行为是否要改为复位归零（M3 上板再定）
- [ ] 与 A 线交叉验证：让 A 线用**他们自己的 NumPy 代码**对同一份 `frames.bin` 复算，与 `golden_roi.csv` 对拍

---

*本文件由 C 线维护。所有数字均为真实运行输出的抄录；如需更新，请附新的运行日志。*
