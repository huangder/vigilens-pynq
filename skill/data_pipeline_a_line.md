# A 线 Skill：从"要一个帧源"到"一串能引用的数字"

> 适用线：A（算法/后端）。沉淀来源：2026-10-23 用本机内置相机把 `uvc:` 帧源、时间基准、
> 灰度档位三件事各做了一遍，并踩出 BUG-029 / BUG-038 两个"静默错"。
> **本文件只写验证过的东西**；没跑过的写在 `docs/37` 的待办里，不写进来。

## 1. 适用场景

- 要给管线接一个新帧源（USB 摄像头 / 内置相机 / 视频文件 / 合成 / MJPEG 旁路）；
- 要判"这一串数字能不能写进报告"；
- 要把一个"按帧数"的阈值改成"按时间"，或者反之；
- 要在 640×480 / 1280×720 / 1920×1080 之间换像素口径（灰度抽取比会变）。

## 2. 使用方法（按顺序，别跳）

### 第 1 步：先问"这帧的时间是谁的时间"

- `backend/capture.py:is_live_source(source)` 判实时源：数字序号 / `uvc:` / `mjpeg:` /
  `http(s)://` / `rtsp://` → 实时；`synthetic` / `demo` / 文件路径 → 离线。
- **实时源必须把采集时间戳带进 rPPG**：`run_pipeline.py` 对实时源调
  `rppg.push(..., ts=frame.ts)`，离线源传 `ts=None`（走 `fid_nominal`）。
- 理由（BUG-029）：用 `(fid_last - fid_first)/fps` 反推实际采样率，**帧号连续时它恒等于标称 fps**，
  于是"采样率对不对"这条护栏永远为真。13 fps 的源会被当成 30 fps，报出 166 bpm。
- 判据：`summary.rppg_time_base` 是 `source_ts` / `fid_nominal`；对不上时
  `rppg_fs_eff=null`、`rppg_reject="fs_mismatch"`。

### 第 2 步：朝向先探测，再统一

- `capture.py:choose_rotation(images, detect, min_hits=...)`，`min_hits = max(2, len(images)//4)`。
- 理由（BUG-038）：老规则"命中最多者胜、并列取 0"**没有最小命中数**，
  8 帧里 1 帧偶发误检就能把整段 300 帧转 180° —— 不报错、只是所有坐标都错。
- 判据：日志里那行 `方向 : ...（探测 8 帧，各角度检出帧数 {...}）` 要能读懂；
  非 0 角度的命中数不到 `min_hits` 时应显示"原样"。

### 第 3 步：阈值用时间量，不用帧数

- 形如"连续闭眼 ≥ X 才算闭眼"的阈值，一律用 **ms**（`config.yaml:min_close_ms: 100`）。
- 理由（`docs/37` R8）：旧口径 `min_close_frames=3` 在 30 fps 下是 100 ms，
  到 60 fps 只剩 50 ms —— **语义静默变严**，而且没有任何报错。
- 例外：只有"这件事本来就按帧定义"的才留帧数（`orientation_probe_frames`、
  `age_enroll_restart_frames`）。
- 兼容：旧键保留为别名，只在缺新键时按 `frames*1000/fps_nominal` 折算。

### 第 4 步：像素口径按档位，且必须与 C 线黄金逐字节对拍

| 档位 | 灰度抽取 | 灰度尺寸 | 来源 |
|---|---|---|---|
| 640×480（现行契约 v1.1） | 5 取 3 | 384×288 | `config.yaml:tier_gray_decim` |
| 1280×720（v1.5 草案） | 8 取 3 | 480×270 | `tier_720p60_gray_decim: [3, 8]` |
| 1920×1080（v1.5 草案） | 4 取 1 | 480×270 | `tier_1080p45_gray_decim: [1, 4]` |

- 用 `backend/quality.py:to_gray(image)`（省略 `decim` 时按尺寸取档），不要手写循环。
- 判据：`backend/tests/test_gray_tier.py` 把 `fpga/sim/data_motion_720p60/rgb_frames.bin`
  与 `data_motion_1080p45/rgb_frames.bin` 各 10 帧喂进去，与同目录 `gray.bin` **逐字节相等**。
- 灰度公式冻结为 `Y = (77R + 150G + 29B + 128) >> 8`，**不要换成 OpenCV 的 BT.601**（差 1 LSB）。

### 第 5 步：落盘前看三个字段

| 字段 | 该看到什么 | 看不到时 |
|---|---|---|
| `landmark_source` | `mediapipe` | `stub` ⇒ 这次运行的 EAR/MAR **不得作为算法结果引用** |
| `/api/status.source` | `ingest` | `mock` ⇒ 页面上的数字只是垫场（BUG-014 后真实帧一到会自动变 `ingest`） |
| `quality.overall` / `face.visible` | 与场景相符 | 大面积 0 ⇒ 先怀疑光照/朝向/ROI，不要"补一个数字上去" |

## 3. 失效条件（这套做法什么时候不成立）

- **标称 fps 与真实差 > `rppg_fs_tol`（默认 0.10）且没有时间戳** ⇒ rPPG 拒绝报数。
  这是设计，不是缺陷；想报数就先接上真实时间戳。
- **Windows + MSMF 的内置相机**：`CAP_MSMF` 不套用相机的旋转属性，送出来的图像可能天生是
  转过 90° 的（本机实测：原样 `roll≈72~84°`，旋转后 `roll≈-0.39°`）。此时
  **bbox 坐标空间 = 旋转后的图像**（例如 1280×720 的源旋转后 bbox 的 `y+h` 可以到 766）。
  旁路 JPEG 与 bbox 同源（都取 `frame.image`），所以不会错位 —— 但**写报告时要说明**。
- **高于 1080p 的口径没有验证过**；`decim_for_size()` 对未知尺寸退回 5 取 3，
  这时灰度尺寸不再是 480×270，与 FPGA 黄金参考**必然对不上**。
- **单进程顺序取帧**是当前假设；多线程/多进程各自开相机没有验证过。
- `uvc:` 在 Windows 下请求 `1280x720@60 MJPG` 会协商成 `1280×720@30`，且
  `CAP_PROP_FOURCC` 读回 `0x16`（MSMF 不暴露 fourcc）——**不能据此说相机没有 MJPG**。

## 4. 从哪些失败总结（每条都能在台账里查到）

| 编号 | 现象（当时看到的） | 根因 | 现在钉在哪 |
|---|---|---|---|
| BUG-038 | 日志 `{0: 0, 90: 0, 180: 1, 270: 0}` 就把整段转 180° | `choose_rotation` 无最小命中数 | `backend/tests/test_orientation.py` 17 项 |
| BUG-029 | 13 fps 的源报出 `166 bpm` | 时间基准取 `fid/fps`，护栏恒真 | `backend/tests/test_vital_timebase.py` 19 项 + `probe_rppg_timebase.py` |
| R8（未成 BUG） | 60 fps 下"3 帧闭眼"= 50 ms | 阈值按帧数写 | `config.yaml:min_close_ms` + `test_behavior_min_close_ms.py` 11 项 |
| BUG-032（B 线，教训通用） | 前端重构后探针 13/15 全红 | 探针断言的是**实现细节**（某个 id、某个 URL） | 改成锚定**意图**（过期判定存在、四件元素齐全） |
| BUG-037（C 线，教训通用） | S2MM 长度正好一帧时 IP 收尾写拿不到应答 | 链尾 DMA 收满即 halt，下游 TREADY 撤掉 | `load_overlay.py:GRAY_SINK_MARGIN=384` + 帧末软件停 |

**一句话**：这五条里有四条**都不会报错**，只是数字悄悄变成错的。所以判据要写成
"某个数必须等于什么"，而不是"程序没崩"。

## 5. 已验证效果（2026-10-23，本机 Python 3.12.13 / OpenCV 4.11.0）

- `pytest -q` → **239 passed**；`metrics/scripts/check_all.py` → 工具自检 19/19、
  回归 **PASS 34 / FAIL 0**（用时 65.2 s）。
- 本机内置相机（`Camera\Integrated Camera`，只有索引 0、只有 MSMF 后端）：
  请求 1280×720@60 → 协商 1280×720@30；10 s 抓 300 帧、到达 **29.98 fps**、
  `p50 31.2 ms / p95 62.5 ms`、丢帧 0。
- 灰度两档与 C 线黄金参考**逐字节 0 处不等**（各 10 帧 × 129,600 像素）。
- rPPG 时间基准：30.0 fps → 72.0 / 30.14 fps → **72.3** / 13 fps → **null（fs_mismatch）**。
- BUG-038 现场生效：6 s 那次 `{0: 0, 90: 1, 180: 0, 270: 0}` ⇒ 命中 1 < `min_hits=2` ⇒ 保持原样
  （旧代码会被这 1 帧翻掉）。

## 6. 变更记录

| 日期 | 谁 | 改了什么 |
|---|---|---|
| 2026-10-23 | AI 起草（待人类复核签字） | 初版：帧源时间基准 / 朝向探测 / 时间口径 / 档位灰度 / 三个落盘字段 |

> ⚠️ 按《05》约定，"大模型协作记录"（`report/llm_log/`）必须人类亲手写；
> 本文件是**技能沉淀**，不受那条约束，但"谁改谁签"仍然适用。
