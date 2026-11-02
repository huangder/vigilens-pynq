# PC 等价闭环证据（2026-10-27）

> **范围：PC 模拟，不是上板。** 本文记录“同一份 clip → 模拟 PL 运动量 →
> `quality.motion_score` → 契约 JSON → B 线 API”的本机真实运行结果。
> 板端 phase 10 **仍未上板**：`xsdb connect; targets` 无 target。

## 1. 环境

- Windows，仓库 `D:\Desktop\AMD`，分支 `feat/age-fatigue-db`
- `.venv\Scripts\python.exe`：numpy 1.26.4、cv2 4.11.0、mediapipe 可用
- 测试日期：2026-10-27

## 2. 命令

```powershell
$py = ".\.venv\Scripts\python.exe"
$base = Join-Path $env:TEMP "vt_evidence_manual"
& $py -m pytest backend/tests/test_pc_phase10.py backend/tests/test_video_link.py -q
& $py board/video_link/make_clip.py --test-pattern --tier vga --frames 3 --out "$base\clip"
& $py board/video_link/run_pc_sim.py --clip "$base\clip" --out "$base\run1" --pipeline --max-frames 3
& $py board/video_link/run_pc_sim.py --clip "$base\clip" --out "$base\run2" --pipeline --max-frames 3
Get-FileHash "$base\run1\last.json" -Algorithm SHA256
Get-FileHash "$base\run2\last.json" -Algorithm SHA256
```

网页推送另起一个进程：

```powershell
& $py backend/api.py --no-mock --host 127.0.0.1 --port 8124
& $py board/video_link/run_pc_sim.py --clip "$base\clip" --out "$base\run_api" --pipeline --max-frames 3 --post http://127.0.0.1:8124/api/ingest
Invoke-RestMethod http://127.0.0.1:8124/api/status
```

## 3. 真实结果

### 3.1 单测

```text
14 passed in 0.63s
```

（6 项 PC 模拟 + 8 项视频转运；本轮实测。）

### 3.2 clip

```text
3 帧，640×480 = 2764800 B（2.76 MB）
sha256 e589dd6cc96f98132fcce62e6968fff5f755f4b5fbc8f44c81a5adeaaf36d5c2
```

### 3.3 两次回放确定性

```text
SHA1=52AA68B5BBB81A6BE0AC4F0DA2B5E7291EEC4874123355419A4B59F011AC525A
SHA2=52AA68B5BBB81A6BE0AC4F0DA2B5E7291EEC4874123355419A4B59F011AC525A
```

两次 `last.json` 逐字节相同（SHA256 相同）。

### 3.4 PL 覆盖与契约

```text
pl_motion = {"loaded_frames": 2, "applied_frames": 1, "source": "pl_sim"}
pc_sim    = {"mode": "pc_sim", "is_board_run": false, "vid_ok": 3,
             "vid_fails": 0, "digest_match": true,
             "quality_motion_source": "pl_sim"}
last      = frame_id=2, status=adjust_posture,
            quality={light_score: 0.7904, motion_score: 0.0625, overall: 0.6764}
landmark_source = mediapipe
contract.validate_frame(last) = []
```

`motion_score=0.0625` 来自 `motion_ratio_q16=1024`：

```text
1024 / 65536 × motion_gain(4.0) = 0.0625
```

第 1 帧 `valid=false`（`motion_quality` 的 `prev_buf` 上电未定义），因此
`loaded_frames=2`、`applied_frames=1`；这是刻意的，不是漏算。

### 3.5 推送到 B 线 API

```text
[api] 数据源：ingest（M2 集成）
推送：成功 2 帧 / 节流跳过 2 帧 → http://127.0.0.1:8124/api/ingest（HTTP 次数 2）

GET /api/status:
  ok=true, source=ingest, mock_active=false, published=2
  frame.frame_id=3, frame.status=done
  frame.quality={light_score: 0.7904, motion_score: 0.0625, overall: 0.6764}
```

注：`published=2` 是因为 3 帧测量 + 1 个 `done` 收尾帧按 1 Hz 节流推送；
`frame_id=3` 是收尾帧，`last.json` 仍是测量末帧 `frame_id=2`。

### 3.6 上板状态

```text
xsdb -quiet -eval "connect; targets"
```

只启动 `hw_server`，**没有 target**，退出码 0。phase 10 **未上板**。

## 4. 边界

- 这是 **PC 模拟**：不含 DMA、AXI-Stream、PL 时序、PS7、JTAG 带宽。
- `pc_sim` 只进 `summary.json`，**不进契约帧**；`interface.md` / `contract.py` / `frontend/mock.js` 未改。
- 测试图案不是摄像头画面；报告口径仍按 `docs/44` §8：
  “摄像头在主机侧采集，视频帧经调试链路送入开发板”，不得写成“板载相机”。
- 板端 phase 10 的 `mot_*` 回读代码已补，但**未上板验证**；板子连上后按 `docs/45` §7 的顺序跑。
