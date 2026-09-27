# 2026-09-27 OpenMV 链路 → B 线同源预览：打通记录与五项问题证据

> **性质**：本次测试的**原始证据归档**（原样转录真实运行输出，未做任何数字修饰）。
> **配套卡片**：`docs/16` 的 **BUG-029 / BUG-030 / BUG-031 / BUG-032 / DOC-005**，
> 以及追加到 **BUG-007** 的真机复现证据。
> **执行人**：AI 协作会话（人类在机，全程可复现）；**未代写** `report/llm_log/`（铁律 3）。
> **可复现脚本**：`metrics/scripts/probe_rppg_timebase.py`、`probe_pose_solver.py`、
> `probe_bbox_jitter.py`、`probe_mjpeg_stream.py`（后两个需要自行准备 `data/raw/*.mp4`，
> 或让 `api.py` + 相机桥跑起来）。
> **关于路径**：文中引用的 `metrics/logs/*` 都是**本机运行产物（不入库）**，
> 按各节给的命令可以原样重跑出来；需要长期留存的证据请另存到本目录。
> **分支**：`b-line/preview-openmv` = `origin/c-line/fs30`（36 提交，含 OpenMV 链路）
> 合并 `origin/main`（`86384aa`，B 线同源预览）。

---

## 0. 被合并的两个分叉（背景）

`git merge-base origin/main origin/c-line/fs30` = `ad9ad54`，之后两边各自前进：

| 分叉 | 独有提交 | 独有内容 |
|---|---|---|
| `origin/main` | **2** 个 | `d3a788f`（docs/09）、`86384aa`（**B 线同源预览 + 仪表盘重排**） |
| `origin/c-line/fs30` | **36** 个 | OpenMV 采集/推流、`/api/frame`+`/video.mjpg` 旁路、`check_all.py`、`run_demo.py`、docs/16/17/18 等 |

⇒ 网页要"相机画面 + 真实数字同屏"，必须两边合体：`backend/api.py` 同时保留
`/api/preview`（B）与 `/api/frame`+`/api/video_status`+`/video.mjpg`（C）；
`frontend/` 取 B 线最新；契约、`config.yaml`、`mock.js` 一字未动。

---

## 1. 链路打通的证据（真机 OpenMV Cam H7 R2 / ON Semi MT9M114 @ COM10，usb_gray / GRAYSCALE QQVGA）

命令（一条）：`powershell -NoProfile -ExecutionPolicy Bypass -File metrics\scripts\omv_camera_demo.ps1 -WithMetrics -OpenBrowser`

内部链路：相机 → 串口 → `omv_stream_bridge.ps1` → `POST /api/frame` → `GET /video.mjpg`
→ `run_pipeline.py`（MediaPipe FaceMesh）→ `POST /api/ingest`（数字）**+** `POST /api/preview`（同帧 JPEG+bbox）→ 网页。

```powershell
# GET /api/video_status
{"ok":true,"has_video":true,"age_s":0.169,"stale_after_s":1.5,"bytes":4754,"frame_id":null,"ts":null,"reason":null}

# GET /api/preview/latest（HTTP 200，4528 B）
x-vigilens-frame-id = 453
x-vigilens-source-width = 160
x-vigilens-source-height = 120
x-vigilens-bbox-x = 62
x-vigilens-bbox-y = 28
x-vigilens-bbox-w = 47
x-vigilens-bbox-h = 38
x-vigilens-face-visible = 1.0
x-vigilens-status = fatigue_risk

# GET /api/status（同一时刻的契约帧，节选）
frame_id=448  face.visible=1.0  face.bbox=[62,28,47,39]
behavior: ear_left=0.2495 ear_right=0.2456 blink_state=OPEN blink_count=1
          blink_rate_per_min=2.0 perclos=0.0462 mar=0.0257
quality : light_score=0.9446 motion_score=0.0225 overall=0.9656
status  : fatigue_risk   reason: 触发：眨眼率 2.0/min < 10
vital   : hr_bpm=null hr_conf=null rr_per_min=null rr_conf=null

# GET /  → 200，16174 B，返回 B 线最新 index.html（含「同源视频 / 人脸定位」面板）且引用 preview.js
# GET /preview.js → 200，4892 B
```

**只读对照跑**（不推任何东西，所以不干扰页面；`--limit 1000` 正常退出、写出 summary）：

```powershell
.\.venv\Scripts\python.exe backend/run_pipeline.py --source http://127.0.0.1:8031/video.mjpg `
    --wall-clock --limit 1000 --print-every 250 `
    --summary metrics/logs/openmv_observer_summary.json
```

```json
"landmark_source": "mediapipe",
"frames_processed": 1000,
"logical_fps": 30.0,
"wall_elapsed_s": 126.07,
"throughput_fps": 7.9,
"status_counts": { "normal": 314, "adjust_posture": 205, "unreliable": 4, "fatigue_risk": 477 },
"final": { "blink_count": 7, "blink_rate_per_min": 6.0, "perclos": 0.1423,
           "yawn_count": 1, "long_close_count": 2, "quality_overall": 0.953,
           "status": "fatigue_risk" },
"rppg_window_samples": 900,
"rppg_window_need": 900
```

⇒ **行为类指标（EAR / 眨眼 / PERCLOS / 哈欠 / 长闭眼 / 质量）在真机上确实出数且可用**；
`hr_bpm` 仍为 `null`（原因见 §2 BUG-029）。

---

## 2. BUG-029：rPPG 的时间基准（30 Hz）与链路真实速率（7.9 fps）不一致

### 2.1 链路真实速率（`metrics/scripts/probe_mjpeg_stream.py`）

```
URL           : http://127.0.0.1:8031/video.mjpg
采样时长      : 7.81 s
收到 JPEG 帧数: 63  → 8.07 fps（含重复）
其中不重复    : 29  → 3.72 fps（真实新画面）
重复帧        : 34（54.0%）
单帧字节      : min 2133 / max 2202 / 均值 2177
到达间隔      : min 0.0 / max 0.505 s（成串到达 = 间隔长短交替）
间隔分布      : 0.0, 0.0, 0.505, 0.0, 0.0, 0.0, 0.503, 0.0, 0.0, 0.0, 0.378, ...
```

### 2.2 受控实验（`metrics/scripts/probe_rppg_timebase.py`）

同一个 **1.2 Hz（=72 bpm）正弦**、`frame_id` 一律连续递增（与 `run_pipeline` 一致），
**只改"这些样本实际是按多快采到的"**，喂给同一个 `GreenRppg(fps=30, window_seconds=30)`：

```
输入信号：1.2 Hz 正弦 = 72 bpm；窗口 need = 30s x 30fps = 900 样本
真实采样率      窗口真实时长      代码以为      报告 hr_bpm    hr_conf
30.0          30.0          30.0      72.0          1.0
13.0          69.2          30.0      166.0         0.9998
8.0           112.5         30.0      None          None
3.7           243.2         30.0      146.0         0.6462
```

⇒【已验证】在非 30 fps 的采集链路上，`vital.hr_bpm` **要么不出数、要么"自信地报错数"**。

### 2.3 与 BUG-012 的区别

BUG-012 说的是"**录得太短**（21 s < 30 s 窗口）→ 窗都没满"；
本条是"**窗满了、质量也够，仍然不出数，因为采样率口径对不上**"。
两条同时存在，修复方向不同，不要合并处理。

---

## 3. BUG-007 真机复现证据（姿态 solvePnP 落到镜像解）

同一条链路上**现场复现**（不是回放视频）：

```
20:29:01  f=2327  face=1.0  yaw=-5.5   roll=-167.8  status=adjust_posture
20:28:41  f=2171  face=1.0  yaw= 1.4   roll=-174.1  status=adjust_posture
20:26:21  f=1060  face=1.0  yaw=-2.2   roll=-174.2  status=normal
（期间 /api/status reason：头部 yaw = -177.5° 超出 ±30°）
status_counts（1000 帧）：adjust_posture=205（20.5%）
```

同一帧、同一组 FaceMesh 点、同一 3D 模型，只换求解标志（`metrics/scripts/probe_pose_solver.py`，
输入 `data/raw/blink.mp4` 第 60 帧，该帧**正脸正立**、人工确认）：

```
求解标志                       代码式 p/y/r                   classic x/y/z
SOLVEPNP_ITERATIVE（当前代码）    (38.82, 174.65, 178.99)     (178.99, 38.82, -174.65)
SOLVEPNP_EPNP               (-45.7, -6.2, -168.11)      (-168.11, -45.7, 6.2)
SOLVEPNP_SQPNP              (-36.71, -5.99, -167.79)    (-167.79, -36.71, 5.99)
```

⇒ 换 `EPNP/SQPNP` 后 yaw 由 **174.65° → −5.99°**（镜像解被消除）——
与 BUG-007 的建议一致，本条只是补上**真机链路**的证据。
另注意：`roll` 长期 ±167~179°（`yaw` 正常时它也异常），且 `decision.py` **只校验 yaw/pitch**。

---

## 4. BUG-030：判定证据链（`triggers`）从未被推送 → 网页面板恒为空

```powershell
$s = Invoke-RestMethod 'http://127.0.0.1:8031/api/status'
$s.triggers        # → 空（null）
```

代码依据（【已验证】读源码）：`backend/run_pipeline.py:113` 把证据链挂在 `frame["_triggers"]`，
而 `backend/publish.py:158` 的 `_clean()` 会剔除所有 `_` 开头字段，`post()` 只发
`payload = {"frame": clean}`（`backend/publish.py:166`）—— 全程**没有任何地方**把 triggers 作为
`/api/ingest` 的兄弟字段发出去（尽管 `backend/api.py:495-501` 早就支持 `payload["triggers"]`）。

网页表现：`frontend/app.js` 取不到 `_triggers`，也拿不到 `/api/status` 的 `triggers`，
于是「状态与可解释建议」里的证据链永远显示
「（暂无判定证据链：需 A 线推送时带上 triggers，见 docs/08_B线给A线的接口请求.md）」。

---

## 5. BUG-031：相机 VCP 掉线后看门狗重启救不回，且报错信息误导

`omv_camera_demo.ps1 -WithMetrics -TotalSeconds 1200` 一次完整运行的输出（原样）：

```
[demo] cycle 1: streaming (first fresh frame seen)
[demo] cycle 2: camera stream stalled (age=8.8s) -> restarting
[demo] cycle 3: bridge process exited -> restarting
[demo] cycle 4: bridge process exited -> restarting
... （一直到 cycle 64，全部同一句）
[demo] done after 64 cycles
```

各轮 `%TEMP%\omv_cycle_N.log` 的真因（cycle 3 与 cycle 64 内容相同）：

```
PORT_ERROR: Exception calling "Open" with "0" argument(s): "The port 'COM10' does not exist."
PORT_ERROR: Exception calling "Open" with "0" argument(s): "The port 'COM10' does not exist."
```

时间线（日志写入时间）：cycle 1 正常跑到 **20:34:03**（≈11.5 分钟），cycle 2 于 20:34:18 判停流，
cycle 3~64 为 20:38:44 → 20:43:51，随后到达 1200 s 上限收工。
收工后实测：

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()     # → （空：一个 COM 口都没有）
```

⇒ 相机在运行中途**从 USB 上消失**（`does not exist`），不是脚本问题；看门狗把"串口不存在"
和"流卡住"混为一句话（62 轮 `bridge process exited`），真因埋在每轮的 cycle 日志里。
【不确定】掉线的物理原因（线材 / 供电 / USB 重枚举 / 板子复位）：`Get-WinEvent` 未取到相关事件、
`Get-PnpDevice` 返回"拒绝访问"，本机无法进一步归因。

---

## 6. BUG-032：`check_video_bypass.py` 与 B 线新前端不一致 → 回归常红

```powershell
.\.venv\Scripts\python.exe metrics/scripts/check_video_bypass.py
# … T0~T12 全 PASS …
[FAIL] T13 网页 HTML 含旁路画面元素 #videostream 且引用 /video.mjpg
       HTTP 200 16174 B；含 id=False 含 video.mjpg=False
[FAIL] T14 app.js 会用 /api/video_status 的过期判定（不是'收到过就显示'）
       HTTP 200 40922 B
有失败：13/15 项通过

.\.venv\Scripts\python.exe metrics/scripts/check_all.py
回归结论：❌ 有 1 项失败（PASS 23 / FAIL 1，用时 51.6s）
```

原因：B 线 `86384aa` 把网页画面从"旁路 MJPEG"换成了"同源预览"（`/api/preview` + ETag +
`frontend/preview.js` 的纯函数坐标换算），T13/T14 仍在找旧前端的
`#videostream` / `/video.mjpg` / `/api/video_status`。
**API 侧的 `/api/frame`、`/video.mjpg`、`/api/video_status` 仍然存在且 T0~T12 全过**，
坏掉的只是"探针假定前端长什么样"。

---

## 7. B 线新页面的画面/框行为实测（回答"方框追踪效果实现到什么程度"）

`metrics/scripts/probe_bbox_jitter.py`（`data/raw/blink.mp4` 前 150 帧，同一检测器、同一 bbox 定义）：

| 输入分辨率 | 丢框帧 | 中心位移 均/p95/最大（x · y，源像素） | 宽高变化 均/p95/最大（w · h） | 折算 480 px 显示宽度的抖动 p95 |
|---|---|---|---|---|
| 640×480 | 0 | 0.79/2.00/3.50 · 0.37/1.00/1.50 | 2.26/6.00/10.00 · 1.28/3.00/5.00 | **1.5 px** |
| 320×240 | 0 | 0.39/1.00/1.50 · 0.21/0.50/1.00 | 0.79/2.00/7.00 · 0.38/1.00/2.00 | **1.5 px** |
| 160×120（= 相机现状） | 0 | **0.15/0.50/1.00** · 0.10/0.50/1.00 | 0.41/1.60/4.00 · 0.21/1.00/2.00 | **1.5 px** |

⇒ QQVGA **没有让框变跳**；`bbox` 抖动折算到显示尺寸后三档分辨率相同。
**但框的更新率是传输瓶颈**：预览实际只有 3.7~8 fps，快速转头会有几十像素的滞后。
另：MediaPipe 在 160×120 上仍能检出人脸（公开测试图 `grace_hopper.jpg` 缩到 160×120：
`visible=1.0`、`bbox=(59,32,47,28)`、EAR 0.175/0.136；120×90 也仍能检出）。

**同帧一致性**（设计层面，【已验证】读码 + 单测）：`run_pipeline.py` 用**同一次迭代**的
`clean["face"]["bbox"]` 与 `frame.image.shape` 一起 POST `/api/preview`，
前端从响应头读回该帧的 bbox 与源尺寸——画面与框不可能"错帧"。
**仍未实现**的三点（`docs/16` 尚未立卡，待人类决定是否立）：① 单帧丢检/丢推时框直接消失
（无 200~300 ms 保持，会闪）；② 无平滑/预测（实测抖动小，优先级低）；③
`bbox` 的几何定义是"眼外角+嘴角+15% padding"（= 已有的 **BUG-010**），不是整脸框。

---

## 8. 本次跑过的离线回归（合并分支 `b-line/preview-openmv`）

```powershell
.\.venv\Scripts\python.exe -m pytest -q                        # 98 passed in 16.14s（B 线 main 上是 88）
node frontend/preview.js                                       # checked 5, ok true
node frontend/mock.js --selftest                               # checked 11, ok true
.\.venv\Scripts\python.exe metrics/scripts/check_frontend_wiring.py   # 通过
.\.venv\Scripts\python.exe metrics/scripts/check_all.py        # PASS 23 / FAIL 1（唯一红项 = BUG-032）
.\.venv\Scripts\python.exe metrics/scripts/omv_stream_bridge.ps1 -SelfTest   # PASS（离线，无相机）
```

---

## 9. 本次没有做的事（诚实边界）

- **没有**跑 csim/csynth/cosim（本机受限沙箱跑不了 HLS），**上板结论一个都没有**；
- **没有**改动 `docs/interface.md`、`config.yaml`、`backend/contract.py`、`frontend/mock.js`
  （契约四件套一字未动，`CONTRACT_VERSION` 仍 `v1.1`）；
- **没有**替任何人类写 `report/llm_log/`（铁律 3）；
- **没有**对 BUG-029/030 动手修（分别属 A 线口径与 A 线文件），只登记 + 给方向；
- `metrics/logs/` 下的中间产物（探针输出、截帧、summary）**不入库**，可复现脚本已挪到
  `metrics/scripts/probe_*.py`。
