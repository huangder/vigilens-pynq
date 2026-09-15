"""capture.py —— 帧源（《02》任务 A1）。

三种输入，统一 yield Frame：
  1. 视频文件回放（A 线的主力测试方式，可无限次重复 → 结果可复现）
  2. USB 摄像头（有则更好，没有不影响）
  3. synthetic —— 连 OpenCV 都没装时的**确定性合成帧源**

为什么要有第 3 种：A 线第一周要证明"回放视频 → 契约 JSON"链路通，
但"这台机器没装 opencv"不该成为卡住链路的理由。合成帧源的像素是
由 frame_id 确定性生成的（同 frame_id 必得同画面），所以仍然满足
《02》"重复运行结果一致"的验收口径。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    from .config import load_config
except ImportError:  # 直接以脚本方式运行
    from config import load_config

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}


@dataclass
class Frame:
    """一帧图像 + 它的身份信息。image 可能是 numpy 数组，也可能是 SyntheticImage。"""

    frame_id: int
    ts: float
    image: Any

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(getattr(self.image, "shape", (0, 0, 3)))


class SyntheticImage:
    """极简假图：只提供 shape 与一个确定性的 average_luma()。

    face_landmark 的 stub 只需要 shape；quality 的 stub 需要一点"亮度"来
    演示光照评分会动。真接上 OpenCV 后这个类就不再出现。
    """

    __slots__ = ("width", "height", "seed")

    def __init__(self, width: int, height: int, seed: int) -> None:
        self.width = width
        self.height = height
        self.seed = seed

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.height, self.width, 3)

    def average_luma(self) -> float:
        """0~1，由 seed 决定；模拟"光照缓慢漂移 + 偶发变暗"。"""
        base = 0.55 + 0.35 * (((self.seed * 2654435761) % 1000) / 1000.0)
        if self.seed % 137 == 0:  # 偶发遮挡/变暗，用来演示 unreliable 分支
            base *= 0.35
        return max(0.0, min(1.0, base))


def _have_cv2() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except ImportError:
        return False


def _synthetic_frames(width: int, height: int, limit: int | None, fps: float) -> Iterator[Frame]:
    n = 0
    while limit is None or n < limit:
        yield Frame(frame_id=n, ts=time.time(), image=SyntheticImage(width, height, n))
        n += 1


def open_frame_source(
    source: str,
    *,
    width: int = 640,
    height: int = 480,
    fps: float | None = None,
    limit: int | None = None,
) -> tuple[Iterator[Frame], str]:
    """打开帧源。返回 (帧迭代器, 人类可读的来源描述)。

    source 取值：
      "synthetic" / "demo"  → 确定性合成帧源
      "0" / "1" ...         → 摄像头序号
      其它                   → 视频文件路径

    fps：**默认取 config.yaml 的 fps_nominal**（契约 §0 的唯一来源），
         不传就跟随契约，别在这里写死数字。
    """
    if fps is None:
        fps = float(load_config()["fps_nominal"])
    low = source.strip().lower()
    if low in ("synthetic", "demo"):
        return _synthetic_frames(width, height, limit, fps), f"synthetic {width}x{height}"

    if not _have_cv2():
        # 不抛异常：链路优先。但要**明确**告诉用户现在拿到的是假图。
        print(
            "[warn] 未安装 opencv-python，无法读取真实视频/摄像头，"
            f"已回退到确定性合成帧源（source={source!r} 被忽略）。\n"
            "       装依赖：pip install -r requirements.txt"
        )
        return _synthetic_frames(width, height, limit, fps), f"synthetic(fallback) {width}x{height}"

    import cv2  # type: ignore

    if low.isdigit():
        cap = cv2.VideoCapture(int(low))
        desc = f"camera#{low}"
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(
                f"视频文件不存在：{path}\n"
                f"  请把 4 段标准视频放到 data/raw/（见 data/README.md），或用 --source synthetic 先跑链路。"
            )
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            print(f"[warn] 后缀 {path.suffix!r} 不在已知视频后缀内，仍然尝试用 OpenCV 打开。")
        cap = cv2.VideoCapture(str(path))
        desc = f"file:{path.name}"

    if not cap.isOpened():
        raise RuntimeError(f"OpenCV 打不开帧源 {source!r}（文件损坏 / 摄像头被占用 / 序号不对）")

    real_fps = cap.get(cv2.CAP_PROP_FPS) or fps
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def _gen() -> Iterator[Frame]:
        n = 0
        try:
            while limit is None or n < limit:
                ok, bgr = cap.read()
                if not ok:
                    break
                yield Frame(frame_id=n, ts=time.time(), image=bgr)
                n += 1
        finally:
            cap.release()

    desc = f"{desc}  {real_fps:.1f}fps  {total or '?'} frames"
    return _gen(), desc


if __name__ == "__main__":  # 自检：python backend/capture.py
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from console import enable_utf8_console

    enable_utf8_console()
    it, desc = open_frame_source("synthetic")
    print(f"帧源：{desc}")
    for i, fr in enumerate(it):
        print(f"  frame_id={fr.frame_id} shape={fr.shape} ts={fr.ts:.3f}")
        if i >= 2:
            break
