"""Tests for model-comparison accounting; no fabricated real-video results."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("compare_modes", ROOT/"board/autonomy/compare_landmark_modes.py")
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def mode(visible=1., count=468, ear=.3, mar=.2, status="normal", allowed=True, compatible=True):
    return {"visible": visible, "landmark_count": count,
            "required_points_compatible": compatible, "ear_left": ear, "ear_right": ear,
            "mar": mar, "yaw": 10., "pitch": 2., "roll": -1.,
            "forehead": [0, 0, 10, 10] if visible else None,
            "status": status, "vitals_allowed": allowed, "detect_ms": 2., "behavior": {}}


class CompareModes(unittest.TestCase):
    def test_quantiles_and_empty_distribution(self):
        self.assertEqual(comparison.distribution([])["n"], 0)
        self.assertIsNone(comparison.distribution([])["p95"])
        result = comparison.distribution([4., 1., 3., 2.])
        self.assertEqual(result["p50"], 2.5)
        self.assertAlmostEqual(result["p95"], 3.85)

    def test_roi_half_open_overlap_degenerate_and_absent(self):
        self.assertEqual(comparison.roi_iou([0,0,10,10], [5,0,15,10]), 1/3)
        self.assertEqual(comparison.roi_iou([0,0,10,10], [10,0,20,10]), 0)
        self.assertIsNone(comparison.roi_iou(None, [0,0,10,10]))
        self.assertIsNone(comparison.roi_iou([0,0,0,0], [0,0,0,0]))

    def test_disagreements_use_common_face_for_geometry_and_all_frames_for_gates(self):
        rows = [
            {"base468": mode(), "attention478": mode(count=478, ear=.1, mar=.7)},
            {"base468": mode(visible=0., count=0, allowed=False),
             "attention478": mode(count=478, status="adjust_posture")},
            {"base468": mode(visible=0., count=0, allowed=False),
             "attention478": mode(visible=0., count=0, allowed=False)},
        ]
        result = comparison.summarize(rows, {"ear_close_threshold": .21, "mar_threshold": .6})
        self.assertEqual(result["frames_compared"], 3)
        self.assertEqual(result["both_face_frames"], 1)
        self.assertEqual(result["visibility_disagreements"], 1)
        self.assertEqual(result["ear_closed_disagreements"], 1)
        self.assertEqual(result["mar_open_disagreements"], 1)
        self.assertEqual(result["decision_status_disagreements"], 1)
        self.assertEqual(result["vitals_gate_disagreements"], 1)
        self.assertEqual(result["differences"]["ear_left"]["absolute"]["n"], 1)
        self.assertAlmostEqual(result["differences"]["ear_left"]["absolute"]["p50"], .2)
        self.assertEqual(result["modes"]["base468"]["lost_face_frames"], 2)

    def test_no_face_does_not_create_fake_geometry_equality(self):
        row = {key: mode(visible=0., count=0) for key in ("base468", "attention478")}
        result = comparison.summarize([row], {"ear_close_threshold": .21, "mar_threshold": .6})
        self.assertEqual(result["both_face_frames"], 0)
        self.assertIsNone(result["differences"]["yaw"]["absolute"]["mean"])
        self.assertIsNone(result["differences"]["forehead_roi_iou"]["p50"])

    def test_required_index_set_fits_nonrefined_model(self):
        indices = comparison.MP_LEFT_EYE + comparison.MP_RIGHT_EYE + comparison.MP_MOUTH + comparison.MP_FOREHEAD + comparison.MP_FACE_OVAL + comparison.MediaPipeLandmarker._POSE_IDX
        self.assertLess(max(indices), 468)
        self.assertNotIn(468, indices)

    def test_point_distance_normalization_and_pose_validity_accounting(self):
        a, b = mode(), mode(count=478)
        for record, shift in ((a,1.),(b,0.)):
            record["eyes"] = {side: {f"p{k}": [x+shift,0.] for k in range(1,7)} for side,x in (("left",0.),("right",10.))}
            record["mouth"] = {key: [shift,0.] for key in ("left","right","top","bottom")}
        a["pose_audit"] = {"valid": True,"reprojection_error_over_interocular": .1}
        b["pose_audit"] = {"valid": False,"reprojection_error_over_interocular": None}
        result = comparison.summarize([{"base468":a,"attention478":b}], {"ear_close_threshold":.21,"mar_threshold":.6})
        self.assertEqual(result["differences"]["corresponding_point_distance_pixels"]["n"],16)
        self.assertEqual(result["differences"]["frame_point_rms_pixels"]["mean"],1.)
        self.assertAlmostEqual(result["differences"]["point_distance_normalized_by_attention_mp33_mp263"]["mean"],.1)
        self.assertEqual(result["modes"]["base468"]["pose_audit"]["solver_valid_frames"],1)
        self.assertEqual(result["modes"]["attention478"]["pose_audit"]["solver_rejected_frames"],1)

    def test_count_audit_uses_same_process_result_not_second_inference(self):
        mesh = SimpleNamespace(process=lambda image: SimpleNamespace(multi_face_landmarks=[SimpleNamespace(landmark=[None]*468)]), close=lambda: None)
        detector = SimpleNamespace(_mesh=mesh)
        with patch.object(comparison, "MediaPipeLandmarker", return_value=detector) as constructor:
            audited = comparison.AuditedLandmarker(640,480,False)
            constructor.assert_called_once_with(640,480,refine_landmarks=False)
            output = mesh.process(object())
            self.assertEqual(audited.last_count, 468)
            self.assertEqual(len(output.multi_face_landmarks[0].landmark),468)
            audited.close()


if __name__ == "__main__":
    unittest.main()
