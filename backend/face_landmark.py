"""face_landmark.py —— 人脸框 + 关键点 + 头部姿态（《02》任务 A2）。

分层原则（《04》第 4 节）：
  - MediaPipe 本身很成熟，**不算"算法工作量"，属于框架内**；
  - 本文件只负责"从图像拿到几何量"，不负责阈值判断（那是 behavior_metrics 的事）。

有 mediapipe 时走真检测；没有时走 **StubLandmarker**：
  - 它按 frame_id 确定性生成关键点（同 frame_id 必得同结果），因此
    "同一段回放视频连续跑两次、输出数值一致"这条验收口径**在装依赖之前就能满足**；
  - 它按 `pattern` 造出眨眼 / 打哈欠 / 转头等情形，让下游 EAR/MAR 状态机有真实数据可吃。

⚠️ StubLandmarker 的输出**不是算法结果**，只是让链路可测的占位数据。
   报告与答辩里必须写清哪些数字来自 stub、哪些来自 MediaPipe 实测。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

try:
    from .config import load_config
except ImportError:  # 直接以脚本方式运行
    from config import load_config

# 眼睛 6 点（p1..p6，标准 EAR 定义，p1 在眼角外侧）
EYE_KEYS = ("p1", "p2", "p3", "p4", "p5", "p6")
MOUTH_KEYS = ("left", "right", "top", "bottom")

# MediaPipe FaceMesh 中左/右眼与嘴部的常用点号（478 点模型）
MP_LEFT_EYE = (33, 160, 158, 133, 153, 144)
MP_RIGHT_EYE = (362, 385, 387, 263, 373, 380)
MP_MOUTH = (61, 291, 13, 14)


@dataclass
class FaceObservation:
    """一帧的人脸几何量。landmarks 为 None 表示本帧没检测到脸。"""

    visible: float                                   # 人脸可见率 0~1
    bbox: tuple[int, int, int, int]                  # [x, y, w, h]，左上角原点，像素
    pose: dict[str, float]                           # {"yaw","pitch","roll"}，度
    eyes: dict[str, dict[str, tuple[float, float]]] = field(default_factory=dict)
    mouth: dict[str, tuple[float, float]] = field(default_factory=dict)
    source: str = "stub"                             # "mediapipe" | "stub"


def _have_mediapipe() -> bool:
    try:
        import mediapipe  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Stub：确定性关键点生成
# ---------------------------------------------------------------------------

def _deterministic_unit(*parts: int) -> float:
    """把若干整数打散成 [0,1) 的确定性伪随机数（不用 random 模块，避免全局状态）。"""
    h = 2166136261
    for p in parts:
        h ^= (p + 0x9E3779B9) & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    return ((h >> 8) & 0xFFFFFF) / float(0x1000000)


class StubLandmarker:
    """不依赖任何第三方库的人脸几何量发生器（确定性）。"""

    def __init__(self, width: int = 640, height: int = 480, pattern: str = "blink",
                 fps: float | None = None):
        # fps 默认取 config.yaml 的 fps_nominal（契约 §0），**不要在这里写死**：
        # 它决定了合成时间轴 t = frame_id / fps，写死会让"改帧率"变成静默错误。
        if fps is None:
            fps = float(load_config()["fps_nominal"])
        self.width = width
        self.height = height
        self.pattern = pattern
        self.fps = fps

    # -- 各种 pattern 的"眼睛开合度"曲线（0=完全闭合，1=完全睁开）--
    def _openness(self, frame_id: int) -> float:
        t = frame_id / self.fps  # 秒
        if self.pattern == "still":
            return 1.0
        if self.pattern == "turn":
            # 0~3s 正常；3~6s 转头（可见率下降、yaw 越界 → adjust_posture）；
            # 6s 之后人脸完全出框（可见率 0 → 触发"人脸丢失"分支）
            return 1.0 if t < 3.0 else 0.85
        if self.pattern == "yawn":
            # 每 6 秒打一次哈欠式的持续张口（眼睛仍睁着）
            return 1.0
        # 默认 blink：每 3 秒眨一次，眨眼持续约 130 ms；第 20 秒起加一次长闭眼（演示 fatigue_risk）
        phase = t % 3.0
        if t >= 20.0 and t < 21.2:
            return 0.0  # 长闭眼 1.2 s
        if phase < 0.13:
            return 0.0
        if phase < 0.20:
            return 0.5
        return 1.0

    def _mouth_openness(self, frame_id: int) -> float:
        t = frame_id / self.fps
        if self.pattern == "yawn":
            # 每 6 秒一次、持续 1.5 秒的大张口（> yawn_min_duration_ms=800ms，应被计为打哈欠）
            return 1.0 if (t % 6.0) < 1.5 else 0.1
        if self.pattern == "blink":
            if t >= 20.0 and t < 21.5:
                return 0.9  # 长闭眼期间顺带一个哈欠，便于演示复合触发
            return 0.08
        return 0.1

    def _visible(self, frame_id: int) -> float:
        if self.pattern == "turn":
            t = frame_id / self.fps
            if t < 3.0:
                return 0.95
            if t < 6.0:
                return 0.35      # 低于 face_visible_min(0.7) → adjust_posture
            return 0.0           # 人脸完全出框 → 检测器返回"没人脸"
        jitter = _deterministic_unit(frame_id, 7) * 0.03
        return max(0.0, 0.96 - jitter)

    def detect(self, frame: Any, frame_id: int | None = None) -> FaceObservation:
        fid = frame.frame_id if frame_id is None else frame_id
        h, w = (frame.shape[0], frame.shape[1]) if hasattr(frame, "shape") else (self.height, self.width)
        self.width, self.height = w, h

        visible = self._visible(fid)
        if visible <= 0.0:
            # 真检测器在没人脸时就是这个形态（空 eyes/mouth）。
            # 下游据此把 EAR 判为 0 而不是"睁眼正常"，见 behavior_metrics.update()。
            return FaceObservation(visible=0.0, bbox=(0, 0, 0, 0),
                                   pose={"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
                                   eyes={}, mouth={}, source="stub")

        # 人脸框随 frame_id 轻微漂移，模拟真实抖动（确定性）
        jx = int((_deterministic_unit(fid, 1) - 0.5) * 6)
        jy = int((_deterministic_unit(fid, 2) - 0.5) * 4)
        bw, bh = int(w * 0.28), int(h * 0.42)
        bx, by = int(w * 0.36) + jx, int(h * 0.22) + jy

        openness = self._openness(fid)
        mouth_open = self._mouth_openness(fid)

        # 由 openness 反推关键点：EAR = 2*o/W（见下方 ear() 定义）
        eye_w = max(8.0, bw * 0.22)
        o = 0.5 * 0.28 * eye_w * openness          # openness=1 → EAR≈0.28
        cx_l, cx_r = bx + bw * 0.30, bx + bw * 0.70
        cy = by + bh * 0.42

        def _eye(cx: float) -> dict[str, tuple[float, float]]:
            x0, x1 = cx - eye_w / 2.0, cx + eye_w / 2.0
            return {
                "p1": (x0, cy),
                "p2": (x0 + 0.25 * eye_w, cy - o),
                "p3": (x0 + 0.75 * eye_w, cy - o),
                "p4": (x1, cy),
                "p5": (x0 + 0.75 * eye_w, cy + o),
                "p6": (x0 + 0.25 * eye_w, cy + o),
            }

        mw = max(10.0, bw * 0.34)
        # mouth_open=1 → MAR = 2*m_half/mw ≈ 0.75，**高于** config 的 mar_threshold=0.6，
        # 留出余量：如果这里恰好等于阈值，`mar > threshold` 会因为浮点相等而不触发，
        # 让打哈欠状态机静默失效（这是实测踩到的坑，别再往回调）。
        m_half = 0.5 * mouth_open * 0.75 * mw
        mx, my = bx + bw * 0.5, by + bh * 0.78
        mouth = {
            "left": (mx - mw / 2.0, my),
            "right": (mx + mw / 2.0, my),
            "top": (mx, my - m_half),
            "bottom": (mx, my + m_half),
        }

        yaw = 0.0 if self.pattern != "turn" else (2.0 if fid < 90 else 34.0)
        pitch = 1.2 + (_deterministic_unit(fid, 3) - 0.5) * 2.0
        roll = (_deterministic_unit(fid, 4) - 0.5) * 3.0

        return FaceObservation(
            visible=visible,
            bbox=(bx, by, bw, bh),
            pose={"yaw": round(yaw, 2), "pitch": round(pitch, 2), "roll": round(roll, 2)},
            eyes={"left": _eye(cx_l), "right": _eye(cx_r)},
            mouth=mouth,
            source="stub",
        )


# ---------------------------------------------------------------------------
# MediaPipe 真检测
# ---------------------------------------------------------------------------

class MediaPipeLandmarker:
    """真检测：MediaPipe FaceMesh → 关键点 → 眼/嘴几何量 + 头部姿态。

    注意：本类在**没有 mediapipe 的机器上无法实例化**（构造函数会抛），
    调用方见 make_landmarker() 的自动回退逻辑。
    """

    def __init__(self, width: int = 640, height: int = 480, refine_landmarks: bool = True):
        import mediapipe as mp  # type: ignore

        self._mp = mp
        self._mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=refine_landmarks,   # 478 点（含虹膜）
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.width, self.height = width, height

    def detect(self, frame: Any, frame_id: int | None = None) -> FaceObservation:
        import cv2  # type: ignore

        h, w = frame.shape[0], frame.shape[1]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # ⚠️ 契约要求 R-G-B，见 docs/interface.md 0.1
        res = self._mesh.process(rgb)
        if not res.multi_face_landmarks:
            return FaceObservation(visible=0.0, bbox=(0, 0, 0, 0), pose={"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
                                   eyes={}, mouth={}, source="mediapipe")

        lm = res.multi_face_landmarks[0].landmark

        def px(i: int) -> tuple[float, float]:
            return (lm[i].x * w, lm[i].y * h)

        eyes = {}
        for name, idx in (("left", MP_LEFT_EYE), ("right", MP_RIGHT_EYE)):
            pts = [px(i) for i in idx]
            eyes[name] = {k: pts[n] for n, k in enumerate(EYE_KEYS)}
        mouth = {k: px(i) for k, i in zip(MOUTH_KEYS, MP_MOUTH)}

        xs = [p[0] for p in (eyes["left"]["p1"], eyes["right"]["p4"], mouth["left"], mouth["right"])]
        ys = [p[1] for p in (eyes["left"]["p1"], eyes["right"]["p4"], mouth["top"], mouth["bottom"])]
        x0, x1 = int(min(xs)), int(max(xs))
        y0, y1 = int(min(ys)), int(max(ys))
        pad = int(0.15 * max(x1 - x0, 1))
        bbox = (max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad) - max(0, x0 - pad),
                min(h, y1 + pad) - max(0, y0 - pad))

        pose = self._pose_from_landmarks(lm, w, h)
        return FaceObservation(visible=1.0, bbox=bbox, pose=pose, eyes=eyes, mouth=mouth, source="mediapipe")

    @staticmethod
    def _pose_from_landmarks(lm: Any, w: int, h: int) -> dict[str, float]:
        """用 solvePnP 解头部姿态（度）。失败时返回 0 而不是崩溃。"""
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        model = np.array([
            [0.0, 0.0, 0.0], [0.0, -63.6, -12.5], [-43.3, 32.7, -26.0],
            [43.3, 32.7, -26.0], [-28.9, -28.9, -24.1], [28.9, -28.9, -24.1],
        ], dtype="double")
        idx = [1, 199, 33, 263, 61, 291]
        img_pts = np.array([[lm[i].x * w, lm[i].y * h] for i in idx], dtype="double")
        focal = w
        cam = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1]], dtype="double")
        ok, rvec, _ = cv2.solvePnP(model, img_pts, cam, np.zeros((4, 1)),
                                   flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            return {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}
        rmat, _ = cv2.Rodrigues(rvec)
        sy = math.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
        pitch = math.degrees(math.atan2(-rmat[2, 0], sy))
        yaw = math.degrees(math.atan2(rmat[1, 0], rmat[0, 0]))
        roll = math.degrees(math.atan2(rmat[2, 1], rmat[2, 2]))
        return {"yaw": round(yaw, 2), "pitch": round(pitch, 2), "roll": round(roll, 2)}


def make_landmarker(
    *, width: int = 640, height: int = 480, pattern: str = "blink",
    fps: float | None = None, force_stub: bool = False
) -> Any:
    """工厂：能用 MediaPipe 就用，否则回退到 StubLandmarker，并明确打印用的是哪个。

    fps=None 时取 config.yaml 的 fps_nominal（契约 §0 的唯一来源）。
    """
    if fps is None:
        fps = float(load_config()["fps_nominal"])
    if force_stub:
        print("[face_landmark] 使用 StubLandmarker（--stub 指定）。输出为占位几何量，不是算法结果。")
        return StubLandmarker(width, height, pattern, fps)
    if _have_mediapipe():
        try:
            mk = MediaPipeLandmarker(width, height)
            print("[face_landmark] 使用 MediaPipe FaceMesh（478 点）。")
            return mk
        except Exception as exc:  # noqa: BLE001 —— 回退要稳，不让链路因为检测器挂掉
            print(f"[warn] MediaPipe 初始化失败（{exc}），回退到 StubLandmarker。")
    else:
        print("[warn] 未安装 mediapipe，回退到 StubLandmarker（占位几何量）。")
    return StubLandmarker(width, height, pattern, fps)


def ear(eye: dict[str, tuple[float, float]]) -> float:
    """眼睛纵横比 EAR = (|p2-p6| + |p3-p5|) / (2*|p1-p4|)。"""
    if not eye:
        return 0.0

    def d(a: str, b: str) -> float:
        (x1, y1), (x2, y2) = eye[a], eye[b]
        return math.hypot(x1 - x2, y1 - y2)

    horiz = d("p1", "p4")
    if horiz <= 1e-9:
        return 0.0
    return (d("p2", "p6") + d("p3", "p5")) / (2.0 * horiz)


def mar(mouth: dict[str, tuple[float, float]]) -> float:
    """嘴部纵横比 MAR = |top-bottom| / |left-right|。"""
    if not mouth:
        return 0.0
    (lx, ly), (rx, ry) = mouth["left"], mouth["right"]
    (tx, ty), (bx, by) = mouth["top"], mouth["bottom"]
    width = math.hypot(rx - lx, ry - ly)
    if width <= 1e-9:
        return 0.0
    return math.hypot(bx - tx, by - ty) / width


if __name__ == "__main__":  # 自检：python backend/face_landmark.py
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from capture import open_frame_source
    from console import enable_utf8_console

    enable_utf8_console()

    mk = make_landmarker(pattern="blink")
    it, desc = open_frame_source("synthetic")
    print(f"帧源：{desc}")
    for fr in it:
        if fr.frame_id > 100:
            break
        if fr.frame_id % 20 == 0:
            obs = mk.detect(fr.image, fr.frame_id)
            if obs.eyes:
                print(f"  f{fr.frame_id:4d} EAR(L)={ear(obs.eyes['left']):.3f} "
                      f"MAR={mar(obs.mouth):.3f} visible={obs.visible:.2f} src={obs.source}")
