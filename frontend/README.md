# frontend/ —— B 线（前端 / 可视化）

> 依据《02》第三节 B 线任务表（B1~B8）、第四节接口契约；《04》第 4 节"Mock JSON → 网页渲染"。
> **验收口径（《04》）：浏览器里能看到一个六态齐全、用 Mock 数据驱动的仪表盘；关掉后端时页面显示"连接中断"。**

## 文件

| 文件 | 说明 |
|---|---|
| `index.html` | 页面骨架 + 深色主题样式（内联 CSS，无外部依赖） |
| `app.js` | 仪表盘逻辑：数据源切换、渲染、曲线、日志、契约校验 |
| `mock.js` | 离线 mock 数据源 + **契约校验器**；同时是 node 可执行脚本（跨语言契约检查用） |

## 三种打开方式（都试一遍，它们验证的是不同的事）

```bash
# 方式 1：双击 index.html（file://）—— 零依赖，页面自动进入"离线 Mock 演示"
#         验证的是：前端自己在没有后端、没有摄像头时也能演示，答辩断网也不怕

# 方式 2：WebSocket 推送（最接近最终形态）
python backend/websocket.py                 # 默认 ws://127.0.0.1:8765
#         然后打开 index.html，地址框填 ws://127.0.0.1:8765，点"连接 WebSocket"
#         验证的是：B3 的 1 帧/秒推送链路

# 方式 3：后端托管页面（省掉 file:// 的麻烦，M2 推荐）
python backend/api.py                       # 浏览器打开 http://127.0.0.1:8000/
#         验证的是：REST + WS + 静态页面同源，无跨域问题
```

`frontend/index.html` 默认地址填的是 8765；若用 `api.py`，把地址改成 `ws://127.0.0.1:8000/ws`。

## 六种状态怎么演示（B6 验收）

两种方式，任选：

1. **自动轮转**：离线演示下按 `normal → fatigue_risk → adjust_posture → unreliable → disconnected → done` 每秒切一态；
2. **手工强制**：右下"强制状态"下拉框任选一态，状态灯、建议、指标卡会同步变成与该状态自洽的数值
   （Mock 是**按 status 反推指标**的，不是各字段独立乱随机 —— 否则会出现"写着疲劳、数字说正常"的演示事故）。

`disconnected` 另外还能用"真断开"复现：连上 WebSocket 后把后端 Ctrl+C，3 秒内页面自动切到"无数据 / 连接中断"。

还有一种更容易漏的情况：**WS 还连着、但数据停了**（例如 `--no-mock` 下 A 线停止推送）。
这种断流由**服务端**宣布 —— `api.py` 在 `ws_disconnect_timeout_s` 后主动下发一帧契约合法的
`disconnected`，页面只是照常渲染它（不再依赖自己的看门狗）。

## 为什么不用 CDN / 不用框架

- **不用 CDN**：赛制要求"陌生人干净机器一键复现"，答辩现场经常没有外网；
  引 CDN 的页面断网后会静默变空白，这是最致命的演示事故。曲线用原生 canvas 手绘。
- **不用 `<script type="module">`**：`file://` 下浏览器按 CORS 拦截 ES module，
  双击打开会白屏。所以全部用传统 script 标签 + 全局对象 `window.VigiLensMock`。
- 图表自己画还有个副作用：**没有任何第三方许可证问题**，报告里的"第三方依赖"一栏更干净。

## 契约怎么保证不跑偏

这个前端**对每一帧都跑契约校验**（`VigiLensMock.validateFrame`，与 `backend/contract.py` 同规则）：

- 每帧合法 → 右上角"契约：✓ N"；
- 出现不合法帧 → 变红"✗ N"，并把**第一条错误原因**写进事件日志，坏数据不渲染。

这样 M2 接 A 线真数据时，"字段对不上"会**当场在界面上暴露**，而不是等到答辩发现某个卡片一直是空的。

跨语言一致性与接线一致性都可自动检查（在仓库根执行）：

```bash
node frontend/mock.js --selftest                                  # JS 侧：六态帧合法 + 5 个坏帧必须被抓
node frontend/mock.js --limit 6 > metrics/evidence/js_frames.jsonl
python metrics/scripts/check_frontend_contract.py metrics/evidence/js_frames.jsonl   # Python 侧再验一遍
python metrics/scripts/check_frontend_wiring.py                   # app.js 引用的 DOM id 是否都存在
```

> `check_frontend_wiring.py` 抓的是最难自查的一类前端事故：`getElementById("charts")` 拼错成
> 不存在的 id 时，**页面不报错、某块区域永远空白**。

## 当前状态与欠账（B1~B8）

- [x] B1 FastAPI 三接口（`/api/status` `/api/metrics` `/api/events`）+ `/api/ingest`
- [x] B2 Mock 数据生成器（契约一致、状态自洽、可复现）
- [x] B3 WebSocket 每秒推送，断开有明确提示
- [x] B4 页面骨架：视频占位区 + 指标卡 + 状态区（占位区额外叠了 `face.bbox` 检测框，用来验证"指标与画面同源"）
- [x] B5 趋势曲线（眨眼率 / PERCLOS / 心率 / 质量，原生 canvas，保留 120 秒）
- [x] B6 事件日志 + 六态状态机展示，样式区分清晰
- [x] B7 软硬件模式切换**占位**（顶部"模式：软件模式"标签；M3 接 C 线后切"硬件模式"）
- [x] B8 深色仪表盘 UI 打磨 + **产出可直接进报告的截图** → `metrics/evidence/2026-09-15_b8_*.jpg`
      （说明与复现命令见同目录 `2026-09-15_b8_ui_screenshots.md`；**图中无真人数据，均为 Mock / stub 合成**）
- [x] 新增「**信号质量门控 · 能不能测**」面板：4 条门控条 + 总判定，不通过时列出未通过项
      —— 这是产品承诺"先判断能不能测"在界面上的落点
- [x] 新增**眨眼状态机指示**（`behavior.blink_state` 四态高亮）与**长闭眼次数卡**（`behavior.long_close_count`）
- [x] 心率/呼吸卡改为**门控三态**：有值（+ 置信度）/ 质量不足「暂不出数」/ 质量过低「已锁定」
      —— 任何情况下**不写 0、不沿用上一个数字**（契约 §1 与产品承诺的落地）
- [x] 呼吸率卡在**没有数值**时显示契约级限制说明（§3.5：63 阶 @30 fps 做不了呼吸带，
      待 PS 侧降采样）；**有值时不显示**，避免"卡上有数字、备注却说这指标出不来"的自相矛盾
- [x] **`/ws` 断流兜底**（A 线 2026-09-16 交接项，见 `backend/A_LINE_DEV_STEPS.md` §9 第 8 条）：
      服务端在 `ws_disconnect_timeout_s` 后下发**契约合法**的 `disconnected` 帧（沿用上一帧的
      `frame_id`/`ts`，不倒退；持续断流按周期重复），而不再是"静默不发"。
      客户端看门狗因此**退到 4 s**（比服务端慢 1 s），只在服务端整个挂掉时兜底。
      回归检查：`python metrics/scripts/check_b_line_ws_disconnect.py`（5 项）
- [x] 页面由 `api.py` 托管时**自动填好** `ws://<host>/ws`（探测 `/api/status` 是否可用），
      不用再手填；`file://` 或普通静态服务器下仍保留 `websocket.py` 的 8765 默认值
- [x] 断流帧**不进趋势曲线**：把它当"这段没有数据"（留空），而不是画出一根掉到 0 的假尖峰
- [ ] 视频区接入真实画面（现在是占位网格 + 检测框）
- [ ] 心率/呼吸趋势在 M2 后的真实曲线形态复核（现在心率多数为 `null`，符合门控承诺）
- [x] **门控阈值改为后端下发**（原本是 `app.js` 里 `THRESHOLDS` 的一份副本，A 线一标定就会
      无声漂移）：`api.py` 的 `/api/status` 带回 `thresholds`（值取自 `config.yaml`，键名逐字一致），
      前端优先用它；**离线兜底**才用 `app.js` 的内置副本（双击 `index.html` 走这条）。
      随阈值一起下发的还有 `ws_disconnect_timeout_s` —— 客户端看门狗按 `服务端值 + 1 s` 计算，
      保证它始终比服务端晚触发。
- [ ] **判定证据链（`_triggers`）：B 侧已就绪，等 A 线推送。**
      走**旁路**而不是改契约（帧一个字节不动）：`api.py` 的 `/api/ingest` 已接受与 `frame`
      平级的可选字段 `triggers`，`/api/status` 已回传 `{frame_id, items}`，前端在 WebSocket
      模式下按 1 Hz 取用，并做两道防呆（`frame_id` 必须与当前帧一致；断流帧不显示证据链）。
      **A 线需要做的只有一处一行**，见 [`docs/08_B线给A线的接口请求.md`](../docs/08_B线给A线的接口请求.md)。
      在 A 线改之前，界面显示"暂无判定证据链"——**无副作用，不阻塞任何事**。

## 依赖

前端**零依赖**（唯一"依赖"是 `frontend/mock.js`，自己写的）。
后端服务依赖见根目录 `requirements.txt`；未安装时 `websocket.py` / `api.py`
会打印可操作提示而不是抛 traceback，且**不影响双击 index.html 看界面**。

---

*本目录由 B 线维护；契约字段有异议时先提 `docs/interface.md` 变更，不要在 `mock.js` 里私自加字段。*
