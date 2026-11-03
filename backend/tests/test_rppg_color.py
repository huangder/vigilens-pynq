"""Synthetic engineering signals are not human heart-rate ground truth."""
import numpy as np
import pytest
from pathlib import Path

from backend.config import load_config
from backend.rppg_color import PosRppg, make_rppg, pos_q15, pos_waveform
from backend.vital import GreenRppg, q15_from_roi_sum, q15_of_mean, fir_process
from backend.decision import DecisionEngine
from backend.run_pipeline import update_rppg
from backend.tests.test_decision_quality_gate import observation, GOOD_Q

CFG = {**load_config(), "rppg_method": "pos"}


def colors(times, illumination=True):
    t = np.asarray(times)
    pulse = np.sin(2 * np.pi * 1.2 * t)
    light = 1 + .2 * np.sin(2 * np.pi * 2 * t) if illumination else np.ones_like(t)
    return np.array([120., 90., 70.]) * light[:, None] * (1 + pulse[:, None] * [.002, .01, .001])


def feed(est, times, *, timestamps=True):
    for fid, (ts, rgb) in enumerate(zip(times, colors(times))):
        sums = np.rint(rgb * 1_000_000).astype(np.int64)
        if isinstance(est, PosRppg):
            est.push_rgb(sums, 1_000_000, fid, ts=float(ts) if timestamps else None)
        else:
            est.push(q15_from_roi_sum(sums[1], 1_000_000), fid, ts=float(ts) if timestamps else None)
    return est.estimate()


def test_common_illumination_is_reduced_without_lowering_confidence_guard():
    # Preserve the original 150-sample fixture and its strict 2 bpm assertions.
    pos = make_rppg({**CFG, "rppg_method": "pos", "rppg_window_seconds": 5})
    green = make_rppg({**CFG, "rppg_method": "green", "rppg_window_seconds": 5})
    times = np.arange(pos.need) / pos.fps
    result, baseline = feed(pos, times), feed(green, times)
    assert result["hr_bpm"] == pytest.approx(72, abs=2)
    assert result["hr_conf"] > .5
    assert baseline["hr_bpm"] == pytest.approx(120, abs=2)
    assert pos.stats()["method"] == "pos"


def test_hundred_sample_candidate_exposes_coarser_illumination_frequency_grid():
    pos=make_rppg({**CFG,"rppg_method":"pos","rppg_window_seconds":100/30})
    green=make_rppg({**CFG,"rppg_method":"green","rppg_window_seconds":100/30})
    assert pos.need == green.need == 100
    times=np.arange(pos.need)/pos.fps
    result,baseline=feed(pos,times),feed(green,times)
    assert result['hr_bpm'] == pytest.approx(72,abs=2)
    assert result['hr_conf'] > .5
    # 120 bpm lies between the 108 and 126 bpm bins; no invented precision.
    assert baseline['hr_bpm'] == pytest.approx(126,abs=1e-9)


@pytest.mark.parametrize("timestamps", [True, False])
def test_valid_rgb_pulse_works_with_capture_time_or_contiguous_file_frames(timestamps):
    est = make_rppg(CFG)
    result = feed(est, np.arange(est.need) / est.fps, timestamps=timestamps)
    assert result["hr_bpm"] == pytest.approx(72, abs=2)
    assert est.samples == est.need


def test_uneven_time_is_interpolated_in_rgb_before_pos():
    est = make_rppg(CFG)
    dt = np.r_[np.full(est.need // 2, .8 / est.fps),
               np.full(est.need - 1 - est.need // 2, 1.2 / est.fps)]
    result = feed(est, np.r_[0., np.cumsum(dt)])
    assert result["hr_bpm"] == pytest.approx(72, abs=2)
    assert est.last_spectral_fs == est.fps
    assert est.last_uniform_samples <= est.need


def test_q15_mapping_matches_frozen_scalar_step_and_refuses_range_violation():
    pulse = np.linspace(-.75, .75, 1001)
    actual = pos_q15(pulse)
    expected = [q15_of_mean(128 * (1 + float(p))) for p in pulse]
    assert actual.tolist() == expected
    for bad in ([np.nan], [np.inf], [1.], [-1.1]):
        with pytest.raises(ValueError):
            pos_q15(bad)


def test_projection_matches_independent_explicit_window_reference_including_edges():
    rgb = colors(np.arange(91) / 30)
    length = 48
    expected, weight = np.zeros(len(rgb)), np.zeros(len(rgb))
    for start in range(len(rgb) - length + 1):
        block = rgb[start:start + length]
        r, g, b = (block / block.mean(axis=0)).T
        u, v = g - b, g + b - 2 * r
        pulse = u + u.std() / v.std() * v
        expected[start:start + length] += pulse - pulse.mean()
        weight[start:start + length] += 1
    actual = pos_waveform(rgb, length)
    np.testing.assert_allclose(actual, expected / weight, atol=1e-14, rtol=1e-12)
    assert actual[-1] != 0  # No accidentally unprocessed final input sample.


@pytest.mark.parametrize("rgb", [np.zeros((900, 3)), np.tile([120, 90, 70], (900, 1))])
def test_singular_or_dark_rgb_never_produces_heart_rate(rgb):
    est = make_rppg(CFG)
    for fid, row in enumerate(rgb):
        est.push_rgb(row, 1, fid, fid / est.fps)
    assert est.estimate()["hr_bpm"] is None
    assert est.last_reject in {"color_projection_invalid", "no_band_energy"}


def test_monochromatic_motion_has_no_synthetic_pulse_from_overlap_edges():
    est = make_rppg(CFG)
    for fid in range(est.need):
        level = int(90 + 15 * np.sin(2 * np.pi * 1.5 * fid / est.fps))
        est.push_rgb([level, level, level], 1, fid, fid / est.fps)
    assert est.estimate()["hr_bpm"] is None


def test_pipeline_explicitly_converts_bgr_roi_sums_to_rgb():
    est = make_rppg(CFG)
    obs = observation()
    image = np.empty((480, 640, 3), dtype=np.uint8)
    image[:] = (31, 77, 143)
    update_rppg(est, DecisionEngine(CFG), obs=obs, quality=GOOD_Q,
                image=image, frame_id=0, ts=0., fallback=(640, 480))
    assert list(est._rgb) == [(143., 77., 31.)]


def test_face_loss_clears_all_color_history_and_quality_pause_never_reuses_a_rate():
    est = make_rppg(CFG)
    assert feed(est, np.arange(est.need) / est.fps)["hr_bpm"] is not None
    last = est.last_sample_ts
    est.invalidate("quality_invalid", last + 1 / est.fps, allow_recovery=True)
    assert est.estimate()["hr_bpm"] is None
    for i in range(3):
        t = last + (i + 2) / est.fps
        sums = np.rint(colors([t])[0] * 1_000_000).astype(np.int64)
        est.push_rgb(sums, 1_000_000, est.need + i + 1, t)
        if i < 2:
            assert est.estimate()["hr_bpm"] is None
    assert len(est._rgb) == len(est._raw)
    est.invalidate("face_invalid", last + 1)
    assert est.samples == len(est._rgb) == len(est._raw) == 0
    assert est.estimate()["hr_bpm"] is None


def test_color_history_is_bounded_and_missing_warmup_is_refused():
    est = make_rppg(CFG)
    times = np.arange(4 * est.need) / (4 * est.fps)
    feed(est, times)
    assert len(est._raw) == len(est._rgb) <= est._raw_limit
    assert est.estimate()["hr_bpm"] is None
    assert est.last_reject == "fs_mismatch"


def test_unknown_method_and_scalar_substitution_fail_explicitly():
    with pytest.raises(ValueError):
        make_rppg({**CFG, "rppg_method": "unknown"})
    with pytest.raises(ValueError):
        make_rppg(CFG).push(123, 0)


def test_color_warmup_matches_full_history_pos_and_fir():
    est = make_rppg(CFG)
    n = 1600
    times = np.arange(n) / est.fps
    rgb = colors(times)
    for fid, row in enumerate(rgb):
        est.push_rgb(row * 1_000_000, 1_000_000, fid, times[fid])
    actual = est._uniform_filtered(est.need)
    # The last local window is identical; early overlap/FIR boundaries must
    # have settled before the measured 900-sample range begins.
    pulse = pos_waveform(rgb, est.color_window_samples)
    reference, _, _ = fir_process(pos_q15(pulse), est.coeffs, est.shift)
    np.testing.assert_allclose(actual, reference[-len(actual):], atol=0, rtol=0)


def test_stored_independent_pos_q15_fir_golden_sequence():
    path = Path(__file__).resolve().parents[2] / "data/golden/rppg_pos_synthetic.csv"
    reference = np.genfromtxt(path, delimiter=",", names=True)
    rgb = np.column_stack([reference[f"mean_{channel}"] for channel in "rgb"])
    waveform = pos_waveform(rgb, 48)
    np.testing.assert_allclose(waveform, reference["pos"], atol=1e-14, rtol=1e-12)
    quantized = pos_q15(waveform)
    np.testing.assert_array_equal(quantized, reference["q15"])
    estimator = make_rppg(CFG)
    filtered, saturated, _ = fir_process(quantized, estimator.coeffs, estimator.shift)
    assert saturated == 0
    np.testing.assert_array_equal(filtered, reference["fir"])


def test_cli_pos_override_is_visible_and_does_not_change_default_config(tmp_path):
    import json
    from backend.run_pipeline import main
    default = load_config()["rppg_method"]
    code = main(["--source", "synthetic", "--seconds", "1", "--stub", "--quiet", "--rppg-method", "pos",
                 "--json", str(tmp_path / "last.json"), "--jsonl", str(tmp_path / "stream.jsonl"),
                 "--csv", str(tmp_path / "metrics.csv"), "--summary", str(tmp_path / "summary.json")])
    assert code == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["rppg_method"] == summary["rppg_diagnostics"]["method"] == "pos"
    assert load_config()["rppg_method"] == default
