# data/ —— A 线数据（标准测试视频 / 标注 / 黄金结果）

> 依据《02》第三节 A 线"无硬件测试方式"与《04》M0 第 4 步。
> 核心思路：**用"回放视频"替代实时摄像头**，使 A 线全程不碰板卡、
> 结果可无限次重复、可复现。

## 目录

| 目录 | 内容 | 是否入库 |
|---|---|---|
| `raw/` | 4 段标准测试视频 | ❌ **默认不入库**（体积大；见下方说明） |
| `annotations/` | 人工标注（每个标签的起止帧/时间） | ✅ 入库 |
| `golden/` | 黄金结果（基准 CSV），**锁版本** | ✅ 入库 |

## 必须录的 4 段标准视频（M0 就要录，越早越好）

| 文件名（建议） | 内容 | 主要验证 |
|---|---|---|
| `still.mp4` | 静止，正常睁眼，均匀光照 | 基线；EAR/PERCLOS 应稳定、几乎不触发 |
| `blink.mp4` | 正常眨眼，约 15~20 次/分 | **眨眼计数与眨眼率**（A3） |
| `yawn.mp4` | 正常说话 + 至少 3 次明显打哈欠 | **打哈欠去抖**（A5）：说话不能被误计为哈欠 |
| `turn.mp4` | 转头、低头、用手遮挡、短暂出框 | **头姿与可见率**（A6）；应触发 `adjust_posture` |

**录制规范（三人一致，否则黄金结果没有可比性）：**

- 分辨率 640×480、**30 fps**，与契约 `docs/interface.md` 第 0 节一致（转码命令见下）；
  > ⚠️ 帧率沿革：**30（v1.0）→ 45（v1.1，2026-09-13）→ 30（🚧 v1.2 草案，2026-09-20，C 线发起）**。
  > C 线已按 30 Hz 重生成 FIR 系数与黄金参考，**当前有效录制规范是 30 fps**。
  > 按 45 fps 录的视频会被管线按 30 fps 解释，眨眼率、长闭眼、哈欠去抖全部失真。
  > 本草案按 `AGENTS.md` §4 **待 A/B 会签**（见 `docs/interface.md` 第 6 节 2026-09-20 行）。
- 正面、均匀光照，不要逆光；每段 **20~30 秒**（`window_seconds: 30` 需要窗口填满）；
- 每段视频**只做一件事**（不要把眨眼和打哈欠混在一段里），否则无法判断是哪一项出的错；
- 出镜者须**同意**收录（`README.md` 与 `LICENSE` 的附加声明已写明这一条）。

```bash
# 用 ffmpeg 统一转码（本机 ffmpeg 见 .tools/ffmpeg，放 PATH 后可直接用）
ffmpeg -i 原视频.mp4 -vf scale=640:480 -r 30 -pix_fmt yuv420p data/raw/blink.mp4
```

转码后**必须回读确认真实帧率**，别只信命令没报错：

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -of default=noprint_wrappers=1 data/raw/blink.mp4
# 期望：width=640  height=480  r_frame_rate=30/1
```

## 为什么默认不把视频入库

`.gitignore` 里有这一段（可自行注释掉取消忽略）：

```
data/raw/*.mp4  data/raw/*.avi  data/raw/*.mov  data/raw/*.mkv
```

理由三条，都很实际：

1. GitHub 单文件 **> 50 MB 会警告、> 100 MB 直接拒绝推送**，手机录的 30 秒 1080p 很容易超；
2. 视频是**二进制**，改一次仓库就永久胖一圈，三人协作会越来越慢；
3. 评审真正要的是**可复现**，而"复现"靠的是 `annotations/` + `golden/` + 生成脚本，
   不是原始视频文件本身。

需要随仓库分发时的正确做法：**GitHub Releases 附件**（或 Git LFS），
并在本节记录"视频在哪个 Release / 网盘、校验用的 sha256 是多少"。

## annotations/ 格式（建议）

每段视频一个 CSV，文件名与视频同名：

```csv
# video=blink.mp4 fps=45 duration_s=25.0 annotator=姓名 date=2026-mm-dd
# 说明：event 只允许 blink / long_close / yawn / turn / occluded
start_frame,end_frame,event,note
24,31,blink,正常眨眼
52,58,blink,
...
```

> 标注只标"明显可判"的事件。**不确定的不要硬标** —— 拿不准的标注会污染
> 误检调参的判断依据，比没有标注更糟。

## golden/ 黄金结果规则（《02》A10）

- 内容：`run_pipeline.py` 对 4 段视频跑出的指标 CSV，作为后续任何改动的回归基准；
- 命名：`golden/<视频名>_<config 版本或日期>.csv`，并在 `report/` 记账；
- **锁版本**：黄金结果一旦生成，除非三人一致同意（例如换了关键点库、改了阈值口径），
  否则任何代码改动都不允许让它的主要指标变化；
- 生成命令（示例）：
  ```bash
  python backend/run_pipeline.py --source data/raw/blink.mp4 \
      --csv data/golden/blink_$(date +%F).csv --json metrics/logs/last.json
  ```

> ⚠️ 当前 A 线的 EAR/MAR 来自 `StubLandmarker`（占位几何量，见 `backend/README.md`）。
> **在装上 mediapipe 并用真实视频重标定之前，不要生成"黄金结果"** ——
> 否则锁下的是占位数据的版本，毫无意义。
