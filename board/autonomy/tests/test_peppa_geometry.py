"""Synthetic geometry tests for the 68-point helpers.

No model, no video, no board: these pin the formulas and the rejection rules so a
later C port can be checked against the same numbers.
"""
import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import peppa_geometry as g  # noqa: E402


def synthetic_points():
    """A frontal, open-eye, closed-mouth face in a 100x140 box."""
    pts = [(0.0, 0.0)] * 68
    # Jaw 0..16: left edge -> chin (8) -> right edge.
    for i in range(17):
        t = i / 16.0
        pts[i] = (100.0 * t, 80.0 + 40.0 * math.sin(math.pi * t))
    # Brows 17..26 just above the eyes.
    for k, i in enumerate(range(17, 22)):
        pts[i] = (18.0 + 4.0 * k, 35.0 + (k % 2))
    for k, i in enumerate(range(22, 27)):
        pts[i] = (62.0 + 4.0 * k, 35.0 + (k % 2))
    # Nose 27..30: bridge -> tip.
    pts[27], pts[28], pts[29], pts[30] = (50.0, 45.0), (50.0, 52.0), (48.0, 58.0), (50.0, 60.0)
    for i in range(31, 36):
        pts[i] = (50.0 + (i - 33) * 4.0, 65.0)
    # Eye A (image left) 36..41, closed loop 20 px wide, 8 px tall.
    pts[36], pts[37], pts[38] = (20.0, 50.0), (28.0, 46.0), (34.0, 46.0)
    pts[39], pts[40], pts[41] = (40.0, 50.0), (34.0, 54.0), (28.0, 54.0)
    # Eye B (image right) 42..47, same shape shifted +40 px.
    pts[42], pts[43], pts[44] = (60.0, 50.0), (68.0, 46.0), (74.0, 46.0)
    pts[45], pts[46], pts[47] = (80.0, 50.0), (74.0, 54.0), (68.0, 54.0)
    # Outer mouth 48..59: 30 px wide, 10 px tall.
    for k, i in enumerate(range(48, 54)):
        pts[i] = (35.0 + 6.0 * k, 80.0 if k in (0, 5) else (82.0 if k in (1, 4) else 84.0))
    for k, i in enumerate(range(54, 60)):
        pts[i] = (65.0 - 6.0 * k, 80.0 if k == 0 else (87.0 if k in (1, 4) else 90.0))
    # iBUG mouth slab: 51 top centre, 57 bottom centre -> MAR = 10/30.
    pts[51], pts[57] = (50.0, 80.0), (50.0, 90.0)
    for k, i in enumerate(range(60, 68)):
        pts[i] = (40.0 + 3.0 * (k % 4), 83.0 + 3.0 * (k // 4))
    return pts


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.points = synthetic_points()
        self.obs = g.build_observation(self.points)

    def test_ear_matches_closed_form(self):
        # (|p2-p6| + |p3-p5|) / (2*|p1-p4|) = (8+8)/40
        self.assertAlmostEqual(g.ear(self.obs.eyes["left"]), 0.4, places=9)
        self.assertAlmostEqual(g.ear(self.obs.eyes["right"]), 0.4, places=9)

    def test_mar_matches_closed_form(self):
        self.assertAlmostEqual(g.mouth_ratio(self.obs.mouth), 10.0 / 30.0, places=9)

    def test_point_order_hits_the_named_corners(self):
        self.assertEqual(self.obs.eyes["left"]["p1"], (20.0, 50.0))
        self.assertEqual(self.obs.eyes["left"]["p4"], (40.0, 50.0))
        self.assertEqual(self.obs.mouth["left"], (35.0, 80.0))
        self.assertEqual(self.obs.mouth["right"], (65.0, 80.0))

    def test_wrong_point_count_rejected(self):
        with self.assertRaisesRegex(ValueError, "68"):
            g.build_observation(self.points[:-1])

    def test_mirrored_eye_order_fails_layout(self):
        mirrored = synthetic_points()
        mirrored[36], mirrored[39] = mirrored[39], mirrored[36]
        self.assertFalse(g.layout_checks(mirrored)["eye_a_loop"])


class LayoutTests(unittest.TestCase):
    def test_synthetic_face_passes_every_check(self):
        checks = g.layout_checks(synthetic_points())
        self.assertTrue(all(checks.values()), checks)

    def test_swapped_eyes_fail_side_by_side(self):
        pts = synthetic_points()
        for a, b in zip(range(36, 42), range(42, 48)):
            pts[a], pts[b] = pts[b], pts[a]
        self.assertFalse(g.layout_checks(pts)["eyes_side_by_side"])

    def test_chin_not_lowest_fails_jaw_arc(self):
        pts = synthetic_points()
        pts[8] = (50.0, 81.0)
        self.assertFalse(g.layout_checks(pts)["jaw_arc"])

    def test_nose_above_eyes_fails(self):
        pts = synthetic_points()
        pts[30] = (50.0, 40.0)
        self.assertFalse(g.layout_checks(pts)["nose_below_eyes"])


class ValidityTests(unittest.TestCase):
    def test_normal_face_has_two_valid_eyes(self):
        report = g.eye_validity(synthetic_points(), 100.0)
        self.assertTrue(report["left"] and report["right"] and report["frame_ok"])
        self.assertAlmostEqual(report["ear"], 0.4, places=6)
        self.assertAlmostEqual(report["iod_over_box"], 0.4, places=6)

    def test_collapsed_far_eye_is_excluded_from_ear(self):
        pts = synthetic_points()
        pts[39] = (21.5, 50.0)          # width 1.5 px instead of 20
        pts[37], pts[38] = (20.5, 49.0), (21.0, 49.0)
        pts[40], pts[41] = (21.0, 51.0), (20.5, 51.0)
        report = g.eye_validity(pts, 100.0)
        self.assertFalse(report["left"])
        self.assertTrue(report["right"])
        self.assertTrue(report["frame_ok"])
        self.assertAlmostEqual(report["ear"], 0.4, places=6)

    def test_iod_collapse_invalidates_the_frame(self):
        pts = synthetic_points()
        for i in range(42, 48):
            pts[i] = (pts[i][0] - 39.0, pts[i][1])
        report = g.eye_validity(pts, 100.0)
        self.assertFalse(report["frame_ok"])
        self.assertIsNone(report["ear"])

    def test_zero_box_width_does_not_divide(self):
        report = g.eye_validity(synthetic_points(), 0.0)
        self.assertFalse(report["frame_ok"])


class PoseProxyTests(unittest.TestCase):
    def test_frontal_face_has_zero_roll_and_proxy(self):
        pose = g.pose_proxy(synthetic_points())
        self.assertAlmostEqual(pose["roll_deg"], 0.0, places=6)
        # The synthetic eye centroids sit at x=50.67, so a nose at x=50 is 0.0167 IOD off.
        self.assertAlmostEqual(pose["yaw_proxy"], 0.0, delta=0.02)

    def test_roll_follows_eye_line(self):
        pts = synthetic_points()
        pts[42], pts[43], pts[44] = (60.0, 60.0), (68.0, 56.0), (74.0, 56.0)
        pts[45], pts[46], pts[47] = (80.0, 60.0), (74.0, 64.0), (68.0, 64.0)
        self.assertGreater(g.pose_proxy(pts)["roll_deg"], 5.0)

    def test_yaw_proxy_sign_follows_nose_offset(self):
        pts = synthetic_points()
        pts[30] = (60.0, 60.0)
        self.assertGreater(g.pose_proxy(pts)["yaw_proxy"], 0.0)
        pts[30] = (40.0, 60.0)
        self.assertLess(g.pose_proxy(pts)["yaw_proxy"], 0.0)

    def test_pitch_proxy_grows_when_nose_drops(self):
        pts = synthetic_points()
        before = g.pose_proxy(pts)["pitch_proxy"]
        pts[30] = (50.0, 70.0)
        self.assertGreater(g.pose_proxy(pts)["pitch_proxy"], before)


class ForeheadTests(unittest.TestCase):
    def test_roi_sits_above_brows_and_excludes_face_points(self):
        pts = synthetic_points()
        roi = g.forehead_roi(pts, 640, 480)
        self.assertIsNotNone(roi)
        x0, y0, x1, y1 = roi
        self.assertLess(y1, min(pts[i][1] for i in g.BROWS))
        self.assertFalse(g.roi_contains_points(roi, pts, g.BROWS + g.EYE_A + g.EYE_B))
        self.assertTrue(0 <= x0 < x1 <= 640 and 0 <= y0 < y1 <= 480)

    def test_clipped_at_frame_top_returns_none_when_too_small(self):
        pts = synthetic_points()
        for i in g.BROWS:
            pts[i] = (pts[i][0], 2.0)
        self.assertIsNone(g.forehead_roi(pts, 640, 480))

    def test_wrong_point_count_returns_none(self):
        self.assertIsNone(g.forehead_roi(synthetic_points()[:10], 640, 480))


if __name__ == "__main__":
    unittest.main()
