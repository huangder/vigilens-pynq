# frontend/ —— B 线（前端 / 可视化）

> **2026-11-02 心率连续显示候选**：[文档77](../docs/architecture.md)。
> `hr_display.js`共享状态管理：首次显示“正在测量”；有效值后丢信号/断网保留数字，标为历史值并更新年龄。
> 切换来源或设备重启清除缓存；历史值不进入趋势/告警。检查：`node frontend/hr_display.test.js`。

> 依据《02》第三节 B 线任务表（B1~B8）、第四节接口契约；《04》第 4 节"Mock JSON → 网页渲染"。
> **验收口径（《04》）：浏览器里能看到一个六态齐全、用 Mock 数据驱动的仪表盘；关掉后端时页面显示"连接中断"。**

## 文件

| 文件 | 说明 |
|---|---|
| `index.html` | 页面骨架：左侧视频 / 当前判断 / 质量门控，右侧实时读数与卡内趋势，底部开发者工具 |
| `style.css` | iOS 语义色参考的浅色主题、响应式排版和本地字体声明 |
| `app.js` | 仪表盘逻辑：数据源切换、渲染、曲线、日志、契约校验 |
| `mock.js` | 离线 mock 数据源 + **契约校验器**；同时是 node 可执行脚本（跨语言契约检查用） |
| `preview.js` | 同源预览坐标换算 + 响应头解析；浏览器和 Node 共用 |
| `ui_state.js` | 等待 / 断流 / 无人脸 / 可测量的统一显示语义 + 数据来源标签；浏览器和 Node 共用 |
| `trends.js` | 卡片历史窗口、断流空档、按时间分段与独立量纲范围；浏览器和 Node 共用 |
| `alerts.js` | 疲劳 / 心率持续提示：独立计时、质量门控、中断重置、确认去重；浏览器和 Node 共用 |
| `tests/alerts.test.js` | 提示时长边界、缺测、重播、门控、恢复与组合提示的合成回归 |
| `tests/trends.test.js` | 趋势窗口 / 最新样本 / 空档 / 重播 / 零值 / 量纲的 Node 回归测试 |
| `icons/` | Microsoft Fluent UI System Icons 的本地 Regular SVG 与 MIT `LICENSE` |
| `font/` | 可选的本地 MiSans 字体目录（不入库）；缺失时自动回退到系统中文字体 |

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

# 方式 4：真实视频预览（两个终端）
python backend/api.py --no-mock
python backend/run_pipeline.py --source <本地视频> --post auto \
    --preview-post auto --preview-hz 12 --realtime
#         指标仍是冻结契约 1 Hz；视频 JPEG 约 12 fps，经 /api/preview 独立旁路传输
```

预览验收使用持续的视频/摄像头源。`--no-mock` 只启动接收服务；A 线回放结束后页面会在超时后隐藏过期画面。视频区域只显示源画面，已移除前端人脸定位框与跟踪绘制；后端用于计算指标的人脸检测与质量门控保持原有逻辑。算法结果仍须核对摘要中的 `landmark_source`：`stub` 是占位实现，不能作为真实测量结果引用。

### 该开哪一个？（一张表，防"把 mock 当测量"）

| 你想干的事 | 起来的命令 | `/api/status.source` | WebSocket 地址 |
|---|---|---|---|
| 只看界面（没后端、断网答辩） | 双击 `frontend/index.html` | 读不到（页面自报「离线 Mock」） | — |
| 验 1 帧/秒推送链路 | `python backend/websocket.py` | 读不到 ⇒ 显示「来源未声明」 | 手填 `ws://127.0.0.1:8765` |
| 看**真实测量**（M2 集成） | 终端 1：`python backend/api.py --no-mock`<br>终端 2：`run_pipeline.py ... --post auto` | `ingest` | 自动同源 `/ws` |
| 一键演示（自动挑端口） | `python metrics/scripts/run_demo.py --source 0` | `ingest` | 自动同源 `/ws` |
| 改界面、要假数据垫场 | `python backend/api.py`（默认 mock） | 先 `mock`，**收到真实帧即变 `ingest`**（mock 自动停） | 自动同源 `/ws` |

**唯一的红线**：任何要写进报告的截图/数字，页面上必须是 `source=ingest`（A 线 ingest）——
`mock` 的数字一律不引用，它只是把界面撑起来用的（《05》铁律 1）。

`frontend/index.html` 的地址框默认值是 8765（给独立运行的 `websocket.py` 用）；由 `api.py` 托管页面时**不用手改** —— 页面按当前页面的 `location.host` 自动填成同源 `ws(s)://<host>/ws`，`--port 8897` 这类非默认端口也一样。

页面把三个容易混淆的概念分开显示：顶部连接标签只回答 WebSocket 是否连接；数据来源标签读取
`/api/status.source`，分别显示「离线 Mock / 服务 Mock / A 线 ingest / 来源未声明」；开发者工具里的
计算模式在 M3 前固定为「软件模式」。**WebSocket 是传输方式，不是数据来源**；独立运行
`backend/websocket.py`、无法读取同源 `/api/status` 时，页面会如实显示「来源未声明」。

## 页面布局与历史曲线

- 桌面采用左右分栏：左侧是视频、当前判断与紧凑质量门控；右侧是测量值。
- 每张指标卡保持「左侧大读数、右侧趋势」，心率与呼吸率优先展示；置信度以小字辅助呈现。
  **置信度不等于经标定的测量准确率**，前端不提供编造的准确率。
- 每项指标使用自己的数值量纲，灰线绘制历史，彩色末段与圆点突出最新值；头部姿态同时绘制
  偏航实线与俯仰虚线。原先混合量纲的汇总曲线改为卡内曲线。
- 历史指**本次连接接收到的最近 120 秒样本**，最多保留 120 个点，按契约 `ts` 定位；
  不持久保存跨次测量数据。切换数据源 / 重新连接 / 上游序号或时间回退时重置窗口，避免混源。
- 没有历史时只显示当前采样点；无人脸、断流、生命体征门控未通过或缺少样本时留空，
  不补零、不跨空档连线、不把历史值当当前值。停止接收后历史轴不继续制造采样点。
- 窄屏时页面上下堆叠，但卡片内部仍保持数值在左、曲线在右；导航可直接跳到测量区。

## 持续状态警示

- 疲劳风险状态持续**超过 10 秒**，或有效心率读数持续高于提示线超过该时长时，在整个网页上层靠顶部弹窗。
  疲劳和心率独立计时；两者都达到持续条件时合并提示，并提供暂停任务、休息和放松等辅助建议。
- 参数唯一来源是根 `config.yaml`：`alert_duration_seconds` 为用户指定的 10 秒；
  `hr_alert_high_bpm` 暂为 **100 bpm 的可调原型提示线，未标定且不是诊断标准**。
  `/api/status.thresholds` 下发参数；离线页面使用同值兜底。调整服务配置后需重启服务并刷新页面。
- 按契约 `ts` 的连续有效样本计时，恰好 10 秒不弹；下一条超过该时长的有效样本才触发。
  无人脸、低质量、不合法帧、断流、超过连接超时的采样空档、停止、完成、切换数据源或上游回退均重置。
  疲劳使用既有 `fatigue_risk`；心率另须通过生命体征质量门控，不改变后端算法或六态契约。
- 点击「我知道了」、按 Escape 或点击遮罩可确认；同一段持续异常不反复打扰。
  有效数据恢复后再次持续异常才重新提醒；恢复或停止时弹窗自动清除。
- 没有新增身份识别。提示针对本次连续有效数据流，不能确认更换了一个人；
  轮换被测者时请停止并重新连接。Mock 告警在弹窗内明确标注为演示。
- 离线演示可在开发者工具强制 `fatigue_risk` 并持续等待超过提示时长；六态每秒轮转不会触发持续告警。

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
- 图表继续使用原生 canvas；界面图标来自 Microsoft Fluent UI System Icons，随 `frontend/icons/LICENSE`
  保留 MIT 许可。MiSans 仅从项目方本机的 `frontend/font/` 加载；其官方许可禁止进一步分发字体软件，
  因此字体文件不入公开仓库，缺失时页面自动使用系统中文字体。

## 契约怎么保证不跑偏

这个前端**对每一帧都跑契约校验**（`VigiLensMock.validateFrame`，与 `backend/contract.py` 同规则）：

- 每帧合法 → 右上角"契约：✓ N"；
- 出现不合法帧 → 变红"✗ N"，并把**第一条错误原因**写进事件日志，坏数据不渲染。

这样 M2 接 A 线真数据时，"字段对不上"会**当场在界面上暴露**，而不是等到答辩发现某个卡片一直是空的。

跨语言一致性与接线一致性都可自动检查（在仓库根执行）：

```bash
node frontend/mock.js --selftest                                  # JS 侧：六态帧合法 + 5 个坏帧必须被抓
node frontend/preview.js                                          # 同源 bbox 的 contain/留白/缩放自检
node frontend/ui_state.js                                         # 无脸/断流/趋势留空/来源标签自检
node --test frontend/tests/trends.test.js                          # 各指标历史曲线的数值与空档语义
node --test frontend/tests/alerts.test.js                          # 持续提示时长、门控、恢复与确认语义
node frontend/mock.js --limit 6 > metrics/logs/_js_frames.jsonl
python metrics/scripts/check_frontend_contract.py metrics/logs/_js_frames.jsonl      # Python 侧再验一遍
python metrics/scripts/check_frontend_wiring.py                   # app.js 引用的 DOM id 是否都存在
```

浏览器级回归不依赖 npm 包；先启动 `api.py`，再用本机 Edge/Chrome 执行：

```bash
.venv\Scripts\python.exe backend\api.py --no-mock --port 8897
node metrics/scripts/check_b_line_browser.mjs --scenario normal
node metrics/scripts/check_b_line_browser.mjs --scenario noface
node metrics/scripts/check_b_line_browser.mjs --scenario disconnected
node metrics/scripts/check_b_line_browser.mjs --scenario normal --width 390 --height 844
node metrics/scripts/check_b_line_browser.mjs --scenario history   # 合成 Mock 120 秒历史、缺测恢复与最新点
node metrics/scripts/check_b_line_browser.mjs --scenario gated     # 低质量帧即使携带数值也不展示生命体征
node metrics/scripts/check_b_line_browser.mjs --source offline     # file:// 离线打开，不需要后端
node metrics/scripts/check_b_line_browser.mjs --scenario alert-fatigue  # 合成持续疲劳提示
node metrics/scripts/check_b_line_browser.mjs --scenario alert-heart    # 合成心率提示
node metrics/scripts/check_b_line_browser.mjs --scenario alert-combined --width 390 --height 900 # 合成组合提示与窄屏
# 另起 backend/websocket.py 后，验证独立 WS 不冒充数据来源：
node metrics/scripts/check_b_line_browser.mjs --source standalone --ws-url ws://127.0.0.1:8765
```

> `check_frontend_wiring.py` 抓的是最难自查的一类前端事故：`getElementById("charts")` 拼错成
> 不存在的 id 时，**页面不报错、某块区域永远空白**。

## 当前状态与欠账（B1~B8）

- [x] B1 FastAPI 三接口（`/api/status` `/api/metrics` `/api/events`）+ `/api/ingest`
- [x] B2 Mock 数据生成器（契约一致、状态自洽、可复现）
- [x] B3 WebSocket 每秒推送，断开有明确提示
- [x] B4 页面骨架：左侧同源视频 + 状态 / 紧凑门控，右侧指标；开发者控制和日志收在页面底部折叠区
- [x] B5 每项指标卡内的历史 / 最新值曲线（原生 canvas，独立量纲，保留本次连接最近 120 秒）
- [x] B6 事件日志 + 六态状态机展示，样式区分清晰
- [x] B7 软硬件模式切换**占位**（顶部"模式：软件模式"标签；M3 接 C 线后切"硬件模式"）
- [x] B8 2026-10-04 版浅色信息工作台截图与浏览器验收记录 →
      `metrics/evidence/2026-10-04_b8_light_ui_screenshots.md`；历史深色截图仅用于追溯当时版本。
      新旧证据均无真人数据，只使用 Mock / stub 合成数据
- [x] **BUG-014 已关闭**：连接状态、数据来源、计算模式分开展示；`/api/status.source` 明确区分
      服务 Mock 与 A 线 ingest，WebSocket 不再冒充数据来源；M3 前硬件模式不可选
- [x] **无人脸显示语义已收口**：`face.visible == 0` 时当前指标、门控和眨眼高亮清空（心率卡可显示带年龄的历史值），
      趋势写入空档而不是假 0；断流优先显示「无数据」，下一帧检测到人脸后自动恢复
- [x] 新增「**信号质量门控 · 能不能测**」面板：4 条门控条 + 总判定，不通过时列出未通过项
      —— 这是产品承诺"先判断能不能测"在界面上的落点
- [x] 新增**眨眼状态机指示**（`behavior.blink_state` 四态高亮）与**长闭眼次数卡**（`behavior.long_close_count`）
- [x] 当前心率/呼吸测量使用**门控三态**：有值 / 质量不足 / 质量过低；不把缺测写成0。
      心率卡的2026-11-02显示层更新允许缓存最近有效数字，必须标“历史值”及年龄，不能冒充当前测量。
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
- [x] 视频区接入**同源画面旁路**：A 线把 JPEG 与预览元数据推到 `/api/preview`，页面用
      `object-fit: contain` 显示源画面，不叠加人脸定位框。服务端只保留最新帧，GET 用 ETag/304；
      断流后页面隐藏过期画面。`file://` 明确显示离线 Mock，不绘制模拟人脸框。
- [ ] 心率/呼吸趋势在 M2 后的真实曲线形态复核（现在心率多数为 `null`，符合门控承诺）
- [x] **门控阈值改为后端下发**（原本是 `app.js` 里 `THRESHOLDS` 的一份副本，A 线一标定就会
      无声漂移）：`api.py` 的 `/api/status` 带回 `thresholds`（值取自 `config.yaml`，键名逐字一致），
      前端优先用它；**离线兜底**才用 `app.js` 的内置副本（双击 `index.html` 走这条）。
      随阈值一起下发的还有 `ws_disconnect_timeout_s` —— 客户端看门狗按 `服务端值 + 1 s` 计算，
      保证它始终比服务端晚触发。
- [x] **判定证据链（`_triggers`）旁路已接通。** `api.py` 的 `/api/ingest` 接受与 `frame`
      平级的可选字段 `triggers`，`/api/status` 回传 `{frame_id, items}`；前端在 WebSocket 模式下
      按 1 Hz 取用，并校验 `frame_id`、在断流时清空过期链条。A 线已于 2026-09-28 在
      `backend/publish.py` 完成发送并补测试，过程见
      [`docs/08_B线给A线的接口请求.md`](../docs/README.md)。

## 三组拍摄播放（2026-11-02）

双击 [`recording.html`](recording.html) 即可打开独立拍摄入口；服务托管时访问
`/recording.html`。也可直接用 `index.html?recording=rest`、`?recording=exercise`、
`?recording=fatigue` 选组，`?recording=all` 播放完整一轮。

- 默认静息 → 短时运动后 → 疲劳，每组 15 秒，完整一轮约 45 秒；可改为 15～300 秒，单组选播或勾选循环。
- 三组选择、时长、循环、暂停与重播默认收在右上角「播放设置」小按钮中；点空白处或 Escape 收起，
  展开/收起不重启播放。相机按钮移到画面标题栏，开发者工具也从播放设置打开。
- 桌面拍摄保持左画面、右指标：摄像头按源画面宽高比居中显示，默认 4:3，开启相机或收到预览后自动适配，
  不撑成横向长条；已移除「当前判断」和「人物实拍」区域。测量可信度压缩为画面下方的窄条，
  右侧保留全部 13 张非呼吸率指标卡及趋势。
  宽度至少 1100、高度至少 600 的浏览器视口使用整屏布局；更小窗口保留正常滚动以保证可读性。
- 移除顶部「测量值与趋势」导航按钮；底部眨眼状态机状态条和说明不再显示，指标卡中的眨眼数据保留。
- 拍摄模式不显示呼吸率卡片和相关文案，也不生成呼吸率预设；契约必需的 `rr_per_min` / `rr_conf` 保持 `null`。
- 空格暂停/继续，数字 1/2/3 选组，R 重播；弹窗出现时先确认提示。
  一轮结束后保留最后数值和曲线，不显示「本轮播放结束」，点击「重新播放」再来一轮。
- 曲线预设使用独立种子的不规则漂移和非等间隔事件，不再共用周期正弦；同一段重播仍可复现。
  静息与疲劳组心率围绕各自预设基线小幅上下变化，运动后组在小幅变化中整体回落。
  曲线和读数使用同一份契约帧，不另外生成一条装饰曲线；计数保持单调阶梯。
- 疲劳组播放时包含一次完整哈欠：嘴部 MAR 张开、短暂保持、回落，闭合后 `yawn_count` 仅增加一次；
  持续疲劳提示仍在超过 10 秒后触发。上述都是界面拍摄预设，不是生理实验或真人测量。
- 每组准备 119 秒合成历史，立即呈现曲线；历史不计入持续告警，告警从当前组第一条播放样本开始计时。
  所有样本仍通过既有九字段契约校验，切组清空历史并重置告警，暂停期间不推进样本或告警。
- 「打开本机画面」只用于取景，需用户点击授权；不录音、不上传，本机画面与预设指标独立。
  关闭画面、离开页面或切回普通数据源时停止相机。
- 按用户要求，拍摄页没有额外的仿真水印。**数据是确定性合成演示预设，不是相机/板卡的测量结果，
  不能作为算法准确性、真人心率或硬件性能证据**。预设集中在 `recording.js`，没有修改真实测量算法或门控。
- 已同步 GitHub `origin/main` 的 `47ab722` 界面告警更新（`app.js`、`index.html`、`style.css`、
  `alerts.js` 与告警测试），保留本分支更完整的 `mock.js` 契约和 `preview.js` 回归。

验证命令：`node --test frontend/tests/*.test.js`；
`node metrics/scripts/check_recording_browser.mjs`（本机 Edge/Chrome，虚拟相机，输出在 `metrics/logs/`）。
2026-11-02 布局回归覆盖 1920×1080、1536×864、1440×900、1366×768、1366×650、1280×720 的浏览器视口；
检查无需滚动、13 张指标卡无溢出、相机保持源比例、可信度窄条与折叠控制；
另验哈欠全过程、心率曲线末值与数字一致、静默结束，截图与尺寸记录写入 `metrics/logs/recording_*`。

## 依赖说明

前端运行时**零网络依赖**，脚本不依赖框架或 CDN；本地视觉资产见上方文件表。
后端服务依赖见根目录 `requirements.txt`；未安装时 `websocket.py` / `api.py`
会打印可操作提示而不是抛 traceback，且**不影响双击 index.html 看界面**。

---

*本目录由 B 线维护；契约字段有异议时先提 `docs/interface.md` 变更，不要在 `mock.js` 里私自加字段。*
