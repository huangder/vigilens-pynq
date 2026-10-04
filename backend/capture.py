"""capture.py —— 帧源（《02》任务 A1）。

三种输入，统一 yield Frame：
  1. 视频文件回放（A 线的主力测试方式，可无限次重复 → 结果可复现）
  2. USB 摄像头（有则更好，没有不影响）
  3. synthetic —— 连 OpenCV 都没装时的**确定性合成帧源**

为什么要有第 3 种：A 线第一周要证明"回放视频 → 契约 JSON"链路通，
但"这台机器没装 opencv"不该成为卡住链路的理由。合成帧源的像素是
由 frame_id 确定性生成的（同 frame_id 必得同画面），所以仍然满足
《02》"重复运行结果一致"的验收口径。

**方向归一化（BUG-013）**：相机装反、手机倒着拿、竖屏视频被读成横屏时，
mediapipe 的检出率会掉到 0（180° 倒置实测 0%）。所以这里提供
`rotate_frame()` / `OrientingSource`：先用前几帧**探测**朝向，再把整条流统一旋正。
探测只发生在开头的 `orientation_probe_frames` 帧上，不是每帧都试四遍。
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

# 允许的旋转角度（顺时针，单位度）。0 = 不动。
ROTATE_CHOICES = (0, 90, 180, 270)


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


def rotate_frame(image: Any, degrees: int) -> Any:
    """把一帧图像按**顺时针**旋转 `degrees` 度（0/90/180/270）。

    · `degrees == 0` 或不是 numpy 图像（合成帧源）→ 原样返回，绝不报错：
      方向归一化不该成为链路跑不起来的理由；
    · 90/270 会**交换宽高**（640×480 → 480×640），所以下游拿到的 bbox/ROI
      都在"旋转后"的坐标系里 —— 这一点与 BUG-008（bbox 坐标空间待拍板）有关，别混。
    """
    if degrees not in ROTATE_CHOICES:
        raise ValueError(f"旋转角度只能是 {ROTATE_CHOICES} 之一，收到 {degrees!r}")
    if degrees == 0 or not hasattr(image, "shape") or getattr(image, "ndim", 0) == 0:
        return image
    if not _have_cv2():
        return image
    import cv2  # type: ignore

    code = {90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE}[degrees]
    return cv2.rotate(image, code)


def image_size(image: Any, *, fallback: tuple[int, int] = (640, 480)) -> tuple[int, int]:
    """返回 `(width, height)`；拿不到就退回 `fallback`。

    ⚠️ 为什么必须有这个函数（实测踩到的坑，861/908 帧）：
    `run_pipeline` 里原来把**契约尺寸**（config 的 640×480）当成了 ROI 的边界，
    而视频可能是 720×1280 —— 于是 `forehead_roi()` 一裁剪就把整个 ROI 裁没了，
    rPPG 一帧样本都喂不进去（症状是"窗口永远填不满"，看着像算法问题，其实是坐标问题）。
    **ROI/裁剪一律用当帧真实尺寸**，契约尺寸只用于"期望输入"的判断。
    """
    shape = getattr(image, "shape", None)
    if shape and len(shape) >= 2:
        try:
            h, w = int(shape[0]), int(shape[1])
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError):
            pass
    return int(fallback[0]), int(fallback[1])


def choose_rotation(images: list[Any], detect: Any,
                    *, candidates: tuple[int, ...] = ROTATE_CHOICES) -> tuple[int, dict[int, int]]:
    """在前几帧上试各种朝向，返回 `(最佳角度, 各角度的命中帧数)`。

    `detect(image) -> bool`：这一帧能不能检出人脸（由调用方注入，通常是 mediapipe）。
    **并列时选 0**（宁可不动）；一帧都检不出也返回 0 —— 宁可"没帮上忙"，
    也不要因为探测失败把一段本来正常的素材转错。
    """
    scores: dict[int, int] = {}
    for deg in candidates:
        hits = 0
        for img in images:
            try:
                if detect(rotate_frame(img, deg)):
                    hits += 1
            except Exception:  # noqa: BLE001 —— 探测失败按"这帧不算命中"处理
                pass
        scores[deg] = hits
    best = 0
    for deg in candidates:                     # 顺序遍历 ⇒ 并列时天然偏向较小的角度，0 优先
        if scores[deg] > scores.get(best, -1):
            best = deg
    return best, scores


class OrientingSource:
    """把帧源包一层：`auto` 时先探测朝向，再让整条流统一旋正。

    用法（`run_pipeline` 就是这么用的）：

        src = OrientingSource(frames, detect=..., rotate="auto", probe_frames=8)
        src.prime()            # 探测，幂等；探测用的那几帧会缓存在内部
        for frame in src: ...   # 缓存帧 + 剩余帧，全部按探测结果旋转

    · `rotate="0"`  → 完全不碰（连探测都不做）；
    · `rotate=90/180/270` → 跳过探测，直接按这个角度旋转；
    · `rotate="auto"` → 用前 `probe_frames` 帧 × 4 个角度比检出帧数。
    """

    def __init__(self, frames: Iterator[Frame], *, detect: Any = None,
                 rotate: str | int = "auto", probe_frames: int = 8) -> None:
        self._frames = frames
        self._detect = detect
        # ⚠️ 命令行传进来的永远是**字符串**（argparse 的 choices 是 "auto"/"0"/"90"…），
        # 所以这里必须先归一化 —— 否则 `--rotate 180` 会被当成"不认识的值"而静默失效。
        if isinstance(rotate, str) and rotate.strip().isdigit():
            rotate = int(rotate.strip())
        if rotate != "auto" and rotate not in ROTATE_CHOICES:
            raise ValueError(f'rotate 只能是 "auto" 或 {ROTATE_CHOICES} 之一，收到 {rotate!r}')
        self._rotate = rotate
        self._probe_frames = max(0, int(probe_frames))
        self._buffer: list[Frame] = []
        self._primed = False
        # 强制角度在构造时就已知，直接生效，不必等 prime()（也方便调用方先打印再迭代）
        self.degrees: int = int(rotate) if rotate in ROTATE_CHOICES else 0
        self.scores: dict[int, int] = {}
        self.probed_frames = 0

    def prime(self) -> None:
        """做一次探测（幂等）。探测用的帧不会丢，会在迭代时原样补出来。"""
        if self._primed:
            return
        self._primed = True
        if isinstance(self._rotate, int) and self._rotate in ROTATE_CHOICES:
            self.degrees = int(self._rotate)
            return
        if self._rotate != "auto" or self._detect is None or self._probe_frames == 0:
            return
        for _ in range(self._probe_frames):
            try:
                self._buffer.append(next(self._frames))
            except StopIteration:
                break
        self.probed_frames = len(self._buffer)
        if not self._buffer:
            return
        self.degrees, self.scores = choose_rotation([f.image for f in self._buffer], self._detect)

    def __iter__(self) -> Iterator[Frame]:
        self.prime()
        for fr in self._buffer:
            yield Frame(frame_id=fr.frame_id, ts=fr.ts, image=rotate_frame(fr.image, self.degrees))
        for fr in self._frames:
            yield Frame(frame_id=fr.frame_id, ts=fr.ts, image=rotate_frame(fr.image, self.degrees))


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
