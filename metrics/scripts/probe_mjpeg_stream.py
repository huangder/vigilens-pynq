"""probe_mjpeg_stream.py —— 旁路 MJPEG 的真实速率 / 重复帧（对应 docs/16 BUG-029）。

**它回答什么**：A 线 rPPG 按 `fps_nominal=30` 换算频率与窗口，而旁路链路到底交付多少 fps？
其中有多少是"同一张画面的重复帧"？

**做法**：直接读 `/video.mjpg` 若干秒，对每个 JPEG 取 sha1，统计总数 / 去重数 / 到达间隔。

前提：`backend/api.py` 已在跑（`--no-mock`）且有人在推 `/api/frame`（相机桥或 `--push-video`）。

用法：  .venv\\Scripts\\python.exe metrics\\scripts\\probe_mjpeg_stream.py [URL] [秒数]
"""
from __future__ import annotations

import hashlib
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from capture import iter_mjpeg_jpegs  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8031/video.mjpg"


def chunks(url: str):
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 —— 本机/自定 URL
        while True:
            block = resp.read(8192)
            if not block:
                break
            yield block


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else DEFAULT_URL
    seconds = float(argv[2]) if len(argv) > 2 else 8.0

    try:
        stream = iter_mjpeg_jpegs(chunks(url))
        hashes: list[str] = []
        stamps: list[float] = []
        sizes: list[int] = []
        t0 = time.time()
        for jpeg in stream:
            hashes.append(hashlib.sha1(jpeg).hexdigest())
            sizes.append(len(jpeg))
            stamps.append(time.time())
            if stamps[-1] - t0 >= seconds:
                break
    except Exception as e:  # noqa: BLE001 —— 连不上就说清楚，别给一半数字
        print(f"[FAIL] 读不到 {url}：{type(e).__name__}: {e}", file=sys.stderr)
        print("       先起 api.py（--no-mock）并确认有人在推 /api/frame。", file=sys.stderr)
        return 2

    if len(stamps) < 2:
        print(f"[FAIL] {url} 在 {seconds:g}s 内只给了 {len(stamps)} 帧（有人在推吗？）", file=sys.stderr)
        return 2

    dur = stamps[-1] - stamps[0]
    uniq = len(set(hashes))
    gaps = [round(stamps[i + 1] - stamps[i], 3) for i in range(len(stamps) - 1)]
    print(f"URL           : {url}")
    print(f"采样时长      : {dur:.2f} s")
    print(f"收到 JPEG 帧数: {len(hashes)}  → {len(hashes) / dur:.2f} fps（含重复）")
    print(f"其中不重复    : {uniq}  → {uniq / dur:.2f} fps（真实新画面）")
    print(f"重复帧        : {len(hashes) - uniq}（{(len(hashes) - uniq) / len(hashes) * 100:.1f}%）")
    print(f"单帧字节      : min {min(sizes)} / max {max(sizes)} / 均值 {sum(sizes) // len(sizes)}")
    print(f"到达间隔      : min {min(gaps)} / max {max(gaps)} s")
    print(f"间隔分布      : " + ", ".join(f"{g}" for g in gaps[:24]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
