# A 线开发步骤（M1 → M2）

> **定位**：A 线（算法 / 后端）的执行主线。回答一个问题——**在 4 段标准视频还没录的情况下，A 线接下来按什么顺序做什么。**
> **维护**：A 线。本文件在 `backend/` 下，属 A 线自有目录，改动用 `A:` 前缀提交。
> **版本**：2026-09-13 起草；**2026-09-15 更新（P0 已完成）**。对应契约 `docs/interface.md` **v1.0 冻结 → 🚧 v1.1 草案（全局 45 fps）**。

---

## 0. 本文件与其他文档的关系

| 文档 | 管什么 | 与本文件的关系 |
|---|---|---|
| `AGENTS.md` | 全局铁律、权威顺序、环境坑 | **最高优先级**。本文件与它冲突时以它为准 |
| `docs/interface.md` v1.0→v1.1草案 | 字段 / 枚举 / 寄存器 / 黄金参考口径 | 本文件的一切"口径"字样都指它。它是唯一技术准绳 |
| `docs/05_AI使用约束.md` | AI 红线、记录模板 | 本文件的每个任务都要配套一条人类手写的 `report/llm_log/` 记录 |
| `docs/04_基础框架搭建指南.md` | M0/M1 骨架与 DoD | 本文件是它 A 线部分的**展开与续接** |
| `backend/README.md` | 模块职责表 + "下一步（A 线）" | 本文件是那份"下一步"清单的**排期版**；两边要同步维护 |
| `docs/02_三人分工与三线并行开发计划.md` | 任务号 A1~A10、里程碑 | 本文件的步骤编号 **P0~P5**，每步都标注对应的 A 编号 |

**本文件不包含**：契约字段的任何新定义、C 线/B 线的开发步骤。
> ⚠️ **帧率议题的状态变了**：2026-09-13 C 线发起"全局 45 fps"（契约 v1.1 草案），帧率已从"暂缓讨论"变成 **§5.2 第 12 项——A 线的待表态事项**。见 §2.2 与 §4 P0。

---

## 1. 一句话策略

> **先验证别人的口径，再写自己的算法。**

理由不是"先易后难"，而是三条硬事实（见 §2 的实测证据）：

1. 契约虽已冻结，但 A 线在 §5.2 **有 6 项书面表态没做**（B 线已补签自己的那项），文档明确写着"不代签、不假设无异议"；
2. 这 6 项里 **5 项不需要视频**（只有第 7 项被视频卡住）；
3. 做这几项会**顺手产出 rPPG 链路需要的零件**——冻结灰度式、ROI 累加口径、Q1.15 量化、FIR 的 NumPy 参考实现。

也就是说：视频没录 ≠ A 线要停。停下来的只有"标定"这一类。

---

## 2. 现状快照（全部本机实测；**2026-09-15 已同步至远端 `0788733`**）

> 引用本表任何一行前，先看"复核命令"列自己跑一遍。数字会过期。

| # | 事实 | 复核命令 / 证据 |
|---|---|---|
| 1 | ✅ **P0 已完成**：本地已 rebase 到远端 `0788733`（原落后 14 个提交）；契约现为 **v1.0 冻结 → v1.1 草案**，`CONTRACT_VERSION = "v1.0"`（草案期不动版本号） | `git status --branch`；`Select-String -Path docs/interface.md -Pattern '状态：'` |
| 2 | 归档提交已 rebase 为 **`f19aa00`**，本地领先 `origin/main` **1 个提交（未推）** | `git log --oneline origin/main..HEAD` |
| 3 | 仓库自检 **49 passed**（同步后一度是 48 passed + 1 failed，见 §2.2，已修复） | `.venv\Scripts\python.exe -m pytest -q` |
| 12 | **v1.1 草案把全局帧率 30→45**：`config.yaml` 的 `fps_nominal` = **45**，`AGENTS.md`、`interface.md §0`、FIR 系数与黄金参考均已按 45 重生成 | `Select-String -Path config.yaml -Pattern fps_nominal` |
| 4 | **mediapipe 0.10.21 可用**，`mp.solutions` 存在，FaceMesh 能初始化（实测 2.07s） | `python -c "import mediapipe as mp; print(hasattr(mp,'solutions'))"` |
| 5 | **真实视频路径通**：90 帧跑完，`landmark_source: mediapipe` | `python backend/run_pipeline.py --source metrics/logs/_smoke.mp4` |
| 6 | **`--source synthetic` 不带 `--stub` 会崩**（`cv2.cvtColor` 收到 `SyntheticImage`）；同一根因让 **3 个模块自检失败**：`face_landmark.py`、`behavior_metrics.py`、`quality.py`（其余 6 个自检退出码均为 0） | 见 §4 P2.1 |
| 7 | **摄像头不可用**：0/1 号打不开；2 号能开但出黑帧（灰度均值 0.0~3.1）、固定 1.00 秒/帧（疑似虚拟设备） | 见 §3"等设备" |
| 8 | **黄金参考可逐字节重现**（2026-09-15 已按 **45 fps 新版**复验）：`golden_roi.csv`、`golden_motion.csv`+`meta.txt`、`golden_fir.csv`+`golden_fir_out.csv`+`meta.txt` —— **6 个文件 SHA256 全部与入库一致**。FIR 现为 **4036 样本**（30 fps 时代是 3940） | 三个 `gen_*.py --out-dir <tmp>` 后逐文件比 `Get-FileHash` |
| 9 | `python fpga/sim/q15_ref.py --selftest` → **PASS**（P1~P5 五项全过） | 直接跑 |
| 10 | **A 线至今零提交**——全仓库没有任何一条 `A:` 前缀提交 | `git log --oneline \| Select-String 'A:'` |
| 11 | `data/raw/` 为空（只有 `.gitkeep`），4 段标准视频未录 | `Get-ChildItem data/raw` |

### 2.1 已发现的口径不一致（截至 2026-09-15：3 处待处理 + 1 处已关闭）

> 按 `AGENTS.md` §3：**发现不一致要如实报告，不要自行拍板**。以下每处后面都在 §4 对应了一个处理步骤。

| # | 不一致 | 证据 | 归属 |
|---|---|---|---|
| A | **运动阈值两边不同**：`config.yaml` 是 **25**，C 线黄金参考是 **16** | `config.yaml:37` vs `fpga/sim/data_motion/meta.txt` | A ↔ C，见 P1.3 |
| B | **运动量的计算分辨率不同**：C 线在 384×288（3/5 抽取后的灰度）上算，`quality.py` 在 640×480 上算 | `golden_motion.csv` 的 `count=110592 = 384×288` vs `quality.py` | A，见 P1.3 |
| C | **灰度公式不同**：C 线是冻结式 `(77R+150G+29B+128)>>8`，`quality.py` 用的是 `cv2.cvtColor(BGR2GRAY)` | 契约 §4.4 明确警告"不能想当然用 cv2.cvtColor" | A，见 P1.1 |
| D | ✅ **已关闭（2026-09-14 由 C 线修正）**：契约 §4.1 现写"用例集合（**9 个 × 5 帧 = 45 行**）"，与 `golden_roi.csv` 一致 | `Select-String -Path docs/interface.md -Pattern '用例集合'` | 已解决，P1.2 按 45 行比对 |

> 注：A、B、C 三处**在 45 fps 变更中未被触及**——`motion_thresh` 仍是 `config.yaml` 25 / 黄金参考 16。P1.3 会把它测出来。

### 2.2 P0 同步时发现的红灯（已处理）

| 项 | 内容 |
|---|---|
| **现象** | 同步远端 14 个提交后，`pytest` = **48 passed, 1 failed** |
| **失败项** | `backend/tests/test_pipeline.py::test_run_pipeline_writes_contract_valid_csv` |
| **根因** | 该用例硬编码 `assert len(rows) == 90  # 3 秒 × 30 fps`；C 线把 `config.yaml` 的 `fps_nominal` 改成了 45 → 实际跑了 135 帧。**帧率变了，但 A 线的测试没跟着改。** |
| **处理** | 改为从配置推导：`expected = round(3 * float(CFG["fps_nominal"]))`，行数与 `frames_processed` 两处断言都用它 |
| **验证** | `.venv\Scripts\python.exe -m pytest -q` → **49 passed** |

> ⚠️ **这个修复不等于对契约 v1.1 的表态。** 它让用例对"将来任何帧率"都成立，与 45/30 之争无关；§5.2 第 12 项仍待 A 线正式表态。
> 教训值得记：**共享文件（`config.yaml`）被改时，受影响那一线的测试必须同批改**。这正是"改契约 → 同步实现 → 跑检查"顺序存在的原因——顺序反了，另一条线就是无声变红。

---

## 3. 阻塞地图

把"现在做不了"的原因分清楚，避免误判成"没得做"。

| 类别 | 项目 | 现在能做吗 | 说明 |
|---|---|---|---|
| **不阻塞** | 契约会签、黄金参考对拍、Q1.15 对拍 | ✅ | 输入向量靠固定 seed 重生成，容差 0 |
| **不阻塞** | rPPG 的**算法与单元测试** | ✅ | 用已知频率的合成正弦驱动，不需要真人视频 |
| **不阻塞** | 合成路径崩溃修复、工程收口 | ✅ | 纯代码 |
| **等视频** | 阈值标定（EAR / MAR / 头姿 / 质量权重）、A10 黄金 CSV、契约 §5.2 第 7 项 | ❌ | 硬做就是编数字，撞《05》铁律 1 |
| **等设备** | 摄像头实时路径 | ❌ | 实测本机摄像头不可用；**不要指望用它绕过视频** |
| **等他人** | 契约 v1.1（若对拍发现必须改口径）、C 线回填 | ⏳ | 走 `interface.md` §7 流程 |

---

## 4. 开发步骤

每一步固定五段：**目标 / 前置 / 动作 / 产出 / 完成判据**。

### P0 —— 对齐基线　✅ 2026-09-15 完成

**目标**：让本地看到的契约与团队冻结的那份一致，否则后面全是白做。

**实际执行记录**（真实命令与输出）

| 动作 | 命令 | 真实结果 |
|---|---|---|
| 拉取远端 | `git fetch origin` | `de65a0f..0788733  main -> origin/main`，**落后 14 个提交** |
| 合流 | `git rebase origin/main` | 归档提交 rebase 为 `f19aa00`，现 `ahead 1` |
| 核对契约 | `Select-String -Path docs/interface.md -Pattern '状态：'` | `🔒 v1.0（2026-09-11 冻结）→ 🚧 v1.1 草案（2026-09-13，C 线发起"全局 45 fps"）` |
| 核对版本号 | `Select-String -Path backend/contract.py -Pattern 'CONTRACT_VERSION'` | `CONTRACT_VERSION = "v1.0"`（草案期不动版本号，符合 §5.3 流程） |
| 全量自检 | `.venv\Scripts\python.exe -m pytest -q` | 首次 **48 passed, 1 failed**（见 §2.2）→ 修复后 **49 passed** |
| 其余三项自检 | `node frontend/mock.js --selftest` / `check_frontend_wiring.py` / `check_frontend_contract.py` | 分别 `ok: true` / 通过 / 通过（6 值枚举齐全） |

**两处环境坑（记录备查）**

1. `git pull --rebase` 因 **github.com 连接被重置**而失败。对象其实已由前一步 `fetch` 拉全，因此改用纯本地的 `git rebase origin/main` 完成合流——**fetch 成功过一次就够了，不必重跑 pull**。
2. `.git/` 在本机受限终端下**只读**，凡是要写 `.git` 的 git 命令（`fetch` / `rebase`）都需要在完整权限终端执行，否则报 `Permission denied`。这与 `AGENTS.md` §6.3 里"受限沙箱跑不了 csim"是同一类问题。

**产出**：本地代码 = 远端 `0788733` + 归档提交 `f19aa00`；基线全绿。

**完成判据**：✅ `docs/interface.md` 显示 v1.0→v1.1 草案；✅ `pytest` **49 passed**；✅ 其余三项自检通过。

**遗留**：本地有 **1 个未推送提交**（归档对话）＋本次的测试修复，尚未 push。push 前按 `AGENTS.md` 的规矩在群里说一声。

---

### P0.5 —— 对新基线的三处确认（进入 P1 前必读）

1. **契约现在是"冻结 + 草案"并存**：第 0~4 节仍是 v1.0 冻结口径，但 **§0 帧率与 §3.5 FIR 采样率已被 v1.1 草案改成 45**，且系数与黄金参考**已经按 45 重生成**（C 线 2026-09-14 实测 csim 8/8+17/17、cosynth II=1 / Fmax 154.38 MHz、cosim PASS）。
   → **含义**：P1 的所有对拍都必须以**当前磁盘上的**黄金参考为准（即 45 Hz 版），不要再引用 v1.0 时代的旧数字。
2. **A 线的会签项从 5 项变成 6 项**：新增 **§5.2 第 12 项**——确认采集/关键点/指标链路能跑 45 fps，且 rPPG 的 q 序列按 45 Hz 生成并与新黄金参考对拍。
3. **B 线的补签格式可以照抄**：契约 §5.2.1 里 B 线那条记录写了"验的是什么 / 命令 / 真实输出"三列 + 环境说明，A 线补签时沿用同一格式即可。

---

### P1 —— A ↔ C 黄金参考对拍　✅ 2026-09-15 完成

**执行结果（本机真实运行；证据 `metrics/evidence/2026-09-15_a_line_p1_golden_checks.json`）**

| 对拍项 | 契约 | 结果（容差 0） |
|---|---|---|
| 灰度 + 缩放口径 | §4.4 | ✅ 10 帧**逐像素**全等 |
| `roi_statistic` 黄金参考 | §4.2 | ✅ 45 行（9 用例 × 5 帧）逐行全等 |
| `motion_quality` 黄金参考 | §3.3 | ✅ 9/9 帧对逐项全等（**冻结口径下**） |
| Q1.15 量化口径 | §4.6 | ✅ 35 个真实样本 + 393,222 个扫描组合全等 |
| `fir_filter` 逐样本 | §4.5 | ✅ 16 段 / 4036 样本全等，含饱和段与跨段状态 |

一键复跑：`python metrics/scripts/check_a_line_p1_all.py`（真实执行 5 个脚本并写证据 JSON）。

**产出物**

| 文件 | 作用 |
|---|---|
| `backend/vital.py` | A 线侧实现：Q1.15 量化（`q15_from_roi_sum`）+ FIR 带通（`fir_process`，**系数从 C 线头文件读**，遵守"系数只有一处来源"） |
| `metrics/scripts/check_gray_formula.py` | §4.4 对拍 |
| `metrics/scripts/check_roi_golden.py` | §4.2 对拍 |
| `metrics/scripts/check_motion_golden.py` | §3.3 对拍 + 差异实测表 |
| `metrics/scripts/check_q15_golden.py` | §4.6 对拍 |
| `metrics/scripts/check_fir_golden.py` | §4.5 对拍 |
| `metrics/scripts/check_a_line_p1_all.py` | 一键复跑 + 证据归档 |

**P1 拿到的会签依据（可直接用于群里表态）**

- **第 1、2 项**（RGB 通道顺序 / 半开区间 ROI / `roi_statistic` 时间序列口径）：45 行逐行全等 → **认可**；
- **第 4 项**（灰度口径）：10 帧逐像素全等 → **认可**。附实测对照：`cv2.cvtColor` 与冻结式最大差 **1 LSB**、**13.4%** 像素不同；`cv2.resize` 最大差 **241**、**61.1%** 不同 —— 坐实契约"不能想当然用 OpenCV"；
- **第 11 项**（Q1.15 + `count==0` 丢弃）：全等 → **认可**；
- **第 5 项**（`motion_thresh` 取 16 还是 25）：**结论已具备**，但需要三方拍板 —— 见下；
- **第 7 项**（3/5 点采样混叠）：**仍待真实视频**；
- **第 12 项**（45 fps）：链路侧已有证据（1350 帧 / 真实视频 45.0 fps），**时间序列侧本轮补齐**（FIR 4036 样本全等）；但"rPPG q 序列"的端到端仍要等 P3 接上真实 ROI。

**P1 暴露的待拍板项（契约 §5.2 第 5 项）—— 实测数字**

| 口径 | 黄金参考 / C 线 | A 线现状 | 影响 |
|---|---|---|---|
| `motion_thresh` | **16**（`data_motion/meta.txt`） | **25**（`config.yaml`） | 9 个帧对里 1 对不一致，`motion_pixels` 偏 **698** |
| 计算分辨率 | **384×288**（`rgb2gray` 缩小后） | 640×480 | `count` 307200 vs 110592，**数值不可比**，9/9 全不一致 |
| 灰度公式 | 冻结式 `(77R+150G+29B+128)>>8` | `cv2.cvtColor` | 最大差 1 LSB，13.4% 像素不同 |

> ⚠️ 这三条**都不是 A 线单方面能改的**：改 `motion_thresh` 要重生成 C 线的黄金参考，
> 改分辨率等于改 `rgb2gray` 的工作尺寸。按 `AGENTS.md` §3，**如实报告 + 三方拍板**，
> 所以本轮只出数字，不动 `config.yaml`。

**目标**：用 A 线**自己的 NumPy 代码**证明"两边算的是同一个东西"，并据此完成契约会签里的书面表态。

**前置**：P0 完成。

**为什么先做这个**：这是 A 线清单里唯一不依赖视频、又能解锁 4 项契约待确认的活；而且产出的模块就是 rPPG 要用的零件。

#### P1.0 生成输入向量（已实测可复现）

```powershell
# 输出到临时目录，不要覆盖 C 线的入库物
python fpga/sim/gen_frames.py          --out-dir metrics/logs/_golden_check
python fpga/sim/gen_motion_vectors.py  --out-dir metrics/logs/_golden_check_motion
python fpga/sim/gen_fir_vectors.py     --out-dir metrics/logs/_golden_check_fir
```

**完成判据**：重生成的 `golden_*.csv` / `meta.txt` 与 `fpga/sim/data*/` 下的入库文件 **SHA256 一致**（本机已实测：`gen_frames` 45 行、`gen_motion_vectors` 9 帧对全等）。

#### P1.1 灰度口径对拍（契约 §4.4）

- 冻结式（唯一权威口径）：`Y = (77*R + 150*G + 29*B + 128) >> 8`，**容差 0**
- 输入：`metrics/logs/_golden_check_motion/` 下的 `rgb_frames.bin`（**10 帧** × 640×480×3 = 9,216,000 B）与 `gray.bin`（10 帧 × 384×288 = 1,105,920 B）
- **额外要求**：顺手打印 `cv2.cvtColor(..., COLOR_RGB2GRAY)` 与冻结式的差异（预期 ±1 LSB，纯红 76 vs 77），把"为什么不能用 OpenCV"变成实测数字。

**产出**：`metrics/scripts/check_gray_formula.py`
**完成判据**：`np.array_equal` 通过；脚本可重复执行且退出码为 0。

#### P1.2 ROI 累加对拍（契约 §4.2）

- 口径：**半开区间** `[x0,x1) × [y0,y1)`，等价 `img[y0:y1, x0:x1]`；`sum_r/sum_g/sum_b/count` 全整数
- 输入：`frames.bin` + `golden_roi.csv`
- ⚠️ **按 45 行 / 9 用例比对**（含文档漏写的 `oversized_roi`），不是文档里写的 40 行；顺手把这个不一致回报给 C 线

**产出**：`metrics/scripts/check_roi_golden.py`
**完成判据**：45 行逐行全等，容差 0。

#### P1.3 运动量对拍（契约 §3.3）—— **预判会失败，这正是它的价值**

- 冻结语义：阈值**严格大于**；`motion_ratio_q16 = (motion_pixels << 16) // count` **向下取整**；`count == 0` 输出 0；**第 0 帧无效**
- 输入：`gray.bin`（384×288）+ `golden_motion.csv`
- 已知的三处差异（§2.1 的 A/B/C）：`quality.py` 现在用 640×480 + `cv2` 灰度 + 阈值 25；黄金参考是 384×288 + 冻结灰度式 + 阈值 16

**产出**：`metrics/scripts/check_motion_golden.py` + 一份"差异实测表"
**完成判据**：要么对齐后全等，要么拿着一份**带真实数字的差异报告**去群里提契约变更。**两条路都算完成，但不能悬着。**

#### P1.4 Q1.15 量化口径对拍（契约 §4.6）

```powershell
python fpga/sim/q15_ref.py --selftest        # 已实测 PASS
python fpga/sim/q15_ref.py --sums-csv <你的 ROI 累加 CSV> --out-csv <对比输出>
```

- 权威式：`q = rha_div(256*sum_c − 32768*count, count)`，中心 128 → 0，合法均值**永不裁剪**，`count==0` 的帧**必须丢弃**
- 数据来源就是 P1.2 产出的 `sum_r/sum_g/sum_b/count` —— P1.2 和 P1.4 是同一条链路的上下游

**完成判据**：你的实现与 `q15_ref.py` 逐样本相同。

#### P1.5 FIR 黄金参考对拍（契约 §4.5）

- 输入：`fpga/sim/data_fir/`（`golden_fir.csv` 段元数据 + `golden_fir_out.csv` 逐样本期望值，3940 行）
- 算术：`acc = Σ h[k]·x[n−k]`，`y = sat16(acc >> 15)`，Q15 系数**唯一来源**是 `fpga/src/fir_coeffs_q15.h`
- 参照：`fpga/sim/host_model_fir.cpp`（C 线的主机模型）

**产出**：`backend/vital_filters.py`（或等价模块）里的 NumPy 实现 + `metrics/scripts/check_fir_golden.py`
**完成判据**：与 `golden_fir_out.csv` 逐样本相同，**容差 0**（v0.94 起从 ±1 LSB 收紧为 0）。
**这一步是双赢**：对拍做完，rPPG 的带通滤波器也就有了。

#### P1.6 回写与会签

1. 把 P1.1~P1.5 的**实际数字**整理成一份结论（放 `metrics/evidence/`，正式证据要入库）。
2. 在群里回"**收到、不冲突**"，逐条说明 §5.2 里 A 线的 5 项；第 7 项注明"**待真实视频**"。
3. 由任一人在 `interface.md` §5.2 补记结论与日期。

**产出**：**A 线的第一条提交**（`A:` 前缀，正文含"怎么验证的"）。
**完成判据**：§5.2 里 A 线的 5 项都有结论。

> ⚠️ `interface.md` 是共享文件。**不要自己直接改**——按 §5.3 的流程走：公告 → 补签 → 提交。
> ⚠️ 如果对拍发现口径不可用（例如 §4.6 的量化需要留余量），这是**合法的变更理由**，但要走 §7 提变更出 v1.1。**不要在两边各实现一套。**

---

### P2 —— 骨架收口　✅ 2026-09-15 完成

**执行结果**

| 子步骤 | 内容 | 结果 |
|---|---|---|
| P2.1 | 修合成路径崩溃 | ✅ 见下 |
| P2.2 | 自检脚本化 | ✅ `python metrics/scripts/check_a_line_all.py` → **15/15 通过**（约 18 s） |
| P2.3 | **自检不再改脏工作区**（P2.2 暴露出来的） | ✅ 见下 |

**P2.3 修了什么**

P2.2 做完后按 `AGENTS.md` §6.1 的清单逐条实跑，发现**"提交前自检"本身会把工作区搞脏**，
与它自己最后那行"`git status --short` 只应出现你本线的改动"直接矛盾：

| 现象 | 根因 | 改法 |
|---|---|---|
| 跑一次自检后 `metrics/evidence/*.json` 变脏 | 该证据文件带**运行时间戳**，每次跑内容都不同，而我把它无条件写进了**已入库**目录 | 改为**默认写入 `metrics/logs/`（不入库）**；要归档某一次运行，显式加 `--evidence` 再提交 |
| `metrics/evidence/js_frames.jsonl` 变脏 | 入库版本是 **UTF-16**（Windows PowerShell 5.1 的 `>` 重定向产物）；当前 shell 是 UTF-8，重跑必然产生不同字节 | 归一为 **UTF-8**。**JS mock 的输出本身是完全确定性的**（连跑两次 SHA256 相同，`ts` 是逻辑值 0/1/2…），所以归一之后重跑即逐字节相同 → 不再变脏 |

**判据（实测）**：跑完整自检入口前后，`git status --porcelain` 输出**完全一致**。

**P2.4（自查 P2 时补的）—— 让"一条命令"在干净检出上也成立**

自查时发现两个问题：

1. **新检出跑不通**：测试向量是生成物（`.gitignore` 忽略、靠固定 seed 重建），
   队友克隆下来直接跑自检入口，[D] 段会 5 项全红 —— 而它们的报错信息只是"缺少文件"。
   改法：`check_a_line_p1_all.py` 增加 `ensure_vectors()`，**检测缺失并自动补跑对应生成器**。
   实测（把 `gray.bin` / `series.bin` 移走后跑）：自动跑 `gen_motion_vectors.py` + `gen_fir_vectors.py`
   → 5/5 通过，退出码 0。
2. **跳过数量报错**：缺 node 时跳过 3 项，总结论却打印"另有 **1** 项跳过"。
   改法：按实际跳过的项计数（现在会列出具体是哪 3 项）。

**这一轮还验证了"自检本身会不会失败"**（之前只测过通过路径）：

| 场景 | 做法 | 期望 | 实测 |
|---|---|---|---|
| 失败传播 | 临时移走 `gray.bin` | 相关对拍报红、总退出码 1 | ✅ 2 项 FAIL → P1 聚合 3/5 → 总退出码 **1** |
| 降级路径 | 把 node 从 PATH 移除 | 标 SKIP、不误报失败 | ✅ 跳过 3 项、退出码 **0** |
| 自举 | 干净检出（无任何向量） | 自动生成后跑通 | ✅ 见上 |

**P2.1 修了什么**

根因：合成帧源的 `SyntheticImage` 只有 `shape` 与一个亮度值、**不是图像**，
而 `run_pipeline` 在选择关键点检测器时没有对帧源类型做守卫，于是真 MediaPipe 把它送进
`cv2.cvtColor` 抛 `cv2.error`。受影响的有 4 条路径：`run_pipeline --source synthetic`（不带 `--stub`）
以及 `face_landmark.py` / `behavior_metrics.py` / `quality.py` 三个模块自检。

改法（沿用项目既有的"链路优先 + 明确告知"模式，与 `capture.py` 缺 OpenCV 时回退合成帧源同款）：

1. `run_pipeline`：识别到合成帧源且未显式 `--stub` 时，**打印三条警告并自动改用 StubLandmarker**，
   摘要里 `landmark_source` 如实为 `stub`（引用数字前必须看这个字段）；
2. `MediaPipeLandmarker.detect()`：加输入类型守卫，把晦涩的 `cv2.error` 换成能指出路的 `TypeError`（其他调用者的兜底）；
3. 三个模块自检：对合成帧源显式用 `force_stub=True`。

实测：`backend/` 下 **9 个模块自检全部退出码 0**（此前 3 个崩溃）。

**P2.2 顺带挖到并修掉的一个更大的坑**

`backend/README.md` 的"怎么跑"里写着 `python backend/config.py` 这类命令，但**实际全线跑不通**：

```
E:\fpga> python backend\config.py
ModuleNotFoundError: No module named 'console'
```

根因：`env.ps1` 里的 `python` 指向 `.tools\python312`，那是 **embeddable** 版（带 `python312._pth`），
它**不把脚本所在目录加进 `sys.path`**、也不加 CWD、还忽略 `PYTHONPATH`。

改法：`env.ps1` 把 `.venv\Scripts` 排到 PATH 最前 —— `.venv` 是 virtualenv，行为正常，
且经 `_vigilens_base.pth` 桥接到基础环境的 site-packages（依赖一个没少）。
实测：9 条文档命令 + `python -m pytest`（49 passed）**逐条实跑通过**。
细节记在 `.tools\README.md`（本机笔记，不入库）。

**目标**：把 M1 骨架里"跑不通的路径"补齐，让文档里写的每条命令都真的能跑。

**前置**：无（可与 P1 并行）。

#### P2.1 修合成路径崩溃

```powershell
python backend/run_pipeline.py --source synthetic --pattern blink --seconds 3   # 现在会崩
python backend/face_landmark.py                                                 # 现在会崩
python backend/behavior_metrics.py                                              # 现在会崩
python backend/quality.py                                                       # 现在会崩
```

本机实测：`backend/` 下 9 个模块自检里，**这 3 个 + `run_pipeline` 的合成路径**失败，其余 6 个（`config` / `decision` / `mock` / `capture` / `storage` / `contract`）退出码均为 0。

根因：合成帧源的 `SyntheticImage` 只有 `shape` 和一个 `average_luma()`，而 `MediaPipeLandmarker.detect()` 直接把它当 ndarray 送进 `cv2.cvtColor`。`run_pipeline` 在选择 landmarker 时**没有对帧源类型做守卫**。

修法方向（择一，别都做）：
- 合成帧源 + 真 MediaPipe 时给出**明确报错**（最短路径，不改语义）；
- 或让 `SyntheticImage` 提供 `__array__` / 转成 ndarray（要注意会改变 `quality.py` 的降级路径行为）。

**完成判据**：两条命令都能正常结束，且 `backend/README.md` 里"只想到处看看"那一节列的命令**逐条实跑通过**。

#### P2.2 自检脚本化

把 `backend/README.md`"怎么跑"一节里的命令做成一个可重复执行的入口（或写进 `metrics/scripts/`），一条命令跑完 A 线全部自检。

**完成判据**：新人在干净环境按 README 操作能一次跑通，不需要额外口头解释。

---

### P3 —— rPPG 链路　✅ 2026-09-15 完成

**执行结果**

| 子步骤 | 内容 | 结果 |
|---|---|---|
| P3.1 | 链路与模块边界 | ✅ `backend/vital.py`：ROI → G 通道 Q1.15 → 冻结 FIR 带通 → 频谱峰值 → BPM |
| P3.2 | 合成信号单测 | ✅ `backend/tests/test_vital.py` **16 项**（含命中频点与偏移半频点两种正弦） |
| P3.3 | 质量门控 | ✅ 走既有 `decision.gate_vitals`，未绕过 |
| P3.4 | 接进 `run_pipeline` | ✅ 逐帧喂样本、每帧出估计，替换掉原来"占位恒 null" |

**链路（与硬件同一条链）**

```
额头 ROI 绿通道累加 ──q15_from_roi_sum──▶ Q1.15 序列 ──fir_process──▶ 带通序列
      (roi_statistic 等价)              (契约 4.6)      (冻结 Q15 系数)
                                                              │
                                        去直流 + Hann 窗 ──▶ rfft ──▶ 峰值 ──▶ BPM
```

**几个刻意的设计决定（都对应用户/契约的硬要求）**

- **滑窗填满才出数**：窗口 = `config.yaml` 的 `window_seconds`（30 s → 1350 样本）。没填满一律 null。
- **峰不过半就不报数**：判据是"峰值 ±1 个频点内的能量占带内总能量**过半**"。
  这是**结构性判据**（"这个峰是否占主导"），不是可调阈值，所以没有引入新的魔法数字；
  纯正弦下 conf > 0.9，纯噪声下 conf ≈ 0.03 —— 实测分离度约 30 倍。
- **漏帧不补值，但要校正频率标度**：契约 §4.6 规定 `count==0` 的帧必须丢弃，
  所以人脸丢失的那帧不喂样本。此时窗口会跨越多于 `window_seconds` 的真实时间，
  `estimate()` 用**窗口首尾 frame_id 反推有效采样率**再做 FFT —— 不校正的话漏帧会把频率
  系统性抬高（每漏 1/2 帧就翻倍）。单测里专门断言了这条。
- **`rr_*` 恒为 null**：契约 §3.5 已冻结"63 阶 @45 fps 做不了呼吸带"，
  需要 PS 侧降采样 + **另一组系数**，而那组系数尚未冻结。宁可空着也不给假数字。
- **绿通道下标写死 1**：BGR 与 RGB 里下标 1 都是绿，这样即使上游忘了做通道序转换，
  rPPG 用的仍然是绿通道 —— 把契约 §0.1 点名的 R/B 互换陷阱**结构性规避掉**。

**过程中修掉的一个真 bug（P1 的黄金参考没抓到）**

`vital.py::fir_process` 在"逐样本喂 + `hist=None`"时历史长度算错：
原实现把长度 1 的输入直接当扩展序列，`ext[-(N-1):]` 只取到 1 个元素，
下一帧就报 `hist 长度应为 62，收到 1`。

**为什么 P1 没抓到**：P1 的黄金参考只**整段喂**（段长都 ≥ 63），而这是**逐样本流式路径**
独有的分支。已补回归用例 `test_fir_streaming_equals_batch`：
同一串 200 个样本，逐样本喂与整段喂必须逐样本相同。

**验收（实测）**

- `python -m pytest -q` → **65 passed**（49 → 65，只增不减）；
- 合成正弦：**命中频点**（1.20 Hz）与**跨在两个频点正中间的最坏情况**（1.25 Hz）误差均 ≤ 2 BPM（= 1 个频点间距）；
- 纯噪声、恒定输入、半窗样本 → 一律 `hr_bpm = null`；
- 端到端（合成帧源无真实图像）→ `vital.*` 仍**四项全为 null**；
- 同一串样本喂两次结果逐字段相同（确定性纪律）。

---

### P3 原始动作清单（存档，已全部执行）

**目标**：**不依赖任何真人视频**，把心率的信号处理链做出来并用合成信号验证正确性；视频到位后只剩"接真实 ROI"这一步。

**前置**：P1.4（量化口径）、P1.5（FIR 参考实现）。

**动作**

1. **定模块边界**：新增 `backend/vital.py`。
   - 输入：ROI 时间序列（Q1.15，符合 §4.6；`count==0` 的帧已被丢弃）
   - 链路：去直流 / 归一化 → 带通 → `rfft` 峰值 → BPM
   - 输出：`hr_bpm` / `hr_conf` / `rr_per_min` / `rr_conf`（契约要求**键齐全、允许 null**）
2. **合成信号单测**：喂已知频率的正弦，断言解出的 BPM；喂噪声 / 运动伪影，断言置信度低。
3. **门控**：`vital_require_quality`（现 0.75）不达标时一律 `null`——`decision.gate_vitals` 已有实现，接上即可，**不要绕过它**。
4. **端到端**：把 `vital` 接进 `run_pipeline`，替换掉现在那个"占位恒 null"的逻辑。

**产出**：`backend/vital.py` + `backend/tests/test_vital.py`

**完成判据**
- `pytest` 全绿（在 49 项基础上只增不减）；
- 无真实视频时，`vital.*` **仍然全为 `null`**——这不是 bug，是契约承诺；
- 合成正弦的 BPM 误差在单测里被明确断言（**用合成信号的已知频率作真值，不要写"大约 72"这类模糊断言**）。

> 依赖纪律：不要把 `numpy`/`scipy` 变成必需依赖。`backend/` 现有代码都有"没装 OpenCV / 没装 NumPy 就走降级路径"的写法，沿用同一风格。

---

### P4 —— 视频到位后的标定与锁定（任务号：A2/A3/A5/A6/A7/A10）

**🟡 脚手架已搭好（2026-09-15），等真实视频到位即可执行。**

操作细节全部在 **`backend/P4_CALIBRATION_GUIDE.md`**；这里只记"搭了什么、怎么验证的"。

| 工具 | 作用 | 现在能验证吗 | 实测 |
|---|---|---|---|
| `metrics/scripts/check_p4_readiness.py` | 4 段视频 + 标注的就绪检查（分辨率 / 帧率 / 时长 / SHA256） | ✅ | 空目录 → 正确列出缺 8 项；构造合规假数据 → **就绪 4/4**；换成 320×240@15fps → **正确拦下**并标 ⚠️ |
| `metrics/scripts/make_golden.py` | A10 黄金结果生成 + **锁版本**（视频 / config / git HEAD 三样指纹 + `backend/` 代码脏状态） | ✅（用冒烟视频） | 单文件 + `--verify` → 135 行、两次运行逐字节一致；`_lock.json` **不含时间戳**（重跑不脏工作区）；第一段失败也会继续跑完其余段 |
| `metrics/scripts/sweep_thresholds.py` | 用标注当真值扫阈值（事件级 + 帧级 P/R/F1） | ✅（`--self-test`） | 已知答案下选出 0.16（落在 0.15~0.30 平台区）、F1=1.0、TP=3；反例阈值 0.10 → 漏检 3 次 |

**端到端演练（2026-09-15）：用"刚拿到视频"的场景把三个工具串起来跑**

造一套 4 段 640×480@45fps、20 s 的合规视频（用 matplotlib 自带的公开人像当素材，
这样 MediaPipe 真能检出脸、指标不是全零）+ 4 份标注，然后走完整条链：

| 步骤 | 结果 |
|---|---|
| ① 就绪检查 | 4/4 通过规范校验（分辨率/帧率/时长） |
| ② `make_golden --verify` | 4 段各 900 行，**两次运行逐字节一致**，锁定文件齐备 |
| ③ 扫描器直读 ② 的 CSV | 列名对得上、链路真能连通 ✅ |

**这次演练抓到一个只有端到端才暴露的真问题**：
那张公开人像的静止人脸，MediaPipe 算出的 EAR 均值是 **0.1961**，而占位阈值是 0.21 ——
于是**整段都被判成闭眼**（perclos=1.0、状态 fatigue_risk）。此时若配一条假的眨眼标注，
把阈值调到"整段都判成闭眼"会产生一个覆盖全序列的预测段，
**事件级 P/R/F1 反而全是 1.000** —— "全判成闭眼"能拿满分。

修了两处：

1. **选值改用帧级 F1**（事件级只做复核）—— 帧级不会被"整段判成事件"骗过；
2. **新增可分性判据**：标注区间内外的取值若完全重叠，则**任何阈值都分不开**，
   工具直接**拒绝给建议值**并列出常见原因。这条判据不需要任何阈值参数（纯结构性）。
   实测：那张人像的 CSV 喂进去 → 裕度 -0.0256 ≤ 0 → 正确拒绝，不再输出假建议值。

`--self-test` 已补上这条退化场景的回归断言，避免以后退回去。

> 三个工具的设计都坚持"**同样的输入得到同样的输出**"：锁定文件不含时间戳、`--verify` 跑两次比对字节。
> 这直接来自 P2.3 的教训 —— 工具自己不能把工作区搞脏。

> **这一阶段整体被视频阻塞。** 下面每一条都要求：先有真实视频 → 跑 → 记录**真实数字** → 才改 `config.yaml` 并注明依据。
> 阈值现在全是占位值，`config.yaml` 里已用 `⚠️ 占位值` 标注。

**前置**：`data/raw/` 里至少有 4 段标准视频（`still` / `blink` / `yawn` / `turn`，规范见 `data/README.md`）。

| 步骤 | 任务号 | 标定对象 | 现有占位值 | 依据来源 |
|---|---|---|---|---|
| P4.1 | A3 | `ear_close_threshold` / `min_close_frames` | 0.21 / 3 | MediaPipe 需实测重标定（dlib 常用 0.21，MediaPipe 一般 0.20~0.25） |
| P4.2 | A5 | `mar_threshold` / `yawn_min_duration_ms` | 0.6 / 800 | 用 `yawn` 视频区分"打哈欠"与"说话" |
| P4.3 | A6 | `pose_yaw_max_deg` / `pose_pitch_max_deg` / `face_visible_min` | 30.0 / 25.0 / 0.7 | 用 `turn` 视频 |
| P4.4 | A7 | `quality_weights` / `light_score_min` / `motion_score_max` / `light_target` | 0.5/0.3/0.2 等 | 用"正常 / 大幅晃动 / 变暗"三种场景 |
| P4.5 | A10 | 4 段视频的黄金 CSV | 无 | 锁定版本，改动不得破坏 |
| P4.6 | — | 契约 §5.2 第 7 项：3/5 点采样的混叠是否让运动量偏噪 | 待定 | **只有这一项契约明确要求"接真实视频后给结论"** |

**完成判据**
- 每个改动的阈值都有**指向真实运行日志**的依据；
- 同一段回放跑两次，末帧 JSON 与 CSV **逐字节相同**；
- `data/golden/` 有锁版本的基准；`metrics/evidence/` 有对应的正式证据。

> ⚠️ 若 P4 过程中发现要改 `motion_thresh` 之类**会动黄金参考**的值，必须走契约变更（§7），并同步重新生成黄金参考——`AGENTS.md` §9 已点名这条。

---

### P5 —— 与 B 线合体（里程碑 M2；任务号：A9 的收尾）

**目标**：把 A 线的真实 JSON 接到 B 线的网页上。

**前置**：P3 完成（vital 键齐全）、P4 至少完成 P4.1~P4.3。

**动作**：走 `backend/hub.py` / `websocket.py` 已有的数据源开关（`mock` / `file` / `bus`），把数据源切到 A 线真实产出。

**完成判据**：浏览器里看到的是 A 线跑出来的真实数字，且六种状态都能触发；B 线的 `frontend/mock.js --selftest` 与跨语言契约检查仍然通过。

---

## 5. 完成定义（DoD）汇总

| 阶段 | 判据 |
|---|---|
| P0 | 本地契约 = v1.0；`pytest` 49 passed |
| P1 | 4 项对拍脚本可重复执行；§5.2 中 A 线的 5 项均有结论；**A 线第一条 `A:` 提交已推** |
| P2 | `backend/README.md` 列出的命令逐条实跑通过 |
| P3 | `vital.py` 有单测；无视频时 `vital.*` 仍全 `null`；`pytest` 只增不减 |
| P4 | 每个阈值有真实依据；重复运行逐字节一致；`data/golden/` 锁版本 |
| P5 | 网页显示 A 线真实数据 |

---

## 6. 每步都要跑的纪律命令

```powershell
. .\env.ps1                                              # 载入本机工具链
.venv\Scripts\python.exe -m pytest -q                    # 期望 65 passed（只增不减）
python backend/run_pipeline.py --source metrics/logs/_smoke.mp4 --summary metrics/logs/_smoke_summary.json
git status --short                                       # 只应出现你本线的改动
```

**引用任何数字前先看 `landmark_source`**：为 `stub` 时 EAR/MAR 是占位几何量，**不得作为算法结果引用**。

---

## 7. 提交与产出纪律

| 项 | 要求 |
|---|---|
| 提交前缀 | `A:`（`backend/` 下的改动）；动契约才是 `contract:` |
| 提交正文 | 为什么改 / 改了什么 / **怎么验证的（必须贴真实命令 + 真实结果）** |
| 正式证据 | 入 `metrics/evidence/`；`metrics/logs/`、`metrics/csv/` 是生成物，**不入库** |
| AI 协作记录 | `report/llm_log/YYYY-MM-DD_主题.md`，**必须人类手写**，AI 不得代写 |
| 共享文件 | `config.yaml` / `docs/` / 根 `README.md` 改前先公告 |
| 红线 | 不编数字；不上传可识别人脸视频；不做医疗诊断表述 |

---

## 8. 已知坑（本机实测）

| 现象 | 原因 | 处理 |
|---|---|---|
| `--source synthetic` 不带 `--stub` 崩溃 | 合成帧不是 ndarray | P2.1 |
| `python backend/quality.py` 崩溃 | 同上 | P2.1 |
| 摄像头打不开 / 出黑帧 | 0/1 号不可用，2 号疑似虚拟设备 | **不要用摄像头绕过视频**；如需排查请在普通终端（非受限终端）重试 |
| `python` 命令找不到 | 系统没有 Python，工具都在 `.tools/` | 先 `. .\env.ps1` |
| `.venv` 与 `.tools\python312` 的关系 | `.venv` 靠 `_vigilens_base.pth` 桥接到基础环境 | 依赖装在 `.tools\python312`，不是 `.venv` |
| 装新版 mediapipe 后 EAR/MAR 静默变占位量 | mediapipe ≥0.10.30 删除了 `mp.solutions` | **锁 0.10.21 + Python 3.12**；改依赖后必须复查 `landmark_source` |
| 控制台中文乱码 | 代码页是 GBK | 用 Windows Terminal / VS Code；脚本已内置 `enable_utf8_console()` |

---

## 9. 待决策 / 待他人确认

| # | 事项 | 找谁 | 卡住什么 |
|---|---|---|---|
| 1 | `motion_thresh` 取 25 还是 16（§2.1 的 A） | A ↔ C 会签 | P1.3 |
| 2 | 运动量在 384×288 还是 640×480 上算（§2.1 的 B） | A ↔ C 会签 | P1.3 |
| 3 | ~~`golden_roi.csv` 用例数~~ ✅ **已由 C 线关闭**（§2.1 的 D） | — | 已解决 |
| 4 | 呼吸带降采样到 2 Hz 后 **int32 累加器溢出**（`Σ|h| = 70247 → 上界 2.30e9 > 2^31`），且 63 阶 @2 Hz 群延迟 15.5 秒 | **回填 C 线** | P3 的呼吸率路径 |
| 5 | **契约 v1.1 的 45 fps 是否认可**（§5.2 第 12 项）——A 线要确认链路能跑 45 fps，且 rPPG q 序列按 45 Hz 与新黄金参考对拍 | A 线自己判断，然后会签 | P1/P3 的全部数字 |
| 6 | 未推送提交怎么处理（归档 `f19aa00` + 本次测试修复） | 三人 | push 时机 |
| 7 | **C 线改共享文件 `config.yaml` 时没有同批跑 A 线的测试**，导致基线无声变红（§2.2） | 三人约定流程 | 未来的每一次契约变更 |

---

## 10. 下一步

> ✅ **P0 已完成**（2026-09-15）：同步基线 + 修红灯 + 45 fps 对齐。
> ✅ **P1 已完成**（2026-09-15）：5 项黄金参考对拍全绿，证据
> `metrics/evidence/2026-09-15_a_line_p1_golden_checks.json`；契约 §5.2 补签见 `docs/interface.md` §5.4。
> ✅ **P2 已完成**（2026-09-15）：合成路径崩溃修复 + 一条命令自检入口（15/15 通过）。
> ✅ **P3 已完成**（2026-09-15）：rPPG 链路（合成信号驱动，16 项单测），`pytest` 49 → **65**。
>
> **日常入口**：`. .\env.ps1` → `python metrics/scripts/check_a_line_all.py`（约 20 秒，退出码即结论）。
>
> 🟡 **P4 脚手架已搭好**（2026-09-15），整体仍被真实视频卡住。
> `data/raw/` 一有素材就照 **`backend/P4_CALIBRATION_GUIDE.md`** 走：
> ① 就绪检查 → ② 录+标注 → ③ 锁黄金结果 → ④ 扫阈值 → ⑤ 写回并重锁。
> 三个工具都已写好并各自验证过（见 §4 P4 的表格）。
>
> **在那之前，A 线能离线做完的部分已经做完了。**
> 入场券是：`python metrics/scripts/check_p4_readiness.py` 报"就绪"。
>
> **仍然只能由人做的两件事**：
> 1. 契约 §5.2 的**群公告**（补签记录已写好，见 `docs/interface.md` §5.4）；
> 2. 第 5 项 `motion_thresh`（16 还是 25）的**三方拍板** —— 实测差异表在 §5.4 末节。

---

*本文件由 A 线维护。与 `backend/README.md` 的"下一步（A 线）"章节保持同步；两处冲突时以本文件为准，并把 `README.md` 改过来。*
