"""Injected timing tests, not evidence of physical camera performance."""
import json
import math

import pytest

from backend.capture import Frame, SyntheticImage
from backend.sampling import LiveSampler
from backend.vital import GreenRppg


def frames(rate, count):
    return (Frame(i, 1000 + i / rate, SyntheticImage(64, 48, i)) for i in range(count))


def test_60_to_30_retains_original_pixels_and_timestamps():
    sampler = LiveSampler(frames(60, 120), fps=30)
    got = list(sampler)
    assert len(got) == 60
    assert [f.frame_id for f in got] == list(range(60))
    assert [f.image.seed for f in got] == list(range(0, 120, 2))
    assert [f.ts for f in got] == pytest.approx([1000 + i / 30 for i in range(60)])
    assert sampler.stats()["dropped_frames"] == 60
    assert sampler.stats()["output_fps"] == pytest.approx(30)


def test_slow_input_is_neither_duplicated_nor_retimed():
    sampler = LiveSampler(frames(10, 11), fps=30)
    got = list(sampler)
    assert len(got) == 11
    assert got[-1].ts - got[0].ts == pytest.approx(1)
    assert sampler.stats()["empty_slots"] == 20
    assert sampler.stats()["output_fps"] == pytest.approx(10)


def test_limit_counts_selected_frames_and_closes_source():
    closed = []

    def source():
        try:
            yield from frames(60, 120)
        finally:
            closed.append(True)

    sampler = LiveSampler(source(), fps=30, limit=31)
    got = list(sampler)
    assert len(got) == 31 and sampler.seen == 61
    assert got[-1].ts - got[0].ts == pytest.approx(1)
    assert closed == [True]


def test_seconds_are_source_time_even_if_processing_is_slow():
    sampler = LiveSampler(frames(10, 100), fps=30, seconds=1)
    assert len(list(sampler)) == 10


@pytest.mark.parametrize("bad", [1000, 999, float("nan"), float("inf")])
def test_invalid_capture_timestamps_are_rejected(bad):
    sampler = LiveSampler([Frame(0, 1000, None), Frame(1, bad, None)], fps=30)
    with pytest.raises(ValueError, match="timestamps"):
        list(sampler)


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf")])
def test_invalid_target_rate_is_rejected(rate):
    with pytest.raises(ValueError, match="fps"):
        LiveSampler([], fps=rate)


def test_live_pipeline_uses_capture_time_for_public_and_behavior_timestamps(monkeypatch, workdir):
    from backend import run_pipeline as pipeline

    monkeypatch.setattr(pipeline, "open_frame_source",
                        lambda *a, **kw: (frames(10, 11), "injected live source"))
    stream, summary = workdir / "stream.jsonl", workdir / "summary.json"
    rc = pipeline.main(["--source", "uvc:0", "--stub", "--rotate", "0", "--quiet",
                        "--json", str(workdir / "last.json"), "--jsonl", str(stream),
                        "--summary", str(summary)])
    assert rc == 0
    measured = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
    assert measured[10]["ts"] == pytest.approx(1.0)  # old fid/30 gave 0.333
    assert measured[-1]["ts"] > measured[-2]["ts"]
    result = json.loads(summary.read_text(encoding="utf-8"))
    assert result["time_base"] == "source_elapsed"
    assert result["sampling"]["output_fps"] == pytest.approx(10)


def test_burst_timing_is_rejected_even_with_nominal_average_rate():
    est = GreenRppg(fps=30, window_seconds=2, hr_band_hz=[0.7, 3], coeffs=[1], shift=0)
    t = 0.0
    for i in range(60):
        if i:
            t += 0.001 if i % 2 else 2 / 30 - 0.001
        est.push(round(3000 * math.sin(i)), i, ts=t)
    assert est.estimate()["hr_bpm"] is None
    assert est.last_reject == "sampling_jitter"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -1.0])
def test_partial_or_invalid_rppg_timestamps_cannot_be_hidden(bad):
    est = GreenRppg(fps=30, window_seconds=2, hr_band_hz=[0.7, 3], coeffs=[1], shift=0)
    est.push(100, 0, ts=0.0)
    est.push(200, 1, ts=bad)
    assert est.estimate()["hr_bpm"] is None
    assert est.last_reject == "invalid_timestamps"


def test_rate_override_cannot_relabel_the_frozen_fir_coefficients():
    est = GreenRppg(fps=60, window_seconds=2, hr_band_hz=[0.7, 3])
    assert est.fir_design_fps == 30
    est.push(100, 0, ts=0)
    est.push(200, 1, ts=1/60)
    assert est.estimate()["hr_bpm"] is None
    assert est.last_reject == "fir_rate_mismatch"
