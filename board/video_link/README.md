# board/video_link —— 主机侧视频转运 / PC 模拟

> **现状（2026-10-27）**：主机侧代码与 PC 等价闭环已落地并离线验证；**板端 phase 10 仍未上板**。
> 设计理由、寄存器、传输预算见 [`docs/44`](../../docs/README.md)；
> 无板卡时的替代验证见 [`docs/45`](../../docs/README.md)。

## 它解决什么

板载 USB 相机因 ULPI PHY 不应答而退出关键路径（代码保留）。现行路径是：

```text
主机摄像头/视频文件
  → backend/capture.py 采集
  → 本目录打成 RGB888 frames.bin + manifest.json（sha256）
  → board/s4_bench/s4_run.py 用 xsdb `mwr -bin -file` 写 DDR 0x12000000
  → 裸机 phase 10 逐帧跑 ROI → rgb2gray → motion_quality
  → 回传逐帧灰度 sum + FNV-1a，与本目录的 clip_digests() 对拍
```

**视频是旁路，不进契约帧**。契约仍只走 `POST /api/ingest` 的指标 JSON。

## 文件

| 文件 | 作用 |
|---|---|
| `video_clip.py` | clip 格式、`write_clip` / `verify_clip` / `iter_clip_frames`、灰度式、FNV-1a、传输估算 |
| `make_clip.py` | 主机摄像头/视频/测试图案 → `frames.bin` + `manifest.json` |
| `run_video.py` | 上板入口：校验 clip，调 `board/s4_bench/s4_run.py video`；`--dry-run` 只生成 Tcl |
| `pc_phase10.py` | **PC 模拟** phase 10 的灰度/sum/FNV/帧差运动量（不碰 DMA/PL） |
| `run_pc_sim.py` | PC 模拟 CLI；`--pipeline` 继续跑 A 线契约 JSON，并把 `pc_phase10.json` 的 `motion_ratio` 经 `--pl-motion-json` 覆盖进 `quality.motion_score`；`--post` 可推 B 线 |
| `../s4_bench/build.ps1` | 编译两档裸机固件（产物不入库） |

## 快速命令

```powershell
# 0) 建议先载入项目环境（切到仓库根、.venv 排到 PATH 最前）
. .\env.ps1

# 1) 主机摄像头 → clip（输出默认在 metrics/logs/video_clips/，不入库）
python board/video_link/make_clip.py --source 0 --tier 720p --frames 10

# 2) 只生成上板 Tcl，不碰板子
python board/video_link/run_video.py --clip metrics/logs/video_clips/720p_0 --dry-run

# 3) 接板后真跑 phase 10
python board/video_link/run_video.py --clip metrics/logs/video_clips/720p_0

# 4) 没有板卡：PC 模拟 + A 线契约 JSON
python board/video_link/run_pc_sim.py --clip metrics/logs/video_clips/720p_0 --pipeline

# 5) PC 模拟 + 推 B 线（先起 backend/api.py）
python board/video_link/run_pc_sim.py --clip metrics/logs/video_clips/720p_0 `
    --pipeline --post http://127.0.0.1:8000/api/ingest
```

没有真实视频时，只能用 `make_clip.py --test-pattern` 做链路自检；
**测试图案不是摄像头画面，不能当实验素材**。

## 判据

- 上板：串口日志出现 `vid f=… sum=… hash=0x… fid=…`，且 `vid_fails=0`、`done_video`；
- PC 模拟：`pc_phase10.json` 里 `mode=pc_sim`、`is_board_run=false`、`vid_fails=0`、`digest_match=true`。

## 本次实测

```text
pytest backend/tests/test_pc_phase10.py backend/tests/test_video_link.py -q
→ 14 passed in 0.63s

make_clip.py --test-pattern --tier vga --frames 3
→ 2,764,800 B, sha256 e589dd6c…d5c2

run_pc_sim.py --clip … --max-frames 3
→ frames=3 vid_ok=3 vid_fails=0 digest_match=True
```

> 以上是本机真实输出；**不是上板结论**。`xsdb connect; targets` 在 2026-10-26 没有列出 target，
> 所以 phase 10 仍未实跑。

## 边界

- PC 模拟**不模拟** DMA、AXI-Stream、PL 时序、JTAG 带宽，也不产出 `res.bin`；
- phase 10 只回传 `sum`/FNV，**还没有**把 `motion_ratio` 读回写进契约；
- PC 模拟算出的 `motion_ratio` 只作对拍证据，**当前不注入** A 线 `quality`，契约数字仍由 `backend/quality.py` 的参考实现产生；
- 10 帧 720p = 27.6 MB，按 JTAG 0.26~0.35 MB/s **估算** 79~106 s，不是实时；
- 契约文件（`docs/interface.md` / `backend/contract.py` / `frontend/mock.js`）未改。
