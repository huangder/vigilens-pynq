"""Synthetic fixtures check cross-log rejection; not physical evidence."""
import copy
import pytest
from backend.config import load_config
from backend.mock import mock_frame
from board.w7_buildroot.check_network_run import inspect_run


def fixture():
    log = """CAMERA_PL_FRAME n=1 seq=0 ts_us=1000000 mismatch=0 roi_g=100 roi_count=921600 motion_valid=0 motion_ratio_q16=123 decode_ms=10 reference_ms=5 transfer_pl_ms=10
CAMERA_PL_FRAME n=2 seq=6 ts_us=1100000 mismatch=0 roi_g=100 roi_count=921600 motion_valid=1 motion_ratio_q16=8192 decode_ms=10 reference_ms=5 transfer_pl_ms=10
BOARD_STREAM_PASS sent_frames=2
CAMERA_PL_PASS compared_frames=2 native_camera_mode_verified=0
CAMERA_PL_EXIT=2
"""
    cfg = load_config()
    summary = {"frames_processed": 2, "landmark_source": "mediapipe",
               "post": {"ok": True, "posted": 1}, "preview_post": {"ok": True, "posted": 2},
               "board_source": {"source": "board_pl", "error": None, "end_received": True,
                                "frames": 2, "valid_motion_frames": 1,
                                "first": {"source_sequence": 0, "source_ts_us": 1000000,
                                          "pl_frame_id": 10, "motion_threshold": cfg["motion_thresh_gray"]},
                                "last": {"source_sequence": 6, "source_ts_us": 1100000,
                                         "pl_frame_id": 11}}}
    first = mock_frame(0, ts=0, status="unreliable")
    second = mock_frame(1, ts=.1, status="normal")
    first["quality"]["motion_score"] = 0
    first["vital"] = dict.fromkeys(first["vital"])
    first["quality"]["overall"] = 0
    second["quality"]["motion_score"] = round(min(1, .125*cfg["motion_score_gain"]), 4)
    return log, summary, [first, second], cfg


def test_same_packet_motion_and_timing_crosscheck():
    result = inspect_run(*fixture())
    assert result["network_closed_loop_pass"]
    assert result["hardware"]["realtime_target_met"] is False
    assert result["same_packet_motion_checked_frames"] == 2


@pytest.mark.parametrize("kind", ["motion", "timestamp", "count", "end", "stub", "preview"])
def test_wrong_or_incomplete_integration_is_rejected(kind):
    log, summary, frames, cfg = copy.deepcopy(fixture())
    if kind == "motion": frames[1]["quality"]["motion_score"] = 0
    elif kind == "timestamp": frames[1]["ts"] = 1/30
    elif kind == "count": summary["frames_processed"] = 3
    elif kind == "end": summary["board_source"]["end_received"] = False
    elif kind == "stub": summary["landmark_source"] = "stub"
    elif kind == "preview": summary["preview_post"]["ok"] = False
    with pytest.raises(ValueError):
        inspect_run(log, summary, frames, cfg)


@pytest.mark.parametrize("throughput,passed", [(29.7, False), (30.0, True), (40.0, True)])
def test_processing_throughput_is_reported_separately(throughput, passed):
    log, summary, frames, cfg = fixture()
    summary["throughput_fps"] = throughput
    result = inspect_run(log, summary, frames, cfg)
    assert result["pipeline_throughput_fps"] == throughput
    assert result["processing_target_met"] is passed
    assert result["hardware"]["realtime_target_met"] is False
