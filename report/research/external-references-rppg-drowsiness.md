# 外部参考项目与学术先例调研（rPPG / 疲劳检测）

> 调研人：DSH 子代理（研究任务，**未改动任何既有项目文件**）
> 日期：2026-09-27
> 用途：VigiLens / 知倦 未来功能升级的可引用先例与依赖候选
> 标注约定：`【已验证】` = 我实际 fetch 到了该页面并据此陈述；`【推测】` = 有依据但未验证；`【未核实】` = 无法确认
> ⚠️ 本文件为**调研素材**，不是契约、不是结论。任何进入 `config.yaml` / `docs/interface.md` 的内容须走既有流程。

---

## 0. 调研前提与网络限制（影响"能验证到什么"）

本次会话 `web_search` 工具全程不可用（firecrawl 429，"Every engine for the web source failed"）。**且本机网络对 GitHub 有额外封锁**：

| 现象 | 证据 | 应对 |
|---|---|---|
| `github.com` / `api.github.com` / `raw.githubusercontent.com` 等被 hosts 文件指向 `127.0.0.1`，且本机**没有任何进程监听 443**（加速器已死） | 【已验证】`Select-String ...\etc\hosts` 输出 30+ 条 `127.0.0.1 github...`；`Get-NetTCPConnection -State Listen` 在 443/80 无结果；`Invoke-WebRequest https://github.com/...` 失败 | 改用**镜像**取原始文件 |
| pwsh 无直接外网 | 【已验证】`Invoke-WebRequest https://example.com` → "基础连接已经关闭"；DoH 超时 | 只用 harness 的 fetch 工具 |

**实际可用的取数通道（全部已实测成功）**：

| 通道 | 用途 | 示例 |
|---|---|---|
| `https://ungh.cc/repos/OWNER/REPO` | GitHub 仓库元数据（description / stars / defaultBranch / pushedAt） | 见下表 |
| `https://ungh.cc/users/USER/repos` | 按用户列举仓库（**用作"发现"通道**） | 发现 `akshaybahadur21/Drowsiness_Detection` |
| `https://cdn.jsdelivr.net/gh/OWNER/REPO@BRANCH/PATH` | **取原始文件**（README / LICENSE），这是 license 的一手证据 | 见下表 |
| `https://archive.softwareheritage.org/api/1/origin/search/<kw>/` | **按仓库名关键词发现** GitHub 仓库（含最近访问时间） | 发现多个 PERCLOS 仓库 |
| `https://export.arxiv.org/api/query?...` | arXiv 全文检索 | FPGA×PPG 检索 |
| `https://api.crossref.org/works?...` / `https://api.openalex.org/works?...` | 期刊论文元数据 | 找到硬件 rPPG 论文 |

> ❌ 不可用：所有通用搜索引擎（cn.bing.com 返回**被审查替换后的无关结果**，duckduckgo/mojeek/brave/searx 全部解析到非公网 IP 或连接失败）、paperswithcode.com（已 302 到被封锁的 huggingface.co）、sites.google.com（多个数据集官网托管于此，**不可达**）。

---

## AREA 1 — 摄像头 rPPG（无接触心率 / 呼吸率）

| # | 名称 / URL | 是什么（一句话） | 许可证（一手证据） | 语言 / 框架 | CPU / 边缘 / FPGA | 对本项目的相关性 |
|---|---|---|---|---|---|---|
| 1 | **rPPG-Toolbox**<br>github.com/ubicomplab/rPPG-Toolbox | 摄像头生理感知（rPPG）的**训练+评测工具箱**（NeurIPS 2023），1209★，默认分支 `main`，最后推送 2025-09-15 | **Responsible AI Source Code License v1.1（RAIL，licenses.ai）**<br>【已验证】`LICENSE` 原文首页写 "Version 1.1, Nov 20, 2022 / http://licenses.ai/" | Python / PyTorch；conda 或 uv | 仓库无边缘运行时；【推测】纯 PC 训练评测 | **最高**：可作为"黄金参考"的评测口径来源（见下 §1.1）。⚠️ license 有使用限制条款，**需法务/合规复核** |
| 2 | **pyVHR**<br>github.com/phuselab/pyVHR | Python rPPG 框架（方法池 + 数据集池 + 非参数统计评测），595★，`master`，最后推送 2024-11-20 | **GPL-3.0**<br>【已验证】README 末尾 "This project is licensed under the GPL-3.0 License"（LICENSE 文件 fetch 超时，**未逐字读到**） | Python / NumPy / Numba；GPU 需 CuPy | ✅ **有 CPU-only 发行**：`pip install pyvhr-cpu`，分支 `pyVHR_CPU`（【已验证】README 原文） | **最高（算法侧）**：POS/CHROM/PBV/LGI/SSR/OMIT/ICA/PCA 的**经典实现齐全**，可直接在 PC 上生成 A 线的黄金参考 |
| 3 | **PhysNet**<br>github.com/ZitongYu/PhysNet | BMVC2019 时空网络从人脸视频测 PPG，90★，`master` | **MIT**<br>【已验证】`LICENSE` 原文 "MIT License Copyright (c) 2019 Zitong Yu" | 深度学习（论文代码） | ❌ 无边缘路径 | 经典 3D-CNN 基线；MIT 干净。⚠️ **最后推送 2020-01-02（已停更）** |
| 4 | **iBVP-Dataset**<br>github.com/PhysiologicAILab/iBVP-Dataset | **带高分辨率信号质量标签**的 RGB-热成像 rPPG 数据集（Electronics 2024），27★，`main`，最后推送 2026-04-07 | **EULA 申请制**，仅学术；【已验证】README："only for academic research purposes"，须由**导师**签署 EULA 后邮件申请 | 数据集 + Python（SQA 模型） | 数据集本身非代码 | **最高（质量门控主题）**：见 §1.2 |
| 5 | **PhysioKit**<br>github.com/PhysiologicAILab/PhysioKit | 开源低成本生理计算工具箱（Arduino 采 EDA/呼吸/PPG + 实时可视化），45★，`main`，最后推送 2024-06-26 | 【未核实】`LICENSE` fetch 超时，未能读到 | Python（PC 端）+ Arduino C++ | ✅ 面向低成本微控制器 | **高（流程先例）**：采集界面**内置"先看信号质量、再开始记录"**（见 §1.2） |
| 6 | **MA-rPPG-Video-Toolbox** | 运动增强 rPPG 视频工具箱（WACV 2024 Oral，"Motion Matters"），84★，最后推送 2025-12-29 | 【未核实】未取到 LICENSE | Python | ❌ | 中：造"运动伪影"数据以压测质量门控。<br>⚠️ ungh 解析该 URL 时返回的 `repo` 字段是 **`yahskapar/MA-rPPG-Video-Toolbox`**，即**仓库 slug 似已改名/转移**（rPPG-Toolbox README 仍写作 `Roni-Lab/...`）——**引用前请复核最终 URL** |
| 7 | **GRGB rPPG**（论文）<br>DOI 10.3390/bioengineering10020243 | "GRGB rPPG: An Efficient **Low-Complexity** rPPG-Based Algorithm for HR Estimation"，*Bioengineering* 2023 | 期刊 OA 论文（版权属期刊）<br>【已验证】Crossref 元数据 | 算法（论文） | 【推测】低复杂度 → 可能适合 PS/PL 简化实现 | 中高：**低复杂度 rPPG** 正是我们要在 Zynq 上做的事 |
| 8 | **Low-Complexity Timing Correction for rPPG**（论文）<br>DOI 10.3390/s25020588 | "Low-Complexity Timing Correction Methods for Heart Rate Estimation Using rPPG"，*Sensors* 2025 | 【已验证】Crossref 元数据 | 算法（论文） | 【推测】同上 | 中：帧率抖动/时间戳误差是 Zynq 采集的真实风险，该文正对症 |

### 1.1 rPPG-Toolbox 的具体可复用点（均来自我 fetch 到的 README）

- **无监督方法**：GREEN、ICA、CHROM、LGI、PBV、POS、OMIT —— 与我们"经典算法先跑通"的路线一致。
- **有监督方法**：DeepPhys、PhysNet、TS-CAN、EfficientPhys、BigSmall、PhysFormer、iBVPNet、PhysMamba、RhythmFormer、FactorizePhys。
- **质量/可靠性相关**：`USE_EXCLUSION_LIST`（按视频排除）、`USE_SMALLER_WINDOW`（短窗评测）、
  指标含 **SNR** 与 **Bland-Altman（BA）** 图 —— 即"不只看 MAE，还看一致性/信号质量"。
- **工程约束（重要）**：官方安装流程是 Linux/macOS 的 `bash setup.sh conda|uv`；README 明确
  *"If you use Windows ... consider using Windows Subsystem for Linux (WSL)"*。→ **Windows 上不便直接用**。
- 支持数据集（README 列出）：SCAMPS、UBFC-rPPG、PURE、BP4D+、UBFC-Phys、MMPD、iBVP
  （另附 PhysDrive、SUMS、LADH 的目录约定）。其中 **MMPD 仓库 `McJackTang/MMPD_rPPG_dataset`**、
  **PhysDrive `WJULYW/PhysDrive-Dataset`**、**SUMS `thuhci/SUMS`** 由 README 给出链接（**我未逐一 fetch，属【未核实】**）。

### 1.2 "测量可靠性 / 信号质量门控"——与 VigiLens 核心承诺最贴的三条先例

1. **iBVP 的 SQA-PhysMD**（【已验证】README + `SQA_PhysMD/configs/SQAPhysMD.json`）
   - 数据集每个 `.csv` 含：`BVP`、`SQPhysMD`（模型生成的**逐样本**质量标签）、`SQ1`/`SQ2`（人工标注）、`Perfusion_Value`。
   - README 原文：*"the SQA-PhysMD model trained and validated to conduct **dense (per-sample) signal-quality assessment**"*。
   - 该模块**已集成进 PhysioKit 做实时 SQA**（README 原文），模型权重 `SQAPhysMD.pth`，配置 `target_fs=30`、`window_len_sec=60`。
   - **采集条件与我们高度一致**：RGB **640×480 @ 30 FPS**、约 1 m 距离、含"引导头部运动"条件。
   - → 对"**先判断能不能测**"是**直接可对标的先例**：他们把"质量"做成了**一等输出**，而不是附带字段。
2. **PhysioKit 的采集流程**（【已验证】README 与配置样例）
   - 实验配置项 `"assess_signal_quality": true`；操作步骤 4e：*"the objective at this step is to visually inspect the physiological signals and verify if **good quality signals are being acquired**"*，之后才允许 `Start Recording`。
   - → 与 `docs/interface.md` 的 `unreliable` 状态设计**同构**：质量不达标就不产出结论。
3. **rPPG-Toolbox 的评测口径**（【已验证】README）
   - 每个实验默认产出 **Bland-Altman** 图 + **SNR** 指标；支持排除列表/短窗评测。
   - → 可作为我们 `metrics/evidence/` 报告模板的参考口径（**不要照搬数字**，本项目铁律 1）。

### 1.3 FPGA / ASIC / 硬件加速的 rPPG 与生物信号

| 项 | 结论 |
|---|---|
| **arXiv 上 "FPGA + rPPG" 几乎为空** | 【已验证】arXiv API `abs:"remote photoplethysmography" AND abs:"FPGA"` → `totalResults = 0`；放宽为 `abs:"photoplethysmography" AND abs:"FPGA"` → `totalResults = 1`。**⇒ 硬件 rPPG 的成果基本不在 arXiv，而在 IEEE 期刊/会议**（本机无法访问 IEEE Xplore 验证），这是一条**诚实的"证据缺口"**，不要假装查全了 |
| **唯一的 arXiv 命中** | 【已验证】arXiv **2609.03174** "A Reconfigurable Hybrid Convolutional-Fully Connected Neuromorphic Core for Biomedical Edge Inference"（2026-09-02）。**FPGA** 上实现脉冲卷积网络，用肩带式**接触式 PPG**（红/红外）做低氧分类，16-bit，硬件精度 **88.26%**（5 折），动态功耗 **1.455 W**。→ 先例价值：**FPGA + 生物信号 + 边缘推断**的完整硬件/软件协同流程（PyTorch→量化→部署） |
| **找到但没能读到全文的硬件 rPPG 论文** | 【未核实】OpenAlex 检索到 **"Contactless Camera-Based Heart Rate and Respiratory Rate Monitoring Using AI on Hardware"**（*Sensors* 2023，DOI `10.3390/s23094550`，OpenAlex `W4376630404`）。**元数据来自我 fetch 到的 OpenAlex API**，但出版社页面 `www.mdpi.com/1424-8220/23/9/4550` 返回 **HTTP 403 Access Denied**，故**论文内容完全未验证**。→ **标题与主题与本项目最接近，强烈建议人工用校园网读全文** |
| **"OpenVINO / Open Model Zoo 有 rPPG demo" —— ❌ 不存在** | 【已验证·否定结论】我 fetch 了 `open_model_zoo/demos/README.md`，其中**完整的 demo 目录清单**（约 70 个，含 Smart Classroom、Gaze Estimation、Interactive Face Detection 等）**没有任何心率/rPPG/生命体征 demo**。→ 该传言不成立，别写进报告 |
| **ghrPPG** | 【未核实】未能找到任何叫 `ghrPPG` 的项目或论文；**不要引用** |

### 1.4 Zynq-7020 适配判断（**全部为【推测】，非事实**）

> 前提：双核 Cortex-A9 @667MHz / ~85k LUT / 4.9Mb BRAM / 220 DSP。
> 本项目的既有事实：C 线 3 个 IP 已 csynth（`roi_statistic` 1267 LUT、`rgb2gray` 1410 LUT、`motion_quality` 1501 LUT + 64 BRAM18，Fmax ≈137–140MHz，II=1）。【来源：仓库 AGENTS.md §7.5】

| 项目 | 【推测】Zynq-7020 可做什么 |
|---|---|
| rPPG-Toolbox / pyVHR / PhysNet | 【推测】**不能**在 PS 上跑（PyTorch/Python 栈 + 训练级算力）。用途是**PC 侧离线**生成黄金参考与评测 |
| **经典单色通道算法（POS / CHROM / GREEN）** | 【推测】**PS 端可跑**：本质是每帧 ROI 均值 + 少量矩阵运算 + 带通滤波 + FFT/峰值检测。PS 端算力瓶颈是 Python；用 C 重写 + 复用 C 线已有的 FIR IP 更现实 |
| **ROI 像素统计 / 帧间差分 / FIR 带通** | 【推测】**PL 端已有能力**（与现有 3 个 IP 同类），是"把 rPPG 前处理下沉到 PL"的天然切入点 —— 这条**恰好符合 AGENTS.md 对"PL 不能降级成纯 DMA"的要求** |
| **iBVP 的 SQA-PhysMD** | 【推测】**PS 端可跑降级版**（它输入是 30Hz 的 BVP 单通道，1D 卷积 + NMF，模型很小），但**未经实测**。PL 端做 SQA 特征（如逐窗 SNR/幅度比）更稳 |
| **神经 rPPG（DeepPhys/TS-CAN/EfficientPhys）** | 【推测】**PL 端不可行**：需要帧缓存与卷积算力，4.9Mb BRAM 连几帧 640×480 RGB 都放不下。真要做只能"降采样 + 极简网络 + 重量化"，属高风险探索项 |
| **arXiv 2609.03174 的脉冲网络** | 【推测】作者用较大 FPGA；其"PyTorch→量化→FPGA"流程可借鉴，但**规模与我们的 7020 不可比**，不能直接搬 |

---

## AREA 2 — 疲劳 / 困倦检测：开源项目与数据集

### 2.1 开源仓库（含 license 与维护状态）

| # | 仓库 | 是什么 | License | 维护状态 | 相关性 / 风险 |
|---|---|---|---|---|---|
| 1 | **akshaybahadur21/Drowsiness_Detection**<br>github.com/akshaybahadur21/Drowsiness_Detection | "A simple Drowsiness Detection module for humans. 😴🚫🚗" | ❌ **无 LICENSE 文件**<br>【已验证】`@master/LICENSE` → HTTP 404；根 `README.md` 亦 404 | **544★**；最后推送 **2025-06-09**（仍在动） | 生态里**最有名的入门实现**。⚠️ **无 license ⇒ 默认保留全部权利，不得复制其代码进本仓库**（只可作"思路参考"并自行实现） |
| 2 | **misbah4064/drowsinessDetector**<br>github.com/misbah4064/drowsinessDetector | "OpenCV Drowsiness Detector using Python and Dlib" | 【未核实】`@main/LICENSE` → 404，`@master/LICENSE` fetch 失败（未能定论） | 38★，51 fork；最后推送 **2023-07-20** | 中：**dlib EAR** 路线的对照实现。<br>⚠️ dlib 68 点在本项目里已被 mediapipe 取代，参考价值主要在**判据/告警逻辑** |
| 3 | **srakrn/webcam-perclos**<br>github.com/srakrn/webcam-perclos | "Detecting PERCLOS from webcam" | ❌ 无 LICENSE<br>【已验证】`@master/LICENSE` → 404 | 0★；最后推送 **2021-10-12**（基本停更） | 低：**专做 PERCLOS** 的小项目，可看其 PERCLOS 窗口实现 |
| 4 | **hackerdud3/Perclos-based-Real-Time-Drowsiness-Detection-System-depending-on-Eye-Aspect-Ratio** | 基于 EAR 的 PERCLOS 实时检测 | ❌ 无 LICENSE<br>【已验证】`@main/LICENSE` → 404 | 【已验证】存在性 + SWH 最后访问 **2024-10-29** | 中：**EAR→PERCLOS** 的完整链路，与 A 线现有 `behavior_metrics.py` 对标 |
| 5 | **qunshansj/Perclos-Enhanced-YOLO-Drowsiness-Detection-System** | **YOLO + PERCLOS 增强**的检测系统 | ❌ 无 LICENSE<br>【已验证】`@main/LICENSE` → 404 | 【已验证】存在性 + SWH 最后访问 **2025-08-15**（较新） | 中高：**YOLO 路线**参考（对应我们"人脸/ROI 检测是否值得上 YOLO"的取舍） |
| 6 | **haibaoo/Perclos-Fatigue-Detection** | PERCLOS 疲劳检测 | ❌ 无 LICENSE<br>【已验证】`@main/LICENSE` → 404 | 【已验证】存在性 + SWH 最后访问 **2023-04-26** | 低—中 |
| 7 | **gaurav-baranwal/Drowsiness-Detection-using-RLDD** | 用 **RLDD** 数据集做困倦检测 | ❌ 无 LICENSE<br>【已验证】`@main/LICENSE` → 404 | 【已验证】存在性 + SWH 最后访问 **2023-02-11** | 中：**UTA-RLDD 数据集的用法参考** |
| 8 | **tigersujie/Perclos-CNN**（另有 `TigerJieZ/Perclos-CNN`、`14641/Perclos-CNN`，SWH 显示**同一快照 id**，即 fork 关系） | PERCLOS + CNN | 【未核实】`@master/README.md` → 404，未取到 license | 【已验证】存在性 + SWH 最后访问 2020-03-15 起 | 低：**CNN 分类**思路参考 |

> ⚠️ **一条重要的、可复用的结论**：我用 jsDelivr **逐个探测了 6 个 fatigue/PERCLOS 仓库的 LICENSE，6/6 都没有 license 文件**（另 2 个因网络抖动未能定论）。
> ⇒ **不要把"GitHub 上有实现"等同于"可以拿来用"**。本项目若要引用外部疲劳检测代码，须**逐仓库确认 license**，否则一律**自己按论文/思路重写**（这也正合 AGENTS.md 铁律 2）。

### 2.2 数据集与获取条款

| 数据集 | 是什么 | 获取方式 / 许可（一手证据） | 对本项目的用途 |
|---|---|---|---|
| **iBVP**（=§1 第 4 项） | RGB+热成像 rPPG，**逐样本 SQA 标签** | 【已验证】**EULA 申请制**，仅学术；README 要求 EULA 由**学术导师**填写后邮件发给 Jitesh Joshi / Youngjun Cho；**约 400GB**；部分受试者（p08/p10/p13/p16/p29/p31/p33）数据在 `Confidential_No-media-use` 目录 | 质量门控的**标注真值**（含真实头动） |
| **DMD – Driver Monitoring Dataset**<br>dmd.vicomtech.org | Vicomtech 的**多模态驾驶员监控**数据集；**3 台 Intel RealSense**（2×D415 脸/手 + 1×D435 身体），每条会话同步 **RGB + 红外 + 深度**；标注为 **VCD** 格式 | 【已验证】许可 = **Creative Commons Attribution-NonCommercial-NoDerivatives 4.0（CC BY-NC-ND 4.0）**，*"can only be used for academic purposes"*，需 18 岁以上；下载分三包（Gaze & Hands / **Drowsiness** / Distraction），经 `opendatasets.vicomtech.org` 分发；⚠️ 官网声明因许可修订**已下架仿真器录制、红外与深度素材，只公开 RGB**；原始规模约 **25TB** | 场景最贴近"驾驶/久坐"；**含 yawning / microsleep / sleepy driving 标签**。<br>⚠️ **NC-ND**：不可商用、不可分发衍生数据集 |
| **NTHU-DDD** | 国立清华（台湾）驾驶困倦数据集，被大量论文当作基准 | 【未核实】**官网地址失效**：【已验证】`http://cv.cs.nthu.edu.tw/php/callforpaper/drowsinessdetection/` → **HTTP 404**；同站根路径亦连接失败。**获取条款完全未能确认** | 【已验证】被 arXiv 论文广泛使用（见 §2.3），是最常见基准之一 |
| **UTA-RLDD** | UT Arlington **真实生活**困倦数据集（2 分类） | 【未核实】官网托管于 `sites.google.com/view/utarldd`，**本机解析到非公网 IP、不可达**。特征由中国境外论文描述：【已验证】arXiv 2509.17498 称其 *"containing both awake and drowsy conditions, ensuring variability in **gender, eyewear, illumination, and skin tone**"* | 跨光照/跨人群**泛化性**验证的候选 |
| **YawDD** | 打哈欠检测数据集 | 【未核实】官网不可达（同上） | 【已验证】arXiv 论文广泛使用 |
| **YawDD+** | YawDD 的**帧级标注**升级版 | 【未核实】论文称 *"The YawDD+ dataset and trained models are available online"*（**具体 URL 我未验证**） | **高**：见 §2.3 —— 它做的正是"提升数据质量 → 边缘设备可用" |
| **MRL Eye Dataset** | VŠB-TUO MRL 实验室的眼部图像数据集 | 【未核实】**官方页面已不存在于该路径**：【已验证】`mrl.cs.vsb.cz/eyedataset`（http 与 https **都**返回**实验室首页**内容，非数据集页）；`mrl.cs.vsb.cz/projects.html` 证实该组做过 VW 合作的"疲劳与注视方向识别（2014–2017）"，但**未提供数据集下载链接** | 低（本机无法确认获取条款） |

### 2.3 值得引用的学术先例（AREA 2）

| 论文 | 关键内容（均【已验证】，来自我 fetch 的 arXiv 摘要） | 为什么对 VigiLens 有用 |
|---|---|---|
| arXiv **2509.17498**<br>Vision-Based Driver Drowsiness Monitoring: Comparative Analysis of YOLOv5-v11 | 在 **UTA-RLDD** 上微调 7 个 YOLO 变体；YOLOv9c 最高 **0.986 mAP@0.5 / 0.978 Recall**；**"YOLOv11n strikes the optimal balance between precision (0.954) and inference efficiency, making it highly suitable for embedded deployment"**；并明确指出 **EAR + dlib 计算量小但在姿态变化与遮挡下鲁棒性下降** | **直接支撑我们的技术取舍**：EAR 便宜但对姿态敏感（正对应我们 `pose_*` 与质量门控的必要性）；YOLO 精度高但算力代价大 |
| arXiv **2512.11446** (ICIP 2026)<br>YawDD+: Frame-level Annotations for Accurate Yawn Prediction | 用半自动 + 人工复核把 YawDD 升级为**帧级标注**；在 **NVIDIA Jetson NANO / AGX** 上训练与推断；MNasNet 分类准确率 **99.34%**、YOLOv11 检测 mAP **95.69%**、**115 FPS**（AGX）；结论：*"enhanced data quality alone supports on-device driver fatigue monitoring systems **without server-side computation**"* | **与我们同一命题的当代先例**：边缘设备 + 标注质量。可引用来论证"先把标注/质量做对，再谈模型" |
| arXiv **2209.05269** (ECCV 2022 ISM Workshop)<br>Detecting Driver Drowsiness as an Anomaly Using LSTM Autoencoders | 把困倦当作**异常检测**（只学正常驾驶），NTHU-DDD 上 **AUC 0.8740**；讨论了不同置信度下的标签分配 | 思路先例：**只用"正常"建模**，天然对应我们"不能测就报不可靠"的理念 |
| arXiv **2010.06235**<br>Robust Two-Stream Multi-Feature Network | 用 **CLAHE 降低光照差异影响**、双流网络 + 注意力；NTHU-DDD 准确率 **94.46%** | **CLAHE 光照归一化**是低成本的、可能下沉到 PL 的预处理（【推测】） |
| arXiv **2207.12148**<br>Spatiotemporal Attention / Vision Transformers | 困倦模型在 NTHU-DDD 上只到 **44%** 且过拟合（作者自述 *"indicating overfitting and poor model performance"*）；干扰模型在 **DMD** 上到 **97.5%** | **反面证据**：NTHU-DDD **数据量有限**，小样本上大模型会崩。对我们"先用小模型 + 场景标定"是支持性论据 |

---

## 3. Unverified / could not confirm（**未验证，不得当作事实引用**）

1. **ghrPPG** —— 未找到任何同名项目/论文。**不要引用**。
2. **OpenVINO / Open Model Zoo 的 rPPG demo** —— 【已**否定**】OMZ 完整 demo 清单里没有。若要找 Intel 生态的 rPPG，需另辟渠道（未查）。
3. **"Contactless Camera-Based Heart Rate and Respiratory Rate Monitoring Using AI on Hardware"（Sensors 2023, DOI 10.3390/s23094550）** —— 元数据经 OpenAlex 验证，但 **MDPI 页面 403，正文未读**。**这是与本项目最接近的硬件 rPPG 先例，建议人工读全文。**
4. **NTHU-DDD 的官方获取条款** —— 官网 URL 404，**未确认**。
5. **UTA-RLDD 的官方获取条款** —— 官网被网络封锁，**未确认**（仅确认其被论文广泛使用及其人群多样性特征）。
6. **YawDD / YawDD+ 的获取条款与下载 URL** —— **未确认**（YawDD+ 论文声称在线可得）。
7. **MRL Eye Dataset 的获取条款** —— 官方页面在该路径已不存在，**未确认**。
8. **pyVHR 的 LICENSE 文件原文** —— README 明确写 GPL-3.0，但 LICENSE 文件本身 fetch 超时，**未逐字读到**。
9. **PhysioKit 的 license** —— LICENSE fetch 超时，**未确认**。
10. **MA-rPPG-Video-Toolbox 的 license 与最终 canonical URL** —— 未取到 license；ungh 返回的 repo slug 为 `yahskapar/...` 而 rPPG-Toolbox README 写 `Roni-Lab/...`，**仓库归属需复核**。
11. **misbah4064/drowsinessDetector 的 license** —— `@master/LICENSE` fetch 失败（网络抖动），**未能定论**（`@main` 为 404，但默认分支是 master，故不能据此判定无 license）。
12. **rPPG-Toolbox 是否能在 CPU 上跑推理** —— README **未声明** CPU-only 模式，也未声明必须 GPU。**未确认**，不要写"支持 CPU"。
13. **MMPD / PhysDrive / SUMS / LADH 数据集** —— 仅由 rPPG-Toolbox README 列出其仓库链接，**我未逐一 fetch 验证**。
14. **IEEE Xplore / ACM 上的硬件 rPPG 论文** —— 本机不可达，**证据缺口**，不代表不存在。
15. **所有 Zynq-7020 相关判断** —— 表中已标【推测】，**全部未经实测/综合**。

---

## 4. 给 A/B/C 三线的三条可执行建议（均为建议，非结论）

1. **A 线**：用 **pyVHR 的 CPU 版**（`pip install pyvhr-cpu`）在 PC 上跑 POS/CHROM/PBV，作为 `backend/` rPPG 链路的**黄金参考**；口径与 `docs/interface.md` 的 `vital.*` + `quality.*` 对齐。注意 **pyVHR 是 GPL-3.0** —— 只做**离线对拍**、不复制代码入库，可规避传染性。
2. **质量门控叙事**：优先引用 **iBVP（逐样本 SQA）+ PhysioKit（采集前质量确认）** 这两条先例，它们是"**先判断能不能测**"这一产品承诺的**外部权威背书**。
3. **合规**：
   - 引入任何外部疲劳检测代码前，**先确认 license**（本次抽样 6/6 无 license）。
   - **rPPG-Toolbox 的 RAIL 许可含"不得推断健康/医疗状况"类限制条款**，与本项目"趋势监测/辅助提示"的表述边界需要**人工合规复核**（我只陈述条款原文，不给法律结论）。
   - 数据集仅学术用途的（iBVP、DMD）**不可对外分发原始数据**，符合《05》隐私红线。

---

*本文件由子代理生成，仅记录"我实际 fetch 到的内容"。凡本文件与仓库 `docs/interface.md` / `config.yaml` 冲突，以后者为准。*
