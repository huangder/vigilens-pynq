# 17 阶段二：OpenMV 采集测试手册（照着做就行）

> **适用**：分支 `c-line/fs30` · 编写 **2026-09-26** · 相机已到位
> **配套**：`docs/14_PC模拟与相机采集_测试方案.md`（框架 + 逐条判据）、
> `board/openmv/刷机与网页画面_操作单.md`（UVC 专项，含 sha256）、`board/openmv/README.md`（接线与故障排查 §4.4）
> **一句话**：阶段二只有两件事 —— **2A 测相机能力（不刷固件）**、**2B 刷成 UVC 看网页画面**。
> **两件事互斥，而且必须先做 2A。**

---

## 0. 现在从哪开始（**2026-09-26 实测的起点**）

| 项 | 实测 | 说明 |
|---|---|---|
| **串口 `COM10`** | ✅ **在** | 相机已接入，而且跑的是 **MicroPython 固件** |
| 视频设备（`run_demo.py --list`） | ❌ 0 个 | **正常** —— 还没刷 `uvc.bin`，它不是 UVC 摄像头 |
| `pyserial` | ❌ 未安装 | 只有走 `--serial COMx` 模式才需要；**矩阵测试不需要它** |
| 4 个离线自检 | ✅ 9/9 · 10/10 · 18/18 · 28/28 | 见 §2 第 1 步 |

> ✅ `COM10` 出现说明**相机活着、固件在跑**。这正是 2A 需要的状态。
> ⚠️ 如果你在 VSCode 的 OpenMV 扩展里连不上，先看 `board/openmv/README.md` **§4.4**（要禁用 `ms-python`；或有别的程序占着 COM10）。

---

## 1. 先想清楚：两件事、互斥、顺序不能反

```
2A 能力矩阵（不刷固件）                    2B 刷 uvc.bin（UVC 摄像头）
  脚本在相机上跑                            相机变成标准 USB 摄像头
  ✅ 拿到：分辨率/帧率/内存/单帧字节          ✅ 拿到：网页上的实时画面
  ❌ 网页看不到画面                          ❌ 脚本跑不了、能力测试做不了
                     ↑                                  ↑
              缺数据的正是这一边            演示好看，但数据是"顺带"的
```

**为什么必须先做 2A**：`fpga/report/t6_openmv_capture_matrix_v1.md` 里**只回来 2 行**（10 组里），
而所有"带宽/帧率/能不能用"的决策都在等这张表。刷了 UVC 就跑不了脚本 → 那张表永远补不齐。

---

## 2. 开工前 5 分钟（每次都要做）

### 第 1 步 · PC 侧自检（4 条，全过才继续）

```powershell
cd D:\Desktop\AMD
. .\env.ps1
python board/openmv/vigilens_link.py --selftest        # 期望 RESULT: PASS  (9/9)
python board/openmv/host_capture_test.py --selftest    # 期望 RESULT: PASS  (10/10)
python board/openmv/raw_to_contract.py --selftest      # 期望 RESULT: PASS  (18/18)
python board/openmv/offline_check.py                   # 期望 RESULT: PASS (28/28)
```

### 第 2 步 · 确认相机在、并**用眼睛看方向**

1. micro-USB 接上相机（**要数据线**，不是纯充电线；直插主机，别用 hub）；
2. 打开 **`D:\OpenMV\bin\openmvide.exe`** → 左下角点连接（它会自动找到 `COM10`）；
3. **连上以后，IDE 右侧就是一个实时画面窗口** —— 这一步顺便解决"相机装反了没有"的问题（见 §7 的 BUG-013）。

**判据**：IDE 能连上 + 右侧画面能看到你的脸；画面**正立**（如果人是倒的，见 §7）。

### 第 3 步 · 三条纪律（踩了要返工）

| ❌ 不要 | 为什么 |
|---|---|
| **把脚本存到相机盘上再编辑** | H7 板载盘只有 **43 KB 级**，一涨到就报 `Not enough disk space`；更糟的是 FAT 挂载失败会**自动格式化整个盘**。正确做法：在 PC 上编辑 → 点 Run（脚本经 USB 推给相机、在 RAM 里跑） |
| **现在就刷 `uvc.bin`** | 刷了就跑不了 2A 了，**顺序反了要重刷两次固件** |
| `pip install csi` / `sensor` / `omv` | 这些包**不存在**；编辑器里的"无法解析导入"是**假警报**（`AGENTS.md` §6.4） |

---

## 3. 2A · 能力矩阵（**本阶段的核心，约 30 分钟**）

### 3.1 怎么跑（三步）

1. 在 **OpenMV IDE** 里打开 `D:\Desktop\AMD\board\openmv\openmv_capture_test.py`（**在 PC 上打开**，不要存到相机盘）；
2. 改文件顶部的两个常量（大约在 278 / 307 行）：

```python
MODE = "matrix"
MATRIX_STAGE = "small"      # 先 small → 再 "mid" → 最后 "big"
```

3. 点 **运行**（脚本会在相机上跑，输出打印在 IDE 下方的串行终端里）。

> ⚠️ `openmv_capture_test.py` 是**自包含**的，**不需要** `vigilens_link.py`（只有 `openmv_stream.py` 才需要）。

### 3.2 这 10 组到底是什么（**顺序是刻意排的，别随手重排**）

| # | 像素格式 | 分辨率 | 帧缓冲 | 段 | 为什么要跑它 |
|---|---|---|---|---|---|
| 1 | JPEG | QVGA | 1 | `small` | **JPEG 的真实压缩字节数**（A 线旁路画面的带宽预算就等它） |
| 2 | JPEG | QVGA | 2 | `small` | 双缓冲对 JPEG 帧率的影响 |
| 3 | GRAYSCALE | QQVGA | 1 | `small` | 最小组合的基线 |
| 4 | RGB565 | QQVGA | 1 | `small` | 最小彩色基线 |
| 5 | **RGB565** | **QVGA** | 1 | `mid` | **已实测 39.76 fps / 153600 B** —— 复测确认可复现 |
| 6 | GRAYSCALE | QVGA | 1 | `mid` | 灰度通路 |
| 7 | **GRAYSCALE** | **VGA** | 1 | `big` | **擦边组**：307200 B vs 可用 308944 B，**只差 1744 B**，最容易把相机拖死 |
| 8 | JPEG | VGA | 1 | `big` | VGA JPEG 单缓冲 |
| 9 | JPEG | VGA | 2 | `big` | **重点**：双缓冲能不能把 VGA JPEG 拉到 20 fps |
| 10 | RGB565 | VGA | 1 | `big` | **已知会失败**（`Frame buffer overflow`）—— **留作证据，不要删** |

### 3.3 每一段跑的时候，盯这三样

1. **表格行**：每组会输出 `fps均值 / fps中位 / 最慢帧 / B/帧 / 理论B / mem可用 / 备注`；
2. **每组开跑前那行**（**这条最关键**）：

```
---- [k/N] JPEG QVGA fb=1 开始（此刻 gc.mem_free() = … B）----
⚠️ 内存余量：…
```

3. **表末的「契约可行性小结」** —— 工具**只用实测数字**自动折算出来的：
   装得下契约帧的组合、≥30 fps 的组合、最坏单帧、按 `min_close_frames` 折算的**最短可检出闭眼**。

**判据**：

| 列 | 它能定论什么 |
|---|---|
| `fps均值` | 契约要 **30 fps**，一眼看出差多少 |
| **`最慢帧fps`** | 最坏情况的采样间隔（运动/眨眼检测关心的是它，不是均值）。⚠️ 它是**帧率不是毫秒**（16.08 = 62.2 ms/帧） |
| `B/帧` vs `理论B` | 差很多 → **实际生效的分辨率/格式跟请求的不是一回事**（工具会打警告）。带 `~` 的是 JPEG 的名义值，只看 `B/帧` |
| **`mem可用`** | 契约帧 640×480 **RGB888** = **921600 B**，而实测可用只有 **308944 B** → **装不下，不需要推算** |

### 3.4 万一相机卡死了（**会有这个风险，先会处理再跑**）

- **PC 侧症状**：IDE 突然"连接没有响应"、串口失联；
- **恢复办法**：**拔掉 micro-USB 再插上**（相机由 USB 供电，拔插 = 整机上电复位）；
- **定位手段**：**最后一次出现的组号，就是把它搞死的那一组**。
  所以第 7/10 组（擦边与已知会失败）排在最后，**就算炸也只剩它自己没跑完**。

### 3.5 要贴回什么（**这段最容易做错**）

> **整段原始输出**，含开头的「环境事实」段（固件/板子标识、走哪套 API）与表末的「契约可行性小结」，
> **一个字都不要手改**。
> ⚠️ 上次就是只贴了表格前两行，导致 5 行的值到现在还是空白。

### 3.6 三段怎么排

| 顺序 | `MATRIX_STAGE` | 大概耗时 | 跑完先看什么 |
|---|---|---|---|
| 1 | `"small"` | 几分钟 | **JPEG 的真实字节数**（A 线等它） |
| 2 | `"mid"` | 几分钟 | 39.76 fps 能否复现 |
| 3 | `"big"` | 几分钟 | `GRAYSCALE/VGA` 那 1744 B 的擦边结果；**随时准备拔插 USB** |

---

## 4. 2A 第二步 · 无损落盘（**需要一张 micro SD 卡**，可选但推荐）

只有 SD 卡才能落盘：一帧 320×240 RGB565 = 153600 B，而 H7 板载盘只有 **43 KB 级**，**一帧都放不下**。

1. 把 `CHOSEN_PIXFORMAT` / `CHOSEN_FRAMESIZE` / `CHOSEN_FRAMEBUFFERS`（约 313~315 行）
   填成 §3 里**可用的最好组合**；
2. 改 `MODE = "dump"` → 点运行（采 10 帧原样写入 SD，并逐帧回读校验）；
3. 把相机里的 `vigilens_frames/` 整个目录拷回 PC（例如 `metrics\logs\openmv_dump`）；
4. PC 侧转成契约布局：

```powershell
python board/openmv/raw_to_contract.py metrics\logs\openmv_dump --info
python board/openmv/raw_to_contract.py metrics\logs\openmv_dump --calibrate      # ← 别跳过
python board/openmv/raw_to_contract.py metrics\logs\openmv_dump `
    --out metrics\logs\openmv_frames.bin --report metrics\logs\openmv_convert.json
```

**判据**：`--calibrate` **唯一判定**出 RGB565 的位扩展公式；输出字节数 = **帧数 × W × H × 3**。

> ⚠️ 产出的 `openmv_frames.bin` 是给 **C 线黄金参考**和 `docs/interface.md` §5.2 那两个"待真实视频"项用的，
> **不是网页/管线的输入**（`backend/capture.py` 只认 `synthetic` / 摄像头序号 / **视频文件**）。
> ⚠️ **JPEG 是死路**：`raw_to_contract.py` 对 JPEG 帧**明确拒绝转换**（有损 → 违反契约 §4.3 的"容差 0"）。

---

## 5. 2B · 刷成 UVC + 网页看实时画面（约 40 分钟）

> 详细步骤（含 sha256、回滚、故障排查）：**`board/openmv/刷机与网页画面_操作单.md`**。这里只给主干。

### 5.1 刷（IDE，`Ctrl+Shift+L`）

相机连着 → IDE 菜单 **工具 / Tools → Run Bootloader (Load Custom Firmware)**，快捷键 **`Ctrl+Shift+L`** →
选 `metrics\logs\_uvc_openmv4.dfu`（**IDE 不收 `.dfu` 就改选 `_uvc_openmv4.bin`**）→ 按提示刷 → **拔插一次 micro-USB**。

⚠️ 三个文件的 sha256 已于 2026-09-26 逐个核对**全部匹配**（见操作单 §2）。
⚠️ 刷 UVC 会**擦除相机内部 FAT**（相机盘上文件会没）—— 先备份。
⚠️ 官方 IDE ≥ v4.7.0 已下架预编译 UVC 固件，所以不能用"IDE 里自带的 uvc.bin"；材料是从 v4.6.20 release 取的。

### 5.2 先量，别急着演示

```powershell
python metrics/scripts/run_demo.py --list                                    # ① 应该多出一个新序号
python board/openmv/host_capture_test.py --list                              # ② 确认它 + 首帧尺寸
python board/openmv/host_capture_test.py --device 0 --seconds 10 --probe 640x480,320x240
#                                          ↑ 0 换成上面打印的序号
```

**判据**：

| # | 判据 | 不合格说明 |
|---|---|---|
| 1 | `--list` 出现**新序号** | 没出现 = **没刷成**（不是故障） |
| 2 | JSON 含 `actual_width/height`、`fps`、`distinct_frames`、`first_frame_latency_ms` | 工具没跑对 |
| 3 | **`distinct_frames ≈ frames`** | `== 1` → **冻结画面**，那个"帧率"是假的（工具会打 ⚠️） |

**这一张表要回填 `fpga/report/`** —— 它也是 A 线旁路画面带宽预算的输入。

### 5.3 看网页（一条命令）

```powershell
python metrics/scripts/run_demo.py --source 0 --seconds 60 --video-hz 25 `
    --csv metrics/logs/_uvc_demo.csv --json metrics/logs/_uvc_demo_last.json
#                     ↑ 换成 5.2 找到的序号
```

浏览器会自动打开脚本打印的网址。

**判据（四条，缺一不可）**：

| # | 判据 | 怎么确认 |
|---|---|---|
| 1 | 网页有**真实画面** | 画面区是实时图像（不是占位网格） |
| 2 | 数字/曲线/状态在动 | 走契约通道 |
| 3 | `distinct_frames ≈ frames` | 5.2 的输出 |
| 4 | `landmark_source = mediapipe` | 终端里 `run_pipeline` 打印的"本次数据来源" |

> 🔴 **一定要给 `--seconds`**：活摄像头永不结束；不给会一直写 CSV（09-25 就留下过一份 **1.01 GiB** 的）。
> 想长时间演示用 `--loop`（记得 `Ctrl-C` 停）。
> 📌 **测量一结束，画面会被判为"过期"并自动隐藏** —— 刻意设计（不显示冻结的旧画面），不是 bug。

### 5.4 ⚠️ 别选错运行模式（今天实测的坑）

| 命令 | 数据源 | 什么时候用 |
|---|---|---|
| `python backend/api.py` | **mock + 真实帧混着推！** | ❌ **演示时别用** |
| `python backend/api.py --no-mock` | 只有真实帧 | ✅ M2 集成/演示用 |
| `python backend/websocket.py` | mock（默认端口 8765） | 只用于界面开发 |
| `python metrics/scripts/run_demo.py ...` | `--no-mock` + 自动挑端口 | ✅ **最省事，推荐** |

（原因见 `docs/16` 的 **BUG-014**：`api.py` 默认会起一个 mock 泵，网页上可能**一半是假数据**。）

---

## 6. 收尾：刷回 MicroPython + 归档

```powershell
# 1) 刷回（要用脚本就得刷回）
#    IDE → Ctrl+Shift+L → metrics\logs\_firmware_openmv4_rollback.dfu → 拔插一次
# 2) 验证回滚成功
python board/openmv/offline_check.py                   # 期望 RESULT: PASS (28/28)
[System.IO.Ports.SerialPort]::GetPortNames()           # 期望又能看到 COM10
```

**归档**：网页截图 → `metrics/evidence/`；相机能力表 → `fpga/report/`；
大模型协作记录 → `report/llm_log/`（**人类亲手写**，铁律 3）。

---

## 7. ⚠️ 两个今天才发现的前提（**不知道会白干**）

### 前提 1 · 先把相机方向摆正（`BUG-013`）

我做了一个实验（60 帧，规范素材）：

```
旋转   0 (正立)      检出率 100.0%
旋转  90 (顺时针)    检出率 100.0%   ← 能检出，但姿态角全错
旋转 180 (倒置)      检出率   0.0%   ← 一帧都没检到 ⚠️
旋转 270 (逆时针)    检出率  98.3%
```

**人话**：**相机装反 180°，系统一帧人脸都检测不到**（不是"测得不准"，是"完全测不到"），
而你在网页上只会看到状态莫名变成"不可靠/请调整姿势"。

**所以阶段二第一步（§2 第 2 步）就要用 IDE 的实时画面确认方向**：人正立、脸正立才算过。
⚠️ 目前代码**没有自动方向适配**（这是 `BUG-013`，待修）。

### 前提 2 · 阶段二采的数据**不能**用来验证算法（`BUG-006/007`）

现在姿态模块有 P1 缺陷：规范素材上 **618/630 = 98.1% 的帧被判 `adjust_posture`**（`yaw ≈ ±177°`），
而人是正对镜头的。

**结论**：
- ✅ 阶段二的数据**可以**用于：**采集能力**结论（分辨率/帧率/单帧字节/内存/画质/能否落盘）；
- ❌ 阶段二的数据**不能**用于：验证眨眼/PERCLOS/哈欠/头姿/心率 —— 那些要等 **P1（BUG-006/007/011）修完**再重采。

### 顺便要做的第三件事：**低帧率影响评估**

拿到真实帧率 F 之后，算这一个数（`config.yaml` 的 `min_close_frames: 3`）：

```
最短可检出闭眼 = 3 / F
  F=10 → 300 ms   ❌ 正常眨眼 100~400 ms，会漏掉一大截 → PERCLOS 偏低 → 疲劳漏报
  F=20 → 150 ms   ⚠️ 还是偏漏
  F=30 → 100 ms   ✅ 合格
```

**判据**：若 F < 20，**必须写进报告**；要不要改 `min_close_frames` 要有依据 + 公告（`config.yaml` 是三人共用）。

---

## 8. 判据总表（一张纸）

| 阶段 | 判据 | 期望 |
|---|---|---|
| 开工前 | 4 个自检 | 9/9 · 10/10 · 18/18 · 28/28 |
| 开工前 | IDE 能连上 + **画面里人脸正立** | 是 |
| 2A | 10 组表格**完整**（或明确哪组失败+原文） | 有「契约可行性小结」 |
| 2A | 记录 `fps均值 / 最慢帧 / B/帧 / mem可用` | 真实值 |
| 2A-dump | `--calibrate` 判定出扩展公式 | 输出字节数 = 帧数×W×H×3 |
| 2B | `--list` 出现**新序号** | 是 |
| 2B | **`distinct_frames ≈ frames`** | 是（`==1` 判不通过） |
| 2B | 网页：画面 + 指标 + `mediapipe` | 三项全过 |
| 收尾 | 回滚后 `COM10` 回来 | 是 |
| 全程 | 数据只用于"采集能力"结论 | 见 §7 前提 2 |

---

## 9. 卡住了去哪查

| 现象 | 去哪 |
|---|---|
| IDE/VSCode 连不上相机、"连接没有响应" | `board/openmv/README.md` **§4.4**（先分 PC 侧/相机侧，含 LED 对照 + 6 个高频原因） |
| 相机疑似卡死 | **拔插 micro-USB** 复位；再用 `MATRIX_STAGE` 分段定位 |
| 编辑器报 `无法解析导入 "csi"/"sensor"` | **假警报**（`AGENTS.md` §6.4）：别 `pip install`、别删 import |
| 刷 UVC 后跑不了脚本 | **正常**（两者不可兼得）；刷 `_firmware_openmv4_rollback.dfu` 回去 |
| 网页有数字没画面（或反过来） | `AGENTS.md` §6.2.2 的两条通道表；`--no-video` 做对照 |
| 网页数字"很漂亮"但不确定真假 | 查 **BUG-014**：`api.py` 不带 `--no-mock` 会混 mock |
| `.venv` / `env.ps1` / 中文乱码 | `AGENTS.md` §6.4 环境坑表 |

---

## 10. 本手册的诚实边界

- **写它的人（AI）没有跑过硬件**：OpenMV 采集、UVC 画面**都还没做**；
  手册里"已实测"的数字来自 **2026-09-24 人类回贴的 2 行** 与 **2026-09-26 我在 PC 侧做的实验**（§7 前提 1）。
- **【已验证，2026-09-26 本机】**：`COM10` 存在、0 个视频设备、`pyserial` 未安装、4 个自检全过、刷机材料 sha256 全匹配。
- **【未验证】**：2A 全部（矩阵只回来 2 行）、2B 全部（从未刷成功）、落盘（无 SD 卡）。
- **本手册不替你决定**：改 `config.yaml` 阈值要公告；改契约要走 `contract:` 流程；"这是不是缺陷"以 `docs/15` 为准。
- 所有判据数字都是**当时的真实运行**；跑出不同结果**先报告事实，不要改期望值去凑绿**。
