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

FPGA 的作用不是转发器：PYNQ-Z2 的 PL 端承担 **ROI 像素统计、帧间运动量、FIR 带通滤波**等确定性视觉与时序预处理（自研 Vitis HLS IP），提供低延迟、低抖动的特征流；PS / 后端负责人脸关键点、指标计算与规则融合；网页端实时呈现指标、趋势曲线与可解释建议。

> ⚠️ **边界声明**：本作品是实验室条件下的**工程原型与健康趋势监测设备**，**不用于医疗诊断**，不提供血氧、血压等 RGB 摄像头原理上不可靠的指标。所有输出均为"疲劳风险提示 / 状态趋势"，不构成医学结论。

---

## 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | **3.14.x**（实测 3.14.7） | 后端 / 算法 / 测试脚本 |
| Vitis HLS | **2026.1** | 仅 C 线需要；入口 `vitis-run --mode hls --tcl <脚本>` |
| 目标板卡 | **PYNQ-Z2（`xc7z020clg400-1`）** | M3 之前**不需要**板卡 |
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
#   默认跑 roi_statistic；换 IP：set "HLS_IP=fir_filter"（四个 IP：roi_statistic / rgb2gray /
#   motion_quality / fir_filter，各自的数据目录见 fpga/README.md）
#   fir_filter 还能秒级自检（不需要 Vitis）：
#   g++ -O2 -std=c++17 -I fpga/src fpga/sim/host_model_fir.cpp -o host_model_fir.exe && host_model_fir.exe fpga/sim/data_fir
```

### 验证"确实跑通了"（而不是"看起来能跑"）

```bash
python -m pytest backend/tests -v       # 契约一致性测试：字段/schema/6 值枚举
```

---

## 目录说明

| 目录 | 线 | 内容 |
|---|---|---|
| `backend/` | **A 线**（算法/后端）+ **B 线**（服务） | 采集、关键点、指标、质量评分、规则融合、存储、FastAPI / WebSocket / Mock |
| `frontend/` | **B 线**（前端） | 零依赖仪表盘：视频占位区 + 指标卡 + 趋势曲线 + 事件日志 + 六态状态灯 |
| `fpga/` | **C 线**（FPGA） | `src/` HLS 源码、`sim/` testbench + Python 黄金参考、`report/` 综合与仿真报告 |
| `board/` | C 线（M3 后） | `bitstream/`、`overlay/`、上板自检脚本 |
| `data/` | A 线 | `raw/` 4 段标准视频、`annotations/` 人工标注、`golden/` 黄金结果 |
| `metrics/` | 三方共享 | `csv/` 指标日志、`logs/` 运行日志、`scripts/` 复现脚本、`evidence/` 证据归档 |
| `docs/` | 三方共享 | **`interface.md`（接口契约，改它先发公告）** + 00~06 方案文档 |
| `skill/` | 三方共享 | 沉淀的 Skill 包（赛制加分项） |
| `report/` | 三方共享 | 设计报告素材 + `llm_log/` 大模型协作记录 |

| `AGENTS.md` | 三方共享 | **AI 协作规范（第一份必读）**：五条铁律、权威顺序、契约变更流程、验证命令与权威事实表 |

**契约入口：`docs/interface.md`** —— 三线并行的唯一耦合点。任何字段、任何寄存器偏移的改动，**先改契约再改代码，并在此文件变更记录签名**。

**AI / 新成员入口：`AGENTS.md`** —— 用 AI（或让队友）动代码前先读它：它规定了"什么不许说、什么不许编、冲突时信谁"。

---

## 当前进度（诚实版）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0** 冻结 | 主场景 / 项目名 / 指标范围 / 接口契约 / 目录结构 / 仓库与协议 | ✅ **完成**（`docs/00`、`docs/interface.md` 🔒 **v1.0 已冻结**、仓库 `vigilens-pynq`、MIT） |
| **M1** 基础框架 | A：回放→JSON；B：Mock→网页；C：最小 IP 仿真+综合 | 🔄 **进行中** |
| **M2** 软件合体 | A 的 JSON 接入 B 的网页，形成完整软件 Demo | ⏳ 待 A/B 骨架跑通 |
| **M3** 硬件接入 | Overlay 上板 + DMA 跑通（**首次需要板卡**） | ⏳ |
| **M4** 完整闭环 | 软硬件同屏对比 + 黄金结果回归 + 可复现脚本 | ⏳ |

各线明细：

- **C 线（FPGA）跑在计划前面**：**四个 IP** 已完成 C 仿真 + C 综合 **+ RTL 协同仿真（cosim）** 并归档报告 ——
  `roi_statistic`（28/28 + 45/45，II=1，Fmax 138.99 MHz，LUT 1267 / FF 723 / BRAM 0 / DSP 1）、
  `rgb2gray` v2（8/8 + 10/10，Fmax 137.46 MHz，BRAM 0）、
  `motion_quality` v2（6/6 + 9/9，Fmax 140.05 MHz，**BRAM 64 = 23%**，91% 风险已闭环）、
  `fir_filter` v1（63 阶 Q15 带通 @30fps，8/8 + 16/16，**比对容差 0**，II=1，Fmax 146.97 MHz，BRAM 0 / DSP 25）。
  四者合计 LUT 8255（15.5%）/ FF 8971（8.4%）/ BRAM 64（23%）/ DSP 32（14.5%）。
  证据见 `fpga/report/` 下的 `c3_c7_roi_statistic_v1.md`、`c4_rgb2gray_motion_quality_v1.md`、
  `cosim_all_ips_v1.md`、`c5_fir_filter_v1.md`。
  C 线 M3 前**已无未实现的离线任务**；`board/`（上板 / Overlay / DMA）待板卡到手后开始。
  P0 四项已收口：契约补上 **「ROI 均值 → Q1.15」量化口径**（4.6 节 + `fpga/sim/q15_ref.py`）、
  四个 IP 的**计数器改为随块复位清零**（`counter_reset`，风险表第 8 条关闭）、
  **M3 系统级预算**（`fpga/report/m3_system_budget_v1.md`，含 9 条上板验收门限）、
  **M4 软硬件对比基线**（`fpga/report/m4_baseline_v1.md` + `metrics/scripts/bench_filter_ps.py`）。
  接口契约已由 C 线推进到 **v1.0 并冻结**（2026-09-11）；第 5.2 节列出 A/B 待补签的 6 项
  （**尚未收到书面表态**，本文件不代签），认可即补签、有异议按第 7 节提变更走 v1.1
  （`docs/interface.md` 第 5 节）。
- **A 线**：骨架就位（`backend/*.py`），`run_pipeline.py` 端到端链路为 **stub 状态**（EAR 为真指标，PERCLOS/MAR/头姿/质量/规则为占位），待按《02》A1~A10 逐项替换。
- **B 线**：`frontend/` 六态仪表盘 + `backend/mock.py` / `websocket.py` 就位，接真实数据只需切一个数据源开关。
- **board / M3 之后**：`board/` 仅占位，未开始。

> 每个目录另有自己的 `README.md` 说明该线的入口、验收口径与当前欠账；`fpga/README.md` 是 C 线的详细工作记录。

---

## 开源协议与第三方依赖

- 本项目以 **MIT License** 开源，见 [`LICENSE`](LICENSE)。
- 第三方依赖均为各自协议（详见 `requirements.txt` 注释）：FastAPI / Uvicorn（MIT）、OpenCV（Apache-2.0）、MediaPipe（Apache-2.0）、NumPy（BSD-3-Clause）、PyYAML（MIT）、pytest（MIT）。
- `fpga/examples/Vitis-HLS-Introductory-Examples/` 为 AMD 官方教学例程，**仅作本地环境验证参考，不纳入本仓库**（见 `.gitignore`），版权归 AMD 所有。
- 本仓库不包含任何真实受试者的可识别影像；`data/` 下的测试视频须取得出镜者同意后使用。

---

## 团队

三人小组，三线并行：A 线（算法/后端）、B 线（前端/可视化）、C 线（FPGA/硬件）。
分工与里程碑见 [`docs/02_三人分工与三线并行开发计划.md`](docs/02_三人分工与三线并行开发计划.md)，当前阶段唯一执行主线见 [`docs/04_基础框架搭建指南.md`](docs/04_基础框架搭建指南.md)。
