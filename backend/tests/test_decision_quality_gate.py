"""Synthetic fixtures verify refusal of invalid measurements, not accuracy."""

from dataclasses import replace
import math

import numpy as np
import pytest

from backend.behavior_metrics import BehaviorTracker
from backend.config import load_config
from backend.decision import DecisionEngine
from backend.face_landmark import StubLandmarker
from backend.run_pipeline import build_frame, update_rppg
from backend.vital import GreenRppg


CFG = load_config()
GOOD_Q = {"overall": 1.0, "light_score": 1.0, "motion_score": 0.0}
# A deliberately supplied old estimate: these are test inputs, not measured BPM.
OLD_VITAL = {"hr_bpm": 72.0, "hr_conf": 0.9, "rr_per_min": None, "rr_conf": None}


def observation():
    obs = StubLandmarker(width=64, height=64, pattern="still").detect(None, 0)
    return replace(obs, visible=1.0, bbox=(0, 0, 64, 64),
                   forehead=(16, 4, 48, 20), pose={"yaw": 0.0, "pitch": 0.0, "roll": 0.0})


def invalid_observation(kind):
    obs = observation()
    if kind == "absent":
        return replace(obs, visible=0.0, bbox=(0, 0, 0, 0), forehead=None)
    if kind == "partial":
        return replace(obs, visible=CFG["face_visible_min"] / 2)
    if kind in ("yaw", "pitch"):
        pose = dict(obs.pose)
        pose[kind] = CFG[f"pose_{kind}_max_deg"] + 1
        return replace(obs, pose=pose)
    return obs


@pytest.mark.parametrize("kind", ["absent", "partial", "yaw", "pitch", "quality"])
def test_build_frame_never_releases_old_vitals_for_invalid_face(kind):
    obs = invalid_observation(kind)
    quality = dict(GOOD_Q)
    if kind == "quality":
        quality["overall"] = CFG["quality_min_score"] / 2
    behavior = BehaviorTracker(CFG).update(0, 0.0, obs)
    frame, _ = build_frame(frame_id=0, ts=0.0, obs=obs, quality=quality,
                           behavior=behavior, engine=DecisionEngine(CFG), vital_input=OLD_VITAL)
    assert all(value is None for value in frame["vital"].values())


def test_adjust_posture_recovery_hysteresis_still_masks_vitals():
    engine = DecisionEngine(CFG)
    bad = invalid_observation("yaw")
    behavior = BehaviorTracker(CFG).update(0, 0.0, bad)
    for fid in range(engine.pose_hold_frames):
        build_frame(frame_id=fid, ts=fid / CFG["fps_nominal"], obs=bad, quality=GOOD_Q,
                    behavior=behavior, engine=engine, vital_input=OLD_VITAL)
    frame, _ = build_frame(frame_id=engine.pose_hold_frames, ts=1.0, obs=observation(),
                           quality=GOOD_Q, behavior=behavior, engine=engine, vital_input=OLD_VITAL)
    assert frame["status"] == "adjust_posture"
    assert all(value is None for value in frame["vital"].values())


@pytest.mark.parametrize("kind", ["absent", "partial", "yaw", "pitch", "quality", "empty_roi"])
def test_invalid_segment_clears_full_window_and_fir_history(kind):
    fps = float(CFG["fps_nominal"])
    # Identity FIR isolates window continuity from the frozen filter design.
    rppg = GreenRppg(fps=fps, window_seconds=2, hr_band_hz=CFG["hr_band_hz"],
                     coeffs=[32767], shift=15, fs_tol=CFG["rppg_fs_tol"])
    for fid in range(rppg.need):
        rppg.push(int(10000 * math.sin(2 * math.pi * 2 * fid / fps)), fid, ts=fid / fps)
    assert rppg.estimate()["hr_bpm"] is not None
    obs = invalid_observation(kind)
    if kind == "empty_roi":
        obs = replace(obs, forehead=(0, 0, 0, 0))
    quality = dict(GOOD_Q)
    if kind == "quality":
        quality["overall"] = CFG["quality_min_score"] / 2
    engine = DecisionEngine(CFG)
    image = np.full((64, 64, 3), 128, dtype=np.uint8)
    result = update_rppg(rppg, engine, obs=obs, quality=quality, image=image,
                         frame_id=rppg.need, ts=rppg.need / fps, fallback=(64, 64))
    assert all(value is None for value in result.values())
    assert rppg.samples == 0
    assert rppg._hist is None
    recovered = update_rppg(rppg, engine, obs=observation(), quality=GOOD_Q, image=image,
                            frame_id=rppg.need + 1, ts=(rppg.need + 1) / fps, fallback=(64, 64))
    assert rppg.samples == 1
    assert all(value is None for value in recovered.values())
    assert rppg.last_reject == "window_not_full"
    assert list(rppg._ts) == [(rppg.need + 1) / fps]


def test_normal_and_fatigue_can_still_release_valid_estimates():
    obs = observation()
    behavior = BehaviorTracker(CFG).update(0, 0.0, obs)
    for perclos, expected in [(0.0, "normal"), (1.0, "fatigue_risk")]:
        frame, _ = build_frame(frame_id=0, ts=0.0, obs=obs, quality=GOOD_Q,
                               behavior={**behavior, "perclos": perclos},
                               engine=DecisionEngine(CFG), vital_input=OLD_VITAL)
        assert frame["status"] == expected
        assert frame["vital"] == OLD_VITAL


def test_disconnected_terminal_frame_cannot_retain_vitals():
    obs = observation()
    result = DecisionEngine(CFG).gate_vitals(
        GOOD_Q["overall"], OLD_VITAL, status="disconnected",
        face={"visible": obs.visible, "pose": obs.pose})
    assert all(value is None for value in result.values())


@pytest.mark.parametrize("light,motion,visible,label,value", [
    (0.1, 0.0, 1.0, "光照", 0.1),
    (0.8, 0.95, 1.0, "运动", 0.95),
    (0.9, 0.0, 0.1, "人脸可见率", 0.1),
])
def test_weakest_quality_component_uses_consistent_direction(light, motion, visible, label, value):
    obs = observation()
    result = DecisionEngine(CFG).decide(
        frame_id=0, behavior={},
        quality={"overall": 0.0, "light_score": light, "motion_score": motion},
        face={"visible": visible, "pose": obs.pose})
    assert f"最弱项：{label} {value:.2f}" in result["reason"]
