# VigiLens · 知倦

**基于摄像头与 FPGA 的无接触疲劳与生命体征趋势监测终端**

> 英文全称：*VigiLens: A Trust-Aware Contactless Fatigue Monitoring Terminal with FPGA-Accelerated Vision Preprocessing*
> 中文规范全称：基于摄像头与 FPGA 的无接触疲劳与生命体征趋势监测终端
> 一句话标语：**先判断能不能测，再决定测出什么。** / *Know when to trust the measurement.*

2026 全国大学生嵌入式芯片与系统设计竞赛 · AMD 赛道 3.3 自主选题（初级组）

---

## 这是什么

面向**长时间学习 / 值守**场景的无接触状态监测终端。只用一个普通 RGB 摄像头，提取眨眼频率、闭眼时长、PERCLOS、打哈欠、头部姿态与运动质量等指标，并在受控条件下给出心率 / 呼吸率**趋势**估计。

与同类作品最大的不同是：**本系统把"测量是否可信"当作核心能力，而不是附加功能。** 光照、运动、人脸可见率与波形稳定性会融合成一个信号质量门控 —— 门控不通过时，系统明确输出"信号不可靠 / 请调整姿势"，**而不是照常报一个跳变的数字**。

FPGA 的作用不是转发器：**Mizar-Z7020**（MicroPhase Mizar-Z7 7020 版）的 PL 端承担 **ROI 像素统计、帧间运动量、FIR 带通滤波**等确定性视觉与时序预处理（自研 Vitis HLS IP），提供低延迟、低抖动的特征流；PS / 后端负责人脸关键点、指标计算与规则融合；网页端实时呈现指标、趋势曲线与可解释建议。

> 🚧 **板卡沿革**：原定 **PYNQ-Z2**（2026-09-10 冻结）→ **Mizar-Z7020**（按实物改指，🚧 v1.3 草案 2026-09-23，待 A/B 会签）。
> 两者**是同一颗器件**（`XC7Z020-1CLG400C` ≡ Vivado 的 `xc7z020clg400-1`），所以 HLS IP 与黄金参考**无需重做**；
> 要重做的只有 PS 配置（DDR 1 GB）、引脚约束（PL 晶振 50 MHz @ H16）与 bitstream。详见 `board/README.md` 与 `board/openmv/README.md`。

> ⚠️ **边界声明**：本作品是实验室条件下的**工程原型与健康趋势监测设备**，**不用于医疗诊断**，不提供血氧、血压等 RGB 摄像头原理上不可靠的指标。所有输出均为"疲劳风险提示 / 状态趋势"，不构成医学结论。

---

## 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | **3.12.x**（本机 `.venv` 实测 **3.12.13**） | 后端 / 算法 / 测试脚本。⚠️ Python **3.14** 的部分 wheel（mediapipe / opencv-python）可能未发布，装不上就换 3.11/3.12 建 venv |
| Vitis HLS | **2026.1** | 仅 C 线需要；入口 `vitis-run --mode hls --tcl <脚本>` |
| 目标板卡 | **Mizar-Z7020**（MicroPhase Mizar-Z7 7020 版，器件 `xc7z020clg400-1`）<br>⚠️ 原定 PYNQ-Z2；🚧 v1.3 草案待会签 | M3 之前**不需要**板卡 |
| OS | Windows（实测）/ Linux 理论可用 | 脚本以 Windows 路径为例 |
| 浏览器 | 任意现代浏览器 | 前端为**零依赖**原生 HTML/JS，不依赖 CDN，离线可跑 |

---

## 一键复现

```bash
# 1) 克隆
git clone https://github.com/huangder/vigilens-pynq.git
cd vigilens-pynq

# 2) 建虚拟环境 + 装依赖（算法/前端依赖；C 线只需看懂第 5 步）
python -m venv .venv
.venv\Scripts\activate            # Windows；Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# 3) 【B 线】看前端仪表盘（无需摄像头、无需 A 线、无需板卡）
python backend/mock.py --frames 5          # 先看一眼契约 JSON 长什么样
python backend/websocket.py                # 启动 1 Hz 推送服务，然后浏览器打开 frontend/index.html
#   没有后端也能看：直接打开 frontend/index.html，页面内置离线 mock 兜底

# 4) 【A 线】视频回放 → 契约 JSON + CSV（无需摄像头、无需板卡）
python backend/run_pipeline.py --source data/raw/xxx.mp4 --json metrics/logs/last.json --csv metrics/csv/last.csv

# 5) 【C 线】HLS C 仿真 + C 综合（无需板卡）
call D:\Xilinx\2026.1\Vitis\settings64.bat
python fpga/sim/gen_frames.py
cd fpga && vitis-run --mode hls --tcl run_hls.tcl
#   默认跑 roi_statistic；换 IP：set "HLS_IP=fir_filter"（七个 IP：roi_statistic / rgb2gray /
#   motion_quality / fir_filter / raw10_unpack / bayer_demosaic / frame_scale，各自的数据目录见 fpga/README.md）
#   两档（720p60 / 1080p45）整张矩阵一条命令跑完：set "VIGILENS_TIER=720p60"（详见 docs/26 §7.1）
```

### 验证"确实跑通了"（而不是"看起来能跑"）

```bash
python metrics/scripts/check_all.py      # 一条命令跑完全部可离线检查
#   2026-10-01 本机实测：[工具自检] 14/14；回归 PASS 27 / FAIL 2（那 2 项见 docs/29 §1）
python -m pytest backend/tests -v       # 契约一致性测试：字段/schema/6 值枚举
```

> 📌 **现行基线、两份红的归属、以及"现在还不存在的东西"**，统一看
> [`docs/29_后续任务清单_20261001.md`](docs/29_后续任务清单_20261001.md)。

---

## 目录说明

| 目录 / 文件 | 线 | 内容 |
|---|---|---|
| `backend/` | **A 线**（算法/后端）+ **B 线**（服务） | 采集、关键点、指标、质量评分、规则融合、存储、rPPG 链路、年龄分层旁路、FastAPI / WebSocket / Mock |
| `frontend/` | **B 线**（前端） | 零依赖仪表盘：视频占位区 + 指标卡 + 趋势曲线 + 事件日志 + 六态状态灯 + 年龄分层面板 |
| `fpga/` | **C 线**（FPGA） | `src/` HLS 源码、`sim/` testbench + Python 黄金参考、`report/` 综合与仿真报告 |
| `board/` | C 线（M3 后） | `bitstream/`（**空**）、`overlay/`、上板自检脚本、`openmv/` 首次测试包 |
| `data/` | A 线 | `raw/` 测试视频（**不入库**）、`annotations/` 人工标注、`golden/` 黄金结果、`fatigue_db/`（库本体不入库） |
| `metrics/` | 三方共享 | `csv/`、`logs/`（生成物，不入库）、`scripts/`（可复现检查脚本，入库）、`evidence/`（**正式证据入库**） |
| `docs/` | 三方共享 | **先看 [`docs/README.md`](docs/README.md)（文档索引）**；**`interface.md`（接口契约，改它先发公告）** + `00`~`29` 方案/测试/记录文档；历史快照在 `docs/archive/`。**"下一步做什么"看 [`docs/29_后续任务清单_20261001.md`](docs/29_后续任务清单_20261001.md)** |
| `skill/` | 三方共享 | 沉淀的 Skill 包（赛制加分项） |
| `report/` | 三方共享 | 设计报告素材 + `research/` 文献侦察 + `llm_log/` 大模型协作记录（**必交项，人类亲手写**） |
| `AGENTS.md` | 三方共享 | **AI 协作规范（第一份必读）**：五条铁律、权威顺序、契约变更流程、验证命令与权威事实表 |

**契约入口：`docs/interface.md`** —— 三线并行的唯一耦合点。任何字段、任何寄存器偏移的改动，**先改契约再改代码，并在此文件变更记录签名**。

**AI / 新成员入口：`AGENTS.md`** —— 用 AI（或让队友）动代码前先读它：它规定了"什么不许说、什么不许编、冲突时信谁"。

---

## 当前进度（诚实版）

> 📌 **本节只回答"到哪一步了"**。逐项证据、**"还没做的东西"**与**下一步**统一看
> [`docs/29`](docs/29_后续任务清单_20261001.md) §1/§3，功能逐项状态看 [`docs/21`](docs/21_已实现功能清单与验证状态.md)。

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0** 冻结 | 主场景 / 项目名 / 指标范围 / 接口契约 / 目录结构 / 仓库与协议 | ✅ **完成**（`docs/00`、`docs/interface.md` 🔒 **v1.1 已会签**、仓库 `vigilens-pynq`、MIT）<br>🚧 v1.2（帧率 45→30）/ v1.3（板卡改 Mizar）/ v1.5（测量口径档位化）**均仍是草案，未会签** |
| **M1** 基础框架 | A：回放→JSON；B：Mock→网页；C：HLS 仿真+综合 | 🔄 **进行中**（A/B 骨架就位；C 线跑到计划前面） |
| **M2** 软件合体 | A 的 JSON 接入 B 的网页，形成完整软件 Demo | 🟡 **链路已具备**：`metrics/scripts/run_demo.py` 一键（含**真实画面旁路**），`check_a_line_p5_m2.py` 真起两个进程验 17 项。⚠️ **本次文档整理未复跑** |
| **M3** 硬件接入 | Overlay 上板 + DMA 跑通（**首次需要板卡**） | ⏳ **未开始** —— `board/bitstream/` 为空、`build_bd.tcl` **从未跑通**、**Mizar-Z7020 从未上电** |
| **M4** 完整闭环 | 软硬件同屏对比 + 黄金结果回归 + 可复现脚本 | ⏳ 未开始 |

- **C 线（FPGA）跑在计划前面**：**7 个 IP**（`roi_statistic` / `rgb2gray` / `motion_quality` / `fir_filter` /
  `raw10_unpack` / `bayer_demosaic` / `frame_scale`）**全部完成 csim + csynth + cosim**（逐字节容差 0、无死锁），
  并落地 **45fps/1080p 与 60fps/720p 两条档位链路**（两档灰度工作尺寸都固定 **480×270**，
  片内帧缓存仍是 **64 个 BRAM18** —— 这是档位化方案能成立的唯一理由）。
  证据：`fpga/report/c6_seven_ips_csim_csynth_20260930.md`、`fpga/report/c7_sim_closed_loop_20261001.md`；
  汇总与缺口见 `docs/26`。
  ⚠️ **上板相关的一切结论都不存在**；也**没有**跑过 "@720p/@1080p 的 5 帧 cosim"（xsim 内存 OOM，见 `docs/29` T25）。
- **A 线**：`run_pipeline.py` 端到端链路可用；**EAR / 眨眼 / PERCLOS / MAR 已是真实现**
  （本文件旧版写的"stub 状态"已过期）。
  ⚠️ **必须看输出里的 `landmark_source` 字段**：为 `"stub"`（未装 mediapipe）时该次运行的 EAR/MAR 是
  **没有算法意义的占位几何量**，**不得作为算法结果引用**（`AGENTS.md` §9）。
  **rPPG 尚未实现**：`vital.*` 大面积为 `null` 是**正确行为**（质量门控不通过时系统承诺不输出心率数值），
  不是 bug，也不要"顺手补一个数字上去"。
- **B 线**：零依赖仪表盘（六态 + 曲线 + 事件日志 + **同源真实画面预览** + **年龄分层面板**），
  `backend/mock.py` / `websocket.py` / `api.py` 就位；跨语言契约检查与前端接线检查均已纳入 `check_all`。
- **🆕 年龄分层疲劳（旁路功能，不改契约）**：A / B 两侧已跑通（43 项测试、档案件跨语言一致 **33/33**），
  **C 线的 PS 路径未开始**；为什么必须分龄、以及 8 条未验证项见 `docs/28`。
- **回归基线**（2026-10-01 本机实测）：`check_all` 工具自检 **14/14**、回归 **PASS 27 / FAIL 2**；
  `pytest` **141 项收集**（⚠️ 这是**收集数**，不等于"全部通过"）。判断标准是"**只增不减**"。

> 每个目录另有自己的 `README.md` 说明该线的入口、验收口径与当前欠账；`fpga/README.md` 是 C 线的详细工作记录。

---

## 开源协议与第三方依赖

- 本项目以 **MIT License** 开源，见 [`LICENSE`](LICENSE)。
- 第三方依赖均为各自协议（详见 `requirements.txt` 注释）：FastAPI / Uvicorn（MIT）、OpenCV（Apache-2.0）、MediaPipe（Apache-2.0）、NumPy（BSD-3-Clause）、PyYAML（MIT）、pytest（MIT）。
- ⚠️ **核查过的许可红线**（引用同类项目前先看这条）：`rPPG-Toolbox` 是 **RAIL v1.1**（含"不得用于推断健康/医疗状况"一类限制）；
  `pyVHR` 是 **GPL-3.0**（**不得并入本 MIT 仓库**）；6 个 fatigue/PERCLOS 仓库**全部没有 LICENSE 文件** ⇒ **只能看思路，不得复制代码**。
  详见 `docs/20` §7.4.2。
- `fpga/examples/Vitis-HLS-Introductory-Examples/` 为 AMD 官方教学例程，**仅作本地环境验证参考，不纳入本仓库**（见 `.gitignore`），版权归 AMD 所有。
- 本仓库不包含任何真实受试者的可识别影像；`data/` 下的测试视频须取得出镜者同意后使用。

---

## 团队

三人小组，三线并行：A 线（算法/后端）、B 线（前端/可视化）、C 线（FPGA/硬件）。
分工与里程碑见 [`docs/02_三人分工与三线并行开发计划.md`](docs/02_三人分工与三线并行开发计划.md)，
当前阶段的动手入口见 [`docs/04_基础框架搭建指南.md`](docs/04_基础框架搭建指南.md)，
"下一步做什么"见 [`docs/29_后续任务清单_20261001.md`](docs/29_后续任务清单_20261001.md)。
