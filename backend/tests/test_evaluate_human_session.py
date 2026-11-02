"""Evaluation integrity tests with synthetic inputs, never accuracy evidence."""
import json

import pytest

from backend.config import load_config
from metrics.scripts import evaluate_human_session as ev
from metrics.scripts import human_session as human


def observation(ts, frame_id):
    # Minimal observations for metric functions, not a contract-frame fixture.
    return {"ts": ts, "frame_id": frame_id, "face": {"visible": 1},
            "behavior": {"ear_left": .3, "ear_right": .3, "mar": 0,
                         "blink_count": 0, "long_close_count": 0, "yawn_count": 0, "perclos": 0}}


def test_overlap_matching_is_one_to_one_and_order_independent():
    # A long prediction can match either event. The short one can only match
    # the first; greedily taking the first event would incorrectly report a FN.
    result = ev.match_events([(1, 10), (1, 2)], [(1, 2), (9, 10)])
    assert (result["tp"], result["fp"], result["fn"]) == (2, 0, 0)
    result = ev.match_events([(0, 20)], [(1, 2), (9, 10)])
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 1)


def test_all_closed_prediction_is_penalized_by_frame_score():
    result = ev.frame_scores([True] * 100, [True] + [False] * 99, [False] * 100)
    assert (result["tp"], result["fp"], result["fn"]) == (1, 99, 0)
    assert result["f1"] < .02
    assert ev.prf(0, 0, 0)["f1"] is None


def test_uncertain_frames_are_not_used_as_negatives():
    result = ev.frame_scores([True, True, False], [False, False, True], [True, False, False])
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 1)
    assert result["evaluated_frames"] == 2


def test_event_scores_follow_production_counters_instead_of_threshold_hits():
    cfg = load_config()
    frames = [observation(ts=i / cfg["fps_nominal"], frame_id=i) for i in range(6)]
    for i, frame in enumerate(frames):
        frame["face"]["visible"] = 1
        frame["behavior"]["ear_left"] = frame["behavior"]["ear_right"] = (
            cfg["ear_close_threshold"] / 2 if 1 <= i <= 3 else cfg["ear_close_threshold"] * 2)
        frame["behavior"]["blink_count"] = int(i >= 4)
    preds = ev.predicted_events(frames, cfg)
    assert preds["blink"] == [(1, 3)]
    assert preds["long_close"] == []
    frames[-1]["behavior"]["blink_count"] = 0
    with pytest.raises(ValueError, match="counter reset"):
        ev.predicted_events(frames, cfg)


def test_perclos_uses_real_window_cutoff_and_skips_uncertain_windows():
    frames = [observation(ts=t, frame_id=i) for i, t in enumerate([0, .25, .75, 1.25])]
    labels = [{"start_frame": 0, "end_frame": 1, "event": "blink"}]
    for frame, expected in zip(frames, [1, 1, 2 / 3, 1 / 3]):
        frame["behavior"]["perclos"] = expected
    score = ev.perclos_error(frames, labels, [0, .25, .75, 1.25], 1)
    assert score["mean_absolute_error"] == 0
    labels.append({"start_frame": 1, "end_frame": 1, "event": "ignore"})
    assert ev.perclos_error(frames, labels, [0, .25, .75, 1.25], 1)["evaluated_frames"] == 1


def test_original_packet_replay_cannot_certify_accuracy_without_human_labels(monkeypatch, workdir):
    from backend.tests.test_human_session import FragmentedSocket, sample_stream
    data, _, _ = sample_stream()
    capture = workdir / "capture"
    capture.mkdir()
    with monkeypatch.context() as local:
        local.setattr(human.socket, "create_connection", lambda *a, **k: FragmentedSocket(data))
        human.Session(capture, "board:127.0.0.1:9000", None).capture()
    run = workdir / "replay"
    assert human.replay(capture, run) == 0
    result = ev.evaluate(capture, run)
    assert result["frames"] == 3
    assert result["human_annotation_status"] == "missing"
    assert result["acceptance_verdict"] == "INCOMPLETE_NO_HUMAN_LABELS"
    automated = ev.evaluate(capture, run, skip_human_labels=True)
    assert automated["human_annotation_status"] == "omitted_by_user"
    assert automated["automatic_checks"]["pass"] is True
    assert automated["accuracy"] is None
    assert automated["full_acceptance_complete"] is False
    recorded = [json.loads(line) for line in (run / "stream.jsonl").read_text(encoding="utf-8").splitlines()]
    observations = [f for f in recorded if f["status"] != "done"]
    index = [json.loads(line) for line in (capture / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    # These black synthetic frames have no face: values must be gated, and a
    # forged closed-eye fraction cannot pass just because JSON is well formed.
    observations[0]["vital"]["hr_bpm"] = 72
    observations[0]["behavior"]["perclos"] = .5
    check = ev.automatic_checks(observations, index, load_config())
    assert check["pass"] is False
    assert check["checks"]["vital_gate"]["violation_count"] == 1
    assert check["checks"]["perclos_window_consistency"]["violation_count"] == 1
    # Relabeling a changed file without updating human review cannot pass.
    (capture / "human_review.json").write_text(json.dumps({
        "reviewer": "synthetic-test", "reviewed_all": True,
        "annotation_source": "human_manual", "wire_sha256": result["wire_sha256"],
        "annotations_sha256": "tampered"}), encoding="utf-8")
    (capture / "annotations.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprints changed"):
        ev.evaluate(capture, run)


def test_coarse_counts_do_not_turn_into_frame_accuracy_or_fabricated_zero(workdir):
    frames = [observation(i / 30, i) for i in range(3)]
    frames[2]["behavior"]["blink_count"] = 1
    for frame in frames:
        frame.update(status="normal", quality={"overall": .8})
    rows = [{"id": 0, "title": "synthetic", "start_frame": 0, "end_frame": 2,
             "confirmed": True, "counts": {"blink": 2, "long_close": None, "yawn": 0},
             "execution": "partial"}]
    human.dump(workdir / "quick_counts.json", rows)
    human.dump(workdir / "quick_human_review.json", {
        "reviewer": "synthetic fixture", "reviewed_all": True,
        "annotation_source": "human_segment_counts", "frame_level_ground_truth": False,
        "wire_sha256": "synthetic", "counts_sha256": human.sha256(workdir / "quick_counts.json")})
    result = ev.evaluate_quick(workdir, frames, {"wire_sha256": "synthetic"})
    assert result["count_totals"]["blink"]["algorithm_minus_human_on_known_segments"] == -1
    assert result["count_totals"]["long_close"]["unknown_segments"] == 1
    assert result["frame_level_accuracy"] is None
    assert result["perclos_error"] is None
    assert result["full_acceptance_complete"] is False
