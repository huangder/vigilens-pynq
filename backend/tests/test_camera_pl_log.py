"""Synthetic log fixtures verify rejection logic, not physical board results."""
import pytest

from backend.config import load_config
from board.w7_buildroot.check_camera_pl_log import inspect_log


FIXTURE = """CAMERA_PL_FRAME n=1 seq=0 ts_us=1000000 mismatch=0 roi_g=100 roi_count=921600 motion_valid=0 motion_ratio_q16=50 decode_ms=10 reference_ms=5 transfer_pl_ms=10
CAMERA_PL_FRAME n=2 seq=6 ts_us=1100000 mismatch=0 roi_g=100 roi_count=921600 motion_valid=1 motion_ratio_q16=50 decode_ms=10 reference_ms=5 transfer_pl_ms=10
CAMERA_PL_PASS compared_frames=2 native_camera_mode_verified=0
CAMERA_PL_EXIT=2
"""


def test_zero_difference_does_not_certify_realtime_or_native_mode():
    result = inspect_log(FIXTURE, load_config())
    assert result["hardware_compare_pass"] is True
    assert result["selected_fps"] == pytest.approx(10)
    assert result["realtime_target_met"] is False
    assert result["native_camera_mode_verified"] is False


@pytest.mark.parametrize("bad", [
    FIXTURE.replace("mismatch=0", "mismatch=1", 1),
    FIXTURE.replace("n=2 seq=6", "n=1 seq=6"),
    FIXTURE.replace("ts_us=1100000", "ts_us=1000000"),
    FIXTURE.replace("compared_frames=2", "compared_frames=3"),
    FIXTURE.replace("CAMERA_PL_EXIT=2", "CAMERA_PL_EXIT=1"),
    FIXTURE + "CAMERA_PL_FAIL timed_out\n",
])
def test_wrong_truncated_or_failed_results_are_rejected(bad):
    with pytest.raises(ValueError):
        inspect_log(bad, load_config())


def audit_fixture(every=30):
    first, second, *rest = FIXTURE.splitlines()
    return (f"PL_READY pipeline=threaded audit_every={every} codec=jpeg\n"
            + first.replace("mismatch=0", f"mismatch=0 audit={int(every > 0)}") + "\n"
            + second.replace("mismatch=0", f"mismatch=0 audit={int(every == 1)}") + "\n"
            + f"AUDIT_SUMMARY frames=2 audits={int(every > 0) + int(every == 1)} failures=0 reference_ms_total=5\n"
            + "\n".join(rest) + "\n")


@pytest.mark.parametrize("every", [0, 1, 30])
def test_audits_are_reported_separately_from_every_frame_comparison(every):
    result = inspect_log(audit_fixture(every), load_config())
    assert result["hardware_invariants_pass"] is True
    assert result["hardware_compare_pass"] is (every == 1)
    assert result["audit_compare_pass"] is (every > 0)
    assert result["audit_frames"] == int(every > 0) + int(every == 1)


@pytest.mark.parametrize("change", [
    lambda s: s.replace("failures=0", "failures=1"),
    lambda s: s.replace("audits=1", "audits=2"),
    lambda s: s.replace("frames=2 audits", "frames=3 audits"),
    lambda s: s.replace("audit=1", "audit=0", 1),
    lambda s: s.replace("AUDIT_SUMMARY", "MISSING_SUMMARY"),
    lambda s: s + "JPEG_DECODE_FAIL sequence=3\n",
    lambda s: s + "BOARD_STREAM_FAIL seq=3\n",
])
def test_bad_audit_or_worker_failure_is_rejected(change):
    with pytest.raises(ValueError):
        inspect_log(change(audit_fixture()), load_config())


def dual_fixture():
    return (audit_fixture().replace("pipeline=threaded", "pipeline=threaded decode_workers=2")
            + "DECODE_SUMMARY workers=2 frames=2 peak_active=2\n")


def test_parallel_decoder_configuration_and_completion_are_checked():
    result = inspect_log(dual_fixture(), load_config())
    assert result["decode_workers"] == result["decode_peak_active"] == 2
    assert result["hardware_compare_pass"] is False


@pytest.mark.parametrize("change", [
    lambda s: s.replace("DECODE_SUMMARY", "MISSING_SUMMARY"),
    lambda s: s.replace("workers=2 frames=2", "workers=1 frames=2"),
    lambda s: s.replace("frames=2 peak", "frames=1 peak"),
    lambda s: s.replace("peak_active=2", "peak_active=3"),
    lambda s: s.replace("peak_active=2", "peak_active=0"),
    lambda s: s.replace("decode_workers=2", "decode_workers=3"),
    lambda s: s.replace("pipeline=threaded", "pipeline=serial"),
])
def test_decoder_configuration_or_incomplete_run_is_rejected(change):
    with pytest.raises(ValueError):
        inspect_log(change(dual_fixture()), load_config())


def test_yield_policy_and_cpu_timing_remain_separate_from_wall_timing():
    log = dual_fixture().replace("pipeline=threaded", "pipeline=threaded pl_wait=yield")
    log = log.replace("decode_ms=10", "decode_ms=10 decode_cpu_ms=8 transfer_pl_cpu_ms=2")
    result = inspect_log(log, load_config())
    assert result["pl_wait"] == "yield"
    assert result["timing_median_ms"]["transfer_pl_cpu_ms"] == 2
    assert result["timing_median_ms"]["transfer_pl_ms"] == 10
    with pytest.raises(ValueError):
        inspect_log(log.replace("pl_wait=yield", "pl_wait=typo"), load_config())
