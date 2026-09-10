# 接口契约（interface.md）

> **状态：草案 v0.92 —— 待 A / B / C 三方会签后冻结为 v1.0。**
> 起草：C 线（2026-09-10）。对应《02》任务 **C2：定义 IP 接口（AXI-Stream + 控制寄存器）**，验收标准"与 A/B 线的数据契约对齐"。
> 当前已实现并通过 csim+综合的 IP：`roi_statistic`(3.2) / `motion_quality`(3.3) / `rgb2gray`(3.4)。
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
| 帧率 | **30 fps**（仅影响时间序列，IP 本身不感知帧率） | 640×480 RGB888@30fps |

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

### 3.3 `motion_quality` v1 —— ✅ 已实现，csim + 综合通过

> 实测（2026-09-10）：csim 6/6 + 9/9、`0 errors`；**Final II = 1**；Estimated **7.140 ns** < 10 ns → **140.05 MHz**；
> LUT 1503 / FF 1162 / **BRAM 256** / DSP 1。完整记录见 `fpga/report/c4_rgb2gray_motion_quality_v1.md`。
>
> 🚨 **BRAM 占用 256/280 = 器件的 91%** —— 这是目前最大的上板风险：M3 还要放 AXI DMA 与互连，
> 只剩 24 个 BRAM18 极可能不够。**方案待决策**（降分辨率 / 双流 / 接受），见上述报告第 5 节。
>
> **根因（已用对照实验坐实）**：片内数组按 **2 的幂地址空间**分配，不是按真实深度。
> `depth=307200` 落在 2¹⁸~2¹⁹ 之间 → 按 **2¹⁹ = 524288** 实现 → 524288÷2048 = **256** 个 BRAM18。
> 三组对照（320×240→64、512×512→128、640×480→256）**全部**符合该规律。
> ⚠️ 推论：**缓存成本是台阶式的**，跨过 2 的幂就翻倍；而像素数 ≤ 131072 的任意尺寸都只需 **64** 个
> （即 320×240 与 384×288 同价），选分辨率时应贴着台阶挑。

**功能**：用片内 BRAM 缓存**上一帧**灰度，逐像素求绝对差，输出运动量与运动像素比例。

**接口**

```cpp
void motion_quality(
    hls::stream<axis_gray_t> &gray_in,     // axis, TDATA=8
    hls::stream<axis_gray_t> &gray_out,    // axis, 原样透传
    ap_uint<16> width, ap_uint<16> height,
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
| `0x10` | `width` | W | 图像宽（640） |
| `0x18` | `height` | W | 图像高（480） |
| `0x20` | `motion_thresh` | W | 运动判定阈值（u8） |
| `0x28` | `diff_total` | R | 帧差总量（+ 0x2c `diff_total_ctrl`） |
| `0x30` | `motion_pixels` | R | 运动像素个数（+ 0x34 `_ctrl`） |
| `0x38` | `motion_ratio_q16` | R | 运动比例 Q16（+ 0x3c `_ctrl`） |
| `0x40` | `count` | R | 本帧像素数（+ 0x44 `_ctrl`） |
| `0x48` | `frame_id` | R | 已处理帧计数（+ 0x4c `_ctrl`） |

**数值精度**

| 量 | 上限 | 结论 |
|---|---|---|
| `diff_total` | 640×480×255 = 78,336,000 < 2³² | ✅ |
| `motion_pixels` | ≤ 307,200 | ✅ |
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

### 3.4 `rgb2gray` v1 —— ✅ 已实现，csim + 综合通过（C4 期间新增）

> 实测（2026-09-10）：csim 9/9 + 10/10、**不一致像素 0**、`0 errors`；**Final II = 1**；
> Estimated **6.580 ns** < 10 ns → **151.98 MHz**；LUT 927 / FF 763 / **BRAM 0** / DSP 3。
> 寄存器映射已与实综合逐行核对一致。

**为什么需要它**：3.3 节的 `motion_quality` 要吃 `gray_in`，但原设计中**没有任何环节产生灰度**，
而 `docs/00` §3.4 明确要求"PL 端完成 RGB/YUV 转换、缩放灰度化"。
若改由 PS 先转灰度再 DMA，PL 就只剩帧差，会被看作转发器（《01》4.2 点名的致命短板）。
故在 PL 内新增本 IP。

**接口**

```cpp
void rgb2gray(
    hls::stream<axis_pix_t>  &rgb_in,    // axis, TDATA=24 (RGB888)
    hls::stream<axis_gray_t> &gray_out,  // axis, TDATA=8
    ap_uint<16> width, ap_uint<16> height,
    ap_uint<32> &pixel_count,            // 本帧像素数
    ap_uint<32> &sum_gray,               // 本帧灰度累加和（供光照质量评分）
    ap_uint<32> &frame_id);
```

**寄存器映射（`bundle=ctrl`）**

| 偏移 | 名称 | 访问 | 说明 |
|---|---|---|---|
| `0x00`~`0x0c` | `CTRL` / `GIER` / `IP_IER` / `IP_ISR` | RW | 同 3.2 节 |
| `0x10` | `width` | W | 图像宽（640） |
| `0x18` | `height` | W | 图像高（480） |
| `0x20` | `pixel_count` | R | （+ 0x24 `pixel_count_ctrl`） |
| `0x28` | `sum_gray` | R | （+ 0x2c `sum_gray_ctrl`） |
| `0x30` | `frame_id` | R | （+ 0x34 `frame_id_ctrl`） |

**🔒 冻结的灰度公式（A 线必须实现同一式）**

```
Y = (77*R + 150*G + 29*B + 128) >> 8
```

| 性质 | 说明 |
|---|---|
| 系数来源 | BT.601 的 0.299 / 0.587 / 0.114 按 ×256 四舍五入：76.5→**77**、150.3→**150**、29.2→**29** |
| 关键优点 | 77+150+29 = **256 恰好** → 纯白映到 255、纯黑映到 0，**无需裁剪**；16 bit 内最大 255×256+128 = **65,408**（不溢出） |
| 参考值（可人工核对） | 纯红 **77**、纯绿 **149**、纯蓝 **29**、白 **255**、黑 **0** |
| 溢出 | `sum_gray` 上限 307,200×255 = 78,336,000 < 2³² ✅ |

> ⚠️ **本式与"按浮点系数四舍五入"的实现会差 1 LSB**：例如纯红，0.299×255 = 76.245 → 76，而本式给 **77**。
> 这是**口径选择，不是 bug**。**A 线必须在 NumPy 里实现本式**（3 行代码）；
> 若坚持用 `cv2.cvtColor`，须先用 4.4 节的对拍脚本确认差异 —— 否则 motion_quality 的黄金参考会系统性偏移。

### 3.5 `fir_filter` v1 —— ⏳ 形状已冻结，系数/阶数待 C5 定稿

| 接口要素 | 冻结值 |
|---|---|
| 输入 / 输出 | AXI-Stream，`TDATA=16`（有符号 `int16`，输入 `Q1.15` 归一化样本） |
| 系数 | `int16` `Q15`；阶数 `N ≤ 64`（编译期常量，**待 C5 定稿具体值**） |
| 运算 | `int32` 累加器 → 算术右移 15 → **饱和**到 `int16` |
| 舍入约定 | **先累加、后一次移位**（不做逐步舍入），A 线 NumPy 参考须完全照此实现 |
| 带通范围 | 心率 `0.7 ~ 3.5 Hz`（30 fps 采样）；呼吸 `0.1 ~ 0.5 Hz`（待定用哪个实例） |

> ⚠️ 舍入/饱和约定必须在两侧**逐字一致**，否则尾样本必然对不上。此条待 C5 用真实比对结果确认后升级为"已验证"。

### 3.6 三条线的对接点

```
A 线 (Python)                                C 线 (FPGA/HLS)
───────────────────────────────────────────  ──────────────────────────────────────────
同一帧 RGB888 (R,G,B)   →  frames.bin     →  tb_roi_statistic  → roi_statistic
同一 ROI 坐标 (半开区间)                     →  roi_x0/y0/x1/y1 寄存器
NumPy 整数累加 → golden_roi.csv            →  sum_r/g/b, count 寄存器   → 逐点比对

同一帧 RGB888           →  rgb_frames.bin  →  rgb2gray  → gray.bin（Python 算的黄金灰度）
Y=(77R+150G+29B+128)>>8 →  gray.bin        →  motion_quality → golden_motion.csv
```

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

用例集合（8 个 × 5 帧 = 40 行）：

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
| `fir_filter` | **±1 LSB（待 C5 实测确认）** | 定点舍入/饱和边界可能有 1 LSB 分歧 |

### 4.4 灰度口径对拍（A 线**必须**先跑这个）

`rgb2gray` 的灰度式是**自定义冻结口径**，与"按浮点系数四舍五入"相差 1 LSB
（例：纯红，浮点式 0.299×255 = 76.245 → **76**，本设计给 **77**）。
所以 A 线**不能**想当然地用 `cv2.cvtColor` 当黄金参考，必须先对拍：

```python
# A 线在 NumPy 里实现冻结式（唯一权威口径）
import numpy as np
img = np.fromfile(r"fpga/sim/data_motion/rgb_frames.bin", dtype=np.uint8) \
        .reshape(-1, 480, 640, 3)[0].astype(np.int32)      # 取第 0 帧
mine = ((77 * img[..., 0] + 150 * img[..., 1] + 29 * img[..., 2] + 128) >> 8).astype(np.uint8)

# 与 C 线产出的黄金灰度逐像素比对（这条必须全 0）
gold = np.fromfile(r"fpga/sim/data_motion/gray.bin", dtype=np.uint8).reshape(-1, 480, 640)[0]
assert np.array_equal(mine, gold), "冻结算式与 C 线黄金参考不一致"

# 可选：看看 OpenCV 差多少（若装了 cv2）
try:
    import cv2
    theirs = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    d = np.abs(theirs.astype(np.int32) - mine.astype(np.int32))
    print("cv2 与本式的最大差:", d.max(), " 不同像素占比:", (d > 0).mean())
except ImportError:
    print("cv2 未安装，跳过 OpenCV 对照")
```

**判据**：
- 上面那条 `assert` **必须通过** —— 这是"两边算的是同一个东西"的硬证据；
- 若 OpenCV 对照显示有差异（很可能 ±1 LSB），**以本式为准**，A 线在黄金参考里改用本式，
  不要为了迁就 OpenCV 去改硬件口径。

---

## 5. 待确认项（会签前需三方各表态）

1. [ ] **A 线**：`frames.bin` 的 **RGB 通道顺序**与**半开区间 ROI** 是否认可？（这是逐点相等的唯一前提）
2. [ ] **A 线**：`roi_statistic` 的整型累加是否就是你们 rPPG / 质量评分要用的那份时间序列口径？
   > 建议做法：让 A 线用**他们自己的 NumPy 代码**对同一份 `fpga/sim/data/frames.bin` 复算一遍，
   > 与他们自己那套实现的结果对拍 —— 这才是真正的"三线对齐"，比口头认可更硬。
3. [ ] **B 线**：第 1 节 JSON schema 与第 2 节 6 值枚举是否已全部支持？
4. [ ] **A 线**：按 **4.4 节的对拍脚本**验证灰度口径一致（`assert` 必须通过）。
5. [ ] **A 线**：`motion_thresh` 默认 16 是否合适？（按你们 OpenCV 帧差口径复核；改则须重生成黄金参考）
6. [ ] **C 线**：`motion_quality` 的 **BRAM 91%** 方案决策（降分辨率 / 双流 / 接受）—— 见 3.3 节与 C4 报告第 5 节。
7. [ ] **C 线**：`fir_filter` 的阶数/系数，待 C5 实测后补入本文件。
8. [x] ~~C 线：`s_axilite` 的 `offset=` 是否按字节生效~~ → ✅ **2026-09-10 实综合已核对**，11 个数据寄存器偏移与本文件 3.2 节逐一吻合；并补记了 5 个 `*_ctrl` 寄存器。
9. [ ] 三方确认后本文件版本号 → **v1.0 冻结**。

---

## 6. 变更记录

| 日期 | 版本 | 谁 | 改动 |
|---|---|---|---|
| 2026-09-10 | v0.9 | C 线 | 初建草案：全局口径（PYNQ-Z2 / 640×480 RGB888）+ `roi_statistic` v1 接口与寄存器映射冻结 + 测试向量格式 + 比对口径；`motion_quality` / `fir_filter` 仅冻结形状 |
| 2026-09-10 | v0.91 | C 线 | **实综合回填**：寄存器表补入 `CTRL/GIER/IP_IER/IP_ISR` 与 5 个 `*_ctrl`（ap_vld）寄存器；3.2 节补实测结论（II=1、Fmax 138.99 MHz、LUT 1267/FF 723/BRAM 0/DSP 1）；§5 第 5 项核销 |
| 2026-09-10 | v0.92 | C 线 | **C4 落地**：3.3 节 `motion_quality` 定稿（寄存器表、三条语义、实测 II=1/Fmax 140.05 MHz/**BRAM 256 = 91% 风险**）；**新增 3.4 节 `rgb2gray`**（含冻结灰度式 `Y=(77R+150G+29B+128)>>8` 与实测数据），`fir_filter` 顺延为 3.5、对接点为 3.6；**新增 4.4 节灰度口径对拍脚本**；4.3 容差表补 `rgb2gray`；§5 待确认项扩到 9 条 |
