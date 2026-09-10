# 各线开发工具与 Skill 调研
## 三线各自的提效工具 + 环境 Skill + 赛制 Skill

> 目标：给三条线分别配"能立刻省时间的工具"，并区分三件事——
> 1. **开发工具**（环境/测试/构建/验证）；
> 2. **本环境（DeepSeek Harness）可用的 Skill**；
> 3. **赛制要提交的"技能包 Skill"**（加分项，可从工具里沉淀）。
>
> 原则：**工具只选"确定能省时间"的，不为炫技引入学习成本。** 配套见《04》框架、《05》AI 约束。

---

## 一、A 线（Python 视觉/算法）工具

| 工具 | 地址/来源 | 用途 | 省时点 | 推荐度 |
|---|---|---|---|---|
| **uv** | https://github.com/astral-sh/uv | 极快的 Python 包/环境管理 | 比 pip+venv 快一个量级，锁依赖 | ★★★★★ |
| **ruff** | https://github.com/astral-sh/ruff | 一键 lint + 格式化 | 一个工具替代 flake8+black+isort | ★★★★★ |
| **pytest** | https://github.com/pytest-dev/pytest | 单元测试 | 让"完成"可验证，配合黄金参考 | ★★★★★ |
| mypy / pyright | — | 静态类型检查 | 早发现字段类型对不上 | ★★★ |
| **Jupyter** | — | 快速实验 EAR/MAR 调参 | 逐帧调试可视化 | ★★★★ |
| CVAT（如需人工标注） | https://github.com/cvat-ai/cvat | 视频标注眨眼/哈欠 | 只在你需要"人工真值"时用 | ★★ |
| MediaPipe / OpenCV / SciPy | 见《03》 | 算法底座 | 已在《03》详述 | ★★★★★ |

**A 线最该先装的三件套：`uv` + `ruff` + `pytest`。** 有了它们，"黄金参考 + 回归测试"才落得了地（这也正是赛制 Skill 里"校验脚本"的雏形）。

---

## 二、B 线（前端/可视化）工具

| 工具 | 地址/来源 | 用途 | 省时点 | 推荐度 |
|---|---|---|---|---|
| **FastAPI**（含 uvicorn） | https://github.com/fastapi/fastapi | 后端 REST + WebSocket | `--reload` 热更新，开发快 | ★★★★★ |
| **Chart.js** | https://github.com/chartjs/Chart.js | 实时曲线 | 上手最平，贴个 `<canvas>` 就行 | ★★★★★ |
| ECharts（备选） | https://github.com/apache/echarts | 更炫仪表盘 | 答辩"好看"，但略重 | ★★★★ |
| **Vite** | https://github.com/vitejs/vite | 前端构建/热更新 | 秒级热更 | ★★★★ |
| Tailwind CSS | https://github.com/tailwindlabs/tailwindcss | 快速出深色 UI | 少写 CSS | ★★★ |
| pnpm | https://github.com/pnpm/pnpm | 前端包管理 | 快、省磁盘 | ★★★ |

**B 线最该先装：`FastAPI` + `Chart.js`**；若追求精致再用 ECharts。**省时关键**：直接抄《03》里的 monitrix / real-time-charts 骨架，别从零搭。

---

## 三、C 线（FPGA/HLS）工具

| 工具 | 地址/来源 | 用途 | 省时点 | 推荐度 |
|---|---|---|---|---|
| **Vitis HLS 2026.1** | AMD 官方 | C→RTL 综合 | 赛制指定，别无选择 | ★★★★★ |
| **cocotb** | https://www.cocotb.org/ | 用 **Python** 写 RTL testbench | 复用 A 线的 Python 黄金参考，不必学 SV TB | ★★★★★ |
| **Verilator** | https://github.com/verilator/verilator | 极快的 Verilog 仿真/检查 | 快速跑通、顺手当 linter | ★★★★ |
| GTKWave / Surfer | — | 波形查看 | 调试时序 | ★★★ |
| FuseSoC（可选） | https://github.com/olofk/fusesoc | 统一构建/依赖管理 | 项目变大后再考虑 | ★★ |
| Verible（可选） | https://github.com/chipsalliance/verible | SystemVerilog 格式化/lint | 若手写 RTL 才有用 | ★★ |
| Vitis HLS Introductory Examples | https://github.com/Xilinx/Vitis-HLS-Introductory-Examples | 官方最小例程 | **第 1 天跑这个锁定工具链** | ★★★★★ |
| Vitis Libraries（Vision） | https://github.com/Xilinx/Vitis_Libraries | 像素流 `ap_axiu` 范式 | 你的 IP 直接套它的写法 | ★★★★★ |
| HLSPilot / llm-fpga-design（了解） | [arXiv 2408.06810](https://arxiv.org/html/2408.06810v1) / https://github.com/rockyco/llm-fpga-design | LLM 辅助 HLS/FPGA 流程 | 参考其"AI 协作调试"思路，**勿依赖** | ★★ |

**C 线最该先装的组合：`Vitis HLS` + `cocotb`（+`Verilator`）。** 有了 cocotb，C 线写 testbench 就复用 Python，与 A 线黄金参考直接对齐，**这才是"不靠板卡验证"的落地工具**。

---

## 四、跨线通用工具

| 工具 | 地址/来源 | 用途 | 省时点 |
|---|---|---|---|
| **Git + GitHub/Gitee** | — | 版本 + 开源 | 赛制要求，第 0 天建仓 |
| **pre-commit** | https://github.com/pre-commit/pre-commit | 提交前自动 ruff/格式 | 减少低级错误回流 |
| **GitHub Actions** | — | CI：自动跑 pytest / cocotb | 每次 push 自动验证，省手工 |
| 大模型协作记录脚本 | 自写 | 按《05》模板归档 | 赛制必交项自动化 |

---

## 五、本环境（DeepSeek Harness）可用的 Skill

> 你们正在使用的这个 AI 环境自带可复用 Skill，**能直接省掉赛制"架构图/文档"的大量手工时间**：

| Skill | 作用 | 对应赛制需求 | 怎么省时间 |
|---|---|---|---|
| **archify** | 生成架构图/数据流图/时序图/状态图（HTML/SVG，可导出 PNG/WebP/SVG） | 设计报告要求"系统架构图与数据流图"、`interface.md` 时序约定 | 描述需求即可出图，不用手画 Visio/PPT |
| **openviking-memory** | 持久化项目决策记忆（跨会话记住"我们定了什么"） | 三人协作、决策不丢失 | 下次开会不用重新对齐"上次定了啥" |

> 用法：在对话里直接让我"用 archify 画系统数据流图"，或"记住这个决策"。**决赛那页英文海报配图、报告里的数据流图，都可以用 archify 快速生成。**

---

## 六、赛制"技能包 Skill"（加分项，从上面工具里沉淀）

赛制（3.3）要求提交 `skill/` 目录，且**通用 PYNQ Skill 单独加分**。上面这些工具用顺手后，把沉淀物写成 Skill：

| 可沉淀的 Skill | 来源 | 命中赛制哪类 |
|---|---|---|
| `pynq_overlay_loader`（加载+校验） | C 线 Overlay 封装过程 | 通用 PYNQ Skill（**单独加分**） |
| `dma_buffer_debug`（缓存一致性排查） | C 线 DMA 踩坑 | 通用 PYNQ Skill / 踩坑清单 |
| `golden_ref_check`（pytest 黄金参考比对脚本） | A 线 + cocotb | 校验脚本 |
| `metric_csv_report`（性能/资源自动采集出报告） | C 线综合报告 | 校验脚本 / 案例模板 |
| `llm_hls_debug`（让 AI 分析综合报告的提示词工作流） | 全组 | 提示词工作流 |

> 每个 Skill 按赛制格式写：`适用场景 / 使用方法 / 失效条件 / 从哪些失败总结 / 已验证效果`，见《01》第 12.4 节。

---

## 七、落地顺序（只做这几步，别贪多）

1. **第 0 天**：git 建仓 + 装 `uv/ruff/pytest`（A）、`FastAPI`（B）、`Vitis HLS`（C），各自跑通一个 hello。
2. **第 1 周**：A 用 pytest 锁黄金参考；B 抄 monitrix 骨架；C 用 cocotb + 官方例程。
3. **第 2 周起**：把踩坑写进 `skill/`，让工具变成赛制加分项。
4. **需要出图时**：直接用本环境 `archify`。

---

*本文是六份文档中的"提效工具库"：评估看《01》、分工看《02》、实现看《03》、框架看《04》、AI 看《05》。*
