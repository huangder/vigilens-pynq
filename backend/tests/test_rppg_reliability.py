"""Known synthetic signals exercise A-scheme behavior, not human HR accuracy."""
import math

import numpy as np
import pytest

from backend.config import load_config
from backend.vital import GreenRppg
from backend.decision import DecisionEngine
from backend.run_pipeline import update_rppg
from backend.tests.test_decision_quality_gate import observation, invalid_observation, GOOD_Q


CFG = load_config()


def test_physical_sine_on_uneven_timestamps_is_regridded_before_fir():
    est = GreenRppg.from_config(CFG)
    n, fps = est.need, est.fps
    dt = np.r_[np.full(n // 2, .8 / fps), np.full(n - 1 - n // 2, 1.2 / fps)]
    times = np.r_[0., np.cumsum(dt)]
    for fid, ts in enumerate(times):
        est.push(round(3000 * math.sin(2 * math.pi * 1.2 * ts)), fid, ts=float(ts))
    result = est.estimate()
    assert result["hr_bpm"] == pytest.approx(72, abs=2)
    assert est.last_reject is None
    assert est.samples == n  # Derived grid points never inflate camera count.
    assert est.stats()["resample_calls"] == 1
    assert est.last_uniform_samples > 0


@pytest.mark.parametrize("amplitude", [1000, 2000, 3000])
def test_strong_second_harmonic_is_refused_instead_of_reported_as_double_rate(amplitude):
    est = GreenRppg.from_config(CFG)
    for fid in range(est.need):
        t = fid / est.fps
        value = 1000 * math.sin(2 * math.pi * 1.2 * t) + amplitude * math.sin(2 * math.pi * 2.4 * t)
        est.push(round(value), fid, ts=t)
    assert est.estimate()["hr_bpm"] is None
    assert est.last_reject == "harmonic_ambiguity"
    assert est.last_harmonic_ratio >= CFG["rppg_harmonic_ratio_min"]


def test_single_long_gap_cannot_hide_behind_a_good_p95_or_average_rate():
    est = GreenRppg.from_config(CFG)
    for fid in range(est.need):
        ts = fid / est.fps + (2 * est.max_gap_s if fid > est.need // 2 else 0)
        est.push(round(3000 * math.sin(2 * math.pi * 1.2 * ts)), fid, ts=ts)
    assert est.estimate()["hr_bpm"] is None
    assert est.last_reject == "sampling_gap"
    assert est.last_uniform_samples == 0


def prepared_recovery_estimator():
    est = GreenRppg(fps=CFG["fps_nominal"], window_seconds=2, hr_band_hz=CFG["hr_band_hz"],
                    coeffs=[32767], shift=15, recovery_ms=CFG["rppg_recovery_ms"])
    for fid in range(est.need):
        ts = fid / est.fps
        est.push(round(10000 * math.sin(2 * math.pi * 2 * ts)), fid, ts=ts)
    assert est.estimate()["hr_bpm"] is not None
    return est


def test_short_soft_failure_masks_result_and_requires_new_real_samples():
    est = prepared_recovery_estimator()
    engine = DecisionEngine(CFG)
    image = np.full((64, 64, 3), 128, dtype=np.uint8)
    fid = est.need
    blocked = update_rppg(est, engine, obs=invalid_observation("yaw"), quality=GOOD_Q,
                          image=image, frame_id=fid, ts=fid / est.fps, fallback=(64, 64))
    assert all(v is None for v in blocked.values())
    assert est.samples == est.need
    assert est.last_reject == "pose_invalid"
    for offset in range(1, CFG["rppg_recovery_samples"] + 1):
        result = update_rppg(est, engine, obs=observation(), quality=GOOD_Q, image=image,
                             frame_id=fid + offset, ts=(fid + offset) / est.fps, fallback=(64, 64))
        if offset < CFG["rppg_recovery_samples"]:
            assert result["hr_bpm"] is None
            assert est.last_reject == "recovering"
    assert result["hr_bpm"] is not None
    assert est.total_samples == est.need + CFG["rppg_recovery_samples"]
    assert est.stats()["soft_pause_frames"] == 1


@pytest.mark.parametrize("kind", ["absent", "partial", "empty_roi"])
def test_real_face_loss_or_empty_roi_never_uses_soft_recovery(kind):
    from dataclasses import replace
    est = prepared_recovery_estimator()
    obs = replace(observation(), forehead=(0, 0, 0, 0)) if kind == "empty_roi" else invalid_observation(kind)
    result = update_rppg(est, DecisionEngine(CFG), obs=obs, quality=GOOD_Q,
                         image=np.full((64, 64, 3), 128, dtype=np.uint8),
                         frame_id=est.need, ts=est.need / est.fps, fallback=(64, 64))
    assert all(v is None for v in result.values())
    assert est.samples == 0 and est._hist is None and not est._raw


def test_persistent_soft_failure_and_late_recovery_clear_history():
    est = prepared_recovery_estimator()
    t = est._ts[-1]
    est.invalidate("quality_invalid", t + est.recovery_s / 2, allow_recovery=True)
    assert est.samples == est.need
    est.push(0, est.need, ts=t + 2 * est.recovery_s)
    assert est.samples == 1
    assert est.estimate()["hr_bpm"] is None
    assert est.stats()["reset_counts"]["recovery_timeout"] == 1


def test_diagnostic_totals_survive_window_reset_without_stale_last_reason():
    est = GreenRppg.from_config(CFG)
    est.push(0, 0, ts=0)
    est.estimate()
    est.invalidate("face_invalid", 1 / est.fps)
    est.estimate()
    est.push(0, 2, ts=2 / est.fps)
    est.estimate()
    stats = est.stats()
    assert stats["estimate_calls"] == 3
    assert stats["reject_counts"]["window_not_full"] == 2
    assert stats["reject_counts"]["face_invalid"] == 1
    assert stats["reset_counts"]["face_invalid"] == 1
    assert est.last_reject == "window_not_full"


def test_fir_warmup_history_is_retained_by_time_not_local_sample_count():
    from backend.vital import fir_process
    est = GreenRppg.from_config(CFG)
    times = np.r_[np.arange(121) / 60, 2 + np.arange(1, est.need + 1) / est.fps]
    values = np.asarray([round(20000 + 1000 * math.sin(2 * math.pi * 1.2 * t)) for t in times])
    for fid, (ts, value) in enumerate(zip(times, values)):
        est.push(int(value), fid, ts=float(ts))
    count = int(np.floor(times[-1] * est.fps)) + 1
    grid = times[-1] - np.arange(count - 1, -1, -1) / est.fps
    interpolated = np.interp(grid, times, values)
    quantized = np.floor(interpolated + .5).astype(np.int64)  # All inputs positive in this fixture.
    expected, _, _ = fir_process(quantized, est.coeffs, est.shift)
    actual = est._uniform_filtered(est.samples)
    assert list(actual) == expected[-len(actual):]


def test_pathological_prefix_has_bounded_storage_and_refuses_incomplete_warmup():
    est = GreenRppg.from_config(CFG)
    prefix = np.arange(3 * est.need) / 10000
    times = np.r_[prefix, 1 + np.arange(1, est.need + 1) / est.fps]
    for fid, ts in enumerate(times):
        est.push(round(3000 * math.sin(2 * math.pi * 1.2 * ts)), fid, ts=float(ts))
    assert len(est._raw) <= est._raw_limit
    assert est.estimate()["hr_bpm"] is None
    assert est.last_reject == "warmup_history_unavailable"
    last = times[-1]
    for offset in range(1, est.need + 1):
        ts = last + offset / est.fps
        est.push(round(3000 * math.sin(2 * math.pi * 1.2 * ts)), len(times) + offset, ts=ts)
    assert est.estimate()["hr_bpm"] == pytest.approx(72, abs=2)
