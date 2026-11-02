"""Synthetic acceptance/rejection tests only; never board performance evidence."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.mock import mock_frame
from metrics.scripts.check_native_realtime import evaluate


class NativeRealtimeGate(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.rows = [mock_frame(i, ts=i/30, status="normal") for i in range(60)]
        self.log = "\n".join(
            [f"CAMERA_PL_FRAME n={i+1} ts_us={round(i*1e6/30)} mismatch=0" for i in range(60)] +
            ["AUTONOMY_SUMMARY submitted=60 accepted=60 skipped=0 processed=60 succeeded=60 rejected=0 failed=0 rc=0 camera_submit_fps=29.999995 analysis_update_fps=30",
             "AUDIT_SUMMARY frames=60 audits=2 failures=0",
             "SAMPLE_RESULT kept=60 native_30fps=no", "CAMERA_PL_PASS compared_frames=60"])

    def test_full_processing_and_native_mode_are_separate(self):
        result = evaluate(self.log, self.rows, self.config)
        self.assertTrue(result["passed"], result)
        self.assertFalse(result["native_camera_mode_verified"])
        self.assertFalse(result["accuracy_verified"])
        self.assertEqual(result["face_bearing_frames"],60)
        self.assertEqual(result["face_bearing_update_fps"],30)

    def test_camera_only_rate_is_not_analysis_rate(self):
        result = evaluate(self.log.replace("analysis_update_fps=30", "analysis_update_fps=4.4"), self.rows, self.config)
        self.assertFalse(result["passed"])

    def test_skips_cannot_be_hidden_by_claimed_rate(self):
        result = evaluate(self.log.replace("skipped=0", "skipped=1"), self.rows, self.config)
        self.assertFalse(result["passed"])

    def test_truncation_and_multiple_runs_fail_closed(self):
        for log in (self.log.replace("CAMERA_PL_PASS", "INCOMPLETE"),
                    self.log + "\nAUDIT_SUMMARY frames=60 audits=2 failures=0",
                    self.log.replace("analysis_update_fps=30", "analysis_update_fps=nan")):
            self.assertFalse(evaluate(log, self.rows, self.config)["passed"])

    def test_bad_or_stale_contracts_and_no_face_fail(self):
        for kind in ("invalid", "nonmonotonic", "no_face", "insufficient_face"):
            rows = copy.deepcopy(self.rows)
            if kind == "invalid":
                rows[0]["vital"]["invented"] = 10
            elif kind == "nonmonotonic":
                rows[1]["frame_id"] = rows[0]["frame_id"]
            else:
                for row in (rows if kind=="no_face" else rows[:30]):
                    row["face"]["visible"] = 0
            self.assertFalse(evaluate(self.log, rows, self.config)["passed"], kind)
            if kind=="insufficient_face":
                self.assertEqual(evaluate(self.log,rows,self.config)["face_bearing_update_fps"],15)

    def test_hardware_audit_and_jitter_are_required(self):
        logs = [self.log.replace("failures=0", "failures=1"),
                self.log.replace("audits=2", "audits=0"),
                self.log.replace("mismatch=0", "mismatch=1")]
        logs.append("\n".join(
            [f"CAMERA_PL_FRAME n={i+1} ts_us={round(i*1e6/15)} mismatch=0" for i in range(60)] +
            [line for line in self.log.splitlines() if not line.startswith("CAMERA_PL_FRAME")]))
        # Missing frame timestamps cannot be replaced by a summary FPS.
        logs.append("\n".join(line for line in self.log.splitlines() if not line.startswith("CAMERA_PL_FRAME")))
        for log in logs:
            self.assertFalse(evaluate(log, self.rows, self.config)["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
