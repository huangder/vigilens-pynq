# metrics/ —— 三方共享的指标 / 日志 / 脚本 / 证据

> 依据《04》第 3 节目录结构。这个目录是**"可复现"和"证据"的落点**：
> 评审问"这个数字哪来的"时，答案应该能在这里一路找到命令、日志与产物。

## 目录

| 目录 | 放什么 | 入库 |
|---|---|---|
| `csv/` | 指标 CSV（`run_pipeline.py --csv` 的产物，报告作图用） | ❌ 生成物，忽略 |
| `logs/` | 运行日志、`last.json`（最新一帧快照）、`stream.jsonl`（逐帧） | ❌ 生成物，忽略 |
| `scripts/` | **可复现脚本**（契约检查、前端接线检查等） | ✅ |
| `evidence/` | **证据归档**：真实运行的输出、截图、摘要 JSON | ✅ |

## scripts/ 里的脚本（每个都可直接跑，退出码 0 = 通过）

### 1. 跨语言契约检查（A 的 Python 与 B 的 JS 对同一份契约必须一致）

```bash
node frontend/mock.js --limit 6 > metrics/evidence/js_frames.jsonl
python metrics/scripts/check_frontend_contract.py metrics/evidence/js_frames.jsonl
```

抓的是：JS 认为合法、Python 认为非法的字段分歧。这种分歧**只会在 M2 集成的当天暴露**，
然后就是"谁都没改坏、但拼不起来"的互相扯皮。所以提前自动化。

### 2. 前端接线检查

```bash
python metrics/scripts/check_frontend_wiring.py
```

抓的是：`app.js` 里 `getElementById("charts")` 这类拼错的 id ——
症状是**页面不报错、某块区域永远空白**，手工点页面很难发现。

### 3. 全量测试（49 项契约与可复现性测试）

```bash
python -m pytest
```

### 4. 一键复现（M4 要交付的东西，现在就开始用）

```bash
python backend/run_pipeline.py --source synthetic --seconds 30 --stub \
    --json metrics/logs/last.json --jsonl metrics/logs/stream.jsonl \
    --csv metrics/csv/metrics.csv --summary metrics/logs/run_summary.json
```

> **日常重跑请把 `--summary` 指到 `metrics/logs/`（已忽略）**，不要指向 `metrics/evidence/`。
> 否则每跑一次都会修改入库的证据文件，工作区永远是"脏的"，真正的改动会被淹没。

## evidence/ 的证据纪律

一条证据 = **一段真实输出 + 它对应的命令 + 时间 + 环境**。五条要求：

1. **必须是真实运行的结果**（《05》铁律 1：AI 不得编造任何测试数据/性能数字）；
2. 文件名带日期与主题：`2026-09-10_a_line_m1_stub_run.json`、
   `2026-09-10_roi_statistic_v1_csim_csynth.log` 这种格式（C 线已在用）；
   证据是**冻结的快照**，重跑后想更新就新存一个日期文件，不要原地覆盖；
3. 同时记下**是哪个数据源**：A 线的摘要 JSON 里有 `"landmark_source": "stub" | "mediapipe"`，
   `stub` 表示数字来自占位几何量，**不能直接写进报告**；
4. 截图（B8）也要归档到这里，文件名写清是"Mock 数据"还是"真实数据"驱动的界面；
5. 现有证据：`2026-09-10_a_line_m1_stub_run.json`（A 线 M1 stub 链路 30 秒跑通的真实摘要）、
   `js_frames.jsonl`（B 线 JS 产出的 6 帧，用于跨语言契约检查）。

## 与 C 线的关系

C 线的黄金参考比对产物在 `fpga/sim/data/golden_roi.csv` 与 `fpga/report/`，
**不**放在这里；本目录只放三方共享的运行时产物与检查脚本。
