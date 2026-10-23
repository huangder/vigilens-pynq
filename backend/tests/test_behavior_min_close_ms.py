"""闭眼判定改成**时间口径**（`min_close_ms`）的回归测试 —— 对应 `docs/37` 风险表 R8。

为什么要有这个文件：
  旧口径是"连续 N 帧低于阈值"（`min_close_frames: 3`）。这个口径在 30 fps 下是 100 ms，
  但契约 §0 的帧率本来就要变（v1.1 45 → v1.2 草案 30 → v1.6 草案 60）——
  一旦跑到 60 fps，"3 帧"就只剩 50 ms，眨眼判定会**静默变严**、PERCLOS 随之偏低，
  也就是"疲劳漏报"。所以阈值必须写成时间，且必须有一条测试钉住"帧率改了，结果不改"。

测试用的合成观测不放真图像：EAR 由 `face_landmark.ear()` 的定义反解出坐标
（EAR = (|p2-p6| + |p3-p5|) / (2*|p1-p4|)），MAR 给空嘴 = 0。
"""

from __future__ import annotations

import math

import pytest

from backend.behavior_metrics import BehaviorTracker


CFG = {
    "ear_close_threshold": 0.21,
    "min_close_ms": 100,
    "min_close_frames": 3,      # 兼容别名：有 min_close_ms 时不该生效
    "fps_nominal": 30,
    "window_seconds": 30,
    "long_close_ms": 500,
    "mar_threshold": 0.6,
    "yawn_min_duration_ms": 800,
}

OPEN_EAR = 0.30
CLOSED_EAR = 0.05


def _eye(ear_value: float) -> dict[str, tuple[float, float]]:
    """反解出一只眼睛的 6 个点，使 `ear()` 恰好返回 `ear_value`。"""
    h = ear_value / 2.0      # (2h + 2h) / 2 = 2h
    return {
        "p1": (0.0, 0.0),
        "p4": (1.0, 0.0),
        "p2": (0.25, h),
        "p6": (0.25, -h),
        "p3": (0.75, h),
        "p5": (0.75, -h),
    }


class _Obs:
    """最小观测对象：只需要 BehaviorTracker 用到的三个属性。"""

    def __init__(self, *, closed: bool = False, visible: float = 1.0) -> None:
        v = CLOSED_EAR if closed else OPEN_EAR
        self.eyes = {"left": _eye(v), "right": _eye(v)}
        self.visible = visible
        self.mouth: dict[str, tuple[float, float]] = {}


def _run(fps: float, seconds: float, closed_at) -> tuple[BehaviorTracker, list[dict]]:
    """按 `fps` 逐帧推进；`closed_at(t)` 返回 True/False，返回 None 表示这一帧没有人脸。"""
    trk = BehaviorTracker(cfg=dict(CFG))
    frames: list[dict] = []
    n = int(round(seconds * fps))
    for k in range(n + 1):
        t = k / fps
        flag = closed_at(t)
        if flag is None:
            obs = _Obs(visible=0.0)
        else:
            obs = _Obs(closed=flag)
        frames.append(trk.update(k, t, obs))
    return trk, frames


def _blink(t0: float, t1: float):
    """在 [t0, t1] 内闭眼，其余睁眼。"""
    return lambda t: t0 <= t <= t1


def test_ear_helper_is_sane() -> None:
    """先证明合成观测真的落在阈值两侧，否则后面的测试是在测空气。"""
    from backend.face_landmark import ear

    assert ear(_eye(OPEN_EAR)) == pytest.approx(OPEN_EAR, abs=1e-9)
    assert ear(_eye(CLOSED_EAR)) == pytest.approx(CLOSED_EAR, abs=1e-9)
    assert CLOSED_EAR < CFG["ear_close_threshold"] < OPEN_EAR


@pytest.mark.parametrize("fps", [30.0, 60.0])
def test_one_blink_counted_at_any_frame_rate(fps: float) -> None:
    """同一次 150 ms 的眨眼，30 fps 与 60 fps 都必须只记 1 次。"""
    trk, _ = _run(fps, 3.0, _blink(1.0, 1.15))
    assert trk.blink_count == 1
    assert trk.long_close_count == 0


def test_short_closure_below_threshold_is_not_a_blink() -> None:
    """60 ms 的闭眼（60 fps 下正好 4 帧）**不算**眨眼。

    这条是 R8 的回归钉：旧口径"连续 3 帧"在 60 fps 下会把它记成一次眨眼
    （3 帧 = 50 ms），而生理上 60 ms 的闭合不该被当成眨眼。
    """
    trk, _ = _run(60.0, 3.0, _blink(1.0, 1.05))
    assert trk.blink_count == 0
    assert trk.long_close_count == 0


@pytest.mark.parametrize("fps", [30.0, 60.0])
def test_long_closure_counted_once(fps: float) -> None:
    """700 ms 的闭眼记"长闭眼"，不记眨眼。"""
    trk, _ = _run(fps, 3.0, _blink(1.0, 1.70))
    assert trk.long_close_count == 1
    assert trk.blink_count == 0


def test_legacy_frames_config_is_converted_by_nominal_fps() -> None:
    """只给旧键 `min_close_frames` 时按 `fps_nominal` 折算成毫秒（老配置不会炸）。"""
    cfg = {k: v for k, v in CFG.items() if k != "min_close_ms"}
    trk = BehaviorTracker(cfg=cfg)
    assert trk.min_close_ms == pytest.approx(100.0)   # 3 帧 / 30 fps

    trk2, _ = _run(30.0, 3.0, _blink(1.0, 1.15))
    assert trk2.blink_count == 1


def test_missing_face_does_not_fabricate_a_long_close() -> None:
    """人脸丢了 0.5 s 再回来，不能把"看不见"算成一次长闭眼。"""

    def pattern(t: float):
        if 1.0 <= t <= 1.06:
            return True
        if 1.06 < t < 1.6:
            return None          # 这一帧没有人脸
        if 1.6 <= t <= 1.66:
            return True
        return False

    trk, _ = _run(30.0, 3.0, pattern)
    assert trk.long_close_count == 0
    assert trk.blink_count == 0


@pytest.mark.parametrize("fps", [30.0, 60.0])
def test_perclos_is_frame_rate_independent(fps: float) -> None:
    """同一段"闭合占 1/6 时长"的序列，PERCLOS 在两种帧率下应基本一致。"""
    trk, frames = _run(fps, 3.0, _blink(1.0, 1.5))
    perclos = frames[-1]["perclos"]
    assert perclos == pytest.approx(0.5 / 3.0, abs=0.02)


def test_state_returns_to_open_after_threshold() -> None:
    trk, frames = _run(30.0, 3.0, _blink(1.0, 1.2))
    assert frames[-1]["blink_state"] == "OPEN"
    assert not math.isnan(frames[-1]["perclos"])
