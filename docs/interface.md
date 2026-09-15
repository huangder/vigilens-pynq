# 接口契约（interface.md）

> **状态：🔒 v1.0（2026-09-11 冻结）→ 🚧 v1.1 草案（2026-09-13，C 线发起"全局 45 fps"）。**
> 第 0~4 节口径已定稿，C 线侧每条均有实测证据（见第 5.1 节与 `fpga/report/`）；
> **第 5.2 节的会签状态**：B 线第 3 项已于 2026-09-13 补签（见 5.2.1）；
> **A 线的 6 项已于 2026-09-15 补签**（第 1/2/4/11 项认可、第 7 与第 12 项明确"待真实视频"，证据见 5.4），
> 并已于 **2026-09-16 按 §5.3 在群里公告**——等 B/C 回"收到、不冲突"后即视为三方会签完成。
> 本文件**不代签、不假设"无异议"**：B/C 的实际回复由人类在 5.2 补记。
> v1.1 变更：§0 帧率 30→45 fps、§3.5 FIR 采样率 30→45 Hz（系数与黄金参考已重生成；csynth/cosim 已重跑，见第 6 节）。
> 起草：C 线（2026-09-10）。对应《02》任务 **C2：定义 IP 接口（AXI-Stream + 控制寄存器）**，验收标准"与 A/B 线的数据契约对齐"。
> 当前已实现并通过 csim+综合的 IP：`roi_statistic`(3.2) / `motion_quality` v2(3.3) / `rgb2gray` v2(3.4)。
> 本文件是"三线并行不干扰"的唯一技术保障。**谁改契约谁发公告**，并在第 6 节变更记录签名。

---

## 0. 全局口径（已拍板，不再讨论）

| 项 | 冻结值 | 依据 |
|---|---|---|
| 目标板卡 / 器件 | **PYNQ-Z2 / `xc7z020clg400-1`**（Zynq-7000） | 2026-09-10 确认；BASIC 免费授权覆盖 |
| 工具链 | **Vitis HLS 2026.1**，入口 `vitis-run --mode hls --tcl <脚本>` | 赛制指定；见 `fpga/report/environment.md` |
| 目标时钟 | **10 ns（100 MHz）** | `create_clock -period 10` |
| 图像尺寸 | **640 × 480** | 2026-09-10 确认 |
| 像素格式 | **RGB888**，24 bit/像素 | 与 Vitis Libraries Vision `ap_axiu<24,1,1,1>` 对齐 |
| 帧率 | **45 fps**（仅影响时间序列，IP 本身不感知帧率） | 640×480 RGB888@45fps |

### 0.1 三条必须记住的硬约定（踩坑重灾区）

1. **通道顺序是 R-G-B，不是 B-G-G 也不是 BGR。**
   字节序：`byte0 = R`、`byte1 = G`、`byte2 = B`。
   ⚠️ OpenCV 读图默认 **BGR**，A 线做黄金参考比对前必须 `cv2.cvtColor(img, cv2.COLOR_BGR2RGB)`，否则 R/B 互换 —— 这是本项目最容易发生、且最难肉眼发现的一类不一致。
2. **ROI 坐标是"半开区间"**：`x0, y0` 含、`x1, y1` 不含。
   与 NumPy / OpenCV 切片 `img[y0:y1, x0:x1]` **完全等价**，无需 ±1 心算。
3. **AXI-Stream 语义**：`TUSER = 1` 标记**一帧的第一个像素**；`TLAST = 1` 标记**一行的最后一个像素**（不是一帧！）。`TKEEP`/`TSTRB` 固定 `0b111`。

---

## 1. A → B：指标 JSON schema（WebSocket 推送）

> 摘自《02》4.1 并冻结。B 线 Mock 与 A 线真实输出**必须完全符合本 schema**。
> 本文件不重复展开，字段定义以《02》4.1 为准，此处仅锁定"不可改"的部分。

| 约束 | 内容 |
|---|---|
| 推送节奏 | 1 帧 / 秒（WebSocket） |
| `status` 枚举 | 仅 6 值：`normal` / `fatigue_risk` / `adjust_posture` / `unreliable` / `disconnected` / `done` |
| `vital.*` | 允许 `null`（前端必须优雅渲染"暂无"） |
| `ts` / `frame_id` | **必须单调递增**，供前端断点重连对齐 |
| 数组字段 | `bbox` 固定 `[x, y, w, h]`，单位像素，**左上角原点** |

---

## 2. `status` 枚举定义（6 值，三线共用）

| 值 | 触发含义 | 归属 |
|---|---|---|
| `normal` | 各项在正常范围、信号质量良好 | A 线 `decision.py` |
| `fatigue_risk` | 疲劳风险升高（PERCLOS / 长闭眼 / 哈欠） | A 线 |
| `adjust_posture` | 人脸可见率不足 / 姿态越界 | A 线 |
| `unreliable` | 信号质量门控不通过（光照 / 运动 / 波形稳定性） | A 线 |
| `disconnected` | 视频源或连接中断 | B 线兜底 |
| `done` | 测量结束 | A/B 线 |

> 阈值全部来自 `config.yaml`，**不得散落在代码里**（《04》第 6 节 DoD）。

---

## 3. FPGA IP 接口（C2 交付物）

### 3.1 通用约定

- 所有 IP 统一形如：**AXI-Stream 视频/数据进出 + 一个 `s_axilite` 控制 bundle（`bundle=ctrl`）**。
- 标量参数与输出统计量全部通过 `bundle=ctrl` 暴露为寄存器，**偏移量在本文件写死**，PS 侧按偏移访问（PYNQ `register_map` 友好）。
- 寄存器偏移为**字节偏移**；`0x00` 是 `s_axilite` 自动生成的 `ap_ctrl`（`ap_start` / `ap_done` / `ap_ready` / `ap_idle`），不手工占用。
- **一次函数调用 = 处理一帧**（`width × height` 个像素）。

### 3.2 `roi_statistic` v1 —— ✅ 已实现，且 C 仿真 + 综合均通过（2026-09-10）

> 实测结论：C 仿真 28/28（内嵌边界）+ 45/45（跨语言黄金参考）、0 errors；综合无 ERROR；
> **Final II = 1**；**Estimated Fmax = 138.99 MHz**（目标 100 MHz）；LUT 1267 / FF 723 / BRAM 0 / DSP 1。
> 完整记录见 `fpga/report/c3_c7_roi_statistic_v1.md`。

**功能**：像素流透传（pass-through），同时统计 ROI 内 R/G/B 累加和与像素计数。

```cpp
void roi_statistic(
    hls::stream<axis_pix_t> &video_in,     // axis, TDATA=24 (RGB888)
    hls::stream<axis_pix_t> &video_out,    // axis, 原样透传（含 user/last/keep/strb）
    ap_uint<11> roi_x0, ap_uint<11> roi_y0,  // 含
    ap_uint<11> roi_x1, ap_uint<11> roi_y1,  // 不含
    ap_uint<16> width,  ap_uint<16> height,
    ap_uint<32> &sum_r, ap_uint<32> &sum_g, ap_uint<32> &sum_b,
    ap_uint<32> &count,     // ROI 内像素个数
    ap_uint<32> &frame_id); // 内部自增，从 1 开始，供 PS 判断"新帧已处理完"
```

**寄存器映射（`bundle=ctrl`）**

> ✅ **本表已与 2026-09-10 的实综合结果逐行核对**（`roi_statistic_csynth.rpt` → S_AXILITE Registers）。
> `s_axi_ctrl`：Data Width 32，**Address Width 7**（128 B 地址空间）。

| 偏移 | 名称 | 访问 | 说明 |
|---|---|---|---|
| `0x00` | `CTRL` | RW | `0=AP_START 1=AP_DONE 2=AP_IDLE 3=AP_READY 7=AUTO_RESTART 9=INTERRUPT` |
| `0x04` | `GIER` | RW | 全局中断使能（`0=Enable`） |
| `0x08` | `IP_IER` | RW | IP 中断使能 |
| `0x0c` | `IP_ISR` | RW | IP 中断状态 |
| `0x10` | `roi_x0` | W | ROI 左边界，**含**（11 bit 有效） |
| `0x18` | `roi_y0` | W | ROI 上边界，**含** |
| `0x20` | `roi_x1` | W | ROI 右边界，**不含** |
| `0x28` | `roi_y1` | W | ROI 下边界，**不含** |
| `0x30` | `width` | W | 图像宽（640） |
| `0x38` | `height` | W | 图像高（480） |
| `0x40` | `sum_r` | R | ROI 内 R 累加和 |
| `0x44` | `sum_r_ctrl` | R | `bit0 = sum_r_ap_vld` |
| `0x48` | `sum_g` | R | ROI 内 G 累加和 |
| `0x4c` | `sum_g_ctrl` | R | `bit0 = sum_g_ap_vld` |
| `0x50` | `sum_b` | R | ROI 内 B 累加和 |
| `0x54` | `sum_b_ctrl` | R | `bit0 = sum_b_ap_vld` |
| `0x58` | `count` | R | ROI 内像素个数 |
| `0x5c` | `count_ctrl` | R | `bit0 = count_ap_vld` |
| `0x60` | `frame_id` | R | 已处理帧计数，从 1 开始自增 |
| `0x64` | `frame_id_ctrl` | R | `bit0 = frame_id_ap_vld` |

> ⚠️ **每个输出都多一个 `*_ctrl` 寄存器**（偏移 = 数据寄存器 + 4）。
> 原因：输出只在函数末尾赋值，HLS 综合成 `s_axilite & ap_vld`，于是为每个输出生成一个 valid 寄存器。
> **PS 侧用途**：判断"统计值是否已刷新"应查对应 `*_ctrl` 的 bit0，配合 `frame_id` 可确认"新一帧已处理完"。
> 这是 2026-09-10 实综合才发现并回填的 —— 契约漏记会导致 M3 上板时误判"值没更新"。

**数值精度与溢出分析（这是"A 线黄金参考能否逐点相等"的关键）**

| 量 | 类型 | 上限推算 | 结论 |
|---|---|---|---|
| 单通道累加 `sum_r/g/b` | `ap_uint<32>` | 640×480×255 = **78,336,000** < 2³² ≈ 4.29×10⁹ | ✅ 整帧满幅也不溢出 |
| `count` | `ap_uint<32>` | 640×480 = 307,200 < 2¹⁹ | ✅ 远有余量 |

> **全整数运算，无定点截断、无浮点。** 因此比对口径是**逐点严格相等**（不是"容差内"）。
> `sum_*` / `count` 每帧清零（不跨帧累加）；PS 须在一帧传完后读取。

**边界与异常行为（已实现，需 A 线一致）**

| 输入 | 行为 |
|---|---|
| `x0 == x1` 或 `y0 == y1` | 空 ROI，`count = 0`，`sum_* = 0` |
| `x0 > x1` 或 `y0 > y1` | 同上（无像素落入，不报错） |
| ROI 超出 `width/height` | 自然裁剪（越界范围无像素匹配），**不做 clamp**，PS 应传合法值 |
| `total = width*height` 与实际流长度不一致 | 会错位/挂死，**契约规定：流长度必须恰为 `width*height`** |

### 3.3 `motion_quality` v2 —— ✅ 已实现，csim + 综合通过

> **工作尺寸：384×288**（`rgb2gray` 缩小后的灰度，见 3.4 节）。**不是** 640×480。
>
> 实测（2026-09-10）：csim 6/6 + 9/9、`0 errors`；**Final II = 1**；Estimated **7.140 ns** < 10 ns → **140.05 MHz**；
> LUT 1501 / FF 1158 / **BRAM 64** / DSP 1。
> 完整记录见 `fpga/report/c4_rgb2gray_motion_quality_v1.md`。
>
> ✅ **BRAM 已从 256（91%）降到 64（23%）**，代价是运动检测在 384×288 上做而不是 640×480。
>
> **为什么必须降**：片内数组按 **2 的幂地址空间**分配，不是按真实深度。
> `depth=307200`（640×480）落在 2¹⁸~2¹⁹ 之间 → 按 **2¹⁹ = 524288** 实现 → **256** 个 BRAM18 = 91%，
> 而 M3 还要放 AXI DMA 与互连，只剩 24 个必然放不下。
> **根因已用三组对照实验坐实**：320×240→**64**、512×512→**128**、640×480→**256**，全部符合该规律。
> ⚠️ 由此得出一条**设计规律**：**缓存成本是台阶式的**，跨过 2 的幂就翻倍；
> 像素数 ≤ 131072（=2¹⁷）的任意尺寸都只需 **64** 个 —— 所以选了 384×288（110,592）而不是 320×240（76,800）：**同价、更清晰**。

**功能**：用片内 BRAM 缓存**上一帧**灰度，逐像素求绝对差，输出运动量与运动像素比例。

**接口**

```cpp
void motion_quality(
    hls::stream<axis_gray_t> &gray_in,     // axis, TDATA=8（384x288 灰度）
    hls::stream<axis_gray_t> &gray_out,    // axis, 原样透传
    ap_uint<16> width, ap_uint<16> height, // 384, 288
    ap_uint<8>  motion_thresh,
    ap_uint<32> &diff_total,        // Σ|cur - prev|
    ap_uint<32> &motion_pixels,     // |cur - prev| > motion_thresh 的像素数
    ap_uint<32> &motion_ratio_q16,  // (motion_pixels << 16) / count
    ap_uint<32> &count,
    ap_uint<32> &frame_id);
```

**寄存器映射（`bundle=ctrl`）**

| 偏移 | 名称 | 访问 | 说明 |
|---|---|---|---|
| `0x00`~`0x0c` | `CTRL` / `GIER` / `IP_IER` / `IP_ISR` | RW | 同 3.2 节 |
| `0x10` | `width` | W | **工作**灰度宽 = **384**（不是 640） |
| `0x18` | `height` | W | **工作**灰度高 = **288**（不是 480） |
| `0x20` | `motion_thresh` | W | 运动判定阈值（u8，暂定 16） |
| `0x28` | `diff_total` | R | 帧差总量（+ 0x2c `diff_total_ctrl`） |
| `0x30` | `motion_pixels` | R | 运动像素个数（+ 0x34 `_ctrl`） |
| `0x38` | `motion_ratio_q16` | R | 运动比例 Q16（+ 0x3c `_ctrl`） |
| `0x40` | `count` | R | 本帧像素数 = 110,592（+ 0x44 `_ctrl`） |
| `0x48` | `frame_id` | R | 已处理帧计数（+ 0x4c `_ctrl`） |

**数值精度**

| 量 | 上限（384×288 = 110,592 像素） | 结论 |
|---|---|---|
| `diff_total` | 110,592×255 = 28,200,960 < 2³² | ✅ |
| `motion_pixels` | ≤ 110,592 | ✅ |
| `motion_ratio_q16` | ≤ 65,536 | ✅ |

**三条必须记住的语义（已冻结）**

1. **阈值是"严格大于"**：`|cur − prev| > motion_thresh` 才算运动像素（**等于不算**）。
   第 1 层测试台专门用"恰好等于阈值"的用例把这个边界钉死。
2. **第 0 帧的输出无效。** `prev_buf` 是片内 BRAM，**上电后内容未定义**（C 代码刻意不给初值）。
   有效结果从第 1 帧起，PS 用 `frame_id` 判断。
   > 为什么刻意不写 `= {0}`：写了以后 **csim 会按 C++ 语义清零**，而 RTL 侧要生成巨大的 BRAM 初值，
   > 两侧行为反而不一致、且测试台会"误通过"。不给初值 → 两侧一致地"未定义" → 统一从帧 1 比对。
3. **`motion_ratio_q16` 整除向下取整**；`count == 0` 时输出 `0`。

> ⚠️ **`motion_thresh` 暂定 16，属"待确认"**：需 A 线用其 OpenCV 口径复核后冻结。
> 改这个值必须**同步重新生成黄金参考**（`gen_motion_vectors.py --motion-thresh`）。

### 3.4 `rgb2gray` v2 —— ✅ 已实现，csim + 综合通过（灰度化 + 3/5 缩放）

> 实测（2026-09-10）：csim **8/8 + 10/10**、**不一致像素 0**、`0 errors`；**Final II = 1**；
> Estimated **7.28 ns** < 10 ns → **137.46 MHz**；LUT 1410 / FF 918 / **BRAM 0** / DSP 5。
> 寄存器映射已与实综合逐行核对一致。
>
> v1（只做灰度化、不缩放）曾通过（9/9+10/10、151.98 MHz、LUT 927/DSP 3）；
> v2 增加 3/5 抽取是为了把 `motion_quality` 的片内缓存从 **256 个 BRAM18（91%）** 压到 **64 个（23%）**，
> 这是"贴着 2 的幂台阶挑尺寸"的直接应用（见 3.3 节）。

**为什么需要它**：3.3 节的 `motion_quality` 要吃 `gray_in`，但原设计中**没有任何环节产生灰度**，
而 `docs/00` §3.4 明确要求"PL 端完成 RGB/YUV 转换、缩放灰度化"。
若改由 PS 先转灰度再 DMA，PL 就只剩帧差，会被看作转发器（《01》4.2 点名的致命短板）。
故在 PL 内新增本 IP。

**接口**

```cpp
void rgb2gray(
    hls::stream<axis_pix_t>  &rgb_in,    // axis, TDATA=24 (RGB888, 640x480)
    hls::stream<axis_gray_t> &gray_out,  // axis, TDATA=8 (缩小灰度, 384x288)
    ap_uint<16> width, ap_uint<16> height,   // 输入尺寸 640, 480（须为 5 的整数倍）
    ap_uint<16> &out_width,              // 输出：384
    ap_uint<16> &out_height,             // 输出：288
    ap_uint<32> &pixel_count,            // 输出：本帧输出像素数 110,592
    ap_uint<32> &sum_gray,               // 输出：本帧灰度累加和（供光照质量评分）
    ap_uint<32> &frame_id);
```

**寄存器映射（`bundle=ctrl`）**

| 偏移 | 名称 | 访问 | 说明 |
|---|---|---|---|
| `0x00`~`0x0c` | `CTRL` / `GIER` / `IP_IER` / `IP_ISR` | RW | 同 3.2 节 |
| `0x10` | `width` | W | **输入**宽 640（必须能被 5 整除） |
| `0x18` | `height` | W | **输入**高 480（必须能被 5 整除） |
| `0x20` | `out_width` | R | 输出宽 384（+ 0x24 `_ctrl`） |
| `0x28` | `out_height` | R | 输出高 288（+ 0x2c `_ctrl`） |
| `0x30` | `pixel_count` | R | 输出像素数 110,592（+ 0x34 `_ctrl`） |
| `0x38` | `sum_gray` | R | 灰度累加和（+ 0x3c `_ctrl`） |
| `0x40` | `frame_id` | R | （+ 0x44 `_ctrl`） |

**🔒 冻结口径一：灰度公式（A 线必须实现同一式）**

```
Y = (77*R + 150*G + 29*B + 128) >> 8
```

| 性质 | 说明 |
|---|---|
| 系数来源 | BT.601 的 0.299 / 0.587 / 0.114 按 ×256 四舍五入：76.5→**77**、150.3→**150**、29.2→**29** |
| 关键优点 | 77+150+29 = **256 恰好** → 纯白映到 255、纯黑映到 0，**无需裁剪**；16 bit 内最大 255×256+128 = **65,408**（不溢出） |
| 参考值（可人工核对） | 纯红 **77**、纯绿 **149**、纯蓝 **29**、白 **255**、黑 **0** |
| 溢出 | `sum_gray` 上限 110,592×255 = 28,200,960 < 2³² ✅ |

> ⚠️ **本式与"按浮点系数四舍五入"的实现会差 1 LSB**：例如纯红，0.299×255 = 76.245 → 76，而本式给 **77**。
> 这是**口径选择，不是 bug**。**A 线必须在 NumPy 里实现本式**（3 行代码）；
> 若坚持用 `cv2.cvtColor`，须先用 4.4 节的对拍脚本确认差异 —— 否则 motion_quality 的黄金参考会系统性偏移。

**🔒 冻结口径二：缩放（3/5 相位抽取）**

```
保留 (x % 5 < 3) 且 (y % 5 < 3) 的像素      // 每 5 列取 3 列、每 5 行取 3 行
640 / 5 * 3 = 384 列      480 / 5 * 3 = 288 行
```

| 性质 | 说明 |
|---|---|
| 目的 | 把 `motion_quality` 的工作尺寸压到像素数 ≤ 2¹⁷，使其片内缓存只用 **64** 个 BRAM18（见 3.3 节） |
| 方法 | **点采样**（保留原始灰度值），**不是**块均值 → A 线用 `np.ix_` 可逐位镜像 |
| 前置条件 | 输入宽高必须是 **5 的整数倍**（640、480 满足） |
| 参考值（可人工核对） | 单帧输出 110,592 个灰度；`x∈{0,1,2,5,6,7,…}`、`y∈{0,1,2,5,6,7,…}` |
| 实现顺序 | **先按行挑、再在保留的行里按列挑**（与 Python `full[:, ys][:, :, xs]` 一致） |

> ⚠️ **已知取舍**：点采样会保留混叠，运动检测可能因此更"敏感"（把噪声判成运动）。
> 若实测发现运动量偏噪，可改为块均值（黄金参考需同步改），或退回 320×240 的 2×2 均值。
> 这条属**待观察**，需要 A 线接上真实视频后才能判断。

### 3.5 `fir_filter` v1 —— ✅ 已定稿（C5 实测回填）

> 实现：`fpga/src/fir_filter.cpp`；系数：`fpga/src/fir_coeffs_q15.h`（**自动生成，唯一来源**）
> 证据：`fpga/report/c5_fir_filter_v1.md`、`fpga/report/logs/2026-09-11_fir_filter_v1_{csim_csynth,cosim}.log`

**滤波器规格（冻结）**

| 接口要素 | 冻结值 |
|---|---|
| 输入 / 输出 | AXI-Stream，`TDATA=16`（有符号 `int16`，Q1.15 归一化样本；**量化口径见 4.6 节**） |
| 采样率 | **45 Hz**（与图像 45 fps 同频） |
| 阶数 | **N = 63**（I 型线性相位，奇数阶 → 群延迟 **31** 样本 = 688.9 ms） |
| 系数 | `int16 Q15`，**偶对称** `h[k] == h[N-1-k]`，由 `fpga/sim/design_fir_coeffs.py` 生成 |
| 设计法 | Hamming 窗理想带通；**通带口径 = −6 dB 点 0.70 / 3.50 Hz**（实测 0.710 / 3.495 Hz） |
| 归一化 | 通带峰值增益 = 1.0（Q15 = 32768）；`Σh = 5710`（DC −15.18 dB）、`Σ|h| = 47582` |
| 实测频率响应 | −3 dB 点 0.998 / 3.206 Hz；1.0~3.0 Hz 起伏 2.99 dB；0.35 Hz −11.5 dB；8 Hz −86.3 dB |
| 运算 | `int32` 精确累加 → **一次算术右移 15** → **饱和**到 `int16` |
| 舍入约定 | **先累加、后一次移位**（不做逐步舍入、不做四舍五入）；C 的 `>>` 与 Python 的 `>>` 对负数**都是向下取整**，**禁止**写成 `/32768`（整数除法是向零取整，负数差 1） |
| 溢出安全性 | `|acc| ≤ 32768·Σ|h| = 1,559,166,976 < 2³¹−1`（生成器与主机端模型均断言） |
| 比对容差 | **0（逐样本严格相等）** —— 原定 ±1 LSB，C5 实测**不需要**（见 4.3） |
| 资源 / 时序（实测） | LUT 4043 / FF 6174 / **BRAM 0** / DSP 26；**Final II = 1**；Estimated Fmax **154.38 MHz**（@45 fps，2026-09-13 实测） |

**段（segment）语义 —— 时间序列 IP 的"帧"**

| 项 | 规定 |
|---|---|
| 一次调用 | 处理 `n_samples` 个样本，输入输出**一一对应、同序、等长** |
| 启动瞬态 | **保留**（不丢弃前 N−1 个样本）—— 让"输入第 i 个 ↔ 输出第 i 个"最直观，且可逐样本严格比对 |
| 延迟线状态 | `static`，**段间保持**（连续流语义）；`reset=1` 的段之间互不影响，`reset=0` 的段承接上一段状态 |
| `reset` | **必做**：延迟线是 static，HLS 实现为**上电初始化**、**复位不清零**（skill 坑 #17）。PS 与测试台都必须用 `reset=1` 建立确定性起点，**不得依赖"上电是 0"** |
| 饱和 | 移位结果 > 32767 → 32767；< −32768 → −32768；饱和样本数计入 `saturation_count`（PS 可当"输入过载"的质量判据） |
| AXI-Stream 侧信道 | `TUSER=1` = **一次调用的第一个样本**；`TLAST=1` = **一次调用的最后一个样本**；`TKEEP/TSTRB = 0b11`；四个侧信道位原样透传到输出 |

> ⚠️ 本 IP 的 `TUSER/TLAST` 语义与第 7 节的**图像 IP**（帧首/行末）**不同**，别照抄。

**寄存器映射（已与 `csynth.rpt` 的 `* S_AXILITE Registers` 表逐行核对）**

| 偏移 | 名称 | 方向 | 说明 |
|---|---|---|---|
| 0x00 / 0x04 / 0x08 / 0x0c | `CTRL` / `GIER` / `IP_IER` / `IP_ISR` | RW | HLS 自动（本 IP 未用中断） |
| 0x10 | `n_samples` | W | 本段样本数（1..65535） |
| 0x18 | `reset` | W | 1 = 读取样本前清空延迟线与饱和计数 |
| 0x20 | `out_count` | R | 本段输出样本数（== `n_samples`） |
| 0x24 | `out_count_ctrl` | R | `ap_vld`（坑 #16：每个输出后面多一个 valid 寄存器） |
| 0x28 | `saturation_count` | R | 本段饱和样本数 |
| 0x2c | `saturation_count_ctrl` | R | `ap_vld` |
| 0x30 | `seg_id` | R | **自 IP 上电以来的调用序号**（每次调用 +1，从 1 开始；power-on 初始化，见风险表第 8 条） |
| 0x34 | `seg_id_ctrl` | R | `ap_vld` |

**🔒 已知限制（契约级）：63 阶在 45 fps 下做不了呼吸带**

Hamming 过渡带宽 `Δf ≈ 3.3/(2πN)·fs` = **0.375 Hz**（N=63、fs=45），而呼吸带 0.1~0.5 Hz 总宽只有 0.4 Hz。
实测按 0.1/0.5 Hz 设计时 −3 dB 边沿在 ±0.3 Hz 内**根本找不到**（通带被过渡带吃掉）；
压到 0.15 Hz 过渡带需 **N ≳ 158**、0.10 Hz 需 **N ≳ 236**，均超 `N ≤ 64`。

> **结论**：呼吸带必须由 **PS 侧先降采样**（如降到 2 Hz 采样，同阶数过渡带降至 0.017 Hz）
> 再用同名 IP 的第二组系数，或对呼吸单独增加阶数。**不要**指望 45 fps 下的 63 阶同时覆盖两个带。

### 3.6 三条线的对接点

```
A 线 (Python)                                    C 线 (FPGA/HLS)
───────────────────────────────────────────────  ────────────────────────────────────────────
同一帧 RGB888 (R,G,B) 640x480  →  frames.bin   →  tb_roi_statistic → roi_statistic
同一 ROI 坐标 (半开区间)                          →  roi_x0/y0/x1/y1 寄存器
NumPy 整数累加 → golden_roi.csv                 →  sum_r/g/b, count  → 逐点比对

同一帧 RGB888 640x480  →  rgb_frames.bin       →  rgb2gray
  口径一 灰度 Y=(77R+150G+29B+128)>>8
  口径二 缩放 保留 x%5<3 且 y%5<3
Y 与缩放后的灰度 →  gray.bin 384x288（黄金）    →  out_width/out_height, sum_gray
                                                →  motion_quality（工作尺寸 384x288）
                                                   diff_total / motion_pixels / motion_ratio_q16
                                                →  golden_motion.csv → 逐帧比对

时间序列（如 ROI 均值序列）→ series.bin（int16 LE） →  fir_filter
  口径：acc = Σ h[k]*x[n-k] (int32 精确)
        y   = sat16(acc >> 15)   （一次算术右移，对负数向下取整；禁止 /32768）
  段语义：n_samples + reset（reset=1 清延迟线）；输出与输入一一对应、含瞬态
  Python 精确整数参考 → golden_fir_out.csv → **逐样本严格相等（容差 0）**
```

### 3.7 IP 计数器的复位语义（四个 IP 统一，2026-09-11 起）

四个 IP 各有一个 `static` 计数器：`roi_statistic` / `rgb2gray` / `motion_quality` 的 `frame_id`、
`fir_filter` 的 `seg_id`。它的用途是让 PS 判断**有没有丢帧 / 丢调用**。冻结语义如下：

| 时机 | 计数器行为 |
|---|---|
| FPGA 上电 / bitstream 加载 | 取**上电初始化值 0** → 首次调用返回 **1** |
| **`ap_rst_n` 被拉低**（PS 侧 PL 复位 / AXI 复位控制器 / overlay 重新加载触发复位） | **清零** → 复位后首次调用返回 **1** |
| 连续正常调用 | 每次调用 **+1** |

- 实现：四个 IP 的计数器声明后各加一行 `#pragma HLS RESET variable=<计数器>`；
  已在生成的 RTL 中逐一点核对（计数器位于 `ap_rst_n_inv == 1'b1` 分支并被赋 0）。
  证据：`fpga/report/counters_reset_v1.md`（含 csim 回归、资源代价 **+2 LUT**、Fmax 不变）。
- ⚠️ **给 PS 侧的做法**：开始采集前**显式触发一次 PL 复位**，然后即可按
  "`frame_id` / `seg_id` 从 1 开始"做同步。**保证是"复位后必为 0"**，而不是"上电必为 0"。
- ⚠️ 不受影响的两处（有意为之）：`motion_quality` 的 `prev_buf` 仍**不初始化**
  （"第 0 帧输出无效"这条语义不变，见 3.3 节）；`fir_filter` 的 `delay_line` 仍由
  **软件 `reset` 寄存器**清零（段级可控，见 3.5 节）。

---

## 4. 黄金参考比对口径（A ↔ C）

> 核心思想（《02》4.2）：**C 线验证"硬件算得对"，靠的是这份软件黄金参考，而不是等板卡。**

### 4.1 测试向量文件格式（冻结）

存放目录：`fpga/sim/data/`（`frames.bin` 为生成物，见 `.gitignore`；`golden_roi.csv` 入库）

**`frames.bin`** —— 无头部裸二进制

| 项 | 规定 |
|---|---|
| 类型 | `uint8` |
| 布局 | 帧序 → 行序 → 列序 → 通道序：`pixel(frame, y, x) = bytes[ frame*W*H*3 + 3*(y*W + x) + c ]`，`c = 0→R, 1→G, 2→B` |
| 单帧字节数 | `W*H*3`（640×480 → 921,600 B） |
| 内容（5 帧，覆盖《skill》要求的四类边界） | `0` 随机 / `1` 全零 / `2` 全满(255) / `3` 单点变化 / `4` 随机（另一 seed） |
| 生成器 | `fpga/sim/gen_frames.py`（确定性，同 seed 必得同文件） |

**`golden_roi.csv`** —— 黄金参考结果

```
# meta width=640 height=480 frames=5 seed=20260910 bytes=921600
case,frame,x0,y0,x1,y1,sum_r,sum_g,sum_b,count
full_frame,0,0,0,640,480,<...>
```

用例集合（9 个 × 5 帧 = 45 行）：

| `case` | ROI | 目的 |
|---|---|---|
| `full_frame` | 0,0,640,480 | 全图基准 |
| `center_roi` | 160,120,480,360 | 常规 ROI |
| `top_left_1x1` | 0,0,1,1 | 左上角单像素（下标 0 的 off-by-one） |
| `bottom_right_1x1` | 639,479,640,480 | 右下角单像素（末元素 off-by-one） |
| `single_pixel` | 320,240,321,241 | 中间单像素 |
| `empty_roi` | 100,100,100,200 | `x0==x1` → 空 |
| `inverted_roi` | 400,300,200,100 | `x0>x1` → 空（健壮性） |
| `odd_offset` | 3,5,637,477 | 奇数偏移，专治 ±1 错误 |
| `oversized_roi` | 600,460,700,500 | 越界 → 自然裁剪（`count = (640-600)*(480-460) = 800`） |

### 4.2 `roi_statistic` 比对口径（冻结）

| 项 | 规定 |
|---|---|
| 比对方式 | C testbench 读 `frames.bin` 送入 IP，与 `golden_roi.csv` **逐行**比对 |
| 容差 | **0**（全整数运算，必须逐点严格相等） |
| 同时校验 | `video_out` 必须与 `video_in` **逐字节一致**（透传不损坏数据，为 C9 回环测试打基础） |
| 通过判据 | 所有用例 `sum_r/g/b`、`count` 全等 + 透传一致 + 无 ERROR |
| 复现要求 | 固定 seed；报告文件名带版本号（见 `fpga/report/`） |

### 4.3 容差表

| IP | 容差 | 理由 |
|---|---|---|
| `roi_statistic` | **0（严格相等）** | 纯整数累加，无舍入空间 |
| `rgb2gray` | **0（严格相等）** | 纯整数定点，逐像素逐字节相等 |
| `motion_quality` | **0（严格相等）** | 纯整数差值 + 整数比例 |
| `fir_filter` | **0（严格相等）** | ✅ 2026-09-11 C5 实测：全整数运算 + **单一舍入点**（`>>15`）+ 可证不溢出 → 两侧逐样本相等（3940/3940，cosim 侧另在小向量上复现）。原定 ±1 LSB **不再需要** |

### 4.4 灰度 + 缩放口径对拍（A 线**必须**先跑这个）

`rgb2gray` 的灰度式与缩放规则都是**自定义冻结口径**：

- 灰度与"按浮点系数四舍五入"相差 1 LSB（例：纯红，浮点式 0.299×255 = 76.245 → **76**，本设计给 **77**）；
- 缩放是 **3/5 相位点采样**，不是 OpenCV 的默认缩放（`cv2.resize` 做的是插值，结果必然不同）。

所以 A 线**不能**想当然地用 `cv2.cvtColor` + `cv2.resize` 当黄金参考，必须先对拍：

```python
# A 线在 NumPy 里实现冻结口径（唯一权威）
import numpy as np
W, H = 640, 480
img = np.fromfile(r"fpga/sim/data_motion/rgb_frames.bin", dtype=np.uint8) \
        .reshape(-1, H, W, 3)[0].astype(np.int32)          # 取第 0 帧（RGB 顺序！）

# 口径一：灰度  Y = (77R + 150G + 29B + 128) >> 8
full = ((77 * img[..., 0] + 150 * img[..., 1] + 29 * img[..., 2] + 128) >> 8)

# 口径二：3/5 相位抽取，先挑行再挑列（与硬件一致）
ys = [y for y in range(H) if y % 5 < 3]      # 288 个
xs = [x for x in range(W) if x % 5 < 3]      # 384 个
mine = full[np.ix_(ys, xs)].astype(np.uint8)

# 与 C 线产出的黄金灰度逐像素比对（这条必须全 0）
gold = np.fromfile(r"fpga/sim/data_motion/gray.bin", dtype=np.uint8) \
         .reshape(-1, len(ys), len(xs))[0]
assert mine.shape == gold.shape, f"尺寸不一致 {mine.shape} vs {gold.shape}"
assert np.array_equal(mine, gold), "冻结算式与 C 线黄金参考不一致"
print("✅ 灰度 + 缩放口径一致；输出尺寸", mine.shape)

# 可选：看 OpenCV 差多少（若装了 cv2）—— 预期会有差异，仅供了解，不要据此改硬件
try:
    import cv2
    mp_gray = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    mp_resized = cv2.resize(mp_gray, (len(xs), len(ys)), interpolation=cv2.INTER_NEAREST)
    d1 = np.abs(mp_gray[np.ix_(ys, xs)].astype(np.int32) - mine.astype(np.int32))
    d2 = np.abs(mp_resized.astype(np.int32) - mine.astype(np.int32))
    print("cv2 灰度(同点) vs 本式 最大差:", d1.max(), " 不同占比:", (d1 > 0).mean())
    print("cv2 resize     vs 本式 最大差:", d2.max(), " 不同占比:", (d2 > 0).mean())
except ImportError:
    print("cv2 未安装，跳过 OpenCV 对照")
```

**判据**：
- 上面两条 `assert` **必须通过** —— 这是"两边算的是同一个东西"的硬证据；
- 若 OpenCV 对照显示有差异（灰度很可能差 ±1 LSB；`cv2.resize` 因为做插值会差得更多），
  **一律以本节冻结口径为准**，A 线在黄金参考里改用本式，不要为了迁就 OpenCV 去改硬件口径；
- 对拍通过后，A 线即可用 `mine` 这套口径作用于**真实视频**，与硬件逐帧比对。

### 4.5 `fir_filter` 测试向量与黄金参考（冻结）

存放目录：`fpga/sim/data_fir/`（生成器 `fpga/sim/gen_fir_vectors.py`）

| 文件 | 入库 | 规定 |
|---|---|---|
| `series.bin` | ❌（`.gitignore`，由 seed 重建） | 输入样本流：`int16` **小端**，按段顺序拼接；段划分见 `golden_fir.csv` |
| `golden_fir.csv` | ✅ | 每段一行：`case,seg_id,n_samples,reset,sat_count,out_count`（`#` 开头为注释） |
| `golden_fir_out.csv` | ✅ | **逐样本**期望输出：`seg_id,index,y`（4036 行）；用文本而非二进制，便于人工评审与 diff |
| `meta.txt` | ✅ | `fs / taps / coeff_shift / band_hz / band_convention / segments / total_samples / series_bytes / scale / seed` |

**冻结的段集合（16 段）**：`impulse`（冲激 → 系数/群延迟/对称性）、`dc_pos` / `dc_neg`（DC 抑制）、
`nyquist_alt`、`sine_{0p2,0p8,1p5,3p2,8}hz`（漂移带 / 通带下沿 / 通带中心 / 通带上沿 / 高阻带）、
`square_1hz_full`（通带内强方波；@30fps 饱和、@45fps 不饱和）、`saturate_matched`（**匹配系数符号的满幅输入，sat_count > 0，专测饱和分支**）、`sine_small_amp`（**不饱和**，sat_count == 0）、
`random_full`（通用覆盖）、`split_part1/2/3`（**段间状态保持**：同一串数据切 3 段、reset = 1,0,0，
其结果必须与 `random_full` 一次调用**逐样本相同**）。

> ⚠️ **系数只有一处来源**：`gen_fir_vectors.py` **解析 `fpga/src/fir_coeffs_q15.h`** 拿系数，
> 不另抄一份。改系数 = 改 `design_fir_coeffs.py` 重新生成头文件 → **必须重跑 `gen_fir_vectors.py`**，
> 否则测试台会以 `meta.taps/shift 与工程不一致` 硬失败（刻意如此，防"改了一边"）。

> ⚠️ `meta.txt` 里的 `taps` / `coeff_shift` 与工程不一致时，测试台**硬失败**而不是跳过 ——
> 与 4.4 节"口径必须对拍"同一条纪律。

### 4.6 时间序列输入（Q1.15）的量化口径（冻结）—— A 线必须实现

> **为什么单列一节**：3.5 节只说 `fir_filter` 收 `int16 Q1.15 归一化样本`，但**没说**
> `roi_statistic` 的 ROI 累加怎么变成 Q1.15。这一步不定义清楚，A 线就无法唯一确定
> `fir_filter` 的黄金参考输入 —— 也就是"A ↔ C 的时间序列链路"接不上。
> 本节把这一步冻结成**整数、逐位可复现**的一步。

**第 1 步：先形成一个实数序列 `mean[n]`（来源由 A 线定）**

- 推荐（**不强制**）：`mean[n] = sum_g[n] / count[n]`，即 `roi_statistic` 绿通道的 ROI 均值。
  > 依据：RGB 摄像头做 rPPG 时绿通道信噪比最好，这是常见做法。**"推荐"不等于"已验证"** ——
  > A 线若改用其他通道或 CHROM/POS 之类的组合，改的只是本节第 1 步，
  > 但**第 2 步的量化口径不变**，且必须在你们的黄金参考里给出所用的同一序列。

**第 2 步：量化到 Q1.15（**唯一强制、必须逐位一致**的一步）**

```
整数版（唯一权威，int64 中间量）：
  num[n] = 256 * sum_c[n] - 32768 * count[n]
  q[n]   = clip( rha_div(num[n], count[n]), -32768, 32767 )
  rha_div(a, b) = a >= 0 ?  (2a + b) // (2b)      // b > 0；四舍五入远离零
                          : -((-2a + b) // (2b))

等价实数式（仅供核对）：
  q[n] = round_half_away( (mean[n] - 128) * 256 )
```

| 性质 | 说明 |
|---|---|
| 中心 | `mean = 128`（中灰）→ **q = 0**。带通因此工作在零点附近，全部 Q1.15 动态范围留给交流分量 |
| 满量程 | Q1.15 的 ±1 对应 ROI 均值的 ±128 灰阶 |
| **永不裁剪**（对合法输入） | `mean ∈ [0, 255]` ⇒ `(mean-128)*256 ∈ [-32768, 32512]`，正好落在 int16 内；`clip` 只是防御（例如 A 线喂了别的序列） |
| 量化台阶 | 1 灰阶 = **256** 个 q 单位；往返误差 ≤ 1/512 灰阶 |
| 溢出 | `256 * sum_c ≤ 256 × 78,336,000 ≈ 2.0×10¹⁰`，**必须用 int64**（int32 会溢出） |
| **空 ROI** | `count == 0` 的帧**必须丢弃**（不产生样本）。**不得**填 0、也不得用上一帧值顶替；`n_samples` 只统计有效样本 |

**手工可核对表**（实现后请先对这几个值）：

| 场景 | `count` | `sum_c` | `mean` | **q（期望）** |
|---|---|---|---|---|
| 全黑 | 1 | 0 | 0 | **−32768** |
| 中灰 | 1 | 128 | 128 | **0** |
| 全白 | 1 | 255 | 255 | **32512** |
| 1 灰阶 | 1 | 1 | 1 | **−32512** |
| 中灰 + 半阶 | 2 | 257 | 128.5 | **128** |
| 半阶以下 | 2 | 255 | 127.5 | **−128** |

**参考实现（已验证，唯一权威实现）**：`fpga/sim/q15_ref.py`

```bash
python fpga/sim/q15_ref.py --selftest
#   P1 合法均值 0~255 半灰阶步进：不裁剪、单调、端点 -32768 ~ 32512
#   P2 mean=128 -> 0；P3 往返误差 0.000000 gray；P4 count==0 被拒绝
#   P5 整数版 vs 浮点版：200000 组随机数不一致 0 处   -> PASS

# A 线把自己算出的逐帧 count + sum_r/sum_g/sum_b 丢进来，直接得到 q 序列：
python fpga/sim/q15_ref.py --sums-csv my_sums.csv --channel g --out-csv q15.csv
```

**A 线要做的 4 件事**：

1. 用**本节整数式**（或调用 `q15_ref.py`）生成 q 序列，**不要**用 `cv2`/`scipy` 的现成缩放；
2. 把它喂给你们的 PS 侧参考滤波（系数取 `fpga/src/fir_coeffs_q15.h`，运算按 3.5 节：
   精确累加 → 一次 `>>15` → 饱和；**禁止**写 `/32768`）；
3. 在真实视频上报告 `mean_g` 的实际摆动幅度（若 `mean` 频繁贴 0 或 255，说明光照条件越界，
   属于**质量门控该拦的情况**，而不是改量化口径的理由）；
4. 黄金参考里同时保留"量化前序列"与"q 序列"，便于定位是量化错还是滤波错。

---

## 5. 会签状态（v1.0 冻结）

### 5.1 已核销项（C 线自行闭环，均附实测证据）

| # | 事项 | 结论与证据 |
|---|---|---|
| 5 | `motion_thresh` 默认 16 | ✅ 2026-09-10 定稿；⚠️ **A 线若按 OpenCV 口径复核后要改，必须重生成黄金参考**（改则走第 7 节） |
| 6 | `motion_quality` 的 **BRAM 91%** 决策 | ✅ 2026-09-10 已决策并实现：`rgb2gray` 加 3/5 缩放，工作尺寸 384×288，BRAM **256（91%）→ 64（23%）**。证据 `fpga/report/c4_rgb2gray_motion_quality_v1.md` |
| 8 | `fir_filter` 的阶数/系数 | ✅ 2026-09-11 C5 定稿：N=63（I 型、群延迟 31）、Hamming 窗、**−6 dB = 0.70/3.50 Hz**；csim 8/8+16/16、**容差 0**、cosim PASS、II=1、Fmax 146.97 MHz（以上 @30 fps）。**2026-09-13 v1.1 起改 @45 fps**：系数与黄金参考已重生成；csim **8/8+17/17**、csynth **II=1 / Fmax 154.38 MHz / LUT 4043 / FF 6174 / BRAM 0 / DSP 26**、cosim **PASS**（2026-09-13 实测）。证据 `fpga/report/c5_fir_filter_v1.md`、`fpga/report/c5_fir_filter_45hz_reserved.md` |
| 9 | `s_axilite` 的 `offset=` 是否按字节生效 | ✅ 2026-09-10 实综合逐行核对吻合，并补记 5 个 `*_ctrl`（ap_vld）寄存器 |
| 10 | 版本号升级 | ✅ **2026-09-11 由 C 线发起升为 v1.0**，说明见 5.3 |

### 5.2 A/B 表态项与会签状态（**不阻塞 C 线冻结，但必须补签**）

| # | 对象 | 事项 | 建议的确认方式（用代码确认比口头认可硬） | 会签状态 |
|---|---|---|---|---|
| 1 | **A 线** | `frames.bin` 的 **RGB 通道顺序**与**半开区间 ROI** 是否认可？（逐点相等的唯一前提） | 用你们自己的 NumPy 代码对 `fpga/sim/data/frames.bin` 复算一遍 | ✅ **2026-09-15 A 线补签：认可**（证据见 5.4） |
| 2 | **A 线** | `roi_statistic` 的整型累加是否就是 rPPG / 质量评分要用的那份时间序列口径？ | 同上：与你们自己那套实现对拍 | ✅ **2026-09-15 A 线补签：认可**（证据见 5.4） |
| 3 | **B 线** | 第 1 节 JSON schema 与第 2 节 6 值枚举是否已全部支持？ | 跑 `node frontend/mock.js --selftest`（应 `ok:true`） | ✅ **2026-09-13 B 线补签：认可**（证据见 5.2.1） |
| 4 | **A 线** | 按 **4.4 节对拍脚本**验证灰度口径一致（`assert` 必须通过） | 直接跑该节脚本 | ✅ **2026-09-15 A 线补签：认可**（证据见 5.4） |
| 7 | **A 线** | 3/5 **点采样**缩放的混叠是否让你们那边的运动量偏噪？（偏噪 → 改块均值或退回 320×240，**会改黄金参考**） | 接真实视频后给结论 | ⏳ **2026-09-15 A 线：待真实视频**（点采样口径本身已逐像素全等；混叠影响判定需要真实视频，见 5.4） |
| 11 | **A 线** | 4.6 节的 **Q1.15 量化口径**与"**`count==0` 的帧一律丢弃**"是否认可？ | 跑 `python fpga/sim/q15_ref.py --selftest`，再用 `--sums-csv` 与自己实现对拍 | ✅ **2026-09-15 A 线补签：认可**（证据见 5.4） |
| 12 | **A 线** | **全局 45 fps 切换**（v1.1）：§0 帧率 30→45、§3.5 FIR 采样率 30→45；系数与黄金参考已重生成，csynth/cosim 已重跑 | 确认采集/关键点/指标链路能跑 45 fps，rPPG q 序列按 45 Hz 生成并与新黄金参考对拍 | 🟡 **2026-09-15 A 线：链路侧与时间序列侧已核**；**rPPG 端到端待真实视频**（P3 已完成链路与合成信号验证，见 5.4） |

> ⚠️ **本表的状态是事实描述**：截至 2026-09-15，**B 线第 3 项已补签**（5.2.1）；
> **A 线第 1、2、4、11 项已补签认可**，第 7 项明确"待真实视频"，第 12 项链路侧与时间序列侧已核（见 5.4）。
> **第 5 项（`motion_thresh` 取值）不在本表编号内**，A 线已给出实测差异与建议，需三方拍板 —— 见 5.4 末节。
>
> 📣 **2026-09-16：A 线已按 §5.3 在群里公告**（逐条结论 + `motion_thresh` 差异表，内容见 5.4）。
> 按 §5.3，收到另两位"收到、不冲突"后本表即视为三方会签完成。
> **B/C 的实际回复请由人类在此补记 —— 本文件不代签。**

#### 5.2.1 补签记录

**第 3 项 · B 线 · 2026-09-13** —— 结论：**认可**。第 1 节 JSON schema 与第 2 节 6 值枚举**已全部支持**，证据如下（本机真实运行，非推测）。

环境：Windows + Node v24.16.0 + `.venv`（Python 3.11.9）。命令均在仓库根执行，`python` 指 `.venv\Scripts\python.exe`。

| 验的是什么 | 命令 | 真实输出 |
|---|---|---|
| JS 侧契约自检（6 态帧合法 + 5 个坏帧必须被抓） | `node frontend/mock.js --selftest` | `{"checked": 11, "failures": [], "ok": true}` |
| 跨语言一致性（JS 产出 → Python 侧校验） | `node frontend/mock.js --limit 6 > metrics/evidence/js_frames.jsonl` + `python metrics/scripts/check_frontend_contract.py metrics/evidence/js_frames.jsonl` | `状态覆盖：['adjust_posture','disconnected','done','fatigue_risk','normal','unreliable']（契约共 6 值）`；`跨语言契约检查：通过 —— 全部 6 帧在 Python 侧同样合法。` |
| 前端接线（`app.js` 引用的 DOM id 是否都存在） | `python metrics/scripts/check_frontend_wiring.py` | `index.html 中 id 数量: 26`；`前端接线检查：通过 —— 所有静态 id 引用都能在 index.html 找到对应元素。` |
| 契约一致性测试（49 项） | `python -m pytest -q` | `49 passed in 1.28s`（⚠️ 本机存在环境性抖动，见下方注） |

可核对之处：

- `frontend/mock.js` 的 `STATUS_VALUES` = 6 值，与第 2 节枚举逐字一致；`TOP_KEYS` = 第 1 节的 9 个顶层字段（`ts` / `frame_id` / `face` / `behavior` / `vital` / `quality` / `status` / `advice` / `reason`）。
- `frontend/app.js` 对 6 种 `status` 各有独立配色与文案，`disconnected` 另有"真断开"复现路径（收到数据超时 → 合成断连帧）。
- `frontend/mock.js` 的校验器与 `backend/contract.py` 同规则，且**逐帧校验**：不合法帧不渲染、并在事件日志里给出第一条错误原因。

> ⚠️ **关于 `pytest` 的一条环境事实（2026-09-13 实测，非推测）**：在本机（工作区 `D:\coding\FPGA`）连续 5 次
> `python -m pytest -q` 的结果为 `2 failed / 1 failed / 49 passed / 1 failed / 49 passed`，
> 失败点均为 `backend/storage.py:63` 的 `os.replace` 报 `WinError 5`。定性证据：
> ① 一个**不 import 本项目任何代码**的裸 Python 探针在同一目录做 `mkstemp + os.replace` 500 次，失败 7/500；
> 同一探针在 `C:\...\Temp` 下 0/500。② 干净检出（`git worktree` @ `de65a0f`，位于 C 盘）跑同一套测试 `49 passed`。
> **结论：这是本机工作区被索引/监视进程瞬时抓句柄导致的环境抖动，不是契约问题、也不是项目代码问题。**
> 影响：本机"提交前自检 5 条全绿"会随机变红，别去改期望值凑绿；是否在 `write_snapshot` 中加有限重试由 **A 线**决定。

本次补签**只登记会签结论，未改动第 0~4 节任何口径**（v1.1 的 45 fps 变更是 C 线另一提交所致，与本补签无关）。


### 5.3 v1.0 冻结说明（2026-09-11 由 C 线发起）

- **冻结范围**：本文件 **第 0~4 节**的全部口径 —— 全局约定、A→B schema 与状态枚举、
  四个 IP 的接口/寄存器/运算/量化口径、黄金参考比对口径与容差。
  （第 1/2 节的 schema 与枚举自基线以来**一字未变**，已核对；本次升级只涉及第 3/4 节的口径定稿。）
- **冻结依据**：C 线侧所有条目**均有实测证据**（见 5.1 与 `fpga/report/`），
  C 线承诺**不再单方面改动**这些口径。
- **A/B 要做的**：
  - **认可** → 在群公告回复"收到、不冲突"，由任一人勾选 5.2 并补签；
  - **不认可** → **按第 7 节流程提变更**：先改本文件 + 同步两套实现（`backend/contract.py`
    / `frontend/mock.js`）+ 跑跨语言检查 + 公告，走完出 **v1.1**。
- **冻结后 C 线的行为**：第 3/4 节的任何口径改动都必须走上述流程；
  **不允许"代码悄悄改、文档不动"**。这条纪律比版本号本身重要。
- **同步记录**：本次升级已把 `backend/contract.py` 的 `CONTRACT_VERSION` 从 `v0.91`
  同步为 `v1.0`（此前落后 4 个版本），并跑通 5 项仓库自检。
  `frontend/mock.js` 无版本常量，**无需改动**（其校验逻辑未被本次升级触及）。
- ⚠️ 若 A 线在实现黄金参考时发现 4.6 节的量化口径不可用（例如需要留余量、或先去均值），
  **这是合法的变更理由**，但要走流程 —— **不要在两边各实现一套**。

### 5.4 A 线的补签记录（2026-09-15）

**A 线 · 2026-09-15** —— 核实方式：**用 A 线自己的 NumPy 实现复算，与 C 线黄金参考逐点比对**，
全部**容差 0**。一键复跑：`python metrics/scripts/check_a_line_p1_all.py`；
证据：`metrics/evidence/2026-09-15_a_line_p1_golden_checks.json`（该文件是上述命令的真实产物，**未手工编辑**）。

环境：Windows + Python 3.12.10 + numpy 1.26.4（项目 `.venv`）。命令均在仓库根执行。
测试向量由固定 seed 重建（`gen_frames.py` / `gen_motion_vectors.py` / `gen_fir_vectors.py`），
重建后 `golden_*.csv` 与入库版本逐字节相同。

| 验的是什么 | 命令 | 真实输出 | 结论 |
|---|---|---|---|
| 第 1、2 项：RGB 顺序 + 半开区间 ROI + `roi_statistic` 累加口径 | `python metrics/scripts/check_roi_golden.py` | `检测通过：45 行逐行全等（容差 0）`（9 用例 × 5 帧） | **认可** |
| 第 4 项：灰度 + 3/5 缩放口径（§4.4） | `python metrics/scripts/check_gray_formula.py` | `检测通过：10 帧全部逐像素全等（容差 0）` | **认可** |
| 第 11 项：Q1.15 量化 + `count==0` 丢弃（§4.6） | `python metrics/scripts/check_q15_golden.py` | `35 个真实样本 + 393222 个扫描组合全等`；`count==0 被拒绝` | **认可** |
| 第 12 项（时间序列侧）：FIR 逐样本（§4.5） | `python metrics/scripts/check_fir_golden.py` | `16 段 / 4036 个样本逐样本全等`（含饱和段 `sat_count=5`、跨段状态承接） | **已核** |
| 第 12 项（链路侧）：45 fps | `python backend/run_pipeline.py --source synthetic --pattern blink --seconds 30 --stub` | `处理帧数 1350`（= 45 × 30）；真实视频路径 `45.0 fps`、`landmark_source: mediapipe` | **已核** |

**第 4 项附带实测对照**（§4.4 要求"不要想当然用 OpenCV"）：

| 对照 | 最大差 | 不同像素占比 |
|---|---|---|
| `cv2.cvtColor(RGB2GRAY)` + 同点抽取 | **1 LSB** | **13.4%** |
| `cv2.cvtColor` + `cv2.resize(..., INTER_NEAREST)` | **241** | **61.1%** |

**第 7 项（3/5 点采样混叠）—— A 线表态：待真实视频。**
点采样口径本身已逐像素全等（上表第 4 项），但"混叠是否让运动量偏噪"必须用真实视频判定，
而 `data/raw/` 目前为空。4 段标准视频录制后 A 线给出结论；若需改块均值或退回 320×240，按本文件第 7 节走变更。

**第 12 项（全局 45 fps）—— A 线表态：链路侧与时间序列侧均已核，rPPG 端到端待 P3。**
A 线一侧已把帧率全部改为从 `config.yaml` 读取（含 `backend/config.py` 的 FALLBACK、
`capture.py` / `face_landmark.py` 的默认值、以及测试用例），代码中不再存在硬编码帧率；
并用"把 `fps_nominal` 临时改为 60 再跑全套测试"验证过改动**与具体帧率无关**（60 fps 下同样 49 passed）。
尚缺的是 **rPPG 的 q 序列端到端**（ROI → Q1.15 → FIR → BPM），它需要真实视频才能产出，计划在 P3/P4。

**关于第 5 项（`motion_thresh` 取 16 还是 25）—— A 线给出实测差异，请三方拍板。**
§5.1 已由 C 线核销第 5 项（"暂定 16"），但附了"A 线若按 OpenCV 口径复核后要改，必须重生成黄金参考"的条件。
A 线本次复核的实测差异：

| 口径 | 黄金参考 / C 线 | A 线现状 | 实测差异 |
|---|---|---|---|
| `motion_thresh` | **16**（`data_motion/meta.txt`） | **25**（`config.yaml`） | 9 个帧对中 **1 对**不一致，`motion_pixels` 偏 **698** |
| 计算分辨率 | **384×288**（`rgb2gray` 缩小后） | 640×480 | `count` **307200 vs 110592**，数值**不可比**，**9/9** 不一致 |
| 灰度公式 | 冻结式 `(77R+150G+29B+128)>>8` | `cv2.cvtColor` | 最大差 1 LSB，13.4% 像素不同 |

**A 线的建议（仅供参考，不构成代签）**：以黄金参考的 **16** 为准。
理由是改 A 线一侧的成本远低于重生成 C 线已验证过的黄金参考（csim/csynth/cosim 都要重跑）。
但**这三条必须"三者取其一"地统一**，且 A 线 `quality.py` 需同步改成
"先走冻结式灰度 + 3/5 抽取，再算帧差"，否则 A↔C 的运动量永远对不上。
最终取值建议与 4 段标准视频的阈值标定一并定，避免二次返工。

> ⚠️ **本节只登记 A 线的核实结论，未改动本文件第 0~4 节的任何口径，也未改动 `config.yaml`。**
> 群公告与三方确认仍由人类完成；本表状态如有异议，按第 7 节流程提出。
>
> 📣 **2026-09-16 补充：本节结论已在群里公告**（逐条结论 + 上表的实测差异），
> 请 B/C 回"收到、不冲突"或在群里提异议；实际回复由人类在 §5.2 补记。

---

## 6. 变更记录

| 日期 | 版本 | 谁 | 改动 |
|---|---|---|---|
| 2026-09-10 | v0.9 | C 线 | 初建草案：全局口径（PYNQ-Z2 / 640×480 RGB888）+ `roi_statistic` v1 接口与寄存器映射冻结 + 测试向量格式 + 比对口径；`motion_quality` / `fir_filter` 仅冻结形状 |
| 2026-09-10 | v0.91 | C 线 | **实综合回填**：寄存器表补入 `CTRL/GIER/IP_IER/IP_ISR` 与 5 个 `*_ctrl`（ap_vld）寄存器；3.2 节补实测结论（II=1、Fmax 138.99 MHz、LUT 1267/FF 723/BRAM 0/DSP 1）；§5 第 5 项核销 |
| 2026-09-10 | v0.92 | C 线 | **C4 落地**：3.3 节 `motion_quality` 定稿（寄存器表、三条语义、实测 II=1/Fmax 140.05 MHz/**BRAM 256 = 91% 风险**）；**新增 3.4 节 `rgb2gray`**（含冻结灰度式 `Y=(77R+150G+29B+128)>>8` 与实测数据），`fir_filter` 顺延为 3.5、对接点为 3.6；**新增 4.4 节灰度口径对拍脚本**；4.3 容差表补 `rgb2gray`；§5 待确认项扩到 9 条 |
| 2026-09-10 | v0.93 | C 线 | **BRAM 问题闭环**：用三组对照实验坐实"片内数组按 **2 的幂地址空间**分配"（320×240→64、512×512→128、640×480→256）；据此决策并实现 —— 3.4 节升 **`rgb2gray` v2**（新增冻结口径二：3/5 相位点采样 `x%5<3 && y%5<3`，640×480→384×288，实测 8/8+10/10、Fmax 137.46 MHz、BRAM 0/DSP 5），3.3 节升 **`motion_quality` v2**（工作尺寸 384×288，**BRAM 64 = 23%**，Fmax 140.05 MHz）；4.4 节对拍脚本补入缩放口径；§5 第 6 项核销、新增第 7 项（混叠待观察） |
| 2026-09-11 | v0.94 | C 线 | **C5 落地**：3.5 节 `fir_filter` **定稿**（N=63 / 群延迟 31 / Hamming 窗 / 通带口径 **−6 dB = 0.70~3.50 Hz** / 归一化增益 1.0 / `Σh=897` / `Σ|h|=55073` / 实测 −3dB 0.895~3.305 Hz / 寄存器表含 3 个 `*_ctrl` / 段语义与 `reset` 必做 / TUSER=段首、TLAST=段末 **与图像 IP 不同**）；**新增契约级限制**：63 阶在 30 fps 下做不了呼吸带；**新增 4.5 节** fir 向量与黄金参考格式（含"系数只有一处来源"的硬约束）；**4.3 容差表 `fir_filter` 从 ±1 LSB 收紧为 0**；3.6 节对接点补时间序列链路；§5 第 8 项核销。证据 `fpga/report/c5_fir_filter_v1.md` |
| 2026-09-11 | v0.95 | C 线 | **P0 收口（两项）**。① **新增 4.6 节**：`fir_filter` 输入序列的 **Q1.15 量化口径**（整数权威式 `q = rha_div(256*sum_c − 32768*count, count)`、中心 128→0、合法均值**永不裁剪**、`count==0` 的帧**必须丢弃**、手工核对表、参考实现 `fpga/sim/q15_ref.py`）—— 补上"A↔C 时间序列链路"此前缺口；§5 新增第 11 项请 A 线表态。② **新增 3.7 节**：四个 IP 的**计数器复位语义统一**（加 `#pragma HLS RESET`，`ap_rst_n` 拉低即清零），关闭风险表第 8 条；四个 IP csim 回归全过、代价 **+2 LUT**、Fmax 不变，证据 `fpga/report/counters_reset_v1.md` |
| 2026-09-11 | **v1.0** | C 线 | 🔒 **冻结**。第 5 节由"待确认项"重构为**会签状态**：5.1 已核销 5 项（附证据）、5.2 待 A/B 表态 6 项（**如实记录"尚未收到书面表态"，不代签**）、5.3 冻结说明（冻结范围/依据/A-B 的两种动作/冻结后纪律）。本次升级**只涉及第 3/4 节口径的定稿**：已核对第 1/2 节（A→B schema 与 6 值枚举）自基线 `79c57a9` 以来**一字未变**，故 `frontend/mock.js` 无需改动；`backend/contract.py` 的 `CONTRACT_VERSION` 由 `v0.91` **同步为 `v1.0`**（此前落后 4 个版本）。同步更新 `README.md` / `fpga/README.md` / `docs/07` 中的版本引用。5 项仓库自检全绿 |
| 2026-09-13 | v1.0（**补签，无口径改动**） | B 线（huangder） | **§5.2 第 3 项由 B 线补签：认可** —— 第 1 节 JSON schema 与第 2 节 6 值枚举已全部支持（`frontend/mock.js` 的 `STATUS_VALUES` / `TOP_KEYS` 一致，`frontend/app.js` 六态各有配色与文案）。第 5.2 节表格加"会签状态"列、新增 5.2.1 补签记录（含 4 条真实命令与输出）；文件头与 §5.2 的会签描述同步为"A 线 6 项待表态"。**第 0~4 节口径一字未改，版本维持 v1.0** |
| 2026-09-13 | **v1.1** | C 线 | 🚧 **草案（待 A/B 会签）—— 全局 45 fps 切换**。① §0 帧率 30→45 fps；② §3.5 `fir_filter` 采样率 30→45 Hz：群延迟 31 样本 = 688.9 ms、−3 dB 0.998/3.206 Hz、−6 dB 0.710/3.495 Hz、`Σh=5710`（DC −15.18 dB）、`Σ|h|=47582`、`|acc|≤1,559,166,976`、实测频率响应（0.35 Hz −11.5 dB、8 Hz −86.3 dB）、已知限制过渡带 0.250→0.375 Hz、N≳105→158 / 158→236。③ 系数 `fir_coeffs_q15.h` 用 `design_fir_coeffs.py --fs 45 --d 0.0` 重生成，黄金参考 `data_fir/` 重生成（numpy 对拍 3940/3940 逐样本相等）。✅ **csynth/cosim 已于 2026-09-13 在 45 fps 重跑**：csim **8/8+17/17**、csynth **II=1 / Fmax 154.38 MHz / LUT 4043 / FF 6174 / BRAM 0 / DSP 26**、cosim **PASS**（29 事务、无死锁）；§5.2 新增第 12 项待 A 线表态 |
