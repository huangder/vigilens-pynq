# B8 界面截图证据（2026-09-15）

> 归档：**B 线**。对应《02》任务表 **B8：深色仪表盘 UI 打磨 + 产出可直接进报告的截图**。
>
> ⚠️ **诚实标注（《05》铁律 1）**：这三张图**都不是真人受试者数据** —— 两张来自前端内置
> Mock，一张来自**合成帧源 + StubLandmarker（`landmark_source=stub`）**。
> 此时 EAR/MAR 是**占位几何量**，**不得作为算法结果或精度证据引用**。
> 它们证明的是**界面与链路跑通**，不是算法准确度。

## 文件

| 文件 | 数据驱动 | 截的是什么 |
|---|---|---|
| `2026-09-15_b8_mock_normal.jpg` | 前端内置 Mock（离线，无后端） | 六态之 `normal`：门控全绿「可以测量」，心率/呼吸有值并显示置信度 |
| `2026-09-15_b8_mock_unreliable.jpg` | 前端内置 Mock（离线，无后端） | 六态之 `unreliable`：门控判「测不准，别采信」，**心率/呼吸显示「已锁定」而不是 0、也不是上一个值** |
| `2026-09-15_b8_jsonl_websocket.jpg` | **A 线真实产出**：`run_pipeline.py` 的 jsonl → `websocket.py --mode file` | M2 链路预演：顶部为「WebSocket / 已连接 / 契约 ✓」，状态灯为 `fatigue_risk` |

三张均为整页截图（1264 px 宽），由浏览器截图 API 取得，再用 Pillow 校验过格式与尺寸。

## 怎么生成的（可复现）

### 1) 两张 Mock 截图（不需要后端、不需要摄像头）

```powershell
python backend/api.py            # 托管页面，浏览器打开 http://127.0.0.1:8000/
# 用右下角"强制状态"下拉分别选「正常」和「信号不可靠」，各截一张
```

### 2) 真实数据链路截图（M2 预演）

```powershell
# ① 造一份 A 线产出（合成帧源，无需摄像头）
python backend/run_pipeline.py --source synthetic --pattern blink --seconds 60 --stub `
    --jsonl metrics/logs/stream_blink.jsonl
#    ⚠️ 在工作区 D:\coding\FPGA 内运行会随机撞上 backend/storage.py:63 的 os.replace
#       WinError 5（环境问题，定性见 docs/interface.md 5.2.1）。本次是在工作区外的
#       副本里生成后拷回来的；四种 pattern（blink/yawn/still/turn）各 2700 帧。

# ② 用 B 线的推送服务回放这份 jsonl
python backend/websocket.py --mode file --file metrics/logs/stream_blink.jsonl
# ③ 页面地址栏保持默认 ws://127.0.0.1:8765，点"连接 WebSocket"
```

截图前的实际读数（本机实测）：数据源 `WebSocket`、连接 `已连接`、
契约计数 `✓ 69`（69 帧全部通过校验，无不合契约帧）、门控 `可以测量`、状态灯 `疲劳风险升高`。

**这条链路的结论**：A 线 `--jsonl` 的产出**不用改一行代码**就能被 B 线的
`websocket.py --mode file` 回放、被前端渲染并通过契约校验 —— M2 合体时只需要换文件路径。

## 本次新增/改造的界面模块

| 模块 | 用到的契约字段 | 做法 |
|---|---|---|
| **信号质量门控 · 能不能测** | `quality.light_score` / `motion_score` / `overall`、`face.visible` | 4 条门控条 + 质量总分 + 心率/呼吸额外门控；总判定「可以测量 / 测不准」，不通过时列出未通过项 |
| **眨眼状态机指示** | `behavior.blink_state` | `OPEN→CLOSING→CLOSED→OPENING` 四段，高亮当前态并给中文含义 |
| **长闭眼次数卡** | `behavior.long_close_count` | 与 `fatigue_long_close_count` 比较，达标变黄 |
| **心率/呼吸卡的门控三态** | `vital.hr_bpm` / `hr_conf`、`vital.rr_per_min` / `rr_conf` | 有值 → 数字 + 置信度；质量低于门控线 → 「暂不出数」；质量过低 → 「已锁定」。**任何情况下都不写 0、不沿用旧值** |

## 已知欠账（别当成已解决）

- 门控阈值是 `frontend/app.js` 里 `THRESHOLDS` 的一份**副本**，当前与 `config.yaml` 一致，
  但 **A 线标定后不会自动同步**（症状：该报警却不报警）。正解是后端随 `/api/status` 下发阈值。
- 视频区仍是占位网格 + `face.bbox` 叠框，**未接真实画面**。
- 页面未显示 `ts` / `frame_id`（只在事件日志里间接出现）。
- `_triggers`（可解释链条）区域仍是空壳：该字段**不在契约里**，A 线 `decision.py` 也未输出，
  需要先决定它是否进契约。
