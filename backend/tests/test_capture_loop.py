"""`--loop` 必须是**同一个进程里的循环**，不是"每轮重启一个进程"（docs/16 BUG-009 成因 A）。

为什么单独测：`--loop` 原来在 `run_demo.py` 里实现成"跑完一轮再 `subprocess.run` 一轮"，
于是 frame_id 靠 `--frame-id-offset` 继续涨，而 blink_count / yawn_count / long_close_count
与 PERCLOS 滑窗都从 0 重来、CSV 还被 `open("w")` 截断 —— 三者互相矛盾，用户看到"测量一直在跳变"。

现在 `--loop` 交给**同一个管线进程**（`capture.open_frame_source(loop=True)` 在文件尾 seek 回 0），
本文件钉死两件事：① 帧源真的是同一条 frame_id / ts 序列；② `run_demo` 只在视频文件源上传 `--loop`。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

cv2 = pytest.importorskip("cv2", reason="循环读文件需要 OpenCV")
import numpy as np  # noqa: E402

from backend.capture import is_video_file_source, open_frame_source  # noqa: E402


def _make_video(path: Path, frames: int = 3, size: tuple[int, int] = (32, 24)) -> Path:
    """写一段**确定性**小视频（纯色帧）：只用来验证读循环，不涉及算法。"""
    w, h = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (w, h))
    assert writer.isOpened(), "VideoWriter 打不开：本机 OpenCV 缺 mp4v 编码器"
    for i in range(frames):
        img = np.full((h, w, 3), fill_value=(i * 60) % 255, dtype=np.uint8)
        writer.write(img)
    writer.release()
    return path


def test_loop_keeps_one_frame_id_sequence_across_the_file_end(workdir: Path) -> None:
    """读到文件尾回卷后，frame_id 继续涨、图像仍然有内容 —— 不是从头再来一遍。"""
    video = _make_video(workdir / "tiny.mp4", frames=3)
    it, desc = open_frame_source(str(video), loop=True, limit=9)

    got = list(it)
    assert len(got) == 9, f"loop=True 时应能连续读 9 帧，实际 {len(got)}"
    assert [f.frame_id for f in got] == list(range(9)), "回卷后 frame_id 必须继续递增"
    assert all(f.image is not None and getattr(f.image, "size", 0) > 0 for f in got), "回卷后读到了空帧"
    assert "loop" in desc, f"来源描述要显式写出 loop，实际 {desc!r}"


def test_without_loop_the_source_stops_at_the_file_end(workdir: Path) -> None:
    """不加 loop 时行为不变：读完 3 帧就结束（防止"顺手给所有源都开了循环"）。"""
    video = _make_video(workdir / "tiny2.mp4", frames=3)
    it, desc = open_frame_source(str(video), limit=9)
    got = list(it)
    assert len(got) == 3
    assert "loop" not in desc


@pytest.mark.parametrize("source,expected", [
    ("data/raw/blink.mp4", True),
    ("/tmp/a.avi", True),
    ("synthetic", False),
    ("0", False),
    ("mjpeg", False),
    ("mjpeg:http://127.0.0.1:8000/video.mjpg", False),
    ("http://127.0.0.1:8000/video.mjpg", False),
    ("uvc:0", False),
])
def test_only_video_files_can_loop(source: str, expected: bool) -> None:
    """循环只对视频文件有意义：摄像头 / MJPEG / 合成源都是活流或无限流。"""
    assert is_video_file_source(source) is expected


def test_run_demo_passes_loop_only_for_video_files() -> None:
    """`run_demo.py` 的 `--loop` 透传规则（纯函数，不需要起子进程）。"""
    spec = importlib.util.spec_from_file_location(
        "run_demo_loop_under_test", REPO_ROOT / "metrics" / "scripts" / "run_demo.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.loop_flag("data/raw/blink.mp4", True) == ["--loop"]
    assert mod.loop_flag("0", True) == [], "摄像头不能循环（活流）"
    assert mod.loop_flag("synthetic", True) == []
    assert mod.loop_flag("data/raw/blink.mp4", False) == []
