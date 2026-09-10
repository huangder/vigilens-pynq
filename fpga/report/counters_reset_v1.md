# P0-2 报告：四个 IP 的计数器改为「随块复位清零」

> 项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
> 起因：`fpga/README.md`「待验证假设 / 已知风险」第 8 条（`static` 计数器是**上电初始化**、**复位不清零**）
> 契约：`docs/interface.md` 第 3.2 / 3.3 / 3.4 / 3.5 节（`frame_id` / `seg_id` 语义）
> 记录人：C 线（**所有数字均为真实运行输出**；涉及推断的地方已显式标注）
> 日期：2026-09-11

---

## 1. 问题是什么（为什么要改）

四个 IP 各自有一个 `static` 帧/段计数器（`roi_statistic` / `rgb2gray` / `motion_quality` 的
`frame_id`，`fir_filter` 的 `seg_id`），用于让 PS 判断"有没有丢帧/丢调用"。

它们的实现是 `static ap_uint<32> fid = 0;`。C++ 语言层面 static 对象是零初始化，
但 **HLS 把它实现为"上电初始化"（power-on initialization）**：综合会逐 bit 打印
`Register 'fid' is power-on initialization`，而**块复位（`ap_rst_n`）不会把它清零**。

**后果**：PS 如果在流程开始时按"`frame_id` 从 1 开始"做首帧同步，那么在一次
**PS 侧复位 / AXI 复位 / overlay 重新加载**之后会失配（计数器从上次的值继续），
表现为"等不到 frame_id == 1"卡死或错位对齐。这是一个**上板才会暴露**的可靠性坑。

## 2. 改了什么

给四个 IP 的计数器各加一行 pragma（写在变量声明**之后**，同坑 #18 的顺序要求）：

```cpp
static ap_uint<32> fid = 0;
#pragma HLS RESET variable=fid      // 或 fir_filter 的 variable=sid
```

| 文件 | 变量 | 改动 |
|---|---|---|
| `fpga/src/roi_statistic.cpp` | `fid` | + `#pragma HLS RESET variable=fid` |
| `fpga/src/rgb2gray.cpp` | `fid` | + `#pragma HLS RESET variable=fid` |
| `fpga/src/motion_quality.cpp` | `fid` | + `#pragma HLS RESET variable=fid` |
| `fpga/src/fir_filter.cpp` | `sid` | + `#pragma HLS RESET variable=sid` |

**刻意不改的两处**（都是有意的设计，不是遗漏）：

- `motion_quality` 的 `prev_buf`（上一帧 BRAM 缓存）：契约 3.3 节规定"**第 0 帧输出无效**"
  正是因为它上电未定义。给它加复位既昂贵（11 万 bit 的复位网络）又会让"第 0 帧无效"这条
  语义变得可疑，故保持原样。
- `fir_filter` 的 `delay_line`：由**软件 `reset` 寄存器**清零（契约 3.5 节，段级可控），
  与这里的块复位互不冲突。

## 3. 验证：`#pragma HLS RESET` 真的生效了吗

### 3.1 ⚠️ 先说一个**判据陷阱**（这次差点误判）

我先按"综合日志里 `Register 'fid' is power-on initialization` 消失 = 生效"来判断 ——
**这个判据是错的**：加了复位之后该警告**依然存在**，因为 HLS 仍然为寄存器保留了
`initial` 上电值（`#0 fid = 32'd0;`），警告只是如实描述"它有上电值"，不代表"它没有复位"。

**正确判据：到生成的 RTL 里看计数器是否处在 `ap_rst_n_inv` 分支的赋值语句中。**

### 3.2 RTL 证据（四个 IP 逐一核对）

`roi_statistic.v` 实测（`hls/syn/verilog/`，其余三个同构）：

```verilog
always @ (posedge ap_clk) begin
    if (ap_rst_n_inv == 1'b1) begin
        fid <= 32'd0;                      // ← 块复位清零（HLS RESET 生效）
    end else begin
        if ((1'b1 == ap_CS_fsm_state4) & (apdone_blk == 1'b0)) begin
            fid <= add_ln120_fu_280_p2;    // 正常自增
        end
    end
end
```

| IP | 计数器 | RTL 中位于 `ap_rst_n_inv` 分支清零 |
|---|---|---|
| `roi_statistic` | `fid` | ✅ 是 |
| `rgb2gray` | `fid` | ✅ 是 |
| `motion_quality` | `fid` | ✅ 是 |
| `fir_filter` | `sid` | ✅ 是 |

### 3.3 回归：四个 IP 的 csim 全部重跑，结果与改动前**逐项一致**

| IP | Layer 1 | Layer 2 | CSim | II |
|---|---|---|---|---|
| `roi_statistic` | 28 / 28 | **45 / 45**（透传不一致 0） | `0 errors` | Final II = 1（Depth 2） |
| `rgb2gray` | 8 / 8 | **10 / 10**（不一致像素 0） | `0 errors` | Final II = 1（Depth 6） |
| `motion_quality` | 6 / 6 | **9 / 9**（流头错误 0） | `0 errors` | Final II = 1（Depth 3） |
| `fir_filter` | 8 / 8 | **16 / 16**（样本不一致 0） | `0 errors` | Final II = 1（Depth 9） |

> 为什么不影响 csim：C 仿真不建模 `ap_rst_n` 的逐次复位，静态变量仍在进程启动时置零，
> 所以两次运行结果必须相同 —— **实测确实相同**，这也验证了"改动只影响 RTL 复位语义"。

### 3.4 资源与时序代价：**+2 LUT，Fmax 不变**

| IP | LUT 旧 → 新 | FF | BRAM18 | DSP | Estimated Fmax |
|---|---|---|---|---|---|
| `roi_statistic` | 1267 → **1269** | 723（不变） | 0 | 1 | 138.99 MHz（不变） |
| `rgb2gray` | 1410 → **1412** | 918（不变） | 0 | 5 | 137.46 MHz（不变） |
| `motion_quality` | 1501（不变） | 1158（不变） | 64（不变） | 1 | 140.05 MHz（不变） |
| `fir_filter` | 4077（不变） | 6172（不变） | 0 | 25 | 146.97 MHz（不变） |
| **合计** | 8255 → **8259**（15.5%） | 8971（8.4%） | 64（23%） | 32（14.5%） | 全部 > 100 MHz 目标 |

**结论**：用 **+2 个 LUT** 换掉了"复位后计数器不归零"这个上板级隐患，值得。

## 4. 改完之后的语义（已同步进契约）

| 时机 | 计数器行为 |
|---|---|
| FPGA 上电 / bitstream 加载 | 取上电初始化值 **0** → 首次调用返回 **1** |
| `ap_rst_n` 被拉低（PS 侧 PL 复位 / AXI 复位控制器 / overlay 重新加载触发复位） | **清零** → 复位后首次调用返回 **1** |
| 正常连续调用 | 每次调用 +1（用于判断有没有丢帧/丢调用） |

> ⚠️ **给 PS 侧的建议做法**：开始采集前**显式触发一次 PL 复位**（PYNQ 里重载 overlay 或
> 用复位控制器），然后就可以放心地按"`frame_id`/`seg_id` 从 1 开始"做同步。
> **不要**把"上电必为 0"当作唯一保证 —— 现在的保证是"**复位后必为 0**"。

## 5. 复现步骤

```bat
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga

set "HLS_IP=roi_statistic"   & vitis-run --mode hls --tcl run_hls.tcl
set "HLS_IP=rgb2gray"        & vitis-run --mode hls --tcl run_hls.tcl
set "HLS_IP=motion_quality"  & vitis-run --mode hls --tcl run_hls.tcl
set "HLS_IP=fir_filter"      & vitis-run --mode hls --tcl run_hls.tcl
```

**判据**（三条都要）：
1. 日志里 `Layer 1 / Layer 2` 通过行与 `==== RESULT: PASS ====`（四份日志见第 6 节）；
2. `component_<ip>/hls/syn/verilog/<ip>.v` 里，计数器在 `ap_rst_n_inv == 1'b1` 分支被赋 0；
3. 资源与 Fmax 与本报告第 3.4 节表格一致。

## 6. 证据文件

| 文件 | 内容 |
|---|---|
| `fpga/report/logs/2026-09-11_roi_statistic_v3_counterreset_csim_csynth.log` | 改动后 csim + csynth 原始日志 |
| `fpga/report/logs/2026-09-11_rgb2gray_v3_counterreset_csim_csynth.log` | 同上 |
| `fpga/report/logs/2026-09-11_motion_quality_v3_counterreset_csim_csynth.log` | 同上 |
| `fpga/report/logs/2026-09-11_fir_filter_v3_counterreset_csim_csynth.log` | 同上 |
| `fpga/component_<ip>/hls/syn/report/csynth.rpt` | 综合报告（本地、`.gitignore`），含资源与 pragma 表 |
| `fpga/component_<ip>/hls/syn/verilog/<ip>.v` | 生成的 RTL，第 3.2 节的复位证据（本地） |

> ⚠️ **cosim 未因本次改动重跑**：改动只增加了一条复位网络，不涉及数据通路与流水线结构；
> 按契约纪律"改数据通路必须重跑 cosim"，本次不属于该类改动。
> 若 M3 前的最终冻结版要做全量回归，四个 IP 的 cosim 一键命令见 `fpga/README.md`「跑 cosim」。

---

*本报告由 C 线维护；`HLS RESET` 的"判据陷阱"已沉淀进 `skill/fpga_hls_c_line.md`。*
