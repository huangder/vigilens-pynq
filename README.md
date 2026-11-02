# VigiLens · 知倦

基于摄像头与 FPGA 的无接触疲劳与生命体征趋势监测原型。

**先判断能不能测，再决定测出什么。**

2026 全国大学生嵌入式芯片与系统设计竞赛 · AMD 赛道 3.3 自主选题（初级组）。

## 当前交付

主链路在 Mizar-Z7020 板端完成相机采集、PL 预处理、人脸/关键点、行为指标、质量门控和心率趋势估计，由 HTTP 页面展示。PC 浏览器负责显示；PC 参考管线、年龄旁路和录像工具另行保留。

【已验证：已有日志】有限帧数的板端真人运行、契约输出与心率候选见 [验证与证据](docs/validation.md)。

【未验证】最新持续采集版的物理 SD 冷启动、故障恢复、完整 30 fps、心率与疲劳准确性仍未验收。历史显示数字不代表当前有效测量。本项目用于趋势监测和辅助提示，不作医疗诊断，不提供血氧或血压。

- [项目交付与运行入口](docs/usage.md)：代码地图、演示、测试、依赖与验收边界。
- [文档索引](docs/README.md)：保留的有效文档。
- [SD 拷贝与启动](board/cold_boot/README.md)：现有启动包的使用步骤。
- [接口契约](docs/interface.md)：字段、枚举与硬件接口的权威定义。
- [已知限制](docs/limitations.md)：尚未验收的功能、准确性和复现条件。

## PC 快速演示

需要 Python 3.12、项目依赖与现代浏览器。首次配置：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
. .\env.ps1
python metrics/scripts/run_demo.py
```

该入口默认使用合成帧，只证明软件接线。真实摄像头先用 `python metrics/scripts/run_demo.py --list` 查询，再用 `--source 0` 等实际设备编号。`landmark_source=stub` 的输出不得作为算法结果。

## 检查与复现

```powershell
. .\env.ps1
python metrics/scripts/check_all.py
python -m pytest -q
```

总入口检查离线软件回归；板端工具测试、ARM 构建、HLS 仿真与物理验收见 [交付入口](docs/usage.md)。真实结果查 `metrics/evidence/` 与 `fpga/report/`，不以旧文档中的通过数代替本次执行。

## 目录

| 目录 | 内容 |
|---|---|
| `backend/`、`frontend/` | PC 参考算法、服务、仪表盘与辅助功能 |
| `board/` | 板端自治、Linux、SD 启动、裸机和仿真工具 |
| `fpga/` | 五个 HLS IP、黄金参考、测试台与真实报告 |
| `data/` | 标注、参照基线与数据库 schema |
| `metrics/` | 检查脚本和正式证据 |
| `docs/` | 契约、交付、标定、保留的技术文档 |
| `report/`、`skill/` | 报告素材、人工协作记录和技能包 |
| `deliverables/` | 本机生成的 RAR、SD 包、校验清单；不入库 |

配置唯一来源为 `config.yaml`。开发前先读 [AGENTS.md](AGENTS.md)、[AI 使用约束](docs/ai_usage.md) 与 [提交规范](docs/contributing.md)。

源码仓库包含当前工程成果，不包含虚拟环境、原始视频、个人数据库、模型权重或第三方工具链。已有 SD 软件包单独交付；物理启动状态见 [运行与构建](docs/usage.md)。
