# AGENTS.md —— 知倦 / VigiLens 项目 AI 协作规范

> **本文件是任何 AI（含 DSH / Claude Code / Cursor / Copilot 等）进入本仓库后的第一份必读文件。**
> 动手改任何文件之前，**先读完本文件**。本文件与 `docs/05_AI使用约束.md`、`docs/07_提交规范与分工提交说明.md` 配套：
> **详版规则以那两份文档为准，本文件只做"入口 + 底线 + 事实索引"**，冲突时以详版文档为准。
> 维护：改动本文件用 `docs:` 前缀提交。最后核对：2026-09-10（基于仓库当时真实状态）。

---

## 0. 30 秒速览（先读这段，再读全文）

| 项 | 内容 |
|---|---|
| 项目 | VigiLens / **知倦** —— 基于摄像头 + FPGA 的**无接触疲劳趋势监测终端**（2026 嵌入式芯片与系统设计竞赛 · AMD 赛道 3.3，初级组） |
| 仓库 | <https://github.com/huangder/vigilens-pynq>（public，默认分支 `main`，MIT） |
| 三线并行 | **A 线**算法/后端（`backend/`）· **B 线**前端/服务（`frontend/` + 部分 `backend/`）· **C 线**FPGA/HLS（`fpga/`、`board/`） |
| 唯一耦合点 | **`docs/interface.md`（接口契约）** —— 三线并行的唯一技术保障，改它必须先公告 |
| 一切阈值 | **`config.yaml`**（唯一来源，代码里不许出现魔法数字） |
| 当前阶段 | M1 基础框架（骨架已跑通，算法在逐步替换占位实现）；M3 之前**不需要板卡** |
| 一句话产品承诺 | **先判断能不能测，再决定测出什么** —— 质量门控不通过时明确输出"不可靠"，**不报跳变数字** |

---

## 1. 五条铁律（违反任意一条 = 返工或失去评审信任）

摘自《05_AI使用约束.md》第一节，**这是本项目的最高优先级规则**：

1. **数据真实性红线** —— AI 不得编造任何测试数据、实验数据、性能数字、资源/时序报告、BPM 数值、准确率。
   所有数字必须来自**真实运行**的输出；AI 只能"分析"用户贴给它的真实日志。
2. **逐行理解 + 本地验证** —— AI 生成的代码必须逐行看懂、本地跑通、写进测试。**不理解的代码不许提交。**
   AI 说"可综合 / 能跑"一律**不算数**，以本地仿真/综合为准。
3. **记录必交** —— 每次 AI 协作按《05》第五节模板存档到 `report/llm_log/YYYY-MM-DD_主题.md`。
   这是赛制**必交项**。/!\ **这条记录必须由人类亲手写** —— AI 不得代写"大模型协作记录"本身。
4. **隐私红线** —— **不上传可识别身份的人脸原始视频到任何外部服务**（含云 AI 平台）。确需上传只传裁剪后的匿名截图。
5. **医疗边界** —— 不得做疾病/健康诊断断言。一律表述为"**趋势监测 / 辅助提示 / 实验室原型**"。

> 一句话：**AI 是"助手 + 评审 + 脚手架生成器"，不是"作者 + 测试员 + 决策者"。**

### 1.1 由铁律直接推导出的"AI 自检三条"

1. **没跑过的，不许说"跑通了"** —— 没执行 `pytest` / csim / 综合，就只能写"未验证，预期为 X"。
   本仓库**不是**"看起来能跑"，全部结论以命令与产物为证。
2. **不确定必须标注** —— 每个结论显式区分 `【已验证】`（有命令与输出）/ `【推测】`（有依据未验证）/ `【不确定】`（待验证假设）。
   参考 `fpga/README.md` 的"待验证假设 / 已知风险"表，那是本项目标注不确定性的范式。
3. **不许发明文件、字段、路径** —— 只能引用**你实际读过**的文件内容。本文件第 7 节给出了"权威事实表"，
   凡与它冲突，先按第 3 节规则核对权威来源，**不要凭印象补全**。

### 1.2 红灯清单（出现即停，摘自《05》第六节）

- [ ] 给了数字，但仓库里没有对应的真实运行日志 → **停，去跑一遍再写**
- [ ] 代码看不太懂但"跑起来了" → **停，读懂或删掉重写**
- [ ] 想写"精确血氧 / 医疗级准确率"之类的表述 → **停，改回"趋势/辅助/原型"**
- [ ] 想上传带清晰人脸的视频给外部 AI → **停，匿名化或换截图**
- [ ] 让 AI 替用户写"大模型协作记录" → **停，记录必须亲手写**

---

## 2. 项目定位与硬边界（防"跑偏式幻觉"）

### 2.1 是什么

只用一个普通 RGB 摄像头，提取**眨眼频率、闭眼时长、PERCLOS、打哈欠、头部姿态、运动质量**等指标，
并在受控条件下给出**心率 / 呼吸率趋势**估计。核心差异化能力是 **"测量是否可信"**：
光照、运动、人脸可见率、波形稳定性融合为**信号质量门控**。

**FPGA 不是转发器**：PYNQ-Z2 的 PL 端承担 ROI 像素统计、帧间运动量、FIR 带通滤波等确定性视觉与时序预处理
（自研 Vitis HLS IP），提供低延迟、低抖动的特征流。若把 PL 降级成纯 DMA 搬运，会正中《01》4.2 点名的致命短板。

### 2.2 绝对不能说的话（本项目红线表述）

| ❌ 禁止 | ✅ 只能说 |
|---|---|
| 医疗诊断 / 疾病识别 / 健康评分 | 疲劳风险提示 / 状态趋势监测 / 非接触测量原型 |
| 血氧 / 血压 / 医疗级精度 | （RGB 摄像头原理上不可靠，**一律不提供**） |
| "准确率 98%"等未实测数字 | 实测得出的指标 + 指向 `metrics/evidence/` 或 `fpga/report/` 的证据 |
| "已经上板验证"（M3 之前） | C 仿真 / C 综合 / RTL cosim 结果（**必须写明来源**） |

---

## 3. 权威顺序（信息冲突时怎么办 —— 最重要的一节）

**遇到任何"说法不一致"，按下列顺序采信，并把不一致如实报告给人类，不要自行拍板：**

1. **`docs/interface.md`** —— 字段、枚举、寄存器偏移、算法口径的唯一技术准绳
2. **`config.yaml`** —— 所有阈值的唯一来源（`fpga:` 段改了等于改契约）
3. **`backend/contract.py` + `frontend/mock.js`** —— 契约的两套**实现**（必须与 1 一致）
4. **`fpga/src/*.cpp` / 测试台 / 黄金参考 CSV** —— 硬件侧实际行为
5. **各目录 `README.md`** —— 现状说明（**可能滞后**，见第 8 节）
6. **`docs/00`~`07`、根 `README.md`** —— 方案与流程（**最新进展不一定同步到这里**）
7. 代码注释 / 提交历史 —— 仅作线索，不作结论

> ⚠️ **"代码与文档冲突"是常态，不是异常。** 正确动作是：确认**哪一个是对的**（跑命令验证），
> 然后**同一提交里把两边改成一致**，而不是只改一边。

---

## 4. 唯一耦合点：契约变更流程（唯一能让三人一起返工的路径）

改契约的代价远高于改代码，因此单独定流程（《07》第 7 节）：

1. **先改 `docs/interface.md`**，并在其**第 6 节「变更记录」**登记：日期 / 版本号 / 谁 / 改了什么；
2. **同步两套实现**：`backend/contract.py`（Python 侧，含 `CONTRACT_VERSION`）与 `frontend/mock.js`（JS 侧）；
   只改一边，另一边的校验器就会把**合法帧判成非法**；
3. **跑跨语言检查**（见第 6 节第 4、5 条）；
4. **群公告**，等另两人确认「收到、不冲突」再提交；
5. **会签升级版本号**。

**约束**：

- 字段名、枚举、寄存器偏移**只允许在一处定义**；A 的真实输出与 B 的 Mock 都必须过
  `new_frame()` / `validate_frame()`（Python）与 `VigiLensMock.validateFrame`（JS）。
- 提交前缀必须是 **`contract:`**，正文写清"影响哪条线、需要谁配合改什么"。
- 顺序**不可颠倒**：先契约 → 再两个校验器 → 最后业务代码。反了就会出现"两边各自合法、拼起来不合法"。

---

## 5. 三条线与目录归属（谁能动哪些文件）

| 线 | 负责人 | 目录 / 文件 | **不要碰** |
|---|---|---|---|
| **A 线** 算法/后端 | A | `backend/`（`capture` / `face_landmark` / `behavior_metrics` / `quality` / `decision` / `storage` / `run_pipeline` / `tests/`）、`data/annotations/`、`data/golden/` | `frontend/`、`fpga/`、`board/` |
| **B 线** 前端/服务 | B | `frontend/`、`backend/`（`api.py` / `websocket.py` / `mock.py` / `hub.py` / `contract.py` 的 JS 侧对应物）、`metrics/evidence/`（界面截图） | A 线算法文件、`fpga/` |
| **C 线** FPGA | C | `fpga/`（`src/` / `sim/` / `report/` / `run_hls.tcl`）、`board/`、`skill/fpga_hls_c_line.md` | `backend/`、`frontend/` |
| **共享** | 三方 | `config.yaml`、`docs/`、根 `README.md`、`metrics/scripts/`、`report/`、`skill/` | —— |

**根文件（`README.md` / `config.yaml` / `.gitignore` / `requirements.txt` / `pytest.ini` / `AGENTS.md`）改前先说一声**，这是三人共用文件。

**分支策略**：只改自己那条线的目录 → 直接推 `main`；改契约 → 必须先公告 + 另两人确认；
实验性/可能推翻的改动 → 开分支 `a-line/xxx`、`b-line/xxx`、`c-line/xxx`。

### 5.1 各目录一句话

| 目录 | 内容 |
|---|---|
| `backend/` | 契约、采集、关键点、指标、质量、规则融合、存储、FastAPI/WebSocket/Mock、49 项 pytest |
| `frontend/` | 零依赖仪表盘（`index.html` / `app.js` / `mock.js`），**不用 CDN、不用 ES module**（`file://` 会白屏），曲线为原生 canvas |
| `fpga/` | `src/` HLS 源码（3 个 IP）、`sim/` 测试台 + Python 黄金参考、`report/` 综合与 cosim 报告 |
| `board/` | **M3 之前只有占位**，未开始 |
| `data/` | `raw/` 视频（不入库）、`annotations/`、`golden/`（当前均为空占位） |
| `metrics/` | `csv/`、`logs/`（生成物，不入库）、`scripts/`（可复现检查脚本，入库）、`evidence/`（**正式证据入库**） |
| `docs/` | `interface.md`（契约）+ `00`~`07` 方案文档 |
| `skill/` | 沉淀的技能包（赛制加分项） |
| `report/` | 设计报告素材 + `llm_log/` 大模型协作记录（**必交项**） |

---

## 6. 验证命令与"跑通"的判据（改动前先跑，改动后再跑）

### 6.1 提交前自检（《07》第 6 节，全绿才算"完成"）

```bash
# 在仓库根执行；Windows 下把 python 换成本项目解释器（见 6.4 环境坑）
.venv\Scripts\python.exe -m pytest -q          # 期望：49 passed
node frontend/mock.js --selftest               # 期望：ok: true
python metrics/scripts/check_frontend_wiring.py    # 期望：前端接线检查：通过
node frontend/mock.js --limit 6 > metrics/evidence/js_frames.jsonl
python metrics/scripts/check_frontend_contract.py metrics/evidence/js_frames.jsonl   # 期望：通过
git status --short                             # 只应出现你本线的改动
```

| 命令 | 期望 | 失败说明什么 |
|---|---|---|
| `pytest` | `49 passed` | 契约被改坏，或引入了非确定性（时间戳/随机数泄漏进指标） |
| `--selftest` | `"ok": true` | JS 的 mock 与校验器不自洽 |
| `check_frontend_wiring` | `通过` | `app.js` 引用了不存在的 DOM id（症状：**页面不报错、区域空白**） |
| `check_frontend_contract` | `通过` | **A 的 Python 与 B 的 JS 对同一份契约判断不一致** —— M2 集成必炸 |

> **基线（2026-09-10 本机真实运行）**：`49 passed in 0.51s`、`ok: true`、接线检查通过。
> 这些是**当时的基线**，不是永久承诺；若你跑出不同结果，先报告事实，不要改期望值去凑绿。

### 6.2 A 线端到端（无需摄像头、无需板卡，甚至无需 OpenCV）

```bash
python backend/run_pipeline.py --source synthetic --pattern blink --seconds 30 --stub \
    --json metrics/logs/last.json --jsonl metrics/logs/stream.jsonl --csv metrics/csv/metrics.csv
python backend/run_pipeline.py --source data/raw/blink.mp4 --json metrics/logs/last.json   # 有真实视频时
python backend/mock.py --frames 6      # 六态契约 JSON
python -m pytest                       # 同 6.1
```

`--pattern` 可选 `blink` / `yawn` / `still` / `turn`。

### 6.3 C 线（HLS，需完整权限终端）

```bat
call D:\Xilinx\2026.1\Vitis\settings64.bat
python fpga\sim\gen_frames.py            :: 生成测试向量 + Python 黄金参考（同 seed 必得同产物）
cd /d D:\Desktop\AMD\fpga
vitis-run --mode hls --tcl run_hls.tcl   :: 默认 roi_statistic；set "HLS_IP=rgb2gray" 切换 IP
```

快速语法自检（秒级，不执行仿真）：`g++ -std=c++17 -fsyntax-only -I D:\Xilinx\2026.1\Vitis\include fpga/src/*.cpp`。

> ⚠️ 本机 MinGW 是 `win32` 线程模型，**只能 `-fsyntax-only`**；链接运行会以 `0xC0000139` 退出。
> ⚠️ **受限沙箱跑不了 csim**（需命名管道 → `Win32 error 5`），这不是代码问题，请在完整权限终端跑。

### 6.4 环境坑（不是代码问题，别浪费时间）

| 现象 | 原因 | 处理 |
|---|---|---|
| `python -m pytest` → `No module named pytest` | 系统 Python 3.14.7 没装 pytest | 用项目解释器 `.venv\Scripts\python.exe -m pytest` |
| `.venv\Scripts\python -m pip` → `No module named pip` | `.venv` 是 `--without-pip` 创建的 | `python -m ensurepip --upgrade`，或重建 venv（**不要在 .venv 里 pip install**） |
| `PyYAML 可用: False` | 没装 PyYAML | 无需处理：`backend/config.py` 会降级为内置解析器读同一份 `config.yaml` |
| 脚本打印中文乱码 | 控制台代码页是 GBK | 已内置 `console.enable_utf8_console()`；在 Windows Terminal / VS Code 里正常 |
| `pip install mediapipe / opencv-python` 失败 | Python 3.14 的 wheel 可能未发布 | **不要**去编译，改用 3.11/3.12 建 venv |
| pytest 报 WinError 5 | 受限 ACL | 已用 `pytest.ini` 的 `-p no:cacheprovider` 与 test fixture 规避 |

---

## 7. 权威事实表（防幻觉用的"已核对事实"，可直接引用）

以下每一条都在本仓库中**可核对**（给出文件或命令）。**引用项目事实前先来这里对一遍。**

### 7.1 全局冻结口径（`docs/interface.md` 第 0 节）

| 项 | 值 |
|---|---|
| 目标板卡 / 器件 | **PYNQ-Z2 / `xc7z020clg400-1`**（Zynq-7000） |
| 工具链 | **Vitis HLS 2026.1**，入口 `vitis-run --mode hls --tcl <脚本>` |
| 目标时钟 | **10 ns（100 MHz）** |
| 图像尺寸 / 格式 / 帧率 | **640 × 480**，**RGB888**（`byte0=R, byte1=G, byte2=B`），**30 fps**（仅影响时间序列） |
| 局部窗口 | `window_seconds: 30`（PERCLOS / 眨眼率 / 质量滑窗） |
| WebSocket 推送 | `ws_push_hz: 1.0`（1 帧/秒） |

### 7.2 三条踩坑重灾区（最容易发生、最难肉眼发现）

1. **通道顺序是 R-G-B，不是 BGR。** OpenCV 读图默认 BGR，A 线比对前**必须** `cv2.cvtColor(img, cv2.COLOR_BGR2RGB)`。
2. **ROI 是半开区间** `[x0,x1)`（`x0,y0` 含、`x1,y1` 不含），等价于 `img[y0:y1, x0:x1]`，无需 ±1 心算。
3. **AXI-Stream 语义**：`TUSER=1` 标记**一帧的第一个像素**；`TLAST=1` 标记**一行的最后一个像素（不是一帧！）**；`TKEEP/TSTRB` 固定 `0b111`。

### 7.3 `status` 枚举（恰好 6 值，**不得扩展**）

`normal` / `fatigue_risk` / `adjust_posture` / `unreliable` / `disconnected` / `done`
（定义见 `docs/interface.md` 第 2 节；唯一实现见 `backend/contract.py` 的 `STATUS_VALUES`）

### 7.4 指标 JSON 顶层字段（`backend/contract.py`）

`ts` / `frame_id` / `face` / `behavior` / `vital` / `quality` / `status` / `advice` / `reason`

- `face` = `visible` + `bbox`(`[x,y,w,h]` 整数) + `pose`(`yaw` / `pitch` / `roll`)
- `behavior` = `ear_left` / `ear_right` / `blink_state` / `blink_count` / `blink_rate_per_min` / `perclos` / `long_close_count` / `mar` / `yawn_count`
- `vital` = `hr_bpm` / `hr_conf` / `rr_per_min` / `rr_conf`，**键必须齐全、允许 `null`**
- `quality` = `light_score` / `motion_score` / `overall`（均 0~1）
- `advice` / `reason` 必须是非空字符串；`ts` / `frame_id` **必须单调递增**

### 7.5 C 线三个已实现 IP（真实运行数据，证据在 `fpga/report/`）

| IP | 工作尺寸 | csim | Final II | Fmax | LUT | FF | BRAM18 | DSP |
|---|---|---|---|---|---|---|---|---|
| `roi_statistic` | 640×480 RGB | 28/28 + 45/45 | 1 | 138.99 MHz | 1267 | 723 | 0 | 1 |
| `rgb2gray` v2 | 640×480 → 384×288 | 8/8 + 10/10 | 1 | 137.46 MHz | 1410 | 918 | 0 | 5 |
| `motion_quality` v2 | 384×288 灰度 | 6/6 + 9/9 | 1 | 140.05 MHz | 1501 | 1158 | **64（23%）** | 1 |

**两个冻结算法口径（A 线必须实现同一式，否则黄金参考必然对不上）**：

```
灰度：Y = (77*R + 150*G + 29*B + 128) >> 8      # 77+150+29=256；纯红=77（浮点式给 76，差 1 LSB，以本式为准）
缩放：保留 (x % 5 < 3) 且 (y % 5 < 3) 的像素       # 3/5 相位点采样，先挑行再挑列；640×480 → 384×288
```

- **比对容差 = 0（逐点严格相等）**，三个 IP 全是整数运算。
- `motion_quality` 的三条语义：阈值是**严格大于**；**第 0 帧输出无效**（片内 BRAM 上电未定义，刻意不给初值）；
  `motion_ratio_q16` 整除**向下取整**，`count==0` 时输出 0。
- 测试向量 `frames.bin` 布局：`bytes[frame*W*H*3 + 3*(y*W+x) + c]`，`c = 0→R, 1→G, 2→B`。
- **设计规律（可复用）**：片内数组按 **2 的幂地址空间**分配，BRAM 成本**台阶式**；
  像素数 ≤ 2¹⁷（131072）只需 64 个 BRAM18 —— 这是选 384×288 而非 320×240 的原因（同价、更清晰）。

### 7.6 契约版本与仓库状态（**引用前必须自行复核**）

- `docs/interface.md` 头部声明 **v0.93，待三方会签冻结 v1.0**（`docs/07` 里写的 v0.92 已滞后）。
- `backend/contract.py` 的 `CONTRACT_VERSION` 当前为 **`v0.91`** —— **与契约文档不同步**（见第 8 节）。
- 本地/远端状态（本节容易过期，**每次用 `git status` / `git log` 复核**）：
  本地 `main` 领先 `origin/main` 1 个提交（`docs: 新增《07 …》` 未推送）；工作区有未提交改动。

---

## 8. 已知不一致与可信度标注（**不要把它当"已修好"**）

以下为本文件建立时**真实观察到**的不一致，**尚未修复**。AI 遇到相关话题时**如实说明**，不要据此编造结论：

| # | 不一致 | 证据 | 处理建议 |
|---|---|---|---|
| 1 | `contract.py` 的 `CONTRACT_VERSION = "v0.91"` 落后于 `docs/interface.md` v0.93 | `backend/contract.py` vs `docs/interface.md` | 属契约同步事项，需 A/B 会签；**改它 = 改契约**，走第 4 节流程 |
| 2 | `fpga/README.md` 部分小节滞后（如末节"M0 欠账"仍列已完成的 `git init` / `README` / `config.yaml`；任务清单写 v0.92、C7"2 份"而实际 3 份） | `fpga/README.md` 末尾 | 正文结论可信；**引用末尾清单前先核对 `fpga/report/` 实际文件** |
| 3 | `docs/07` §0 及 `fpga/README.md` 中的版本号 v0.92 已被 v0.93 覆盖 | `docs/07` §7、`docs/interface.md` | 以 `docs/interface.md` 头部为准 |

> **代码是否真的"实现"某功能，只以三样为准**：源码可读到的实现 + 测试是否覆盖 + 真实运行输出。
> 文档里的"✅ 完成"是**人类维护者的声明**，可能滞后一天到几周 —— 关键判断请自己跑第 6 节的命令。

---

## 9. 当前真实进度（诚实版，防"已完成幻觉"）

| 里程碑 | 状态 |
|---|---|
| **M0** 冻结（主场景/项目名/指标/契约/目录/仓库） | ✅ 完成（契约 v0.93 **待会签**） |
| **M1** 基础框架 | 🔄 **进行中**（A/B 骨架就位；C 线跑到计划前面） |
| **M2** 软件合体（A 的 JSON 接 B 的网页） | ⏳ 待 A/B 骨架替换完成 |
| **M3** 硬件上板（Overlay + DMA，**首次需要板卡**） | ⏳ 未开始 |
| **M4** 完整闭环（软硬件同屏 + 黄金回归 + 可复现脚本） | ⏳ 未开始 |

**具体的欠账（引用时不要含糊）**：

- **A 线**：`run_pipeline.py` 端到端链路**部分为 stub** —— EAR/眨眼/PERCLOS/MAR 已是**真实现**；
  但 `face_landmark.py` 在没装 mediapipe 时走 `StubLandmarker`，此刻 EAR/MAR 是**没有算法意义的占位几何量**。
- **必须看 `landmark_source` 字段**：为 `"stub"` 时该次运行的指标**不得作为算法结果引用**（《05》铁律 1 / 《07》§10）。
- **rPPG 尚未实现**：`vital.*` 大面积为 `null` 是**正确行为**（质量门控不通过时系统承诺不输出心率数值），
  不是 bug，也不要"顺手补一个数字上去"。
- **未标定阈值**：`ear_close_threshold` / `mar_threshold` / `pose_yaw_max_deg` / `pose_pitch_max_deg` /
  `light_score_min` / `motion_score_max` / `quality_weights` 均为**占位值**，待真实视频/场景标定。
  `config.yaml` 里已用 `⚠️ 占位值` 标注，**改它们要写依据**。
- **`motion_thresh` 暂定 16**：待 A 线用 OpenCV 口径复核；改它必须**同步重新生成黄金参考**。
- **C 线**：`fir_filter`（C5）未开始；`board/` 为空；上板相关的一切结论**都不存在**。
- **确定性纪律**：时间戳用 `frame_id / fps` 而非墙上时钟；**同一段回放跑两次，末帧 JSON 与 CSV 必须逐字节相同**。

---

## 10. 写文件 / 提交 / 记录的硬性约定

### 10.1 提交信息格式（《07》第 4 节）

```text
<线>: <一句话主题，动词开头，≤50 字>

为什么改：
改了什么：
怎么验证的：（必须贴真实命令 + 真实结果）
```

**线标签**：`A:` / `B:` / `C:` / `shared:` / `contract:` / `docs:` / `repo:`
主题用中文但**技术名词保留英文**（EAR / PERCLOS / AXI-Stream / HLS / DMA）；一个提交只做一件事；**"怎么验证的"不许空**。

### 10.2 什么不能提交

`.venv/`、`__pycache__/`、`fpga/component_*/`（HLS 构建产物）、`fpga/sim/data/frames.bin` 与 `data_motion/*.bin`
（可由 seed 重建）、`data/raw/*.mp4`（体积 + **隐私红线**）、`metrics/csv/` 与 `metrics/logs/` 产物、`pytest-cache-files-*/`。
**但 `metrics/evidence/` 下的正式证据与 `fpga/sim/data*/golden_*.csv` 必须入库**（`.gitignore` 已按此设计，别加更宽的通配符）。

### 10.3 AI 协作记录（赛制必交项）

每次 AI 协作按《05》第五节模板写 `report/llm_log/YYYY-MM-DD_主题.md`（模板：`report/llm_log/_模板.md`），
含：问题背景 / 提示词原文 / AI 回答 / **自我纠错轨迹** / 实际采用的修改 / 验证命令与真实结果 / 结论 / 是否沉淀为 Skill。
**必须人类亲手写真实过程 —— AI 代写即违规。**

### 10.4 给 AI 的提问规范（人类侧，但 AI 应主动要求补齐）

好的提问含 4 段：**背景 / 约束（贴契约片段 + 阈值出处）/ 现状（贴真实日志，不删不改）/ 要求（可执行步骤 + 每步预期输出 + 验证方法）**。
AI 回答时必须：**区分"确定 / 推测 / 不确定"**，并给出**验证方法**而不只是结论。

---

## 11. 常见任务速查

| 我想做 | 该去哪 / 该跑什么 |
|---|---|
| 改一个指标字段名 | 先改 `docs/interface.md` 变更记录 → `backend/contract.py` + `frontend/mock.js` → 业务代码 → 跑 6.1 全部检查 → `contract:` 提交 + 公告 |
| 调一个阈值 | 只改 `config.yaml`（并注明依据）；代码里读 `load_config()`，**不许写魔法数字** |
| 加一个 HLS IP | 先冻结接口进 `docs/interface.md` 第 3 节 → `fpga/src/` + `fpga/sim/tb_*.cpp` + Python 黄金参考 → csim + 综合 + 归档 `fpga/report/` → 做完补一次 **cosim** |
| 看契约长什么样 | `python backend/mock.py --frames 6`；或读 `docs/interface.md` 第 1 节 |
| 看前端能不能跑 | 双击 `frontend/index.html`（离线 mock）；或 `python backend/websocket.py` / `python backend/api.py` |
| 查某个数字的出处 | `metrics/evidence/`（A/B）、`fpga/report/`（C）；先看 `landmark_source` 是否为 `stub` |
| 查项目做了什么决策、为什么 | `docs/00`~`07` + `docs/interface.md` 第 6 节变更记录 + `report/llm_log/` |

---

## 12. 最后一句

> **AI 可以帮你写得更快、更对，但"对错"和"真实"永远由真实运行、测试与记录负责。**
> 不确定就说不确定；没跑过就说没跑过；文档与代码冲突就报告，不要替项目"圆"一个说法。

---

*本文件由 `docs:` 前缀维护（参考《07》§4 标签表）。它与 `docs/05_AI使用约束.md`（合规红线）、
`docs/07_提交规范与分工提交说明.md`（提交与契约流程）构成 AI 协作的三件套；三者冲突时以《05》《07》为准。*
