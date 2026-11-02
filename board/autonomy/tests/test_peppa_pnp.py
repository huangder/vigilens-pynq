"""PnP validation for the 68-point 3D reference.

Synthetic round-trips prove the solver recovers the rotation it was given; the
model file is checked for the anatomical sign (nose closer than the eye corners).
Real-person angle accuracy is **not** asserted here — the clip has no ground-truth
head pose, and `docs/57` reports only agreement with 2D cues and MediaPipe.
"""
import math
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import peppa_geometry as g  # noqa: E402

CAMERA = np.array([[640.0, 0.0, 320.0], [0.0, 640.0, 240.0], [0.0, 0.0, 1.0]])


def rotation(yaw, pitch, roll):
    y, p, r = (math.radians(v) for v in (yaw, pitch, roll))
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return rz @ ry @ rx


def project(model, yaw, pitch, roll, distance=10.0):
    rvec, _ = cv2.Rodrigues(rotation(yaw, pitch, roll))
    points, _ = cv2.projectPoints(model, rvec, np.array([0.0, 0.0, distance]), CAMERA, None)
    return points.reshape(-1, 2)


class ModelTests(unittest.TestCase):
    def test_shape_and_anatomical_depth(self):
        model = g.load_face_model()
        self.assertEqual(model.shape, (68, 3))
        self.assertTrue(np.isfinite(model).all())
        # Nose tip must sit closer to the camera than the eye corners.
        self.assertLess(model[30][2], model[36][2])
        self.assertLess(model[30][2], model[42][2])

    def test_missing_model_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            g.load_face_model(".tmp/definitely-not-a-model.json")


class RoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = g.load_face_model()

    def test_recovers_known_angles(self):
        for yaw in (-20, -8, 0, 8, 20):
            for pitch in (-10, 0, 10):
                for roll in (-15, 0, 15):
                    with self.subTest(yaw=yaw, pitch=pitch, roll=roll):
                        points = project(self.model, yaw, pitch, roll)
                        out = g.solve_pose_pnp(points, 640, 480, model=self.model)
                        self.assertIsNotNone(out)
                        self.assertAlmostEqual(out["yaw"], yaw, delta=0.05)
                        self.assertAlmostEqual(out["pitch"], pitch, delta=0.05)
                        self.assertAlmostEqual(out["roll"], roll, delta=0.05)
                        self.assertLess(out["reproj_frac"], 1e-9)

    def test_yaw_sign_moves_nose_towards_negative_x(self):
        base = project(self.model, 0, 0, 0)
        turned = project(self.model, 10, 0, 0)
        self.assertLess(turned[30][0], base[30][0])
        out = g.solve_pose_pnp(turned, 640, 480, model=self.model)
        self.assertGreater(out["yaw"], 0)

    def test_roll_follows_eye_line(self):
        points = project(self.model, 0, 0, 12)
        out = g.solve_pose_pnp(points, 640, 480, model=self.model)
        self.assertAlmostEqual(out["roll"], 12, delta=0.1)


class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = g.load_face_model()

    def test_degenerate_points_are_refused(self):
        self.assertIsNone(g.solve_pose_pnp(np.zeros((68, 2)), 640, 480))

    def test_wrong_shape_is_refused(self):
        self.assertIsNone(g.solve_pose_pnp(np.zeros((10, 2)), 640, 480))

    def test_reprojection_guard_rejects_noisy_points(self):
        points = project(self.model, 0, 0, 0)
        rng = np.random.default_rng(7)
        noisy = points + rng.normal(scale=8.0, size=points.shape)
        self.assertIsNone(g.solve_pose_pnp(noisy, 640, 480, model=self.model,
                                           reproj_max_frac=0.01))

    def test_reproj_fields_are_reported(self):
        out = g.solve_pose_pnp(project(self.model, 5, 0, 0), 640, 480, model=self.model)
        self.assertIn("reproj_px", out)
        self.assertIn("reproj_frac", out)
        self.assertGreaterEqual(out["reproj_px"], 0.0)


if __name__ == "__main__":
    unittest.main()
