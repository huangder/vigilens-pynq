# backend/ —— A 线（算法/后端）+ B 线（服务）

> 依据《02》第三节 A 线任务表、第四节接口契约；《04》第 3 节目录与第 4 节最小交付。
> **接口以 `docs/interface.md` 为唯一准绳**，本目录任何字段名都不得自创。

## 模块职责

| 文件 | 线 | 任务号 | 职责 | 当前状态 |
|---|---|---|---|---|
| `contract.py` | A/B 共用 | — | **契约唯一来源**：字段、6 值枚举、校验器 | ✅ 完成 |
| `config.py` | A/B 共用 | — | 读仓库根 `config.yaml`（无 PyYAML 时用内置解析器） | ✅ 完成 |
| `console.py` | A/B 共用 | — | 把 stdout 切 UTF-8，避免 GBK 控制台打印符号时崩 | ✅ 完成 |
| `hub.py` | A/B 共用 | — | 帧总线（A 产出 → B 消费的唯一交接点） | ✅ 完成 |
| `capture.py` | A | A1 | 帧源：视频文件 / 摄像头 / **确定性合成帧源** | ✅ 骨架完成 |
| `face_landmark.py` | A | A2 | 人脸框 + 关键点 + 头姿；MediaPipe 缺失时确定性 stub | ✅ 骨架完成（**stub 占位**） |
| `behavior_metrics.py` | A | A3~A5 | EAR + 眨眼状态机 + PERCLOS + MAR + 打哈欠 | ✅ **真实现**（非 stub） |
| `quality.py` | A | A7 | 光照/运动/人脸 → 质量评分；**帧差运动量黄金参考** | ✅ 真实现（权重待标定） |
| `decision.py` | A | A8 | 规则融合 + 可解释建议 + 心率门控 | ✅ 真实现（阈值待标定） |
| `storage.py` | A | A9 | JSON 快照 / jsonl / CSV 落盘，写入前校验契约 | ✅ 完成 |
| `run_pipeline.py` | A | A1~A9 | **端到端入口**：回放 → 契约 JSON | ✅ 骨架跑通 |
| `mock.py` | B | B2 | 契约一致的假数据源（按 status 反推指标，状态与数字自洽） | ✅ 完成 |
| `websocket.py` | B | B3 | WebSocket 1 帧/秒推送（mock / file / bus 三源） | ✅ 完成 |
| `api.py` | B | B1 | REST + WS + 托管前端；`POST /api/ingest` 为 M2 集成点 | ✅ 完成 |
| `tests/` | A/B | — | 65 项测试（契约一致性 + rPPG 链路） | ✅ 全绿 |

## 怎么跑

```bash
# 0) 先载入本机工具链（只影响当前会话，不改系统环境变量）
. .\env.ps1

# 0.5) 【推荐】一条命令跑完 A 线全部自检（约 20 秒，退出码即结论）
python metrics/scripts/check_a_line_all.py
#   它串起四段：仓库四项自检 / backend 9 个模块自检 /
#   端到端合成回放 + 重复运行逐字节一致 / P1 黄金参考对拍（5 项）
#   ⚠️ 自检**不改动工作区**：结果只写 metrics/logs/（不入库）。
#      要把某一次运行归档成正式证据，才显式加 --evidence，然后把证据文件一并提交：
#      python metrics/scripts/check_a_line_p1_all.py --evidence

# 0.9) 依赖（本机已就绪；要重建才需要）
pip install -r requirements.txt

# 1) A 线端到端（不需要摄像头、不需要板卡、连 OpenCV 都不需要）
python backend/run_pipeline.py --source synthetic --pattern blink --seconds 30 --stub \
    --json metrics/logs/last.json --jsonl metrics/logs/stream.jsonl --csv metrics/csv/metrics.csv

#    有真实视频时（4 段标准视频见 data/README.md）
python backend/run_pipeline.py --source data/raw/blink.mp4 --json metrics/logs/last.json

# 2) B 线服务（二选一）
python backend/websocket.py                 # ws://127.0.0.1:8765，配 frontend/index.html
python backend/api.py                       # http://127.0.0.1:8000/ 直接托管前端页面

# 3) 只想到处看看
python backend/mock.py --frames 6           # 六态各一帧契约 JSON
python backend/config.py                    # 确认阈值读到了什么
python backend/decision.py                  # 四条判定规则的自检
python -m pytest                            # 65 项测试（契约一致性 + rPPG 链路）
```

`--pattern` 可选 `blink`（每 3 秒眨眼，第 20 秒起一次长闭眼）/ `yawn`（每 6 秒一次 1.5 秒张口）/ `still` / `turn`（3~6 秒转头、6 秒后人脸出框）。

## 三条必须知道的边界（写报告时不能含糊）

1. **A 线现在的数字来自 stub 几何量，不是算法结果。**
   没装 mediapipe 时 `face_landmark.py` 用 `StubLandmarker` 按 `frame_id` 确定性造关键点。
   好处是"重复运行结果一致"这条验收口径在装依赖之前就成立；
   代价是 EAR/MAR 的数值**没有算法意义**。每次运行都会打印来源，摘要里也带
   `"landmark_source": "stub"`。**引用数字前先看这个字段。**
2. **时间戳用 `frame_id / fps`，不用墙上时钟。**
   PERCLOS / 眨眼率是滑窗统计，用墙上时钟会让同一段视频跑两次得到不同结果，
   直接违反《02》A3。要真实实时采集时才加 `--wall-clock`（代价：不可复现）。
3. **`vital.*` 大面积为 `null` 是正确行为。**
   rPPG 尚未实现（《02》风险表：rPPG 严格放最后），且质量门控不通过时
   系统承诺**不输出心率数值**（`decision.gate_vitals`，阈值 `vital_require_quality`）。

## 契约纪律（改了会出人命的那条）

- 字段名、枚举值只允许在 `contract.py` 出现一次；A 的真实输出与 B 的 mock 都必须过
  `new_frame()` / `validate_frame()`。
- **改字段的顺序是**：先改 `docs/interface.md` 的变更记录 → 再改 `contract.py` 与
  `frontend/mock.js` → 最后改业务代码。反了就会出现"两边各自合法、拼起来不合法"。
- 跨语言检查（JS 与 Python 对同一份契约的判断必须一致）：
  ```bash
  node frontend/mock.js --limit 6 > metrics/evidence/js_frames.jsonl
  python metrics/scripts/check_frontend_contract.py metrics/evidence/js_frames.jsonl
  ```

## 测试覆盖了什么

`python -m pytest`（65 项）刻意覆盖的是**契约与可复现性**，不是"函数能跑"：

- `docs/02` 4.1 里手写的那份示例 JSON 必须仍然合法（文档与代码不许打架）；
- Mock 必须能产出**全部 6 种 status**，且状态与数字自洽（写"疲劳"时 PERCLOS 就必须高）；
- 质量低时 `vital.*` 必须全 `null`；
- 校验器必须抓得住 10 类坏帧（未知 status、bbox 少一个、多出契约外字段、越界分数…）；
- **同一段回放跑两次，末帧 JSON 与 CSV 必须逐字节相同**（A3 验收口径）；
- 人眼丢失时 EAR 归零，不能被当成"睁眼正常"。

## 已知环境问题（不是代码问题，别浪费时间）

| 现象 | 原因 | 处理 |
|---|---|---|
| `python backend/config.py` 报 `ModuleNotFoundError: No module named 'config'` | `.tools\python312` 是 **embeddable** 版（带 `python312._pth`），**不会**把脚本所在目录加进 `sys.path` | `env.ps1` 已把 `.venv\Scripts` 排在 PATH 最前（`.venv` 是 virtualenv，行为正常）；若手动指定解释器，请用 `.venv\Scripts\python.exe` |
| `PyYAML 可用: False` | 没装 PyYAML | 无需处理：`config.py` 会自动降级为内置解析器读同一份 `config.yaml` |
| `pip install` 在 mediapipe / opencv-python 上失败 | Python 3.14 的官方 wheel 可能尚未发布 | **不要**去编译，改用 3.11/3.12 建 venv：`py -3.12 -m venv .venv` |
| 脚本打印中文出现乱码 | 控制台代码页是 GBK | 已内置 `console.enable_utf8_console()`；在 Windows Terminal / VS Code 里正常 |
| pytest 报 WinError 5 且 `.pytest_cache` 警告 | 受限沙箱/受限 ACL 下 pytest 的随机名目录不可访问 | 已用仓库内 `workdir` fixture 与 `-p no:cacheprovider` 规避（见 `tests/conftest.py`） |
| `run_pipeline --source <视频>` 说"回退到合成帧源" | 没装 opencv-python | `pip install opencv-python`；或用 `--source synthetic` 先验证链路 |

## 下一步（A 线）

- [ ] A2 真接 MediaPipe，用**真实视频**重标定 `config.yaml` 的 `ear_close_threshold` / `mar_threshold`
- [ ] A6 头部姿态与可见率实测标定（`pose_yaw_max_deg` / `face_visible_min` 目前是占位值）
- [ ] A7 质量权重 `quality_weights` 用"大幅晃动 / 变暗 / 正常"三种场景重标定
- [ ] A10 对 4 段标准视频生成黄金 CSV 并锁版本（`data/golden/`）
- [ ] 与 C 线对拍：用同一份 `fpga/sim/data/frames.bin` 跑 `quality.frame_diff_motion`，
      验证 R/G/B 顺序与半开区间 ROI 的口径一致（`docs/interface.md` 第 5 节第 1、2 项待会签）

---

*本目录由 A 线维护；B 线服务相关文件（`mock.py` / `websocket.py` / `api.py`）改动请同步 `frontend/README.md`。*
