"""68-point geometry for the Peppa model: EAR, MAR, layout checks, 2D proxies.

WHAT IS AND IS NOT CLAIMED
--------------------------
The 68-point topology is *assumed* to be the iBUG/300W layout (jaw 0-16, brows
17-26, nose 27-35, eyes 36-47, outer mouth 48-59, inner mouth 60-67) because the
author trained on 300W-LP. That assumption is not taken on faith: `layout_checks`
tests it per frame (eye loops on two sides, jaw arc, mouth loop, nose below the
eyes), and `docs/55` only publishes EAR/MAR after those checks pass on real video.

Pose is **not** PnP here. The 68 points have no 3D reference in this repository,
and `docs/54` §3.2 forbids filling 2D points with invented depth. What this module
offers is a roll angle (a real 2D angle between the eye centres) plus two
dimensionless shape ratios (`yaw_proxy`, `pitch_proxy`) that are monotonic under
translation but are **not** degrees and must not be written into `face.pose`.

The forehead ROI is constructed from brows (17-26) and eye corners, not from a
hairline; it is an untested sampling heuristic until rPPG is validated on it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

EYE_A = tuple(range(36, 42))       # image-left eye (subject's right)
EYE_B = tuple(range(42, 48))       # image-right eye (subject's left)
OUTER_MOUTH = tuple(range(48, 60))
INNER_MOUTH = tuple(range(60, 68))
BROWS = tuple(range(17, 27))
JAW = tuple(range(0, 17))
NOSE = tuple(range(27, 36))

# iBUG corners used by the frozen EAR formula.
_A_OUTER, _A_INNER = 36, 39
_B_OUTER, _B_INNER = 42, 45
_MOUTH_LEFT, _MOUTH_RIGHT, _MOUTH_TOP, _MOUTH_BOTTOM = 48, 54, 51, 57


@dataclass
class LandmarkObservation:
    """Pixel-space geometry in the shape the C core and BehaviorTracker expect."""

    eyes: dict[str, dict[str, tuple[float, float]]]
    mouth: dict[str, tuple[float, float]]
    points: list[tuple[float, float]]

    @property
    def ear_left(self) -> float:
        return ear(self.eyes["left"])

    @property
    def ear_right(self) -> float:
        return ear(self.eyes["right"])

    @property
    def mar(self) -> float:
        return mouth_ratio(self.mouth)


def _distance(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def ear(eye: dict[str, tuple[float, float]]) -> float:
    """EAR = (|p2-p6| + |p3-p5|) / (2*|p1-p4|), identical to `backend/face_landmark.py`."""
    if not eye:
        return 0.0
    width = _distance(eye["p1"], eye["p4"])
    if width <= 1e-9:
        return 0.0
    return (_distance(eye["p2"], eye["p6"]) + _distance(eye["p3"], eye["p5"])) / (2.0 * width)


def mouth_ratio(mouth: dict[str, tuple[float, float]]) -> float:
    """MAR = |top-bottom| / |left-right|, identical to `backend/face_landmark.py`."""
    if not mouth:
        return 0.0
    width = _distance(mouth["left"], mouth["right"])
    if width <= 1e-9:
        return 0.0
    return _distance(mouth["bottom"], mouth["top"]) / width


def build_observation(points) -> LandmarkObservation:
    """68 pixel points -> the six eye points per side + four mouth points.

    Point order inside an eye is the iBUG order that the EAR formula assumes:
    p1 outer corner, p2/p3 upper lid, p4 inner corner, p5/p6 lower lid.
    """
    if len(points) != 68:
        raise ValueError(f"expected 68 points, got {len(points)}")
    pts = [(float(x), float(y)) for x, y in points]
    eye_a = {f"p{i + 1}": pts[EYE_A[i]] for i in range(6)}
    eye_b = {f"p{i + 1}": pts[EYE_B[i]] for i in range(6)}
    mouth = {"left": pts[_MOUTH_LEFT], "right": pts[_MOUTH_RIGHT],
             "top": pts[_MOUTH_TOP], "bottom": pts[_MOUTH_BOTTOM]}
    return LandmarkObservation(eyes={"left": eye_a, "right": eye_b}, mouth=mouth, points=pts)


def _centre(points, indices) -> tuple[float, float]:
    xs = [points[i][0] for i in indices]
    ys = [points[i][1] for i in indices]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def layout_checks(points) -> dict[str, bool]:
    """Per-frame evidence that the output really is the iBUG 68 layout.

    These are *topology* checks, not accuracy checks. A face at 60 degrees yaw can
    legitimately fail `eyes_side_by_side`; the summary keeps the failure rate so
    such frames are visible instead of silently averaged in.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) != 68:
        return {"shape": False}
    a_cx, a_cy = _centre(pts, EYE_A)
    b_cx, b_cy = _centre(pts, EYE_B)
    # Eyes are separated mostly horizontally and A is left of B.
    eyes_side_by_side = (b_cx - a_cx) > abs(b_cy - a_cy) and b_cx > a_cx
    # Each eye: 36/42 and 39/45 are the far corners; upper lid above lower lid.
    eye_a_loop = (pts[36][0] < pts[39][0] and min(pts[37][1], pts[38][1]) < max(pts[40][1], pts[41][1]))
    eye_b_loop = (pts[42][0] < pts[45][0] and min(pts[43][1], pts[44][1]) < max(pts[46][1], pts[47][1]))
    # Jaw arc: 0 and 16 are the horizontal extremes, 8 is the chin (largest y).
    jaw_arc = (pts[0][0] < pts[8][0] < pts[16][0]
               and pts[8][1] >= max(pts[i][1] for i in JAW if i != 8))
    # Mouth: 48/54 corners, 51 above 57.
    mouth_loop = pts[48][0] < pts[54][0] and pts[51][1] < pts[57][1]
    # Nose tip (30) sits below the eyes and above the chin. No x-range check: at a
    # profile turn the nose can pass the jaw outline (measured frame 217), which is
    # geometry, not a wrong point order.
    nose_below_eyes = min(pts[36][1], pts[42][1]) < pts[30][1] < pts[8][1]
    return {"eyes_side_by_side": bool(eyes_side_by_side), "eye_a_loop": bool(eye_a_loop),
            "eye_b_loop": bool(eye_b_loop), "jaw_arc": bool(jaw_arc),
            "mouth_loop": bool(mouth_loop), "nose_below_eyes": bool(nose_below_eyes)}


# Degeneracy guards, not measurement thresholds: they answer "is this frame's eye
# geometry usable at all". Calibrated on `data/raw/blink.mp4` 300 frames, where the
# normal segment has IOD/box_width ~0.39 and both eyes ~0.37 x IOD, while the
# 214-250 segment collapses (IOD/box 0.10, far eye 0.19 x IOD). Absolute pixel
# limits would not survive a resolution change; both ratios do.
MIN_IOD_OVER_BOX = 0.18
MIN_EYE_SYMMETRY = 0.35


def eye_validity(points, box_width: float) -> dict:
    """Per-eye usability at extreme yaw / blur, plus the effective EAR.

    A 68-point regressor has no visibility output: when one eye is occluded its six
    points can collapse onto each other, and `ear()` then reports a huge ratio from a
    width of a few pixels (measured 3.17 at frame 240 of the 300-frame clip). Those
    numbers must not enter PERCLOS. The guard uses the *relative* width of the two
    eyes and IOD relative to the detector box, both dimensionless.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) != 68:
        return {"left": False, "right": False, "frame_ok": False, "ear": None,
                "iod_over_box": 0.0, "symmetry": 0.0}
    width_a = _distance(pts[36], pts[39])
    width_b = _distance(pts[42], pts[45])
    iod = _distance(_centre(pts, EYE_A), _centre(pts, EYE_B))
    iod_over_box = iod / box_width if box_width > 0 else 0.0
    widest = max(width_a, width_b)
    symmetry = (min(width_a, width_b) / widest) if widest > 1e-9 else 0.0
    frame_ok = iod_over_box >= MIN_IOD_OVER_BOX
    left_ok = frame_ok and width_a >= MIN_EYE_SYMMETRY * widest and width_a > 1e-6
    right_ok = frame_ok and width_b >= MIN_EYE_SYMMETRY * widest and width_b > 1e-6
    observation = build_observation(pts)
    values = [v for v, ok in ((observation.ear_left, left_ok),
                              (observation.ear_right, right_ok)) if ok]
    return {"left": bool(left_ok), "right": bool(right_ok), "frame_ok": bool(frame_ok),
            "ear": (sum(values) / len(values)) if values else None,
            "iod_over_box": round(iod_over_box, 4), "symmetry": round(symmetry, 4)}


def pose_proxy(points) -> dict[str, float]:
    """2D-only head-shape proxies. **Not degrees of yaw/pitch.**

    · `roll_deg`: atan2 of the A->B eye-centre line — a genuine in-plane angle.
    · `yaw_proxy`: nose-tip horizontal offset from the eye midpoint, divided by the
      inter-ocular distance. 0 is frontal, sign follows the turn direction.
    · `pitch_proxy`: nose-tip vertical offset below the eye line, divided by the
      eye-line-to-mouth-centre distance. Larger means more nose-down.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    a_cx, a_cy = _centre(pts, EYE_A)
    b_cx, b_cy = _centre(pts, EYE_B)
    dx, dy = b_cx - a_cx, b_cy - a_cy
    iod = math.hypot(dx, dy)
    roll = math.degrees(math.atan2(dy, dx))
    eye_mid_x, eye_mid_y = (a_cx + b_cx) / 2.0, (a_cy + b_cy) / 2.0
    mouth_cx, mouth_cy = _centre(pts, OUTER_MOUTH)
    vertical = math.hypot(mouth_cx - eye_mid_x, mouth_cy - eye_mid_y)
    return {
        "roll_deg": round(roll, 3),
        "yaw_proxy": round((pts[30][0] - eye_mid_x) / iod, 4) if iod > 1e-9 else 0.0,
        "pitch_proxy": round((pts[30][1] - eye_mid_y) / vertical, 4) if vertical > 1e-9 else 0.0,
    }


def forehead_roi(points, width: int, height: int, *, up: float = 0.65, down: float = 0.10,
                 shrink: float = 0.08):
    """Forehead band above the brows, clipped to the frame; `None` if degenerate.

    The band starts `up` x (brow-to-eye distance) above the brow ridge and ends
    `down` x that distance above it, so it never contains brow or eye pixels.
    `shrink` trims the sides. Returns `(x0, y0, x1, y1)` half-open pixels.
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) != 68:
        return None
    brow_x = [pts[i][0] for i in BROWS]
    brow_y = min(pts[i][1] for i in BROWS)
    eye_y = max(pts[i][1] for i in EYE_A + EYE_B)
    gap = eye_y - brow_y
    if gap <= 0 or not math.isfinite(gap):
        return None
    x0, x1 = min(brow_x), max(brow_x)
    dx = (x1 - x0) * shrink
    x0, x1 = x0 + dx, x1 - dx
    y0 = brow_y - up * gap
    y1 = brow_y - down * gap
    x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
    x1, y1 = min(int(width), int(round(x1))), min(int(height), int(round(y1)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return x0, y0, x1, y1


def roi_contains_points(roi, points, indices) -> bool:
    """True if any of `indices` falls inside the half-open ROI (a rejection test)."""
    if not roi:
        return False
    x0, y0, x1, y1 = roi
    return any(x0 <= points[i][0] < x1 and y0 <= points[i][1] < y1 for i in indices)


# ---------------------------------------------------------------------------
# 68-point PnP
# ---------------------------------------------------------------------------

MODEL_PATH = Path(__file__).with_name("face68_3d.json")
_MODEL_CACHE: dict[str, object] = {}


def load_face_model(path: str | Path | None = None) -> "np.ndarray":
    """Load the 68x3 reference model (units = inter-ocular distance).

    The file is generated by `build_face68_model.py`; it is subject-specific and
    must not be described as an anatomical atlas. Importing numpy here keeps the
    geometry helpers usable on machines that only need EAR/MAR.
    """
    import json

    import numpy as np

    target = Path(path or MODEL_PATH)
    key = str(target.resolve())
    if key not in _MODEL_CACHE:
        payload = json.loads(target.read_text(encoding="utf-8"))
        points = np.asarray(payload["points_68"], dtype=np.float64)
        if points.shape != (68, 3):
            raise ValueError(f"model shape must be (68,3), got {points.shape}")
        _MODEL_CACHE[key] = points
    return _MODEL_CACHE[key]


def solve_pose_pnp(points, width: int, height: int, *, model=None,
                   reproj_max_frac: float | None = None):
    """Head pose from the 68 pixel points via solvePnP.

    Returns `{yaw, pitch, roll, reproj_px, reproj_frac}` in degrees, or None when
    the solve is degenerate / behind the camera / over the reprojection guard.
    The decomposition matches `backend/face_landmark.MediaPipeLandmarker`, so the
    angles are directly comparable with the existing `face.pose` field.
    """
    import cv2
    import numpy as np

    pts = np.asarray(points, dtype=np.float64)
    if pts.shape != (68, 2) or not np.isfinite(pts).all():
        return None
    reference = load_face_model() if model is None else np.asarray(model, dtype=np.float64)
    if reference.shape != (68, 3):
        return None
    focal = float(width)
    camera = np.array([[focal, 0.0, width / 2.0],
                       [0.0, focal, height / 2.0],
                       [0.0, 0.0, 1.0]], dtype=np.float64)
    # Degenerate input: every point at the same spot cannot define a pose.
    if float(np.ptp(pts, axis=0).max()) <= 0.0:
        return None
    eye_a = pts[36:42].mean(axis=0)
    eye_b = pts[42:48].mean(axis=0)
    iod = float(np.linalg.norm(eye_b - eye_a))
    if iod <= 1e-6:
        return None
    limit = 0.25 if reproj_max_frac is None else float(reproj_max_frac)
    best = None
    for flags in (cv2.SOLVEPNP_EPNP, cv2.SOLVEPNP_ITERATIVE):
        ok, rvec, tvec = cv2.solvePnP(reference, pts, camera, None, flags=flags)
        if not ok or float(tvec[2][0]) <= 0.0:
            continue
        projected, _ = cv2.projectPoints(reference, rvec, tvec, camera, None)
        residual = projected.reshape(-1, 2) - pts
        error = float(np.linalg.norm(residual, axis=1).mean())
        fraction = error / iod
        if fraction <= limit and (best is None or fraction < best[0]):
            best = (fraction, error, rvec, tvec)
    if best is None:
        return None
    fraction, error, rvec, _tvec = best
    rotation, _ = cv2.Rodrigues(rvec)
    yaw = math.degrees(math.atan2(-float(rotation[2, 0]),
                                  math.hypot(float(rotation[0, 0]), float(rotation[1, 0]))))
    pitch = math.degrees(math.atan2(float(rotation[2, 1]), float(rotation[2, 2])))
    roll = math.degrees(math.atan2(float(rotation[1, 0]), float(rotation[0, 0])))
    return {"yaw": round(yaw, 2), "pitch": round(pitch, 2), "roll": round(roll, 2),
            "reproj_px": round(error, 3), "reproj_frac": round(fraction, 4)}
