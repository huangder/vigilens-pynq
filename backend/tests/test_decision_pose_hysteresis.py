"""姿态越界迟滞（BUG-009 成因 B）。

真实素材 `data/raw/blink.mp4` 实测：`pose.yaw` 相邻跳变 >10° 有 44 次、正负翻号 19 次，
旧实现逐帧判决 ⇒ 网页状态在 adjust_posture / normal 之间来回跳（19 次迁移）。
这里把"连续 N 帧才翻、连续 N 帧才复位"的语义钉死，阈值来自 `config.yaml` 的 `pose_hold_frames`。
"""

from __future__ import annotations

import pytest

from backend.config import load_config
from backend.decision import DecisionEngine

CFG = load_config()
GOOD_Q = {"overall": 0.9, "light_score": 0.9, "motion_score": 0.05}
CALM = {"perclos": 0.05, "long_close_count": 0, "yawn_count": 0, "blink_rate_per_min": 18.0}


def _face(yaw: float) -> dict:
    return {"visible": 0.98, "pose": {"yaw": yaw, "pitch": 1.0, "roll": 0.0}}


IN_RANGE, OUT_OF_RANGE = _face(-2.0), _face(40.0)


def _engine(hold: int = 3) -> DecisionEngine:
    return DecisionEngine({**CFG, "pose_hold_frames": hold, "face_visible_min": 0.7})


def _run(eng: DecisionEngine, faces: list[dict]) -> list[str]:
    return [eng.decide(frame_id=i, behavior=CALM, quality=GOOD_Q, face=f)["status"]
            for i, f in enumerate(faces)]


def test_single_frame_excursion_never_flips_status() -> None:
    """单帧抖到 40° 又回来：不能把网页刷成 adjust_posture。"""
    eng = _engine(3)
    statuses = _run(eng, [IN_RANGE, OUT_OF_RANGE, IN_RANGE, IN_RANGE, IN_RANGE])
    assert statuses == ["normal"] * 5


def test_three_consecutive_out_frames_flag_adjust_posture() -> None:
    eng = _engine(3)
    statuses = _run(eng, [OUT_OF_RANGE, OUT_OF_RANGE, OUT_OF_RANGE])
    assert statuses[:2] == ["normal", "normal"]
    assert statuses[2] == "adjust_posture"


def test_clearing_needs_the_same_confirmation_window() -> None:
    """越界解除也要连续 N 帧，避免"回到范围内一帧又走"造成反向横跳。"""
    eng = _engine(3)
    faces = [OUT_OF_RANGE] * 3 + [IN_RANGE] * 3
    statuses = _run(eng, faces)
    assert statuses[3:5] == ["adjust_posture", "adjust_posture"], \
        f"第 4/5 帧仍在确认窗内，应保持 adjust_posture，实际 {statuses}"
    assert statuses[5] == "normal"


def test_hold_frames_one_restores_immediate_judgement() -> None:
    eng = _engine(1)
    assert _run(eng, [OUT_OF_RANGE]) == ["adjust_posture"]
    assert _run(eng, [IN_RANGE]) == ["normal"]


def test_reason_and_trigger_explain_the_streak() -> None:
    eng = _engine(3)
    _run(eng, [OUT_OF_RANGE] * 3)
    r = eng.decide(frame_id=9, behavior=CALM, quality=GOOD_Q, face=OUT_OF_RANGE)
    trig = [t for t in r["triggers"] if t["rule"] == "pose_range"]
    assert trig and trig[0]["metric"] == "face.pose.yaw"
    assert trig[0]["value"] == pytest.approx(40.0)
    assert trig[0]["threshold"] == pytest.approx(CFG["pose_yaw_max_deg"])
    assert "连续" in r["reason"] and "调整姿势" in r["advice"]
