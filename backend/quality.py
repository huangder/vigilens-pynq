"""quality.py —— 信号质量评分（《02》任务 A7）。

这是本项目差异化卖点的算法落点：**先判断能不能测**。
光照、运动、人脸可见率 → 合成 quality.overall；overall 低于 config 阈值时，
decision.py 会把状态切成 `unreliable`，**并且不允许输出心率/呼吸数值**。

本文件同时承担 C 线需要的**帧差运动量黄金参考**（docs/interface.md 3.3）：
  diff_total    = Σ|cur - prev|              （u32，完全无溢出风险：640*480*255 < 2^32）
  motion_pixels = |diff| > thresh 的像素数   （u32）
  motion_ratio_q16 = (motion_pixels << 16) // count   （整除向下取整；count==0 时输出 0）

⚠️ 定点与整数口径必须与 HLS 侧逐字一致（见 docs/interface.md 3.3 末注），否则
   C4 的"逐点相等"比对必然失败。

⚠️ 权重公式目前是**占位标定**，全部写在 config.yaml（quality_weights 等），
   待 A7 用实测数据（大幅晃动 / 变暗 / 正常三种场景）重标定。
"""

from __future__ import annotations

from typing import Any

try:
    from .config import load_config
except ImportError:  # 直接以脚本方式运行
    from config import load_config


def _numpy():
    try:
        import numpy as np

        return np
    except ImportError:
        return None


def to_gray(image: Any) -> Any:
    """转灰度。有 numpy+OpenCV 时走 cvtColor，否则返回 None（调用方走降级路径）。"""
    np = _numpy()
    if np is None:
        return None
    if isinstance(image, np.ndarray):
        if image.ndim == 2:
            return image
        if image.ndim == 3 and image.shape[2] >= 3:
            try:
                import cv2  # type: ignore

                # ⚠️ 契约要求 R-G-B；OpenCV 读到的是 BGR。灰度权重对这些差异不敏感，
                #    但真正的黄金参考比对（roi_statistic）必须先 BGR2RGB，见 docs/interface.md 0.1
                return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            except ImportError:
                # BT.601 手算，避免依赖 opencv
                return (0.114 * image[:, :, 0] + 0.587 * image[:, :, 1] + 0.299 * image[:, :, 2]).astype(np.uint8)
    return None


def frame_diff_motion(prev_gray: Any, cur_gray: Any, motion_thresh: int = 25) -> dict[str, int]:
    """帧差运动量（与 HLS `motion_quality` 契约一致的**整数**实现）。"""
    np = _numpy()
    if np is None or prev_gray is None or cur_gray is None:
        return {"diff_total": 0, "motion_pixels": 0, "motion_ratio_q16": 0, "count": 0}

    p = prev_gray.astype(np.int32)
    c = cur_gray.astype(np.int32)
    diff = np.abs(c - p)
    count = int(diff.size)
    if count == 0:
        return {"diff_total": 0, "motion_pixels": 0, "motion_ratio_q16": 0, "count": 0}
    diff_total = int(diff.sum())
    motion_pixels = int((diff > motion_thresh).sum())
    motion_ratio_q16 = (motion_pixels << 16) // count      # 整除，向下取整
    return {
        "diff_total": diff_total,
        "motion_pixels": motion_pixels,
        "motion_ratio_q16": motion_ratio_q16,
        "count": count,
    }


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


class QualityScorer:
    """逐帧质量评分。有 OpenCV 时算真像素统计；没有时走确定性占位。"""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        c = cfg or load_config()
        self.motion_thresh = int(c.get("motion_thresh_gray", 25))
        self.light_target = float(c.get("light_target", 0.5))
        self.motion_gain = float(c.get("motion_score_gain", 4.0))
        self.weights = dict(c.get("quality_weights", {"light": 0.5, "motion": 0.3, "face": 0.2}))
        self._prev_gray: Any = None
        self._last_motion: dict[str, int] = {"diff_total": 0, "motion_pixels": 0, "motion_ratio_q16": 0, "count": 0}
        self.mode = "pixels"

    def update(self, frame_id: int, ts: float, frame: Any, obs: Any) -> dict[str, float]:
        image = getattr(frame, "image", frame)
        gray = to_gray(image)

        if gray is not None:
            self.mode = "pixels"
            mean_luma = float(gray.mean()) / 255.0
            if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
                self._last_motion = frame_diff_motion(self._prev_gray, gray, self.motion_thresh)
            self._prev_gray = gray
            motion_ratio = self._last_motion["motion_ratio_q16"] / 65536.0
        else:
            # 占位路径：只依赖 frame_id，仍满足"重复运行结果一致"
            self.mode = "placeholder"
            seed = getattr(image, "seed", frame_id)
            mean_luma = image.average_luma() if hasattr(image, "average_luma") else 0.6
            # 周期性晃动，让 unreliable 分支在 demo 里真的会出现
            sway = ((seed * 2654435761) % 997) / 997.0
            motion_ratio = 0.02 + 0.5 * sway if (seed % 211 == 0) else 0.02 + 0.05 * sway

        light_score = _clamp01(1.0 - abs(mean_luma - self.light_target) * 2.0)
        motion_score = _clamp01(motion_ratio * self.motion_gain)
        face_score = float(getattr(obs, "visible", 0.0))

        w = self.weights
        wsum = max(1e-9, w["light"] + w["motion"] + w["face"])
        overall = (w["light"] * light_score + w["motion"] * (1.0 - motion_score) + w["face"] * face_score) / wsum

        return {
            "light_score": round(light_score, 4),
            "motion_score": round(motion_score, 4),
            "overall": round(_clamp01(overall), 4),
            "_mean_luma": round(mean_luma, 4),
            "_motion_ratio": round(motion_ratio, 4),
            "_mode": self.mode,
        }

    @staticmethod
    def strip_private(quality: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in quality.items() if not k.startswith("_")}


if __name__ == "__main__":  # 自检：python backend/quality.py
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture import open_frame_source
    from face_landmark import make_landmarker

    # 合成帧源不是图像：自检用 StubLandmarker（真 MediaPipe 见 run_pipeline --source <视频>）
    mk = make_landmarker(force_stub=True)
    sc = QualityScorer()
    fps = float(load_config()["fps_nominal"])     # 契约 §0 的唯一来源，别写死 30
    it, desc = open_frame_source("synthetic")
    print(f"帧源：{desc}")
    for fr in it:
        if fr.frame_id >= int(fps * 10):          # 看前 10 秒
            break
        if fr.frame_id % int(fps * 2) == 0:       # 每 2 秒打一行
            q = sc.update(fr.frame_id, fr.frame_id / fps, fr, mk.detect(fr.image, fr.frame_id))
            print(f"  f{fr.frame_id:4d} light={q['light_score']:.2f} motion={q['motion_score']:.2f} "
                  f"overall={q['overall']:.2f}  (mode={q['_mode']})")
    print(f"评分模式：{sc.mode}（pixels = 真像素统计；placeholder = 无 OpenCV 的确定性占位）")
