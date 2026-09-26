# T6 补充：OpenMV 能力矩阵 —— 2026-09-26 两次真实运行（含从相机回收的历史报告）

> **执行**：AI 通过 **COM10 串口直接驱动相机**（不经 OpenMV IDE；PowerShell `System.IO.Ports.SerialPort`，
> 因为本机 `pip install pyserial` 无网络）
> **证据目录**：`fpga/report/logs/2026-09-26_openmv_vigilens_report_recovered.json`（回收的历史报告）
> **配套**：`fpga/report/t6_openmv_capture_matrix_v1.md`（2026-09-24 首测）、`docs/16_测试问题台账.md`、`docs/17_阶段二_OpenMV采集测试手册.md`
> ⚠️ **本文件需 C 线复核**：执行者没有硬件背景。

---

## 0. 本次拿到什么（一句话）

| 来源 | 得到的数据 |
|---|---|
| **从相机 `/flash/vigilens_report.json` 回收** | 用户 2026-09-26 那次运行的 **`small` 4 组**（此前只有 2 行） |
| **AI 通过串口亲自跑 `mid`** | **`mid` 2 组**（`RGB565/QVGA`、`GRAYSCALE/QVGA`） |
| **AI 通过串口读到的环境事实** | 固件/板子/API 全串，含 `omv.arch` / `board_id` |
| **仍未拿到** | `big` 5 组（`GRAYSCALE/VGA`、`JPEG/VGA`×2、`RGB565/VGA`）—— 见 §5 的"为什么没跑" |

---

## 1. 环境事实（串口实测，权威）

```
OpenMV v5.0.0; MicroPython v1.28.0-49; OPENMV4 with STM32H743
  omv.arch            : OMV4 H7 1024
  omv.board_id        : 36303833313351160048001F
  omv.board_type      : H7
  sys.platform        : OpenMV4-H7
  sys.implementation  : micropython 1.28.0.
  uname.version       : v1.28.0-49 on 2026-07-02
  camera_api          : csi（csi.CSI 类 API，v5+）
  sensor_id           : 本固件没有 get_id()
  gc.mem_free()       : 318048 / 322192（空闲时，多次探测）
```

> 📌 **这补齐了 `TBD-001` 缺的"完整固件串"**：固件 **v5.0.0** ≥ 要求的 4.5.6。
> ⚠️ **`sensor_id` 拿不到**（本固件没有 `get_id()`）→ **传感器型号仍未确认**
> （官方 H7 通常配 OV7725；但**这是推测，不要写成事实**）。

---

## 2. 回收的历史报告：`small` 4 组（用户 2026-09-26 那次运行）

来源：相机 `/flash/vigilens_report.json`，原样存档为
`fpga/report/logs/2026-09-26_openmv_vigilens_report_recovered.json`

| pixformat | size | fb | 结果 | fps均值 | 最慢帧 | B/帧 | mem_after |
|---|---|---|---|---|---|---|---|
| **JPEG** | QVGA | 1 | ❌ **`RuntimeError: Sensor control failed.`** | – | – | – | 298224 |
| **JPEG** | QVGA | 2 | ❌ **`RuntimeError: Sensor control failed.`** | – | – | – | 297248 |
| GRAYSCALE | QQVGA | 1 | ✅ | **40.05** | 40.0 | **19200** | 296480 |
| RGB565 | QQVGA | 1 | ✅ | **39.73** | **24.0** ⚠️ | **38400** | 296032 |

**逐项核对**：`160×120×1 = 19200` ✅、`160×120×2 = 38400` ✅ ——
**这两个组的字节口径是对的**（与下面 §3 的 RGB565/QVGA 异常形成对比）。

---

## 3. AI 通过串口亲自跑的 `mid` 2 组

**传输方式**：把脚本 **base64 分片 → `bytearray.extend` 累积 → `decode()` → `exec()`**，
每行等 REPL 提示符（天然流控）；**发送前后做长度校验**。

```
期望 49203 字节（显式 UTF-8 读取的脚本字节数）
实收 49203 字节  -> ✅ 完全一致
MEM_FREED 209536（发送缓冲已释放）
本轮 MATRIX_STAGE=mid → 2 组
```

| pixformat | size | fb | fps均值 | fps中位 | 最慢帧fps | B/帧 | 理论B | mem可用 | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| RGB565 | QVGA | 1 | **40.08** | 40.01 | 40.01 | **0** ⚠️ | 153600 | 206752 | csi API |
| GRAYSCALE | QVGA | 1 | **40.08** | 40.01 | 40.01 | **76800** ✅ | 76800 | 206432 | csi API |

**脚本自己给出的小结（原样）**：

```
  · 契约帧 = 640x480 RGB888 = 921600 B/帧；启动时 gc.mem_free() 实测 = 207872 B（相差 4.4 倍）
  · 本轮成功 2 组 / 失败 0 组
  · 单帧实测字节数最大的成功组合：GRAYSCALE / QVGA / fb=1 → 76800 B（= 契约帧的 8.3%）
  · 「整帧装得下契约帧（≥921600 B）」的成功组合：**无**
  · 「实测均值帧率 ≥ 30 fps」的成功组合：RGB565/QVGA=40.08 fps、GRAYSCALE/QVGA=40.08 fps
  · 最坏单帧：RGB565 / QVGA → 40.01 fps（约 25.0 ms/帧）
  · 最短可检出闭眼（按 min_close_frames=3）：两组都 ≈ 75 ms
```

### 3.1 ⚠️ 与 2026-09-24 的对照（**重点**）

| 组合 | 09-24 实测 | 2026-09-26（本次） | 判定 |
|---|---|---|---|
| `RGB565 / QVGA / fb=1` | **39.76 fps / 153600 B** | **40.08 fps / `0` B** | fps ✅ 可复现（+0.3）；**B/帧 从 153600 变成 0 ⚠️** |
| `GRAYSCALE / QVGA / fb=1` | （未有记录） | **40.08 fps / 76800 B** | ✅ **新增一行**，字节数与 `320×240×1` 完全相符 |
| `RGB565 / QQVGA / fb=1` | （未有记录） | **39.73 fps / 38400 B**（§2） | ✅ 新增，字节数相符 |
| `GRAYSCALE / QQVGA / fb=1` | （未有记录） | **40.05 fps / 19200 B**（§2） | ✅ 新增，字节数相符 |
| **JPEG 各组** | 未测到 | **`Sensor control failed`（两次都失败）** | ❌ **新发现：JPEG 在这台相机/固件上不可用** |

**帧率的高度一致性值得注意**：`QQVGA` 与 `QVGA` 都是 **≈40 fps**
（`40.05 / 39.73 / 40.08 / 40.08`）⇒ 帧率**不随像素数变化**，
**【推测】是固件/传感器的帧率上限（约 40 fps），不是带宽限制**。这与 `docs/10` §3 的"UART 带宽是瓶颈"是**两件不同的事**。

---

## 4. 本次新发现的 4 个问题（详见 `docs/16`）

| # | 现象 | 严重度 | 性质 |
|---|---|---|---|
| **A** | **`/flash/main.py` 是一个 `while True:` 无限 LED 循环**（218 B）—— 上电即执行，**占住 REPL** | **P2** | 这正是 `README.md` §4.4 第 3 条；**直接解释"串口终端无法输入"** |
| **B** | **JPEG 格式两次都 `RuntimeError: Sensor control failed.`** | **P2** | 需要 A 线的"JPEG 字节数/带宽预算"就卡在这里 |
| **C** | **`RGB565/QVGA` 的 `B/帧` 实测为 `0`，而脚本的告警条件被短路所以静默通过** | **P2** | 字节口径不可信；脚本的 `and st["bytes_per_frame"]` 使 `0` 被当作合法值 |
| **D** | **写报告时 `json` 报 `extra keyword arguments given`**，回退成写 **Python repr**（文件却叫 `.json`） | **P3** | 所以回收到的"JSON"其实是 repr（本次用 `ast.literal_eval` 才解开） |

---

## 5. 为什么**没有**跑 `big` 5 组（**诚实说明，不是漏做**）

`big` 组的意义**几乎全在"内存边界"**（`GRAYSCALE/VGA = 307200 B` 与可用内存擦边）。

但**本次串口传输方式本身要占用堆**：脚本源码（49203 B）会一直驻留到 `exec` 结束，
所以 **`mem_free` 实测只有 207872 B，而 09-24 干净环境下是 308944 B —— 少了约 100 KB**。

⇒ **在这种被污染的堆上跑 `big`，得到的"VGA 装不下"结论是不可信的**（可能本来装得下）。
所以**我故意没跑**，避免产出一个会被误引用的数字。

**正确的做法**（两条任选）：
1. **在 OpenMV IDE 里跑 `big`**（堆是干净的，这正是 IDE 的优势）；
2. 或让我用 `compile()` 先把源码编译掉再 `del` 字符串，看能不能把可用堆拉回 300 KB 以上 —— **【未验证】**。

---

## 6. 复现方式（供 C 线复核）

- 驱动脚本：`metrics/logs/_omv_drive.ps1`（ASCII-only；`Invoke-OpenMV` / `Get-OMVProbe` / `Send-OMVScript`）
- 关键坑（都踩过，写下来省事）：
  1. **COM10 被 IDE 独占** → 必须先断开 IDE；
  2. **`Get-Content -Raw` 在中文 Windows PowerShell 下按 GBK 解码** → 会把 UTF-8 源码读成乱码 ⇒
     **必须用 `[IO.File]::ReadAllBytes` + `Encoding.UTF8.GetString`**；
  3. **裸发大脚本会被 raw REPL 截断**（同一行报 `SyntaxError`）⇒ 要分片；
  4. **这台固件不支持 raw-paste（Ctrl-E）** —— 发 Ctrl-E 会被当成源码 ⇒ `SyntaxError` 第 1 行；
  5. **`str +=` 累加 base64 会 `MemoryError`**（每次重新分配）⇒ **用 `bytearray.extend`**；
  6. `SerialPort` 默认 ASCII 编码 ⇒ **显式设 `$p.Encoding = UTF8`**，否则中文变 `?`。

---

## 5.1 `big` 组：**跑了**，但在第 1 组就把相机搞复位了（2026-09-26 补充，**取代 §5 的"没跑"**）

**传输与完整性**：`期望 49203 / 实收 49203 ✅`；相机自报收到的常量 `CAM_SEES ['MATRIX_STAGE = "big"']` ✅
（即：**这次真的是 big**，不是上一次的残留）。`MEM_BEFORE_EXEC = 237312 B`（比上一次的 207872 好，但仍低于干净的 308944）。

**真实输出（原样，到断点为止）**

```
本轮 MATRIX_STAGE=big → 4 组
    1. GRAYSCALE  VGA      fb=1 [big  ] 预计整帧 307200 B   ← 比可用内存大，**预期失败**
    2. JPEG       VGA      fb=1 [big  ] 预计整帧 ~51200 B
    3. JPEG       VGA      fb=2 [big  ] 预计整帧 ~51200 B
    4. RGB565     VGA      fb=1 [big  ] 预计整帧 614400 B   ← 比可用内存大，**预期失败**

---- [1/4] GRAYSCALE / VGA / fb=1 开始（此刻 gc.mem_free() = 257120 B）
     ⚠️ 内存余量：这一组**装不下**（需 307200 B / 此刻可用 257120 B，差 50080 B）→ 预期抛 Frame buffer overflow
（此后 260 秒零输出 ← 相机失去响应）
```

**复位后的决定性证据**（我在串口按 Ctrl-C 得到的栈）：

```
Traceback (most recent call last):
  File "main.py", line 12, in <module>
KeyboardInterrupt:
OpenMV v5.0.0; MicroPython v1.28.0-49; OPENMV4 with STM32H743
Type "help()" for more information.
>>>
```

⇒ **相机在 `GRAYSCALE/VGA` 那组复位了；复位后 `/flash/main.py`（`while True:` LED 循环）立即接管 REPL。**
两个症状——"跑一会儿就停"和"串口无法输入"——**是同一个事件**。详见 `docs/16` 的 **BUG-020** 与 **BUG-016**。

**顺带得到一个比"拔插 USB"更省事的恢复手段**：**串口还活着时按 Ctrl-C 就能打断 `main.py` 拿回 `>>>`**（本次实测有效）。

**`big` 未完成的部分**：第 2/3/4 组（`JPEG/VGA`×2、`RGB565/VGA`）**没跑到**。
其中 `JPEG` 已由 §2 证明在该固件上不可用；`RGB565/VGA`（614400 B）在任何情况下都装不下。

> ⚠️ **再次强调 §5 的保留意见仍然成立**：本次 `mem_free` 被串口传输占用，
> **不能用本次结果判定 `GRAYSCALE/VGA` 在干净环境下是否装得下**（干净环境 09-24 是 308944 B，
> 而该组只需 307200 B —— 文档记的"只差 1744 B"）。**这一格必须在 IDE 里复跑才能填。**

---

## 8. `sustained` 长跑（2026-09-26，首次拿到抖动数据）

```powershell
# CHOSEN_PIXFORMAT/FRAMESIZE/FRAMEBUFFERS 临时改成 GRAYSCALE / QVGA / 1，MODE = "sustained"
```

**真实输出（原样）**

```
长跑 10.0s：GRAYSCALE / QVGA / fb=1
帧数        : 770
帧率 均值   : 76.93 fps   中位: 76.91 fps
瞬时 最低/最高: 76.89 / 94.59 fps
帧间隔      : 均值 12998 us / 最短 10572 / 最长 13005 us（抖动 std 87 us）
每帧字节    : 76800
实际吞吐    : 5.908 MB/s
gc.mem_free : 前 297840 → 后 293696
```

**自洽性**：`770 / 10 = 77.0 fps`，帧间隔均值 `12998 µs → 76.9 fps` ✅ **内部自洽**。

> 🔴 **但与矩阵路径矛盾**：**同一组合**矩阵报 **40.06 fps**，长跑报 **76.93 fps** ⇒ 见 `docs/16` 的 **BUG-021**。
> ⇒ **不要混用这两个数字**；引用帧率时必须写明来自哪条路径。

**对"实时性"的意义**（部分填上 `docs/16` 的 BUG-015）：
- **抖动极小**：`std 87 µs`、最坏帧 `13005 µs` 与均值 `12998 µs` 几乎相同 ⇒ **没有长尾**；
- 但**端到端延迟仍未测**（相机 → PC → 网页），那需要 UVC 模式 + 浏览器侧计时。

---

## 9. `GRAYSCALE/VGA` 那一格：**实测到了**（单组隔离）

```powershell
# MATRIX 临时改成单元素：(("GRAYSCALE", "VGA", 1, "big"),)
```

**真实输出（原样）**

```
启动时 gc.mem_free() = 298064 B
本轮 MATRIX_STAGE=auto → 1 组
    1. GRAYSCALE  VGA      fb=1 [big  ] 预计整帧 307200 B   ← 比可用内存大，预期失败

---- [1/1] GRAYSCALE / VGA / fb=1 开始（此刻 gc.mem_free() = 297904 B）
     ⚠️ 内存余量：这一组**装不下**（需 307200 B / 此刻可用 297904 B，差 9296 B）
GRAYSCALE  VGA       1        -        -        -          -     307200    297520  RuntimeError: Frame buffer overflow, try reducing the frame size.
```

**两个结论**：

1. **`GRAYSCALE/VGA` 优雅失败**：抛 `Frame buffer overflow`，**没有把相机搞复位** ⇒
   **"单组隔离"是安全策略**（在 `big` 里连跑时它才引发复位，见 §5.1）；
2. 🔴 **修正一处既有算术**：`docs` 与 `README` 一直写"`GRAYSCALE/VGA` 离可用内存**只差 1744 B**"，
   那是拿**空闲时**的 `308944 B` 算的。**脚本运行时的可用内存只有 `297904 B`**
   ⇒ 实际差 **9296 B**，**不是擦边，是明确装不下**。
   ⇒ 建议把 `board/openmv/README.md` §2.2 与 `fpga/report/t6_*` 里"只差 1744 B"的表述改成
   "**空闲 1744 B / 运行时 9296 B，均装不下**"。

---

## 10. 矩阵 10 组的最终状态（2026-09-26）

| # | 组合 | 状态 | 结果 |
|---|---|---|---|
| 1 | JPEG / QVGA / fb=1 | ✅ 实测 | ❌ `Sensor control failed` |
| 2 | JPEG / QVGA / fb=2 | ✅ 实测 | ❌ `Sensor control failed` |
| 3 | GRAYSCALE / QQVGA / fb=1 | ✅ 实测 | ✅ 40.05 fps / 19200 B |
| 4 | RGB565 / QQVGA / fb=1 | ✅ 实测 | ✅ 39.73 fps / 38400 B（最慢帧 24.0） |
| 5 | RGB565 / QVGA / fb=1 | ✅ 实测 | ✅ 40.06~40.08 fps / **B/帧 = 0 ⚠️**（BUG-018） |
| 6 | GRAYSCALE / QVGA / fb=1 | ✅ 实测 | ✅ 40.06~40.08 fps / 76800 B（长跑 76.93 fps，BUG-021） |
| 7 | **GRAYSCALE / VGA / fb=1** | ✅ **实测（本次）** | ❌ `Frame buffer overflow`（差 9296 B） |
| 8 | JPEG / VGA / fb=1 | ⛔ **未实测** | 【预测】`Sensor control failed`（JPEG 不可用，见 1/2 行） |
| 9 | JPEG / VGA / fb=2 | ⛔ **未实测** | 【预测】同上 |
| 10 | RGB565 / VGA / fb=1 | ⛔ **未实测** | 【预测】`Frame buffer overflow`（614400 B，是第 7 行的 2 倍） |

> 第 8~10 行是**预测**，不是实测 —— 之所以没跑：传输在最后一次尝试时遇到
> `MemoryError: memory allocation failed, allocating 46500 bytes`（`bytearray` 反复 realloc，
> 堆已碎片化）。**先做一次软复位（Ctrl-D）再传，成功率会高很多** —— 下次补。

---

## 7. 诚实边界

- 执行者（AI）**没有硬件背景**；本文件的所有数字都是**串口原样读回的**，未做任何修改。
- `mid` 两组的 **fps 可信**；**`mem_free` 不可信**（被传输占用约 100 KB，见 §5）。
- `RGB565/QVGA` 的 **`B/帧 = 0` 需要 C 线判断**：是 v5 `csi` API 的取字节方式变了，
  还是本次运行的特例 —— **我没有下结论**。
- **JPEG 失败的原因我没有下结论**（`Sensor control failed` 只说明传感器拒绝了该配置）。
- `big` 组**未跑**，原因见 §5；**不要**把"VGA 装不下"当成已验证事实。
