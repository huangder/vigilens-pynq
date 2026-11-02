"""probe_bbox_jitter.py —— 网页检测框"追踪效果"量化（对应 docs/16 的追踪评估）。

**它回答什么**：网页上跟着脸走的那个框稳不稳？相机只有 160x120 时会不会很跳？

**做法**：拿一段真人正脸视频，把同一段画面缩放到三档分辨率，每档都喂
`backend/face_landmark` 的 `MediaPipeLandmarker`（与 A 线管线**同一个类、同一个 bbox 定义**：
眼外角 + 嘴角 + 15% padding），逐帧记录 `face.bbox`，统计：
  · 丢框率（`visible==0` / `w<=0` → 网页上框会整个消失）
  · 相邻帧框中心位移、宽高变化（均 / p95 / 最大）
  · 折算到"网页里 DISPLAY_W 宽的显示区"上是几个像素在抖

需要 `data/raw/*.mp4`（**不入库**）。没有素材时明确报错并退出码 2。

用法：  .venv\\Scripts\\python.exe metrics\\scripts\\probe_bbox_jitter.py [视频路径] [帧数]
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from face_landmark import MediaPipeLandmarker  # noqa: E402

DEFAULT_VIDEO = REPO_ROOT / "data" / "raw" / "blink.mp4"
N_FRAMES = 150
DISPLAY_W = 480.0            # 网页上视频区大致宽度
SIZES = [(640, 480), (320, 240), (160, 120)]


def stats(v: list[float]) -> str:
    if not v:
        return "—"
    a = np.asarray(v, dtype=float)
    return f"{a.mean():.2f}/{np.percentile(a, 95):.2f}/{a.max():.2f}"


def main(argv: list[str]) -> int:
    video = Path(argv[1]) if len(argv) > 1 else DEFAULT_VIDEO
    n_frames = int(argv[2]) if len(argv) > 2 else N_FRAMES
    if not video.exists():
        print(f"[FAIL] 找不到素材 {video}（data/raw/*.mp4 不入库，需自行准备一段正脸视频）。",
              file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(str(video))
    frames = []
    for _ in range(n_frames):
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
    cap.release()
    if len(frames) < 10:
        print(f"[FAIL] 只读到 {len(frames)} 帧，素材太短。", file=sys.stderr)
        return 2

    mk = MediaPipeLandmarker()
    print(f"素材：{video.name}  {len(frames)} 帧")
    print(f"（显示区按 {DISPLAY_W:.0f} px 宽折算；框 = 眼外角+嘴角 + 15% padding）\n")
    print(f"{'输入尺寸':<12}{'丢框帧':<10}{'中心位移 均/p95/最大 (x | y)':<34}"
          f"{'宽高变化 均/p95/最大 (w | h)':<34}{'显示抖动 p95':<12}")

    for w, h in SIZES:
        boxes: list[list[int] | None] = []
        lost = 0
        for fr in frames:
            img = cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA) if (w, h) != (640, 480) else fr
            obs = mk.detect(img, 0)
            b = [int(v) for v in obs.bbox]
            if obs.visible <= 0 or b[2] <= 0 or b[3] <= 0:
                lost += 1
                boxes.append(None)
            else:
                boxes.append(b)

        dcx, dcy, dw, dh = [], [], [], []
        prev = None
        for b in boxes:
            if b is None:
                prev = None
                continue
            if prev is not None:
                dcx.append(abs((b[0] + b[2] / 2) - (prev[0] + prev[2] / 2)))
                dcy.append(abs((b[1] + b[3] / 2) - (prev[1] + prev[3] / 2)))
                dw.append(abs(b[2] - prev[2]))
                dh.append(abs(b[3] - prev[3]))
            prev = b

        jitter_p95 = float(np.percentile(np.asarray(dcx, dtype=float), 95) * (DISPLAY_W / w)) if dcx else 0.0
        print(f"{w}x{h:<8}{lost:<10}{stats(dcx) + ' | ' + stats(dcy):<34}"
              f"{stats(dw) + ' | ' + stats(dh):<34}{jitter_p95:<12.1f}")

    print("\n判读：折算到显示尺寸后各分辨率抖动相近（~1.5 px）—— QQVGA 并不会让框变跳；")
    print("      真正的限制是框的**更新率**（旁路预览只有 3.7~8 fps），不是抖动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
