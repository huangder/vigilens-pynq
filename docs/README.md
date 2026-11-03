# 项目文档

本目录描述截至 2026-11-04 的源码与候选状态。运行事实以对应版本的原始证据为准；未会签候选不等于正式契约升级。

| 文档 | 内容 |
|---|---|
| [架构与实现](architecture.md) | 当前数据链路、软硬件划分、心率与显示语义 |
| [运行与构建](usage.md) | PC 演示、板端重建与 SD 启动入口 |
| [验证与证据](validation.md) | 检查命令、历史实测与验收层次 |
| [硬件与连接](hardware.md) | 当前板卡、USB 相机与连接要求 |
| [标定与诊断](calibration.md) | 特征采集、人工标注与 rPPG 拒报排查 |
| [心率信号与本地历史候选](hr_signal_history.md) | 30 Hz输入下限、60 BPM低值过滤、浏览器历史和真实同输入回放 |
| [已知限制](limitations.md) | 尚未验收的项目与复现条件 |
| [接口契约](interface.md) | 字段、枚举、硬件接口及会签记录的权威来源 |
| [AI 使用规则](ai_usage.md) | 数据真实性、隐私及人工协作记录要求 |
| [协作与提交规则](contributing.md) | 目录分工、契约流程与提交验证 |

正式证据保留在 [metrics/evidence](../metrics/evidence/) 和 [fpga/report](../fpga/report/)。
设计报告素材与人类协作记录见 [report](../report/)，复用经验见 [skill](../skill/)。
过期阶段计划、采购草案与内部问题台账已移出当前文档目录；已提交版本仍可通过 Git 历史追溯。
