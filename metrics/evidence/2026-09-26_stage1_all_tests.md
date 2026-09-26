# 2026-09-26 阶段一测试 —— 全量执行记录（真实运行输出）

> **执行者**：AI（在用户机器上代跑）· **执行时间**：2026-09-26 20:0x~20:2x
> **环境**：`D:\Desktop\AMD`，Python 3.12.13 @ `.venv`，Vitis HLS **2026.1**
> **判定基准**：`docs/15_功能测试契约.md`（v0.1 草案）· **问题台账**：`docs/16_测试问题台账.md`
> **口径**：本文所有数字都是**本次真实运行的输出**，未做任何修改；跑不了的项**明确标注"未执行"**。

---

## 0. 一页结论

| 范围 | 结果 |
|---|---|
| 软件基线（A/B 线、工具自检、语法） | ✅ **PASS 22 / FAIL 0**（43.6 s，退出码 0）；`pytest` **78 passed** |
| 素材转码（`data/raw/blink.mp4`） | ✅ **1920×1080 → 640×480 / 30 fps**，P4 缺项 **8 → 7** |
| 真实人脸管线（mediapipe） | ⚠️ **跑通，但 98.1% 的帧被判 `adjust_posture`** → 见 BUG-006/007 |
| 网页链路（B3） | ✅ 真实画面 + 指标推送 + `mediapipe` 全部成立 |
| 缺陷验收数据（4 条） | ⚠️ **全部复现**（详见 §5） |
| C 线 HLS：四个 IP 的 csim + csynth | ✅ **4/4 PASS，且与归档逐位一致** |
| OpenMV 工具链自检 | ✅ 9/9 · 10/10 · 18/18 · 28/28 |
| OpenMV 真机 / UVC / 上板 | ❌ **未执行**（相机未接入：0 个视频设备 / 0 个 COM 口；无 bitstream） |

---

## 1. A0 环境与回归基线

```powershell
cd D:\Desktop\AMD
. .\env.ps1
python metrics/scripts/check_all.py
python -m pytest -q
```

**真实输出**

```
[环境] 10/10 通过        （cv2 / numpy / mediapipe / yaml / fastapi / uvicorn / pytest / node 全 PASS）
[A 线] 1/1 通过          check_a_line_all（A~E 五段）
[B 线] 3/3 通过          阈值三处一致 / ws 断流兜底 / 旁路画面链路+契约未被污染（15 项）
[工具自检] 8/8 通过      含 board/openmv 的 4 个自检
[语法] 2/2 通过
回归结论：✅ 全部通过（PASS 22 / FAIL 0，用时 43.6s）        退出码=0

78 passed in 10.99s
```

> 就绪状态（INFO，不计入退出码）：`board/bitstream/` 为空；P4 素材缺失（见 §6）；Vitis HLS 工具链**已安装**。

---

## 2. 素材转码（`DAT-002` / `DAT-001`）

原始 `data/raw/blink.mp4` 实测为 **1920×1080 / 30.053 fps / 656 帧 / 62,212,691 B**，不符 `data/README.md` 的录制规范。

```powershell
$ff = "D:\Desktop\AMD\.tools\ffmpeg\bin\ffmpeg.exe"     # 本机 ffmpeg 不在 PATH
& $ff -y -i data\raw\blink.mp4 -vf scale=640:480 -r 30 -pix_fmt yuv420p data\raw\blink_640x480.mp4
& "D:\Desktop\AMD\.tools\ffmpeg\bin\ffprobe.exe" -v error -select_streams v:0 `
    -show_entries stream=width,height,r_frame_rate,nb_frames -of default=noprint_wrappers=1 data\raw\blink_640x480.mp4

# 就位（原片改名保留，不删）
Move-Item data\raw\blink.mp4 data\raw\blink_1080p_src.mp4
Move-Item data\raw\blink_640x480.mp4 data\raw\blink.mp4
```

**真实输出**（ffprobe 回读）

```
width=640
height=480
r_frame_rate=30/1
nb_frames=656
```

**结果**：`blink.mp4` 2,478,496 B（原 62,212,691 B，**缩到 4%**）；原片保存在 `blink_1080p_src.mp4`（两者都被 `.gitignore:49 data/raw/*.mp4` 覆盖，不会入库）。

**P4 就绪复检**：缺项 **8 → 7**（`blink.mp4 分辨率 1920×1080 ≠ 契约的 640×480` 这一条消失）。

---

## 3. 真实人脸管线（**本阶段核心**）

```powershell
python backend/run_pipeline.py --source data/raw/blink.mp4 --seconds 21 `
    --json metrics/logs/_face_last.json --jsonl metrics/logs/_face_stream.jsonl `
    --csv metrics/logs/_face_metrics.csv --summary metrics/logs/_face_summary.json --print-every 120
```

**真实输出（关键行原样）**

```
帧源   : file:blink.mp4  30.0fps  656 frames
[face_landmark] 使用 MediaPipe FaceMesh（478 点）。
  t=   4.0s f  119 adjust_posture  EAR=0.311 PERCLOS=0.008 Q=0.91 | 头部 yaw = 175.5° 超出 ±30°
  t=   8.0s f  239 adjust_posture  EAR=0.000 PERCLOS=0.017 Q=0.67 | 人脸可见率 0.00 低于下限 0.70
  t=  12.0s f  359 adjust_posture  EAR=0.302 PERCLOS=0.011 Q=0.84 | 头部 yaw = -177.9° 超出 ±30°
  t=  16.0s f  479 adjust_posture  EAR=0.314 PERCLOS=0.017 Q=0.88 | 头部 yaw = -179.8° 超出 ±30°
  t=  20.0s f  599 adjust_posture  EAR=0.325 PERCLOS=0.013 Q=0.92 | 头部 yaw = -176.7° 超出 ±30°

处理帧数  : 630（逻辑时长 21.0s，墙上耗时 11.60s）
关键点来源: mediapipe
状态分布  : adjust_posture=618  fatigue_risk=7  normal=4  unreliable=1
末帧质量  : overall 0.93 (light 0.86 / motion 0.01)
```

**`_face_summary.json` 的关键字段**

```json
"landmark_source": "mediapipe",
"throughput_fps": 54.3,
"status_counts": {"adjust_posture": 618, "normal": 4, "fatigue_risk": 7, "unreliable": 1},
"rppg_window_samples": 603,
"rppg_window_need": 900
```

### 3.1 这一步证明了两件事

| ✅ 成立 | ❌ 不成立 |
|---|---|
| **`landmark_source = mediapipe`**（真实 478 点，不是 stub） | **头姿判定不可用**：618/630 = **98.1%** 的帧被判"头部偏离过大"，而人是正对镜头的 → **BUG-006 / BUG-007** |
| 链路在 PC 上**实时能力充足**：640×480 下 **54.3 帧/秒**（> 契约 30 fps） | **心率必然为 null**：`rppg_window_samples 603 < need 900`（见 §3.2） |

### 3.2 新发现：录制规范与 rPPG 窗口**互相冲突**

- `config.yaml` 的 `window_seconds: 30` × 30 fps = **900** 个样本才填满 rPPG 滑窗；
- 而 `data/README.md` 规定每段 **20~30 秒** → 21 秒的视频只给 **603** 个样本；
- ⇒ **按现行规范录的素材，心率永远出不来**（不是算法问题，也不是门控问题）。
- 建议：录制规范改为 **≥ 35 秒**（留出至少 1 个窗口 + 余量），或把 `window_seconds` 与素材长度一起冻结。
  ⚠️ 改 `config.yaml` 要公告（三人共用）→ 已立卡片 **BUG-012**。

---

## 4. 网页链路验证（B3 · 判据四条）

```powershell
python metrics/scripts/run_demo.py --source data/raw/blink.mp4 --loop --port 8021 `
    --video-hz 25 --no-open --csv metrics/logs/_demo_face.csv --json metrics/logs/_demo_face_last.json
# 另开一个窗口查询：
Invoke-RestMethod http://127.0.0.1:8021/api/video_status
Invoke-RestMethod http://127.0.0.1:8021/api/status
```

**真实输出**

```
=== /api/video_status ===
{ "ok": true, "has_video": true, "age_s": 0.024, "stale_after_s": 1.5,
  "bytes": 52871, "frame_id": 1470 }

=== /api/status（关键字段）===
source=ingest  published=52
frame.status=adjust_posture   visible=1.0   bbox=250,157,168,184
pose: yaw=175.74  pitch=38.02  roll=179.72
quality.overall=0.9048        blink_count=0
```

**判据核对**

| # | 判据 | 结果 |
|---|---|---|
| 1 | 网页有**真实画面** | ✅ `has_video: true`、52,871 B 的 MJPEG 帧、`age_s 0.024` |
| 2 | 指标在动 | ✅ `published=52`，`/api/status` 有完整契约帧 |
| 3 | `landmark_source = mediapipe` | ✅（见 §3 的 summary） |
| 4 | `distinct_frames ≈ frames` | ⚠️ 本项**需用 `host_capture_test.py` 对相机测**；本次是视频文件源，不适用 |

**同时再次实时复现了缺陷**：`yaw=175.74 / roll=179.72`（正面脸）→ 网页上就是"头转开了"。

> 验证完成后已停止该后台进程（`--loop` 会无限跑）；要再看一次，重跑上面那条 `run_demo` 即可。

---

## 5. 缺陷验收数据（4 条，**全部复现**）

在**规范口径 640×480** 的素材上重测（判定基准见 `docs/15`）：

| 卡片 | 验收指标 | 期望（修好后） | **本次实测** |
|---|---|---|---|
| **BUG-009** 成因C | 丢脸率 | — | **27/656 = 4.1%**（连续段） |
| **BUG-006/007** | \|yaw\| ≈ 180° 的帧占比 | 0 | **507/629 = 80.6%** |
| **BUG-006/007** | 相邻帧 \|Δyaw\| 最大 / >90° 跳变次数 | 0 / 0 | **359.0° / 119 次** |
| **BUG-006/007** | `pitch` 是否逐帧翻转 | 否 | **是**（帧0~4：+41.17 / +40.54 / **−40.02** / +39.87 / +40.11） |
| **BUG-010** | 框高覆盖率 | ≥ 90% | **68%**（bbox `(247,156,166,183)`） |
| **BUG-011** | 眼睛落在 ROI 内的点数 | 0/12 | **7/12** |
| **BUG-011** | ROI 是否含额头 | True | **False** |

> 结论：**四条缺陷在规范素材上全部稳定复现**，其中 BUG-006/007/011 会直接毁掉测量输出（98.1% 判错）。

---

## 6. 采集侧（OpenMV）

```powershell
python board/openmv/vigilens_link.py --selftest        # RESULT: PASS  (9/9)
python board/openmv/host_capture_test.py --selftest    # RESULT: PASS  (10/10)
python board/openmv/raw_to_contract.py --selftest      # RESULT: PASS  (18/18)
python board/openmv/offline_check.py                   # RESULT: PASS (28/28)

python metrics/scripts/run_demo.py --list              # 枚举摄像头
python metrics/scripts/check_p4_readiness.py           # P4 标定素材就绪度
```

**真实输出**

```
（4 个自检全部 PASS，见上）

枚举摄像头（每个只读 1 帧；打不开的会跳过）...
  没找到任何可用摄像头。
  · OpenMV 走 USB-VCP 模式时**不会**出现——需先用 OpenMV IDE 刷 uvc.bin 固件；
  · 或者用视频文件：--source data/raw/xxx.mp4

[未就绪] P4 还缺以下 7 项：still.mp4 / still.csv / blink.csv / yawn.mp4 / yawn.csv / turn.mp4 / turn.csv
```

⇒ **未执行**：OpenMV 能力矩阵（L6/O4）、UVC 刷机与网页画面（B1–B3）、无损落盘（O5）。
原因：**相机未接入**（0 个视频设备、0 个 COM 口）。这三项需要人带着相机做，命令见 `docs/14` 与 `board/openmv/刷机与网页画面_操作单.md`。

---

## 7. C 线 HLS：四个 IP 的 csim + csynth（**本次新增，已 PASS**）

```powershell
cd D:\Desktop\AMD\fpga
foreach ($ip in 'fir_filter','roi_statistic','rgb2gray','motion_quality') {
  $env:HLS_IP = $ip
  cmd /c "call D:\Xilinx\2026.1\Vitis\settings64.bat >nul 2>&1 && vitis-run --mode hls --tcl run_hls.tcl"
}
```

> ⚠️ 受限沙箱会以 `couldn't create signal pipe, Win32 error 5` 失败（**不是代码问题**，
> 与 `AGENTS.md` §6.3 记载一致）；**用一次提权重试即可**。四个 IP 合计约 **6 分钟**。

**真实输出（与原归档逐项对照）**

| IP | 本次 csim | 本次 II | 本次 Fmax | 归档 csim | 归档 Fmax | 判定 |
|---|---|---|---|---|---|---|
| `fir_filter` | **8/8 + 17/17**（0 errors） | 1（Depth 9） | **146.97 MHz** | 8/8 + 16/16 | 146.97 MHz | ✅ 一致 ※ |
| `roi_statistic` | **28/28 + 45/45**（0 errors） | 1（Depth 2） | **138.99 MHz** | 28/28 + 45/45 | 138.99 MHz | ✅ 逐位一致 |
| `rgb2gray` v2 | **8/8 + 10/10**（0 errors） | 1（Depth 6） | **137.46 MHz** | 8/8 + 10/10 | 137.46 MHz | ✅ 逐位一致 |
| `motion_quality` v2 | **6/6 + 9/9**（0 errors） | 1（Depth 3） | **140.05 MHz** | 6/6 + 9/9 | 140.05 MHz | ✅ 逐位一致 |

※ `fir_filter` 的 `17/17` 与归档 `16/16` **不矛盾**：`fpga/sim/tb_fir_filter.cpp:543`~`546`
在 16 段之外还有一项**「跨段一致性」检查**（`分段(reset=1,0,0) == 一次调用`，通过后 `pass++`）
⇒ **17 = 16 段 + 1 项**。归档的 "16/16" 早于该检查加入。

**资源（`roi_statistic` 本次实测 `csynth.rpt`）**

```
|Total            |  BRAM_18K 0 | DSP 1 | FF 723 | LUT 1267 | URAM 0 |
（归档：1267 / 723 / 0 / 1 —— 逐位一致）
时序：Estimated 7.195 ns（Uncertainty 2.70 ns）→ Fmax 138.99 MHz
```

**这一步闭合了什么**

| 欠账 | 状态 |
|---|---|
| `AGENTS.md` §7.6「v1.2（45→30 Hz）的 csim/csynth **未重跑**」 | ⚠️ **部分闭合**：`fir_filter`（v1.2 改的正是它）@30 Hz 的 **csim + csynth 已重跑并 PASS** |
| `AGENTS.md` §6.3「受限沙箱跑不了 csim」 | ✅ **已澄清**：提权重试一次即可，四 IP 合计约 6 分钟（→ 卡片 DOC-002） |
| **cosim** | ❌ **仍未跑**（`HLS_EXEC=2`，且需**小尺寸向量**，见 `fpga/README.md`「跑 cosim」） |
| `export`（导出 IP） | ❌ 未跑 |

**产物**：`fpga/component_*/`（四个目录），已被 `.gitignore:28 fpga/component_*/` 覆盖 → **未污染仓库**。

---

## 8. 本次未执行的项（**不要写成"已通过"**）

| 项 | 为什么没做 | 谁来做 |
|---|---|---|
| OpenMV 能力矩阵（L6 / O4） | 相机未接入 | 有相机的人（≈30 min，`MATRIX_STAGE` 分段） |
| UVC 刷机 + 网页实时画面（O1–O3） | 同上 + 刷机与跑脚本互斥 | 同上 |
| 无损落盘 → 契约布局（O5） | 同上 + 需要 micro SD 卡 | 同上 |
| `distinct_frames ≈ frames` 判据 | 需要真实相机 | 同上 |
| **cosim**（四个 IP） | 需要小尺寸向量 + 更长时间 | C 线 |
| L8/L9/L10 上板 | **无 bitstream、板子从未上电** | C 线（先做 bitstream） |
| P4 标定（阈值标定） | 缺 7 项素材 | A 线 |
| `report/llm_log/` 协作记录 | **必须人类亲手写**（铁律 3） | 全员 |

---

## 9. 本次遗留的现场（**工作区状态**）

```
未跟踪的新增文档（均为本次或前几次产出，尚未提交）：
  board/openmv/刷机与网页画面_操作单.md
  docs/14_PC模拟与相机采集_测试方案.md
  docs/15_功能测试契约.md
  docs/16_测试问题台账.md
  metrics/logs/demo_clip.csv      ← 之前没用 `_` 前缀，会脏 git status
  metrics/logs/demo_face.csv      ← 同上
  metrics/logs/tmpm41s086v.tmp    ← 0 字节残留，未被 .gitignore 覆盖

本次产出的诊断/证据文件（都在 metrics/logs/，已被忽略）：
  _face_last.json / _face_stream.jsonl / _face_metrics.csv / _face_summary.json
  _demo_face.csv / _demo_face_last.json
  _face_*.json（无）
```

`data/raw/` 现状：`blink.mp4`（640×480，规范版）+ `blink_1080p_src.mp4`（原片备份）—— **两者都不入库**。

---

## 10. 诚实边界

- 本记录由 **AI 代跑并如实抄录**；**没有跑过任何硬件**（相机、Mizar 都不在本机）。
- 所有数字都是 **2026-09-26 本机真实输出**，未做任何修改；跑不了的项已在 §8 明确列出。
- **判定"是不是缺陷"以 `docs/15` 为准**；本文件只是**执行记录**，不替任何人拍板。
- 本文里的 `17/17`、`98.1%`、`68%`、`7/12`、`603/900` 等数字**都可以用 §1~§7 里的命令复现**；
  若你跑出不同结果，**先报告事实，不要改期望值去凑绿**。
