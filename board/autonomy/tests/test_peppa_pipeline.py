"""Pipeline arithmetic and scheduling tests with a fake detector/model.

These never load MediaPipe or the ONNX model: they pin the crop/unproject maths,
the re-detect cadence, and the record contract, so the real-video run only has to
prove the model wiring.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import peppa_pipeline as p  # noqa: E402


class CropTests(unittest.TestCase):
    def test_expanded_crop_is_centred_and_1_4x(self):
        crop = p.expanded_crop(p.Box(100, 100, 200, 200), 640, 480)
        self.assertEqual(crop, (80, 80, 220, 220))

    def test_expanded_crop_clips_at_frame_edge(self):
        crop = p.expanded_crop(p.Box(0, 0, 100, 100), 640, 480)
        self.assertEqual(crop, (0, 0, 120, 120))

    def test_expanded_crop_clips_bottom_right(self):
        crop = p.expanded_crop(p.Box(580, 420, 640, 480), 640, 480)
        self.assertEqual(crop, (568, 408, 640, 480))

    def test_unproject_uses_clipped_crop_size(self):
        points = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]
        self.assertEqual(p.unproject(points, (80, 80, 220, 220)),
                         [(80.0, 80.0), (150.0, 150.0), (220.0, 220.0)])

    def test_unproject_on_edge_crop_scales_by_actual_size(self):
        got = p.unproject([(1.0, 1.0)], (0, 0, 120, 120))
        self.assertEqual(got, [(120.0, 120.0)])


class PreprocessTests(unittest.TestCase):
    def test_shape_and_normalisation(self):
        frame = np.full((480, 640, 3), 127, np.uint8)
        blob = p.preprocess(frame, (80, 80, 220, 220))
        self.assertEqual(blob.shape, (1, 3, 160, 160))
        self.assertEqual(blob.dtype, np.float32)
        self.assertTrue(np.allclose(blob, 0.0))

    def test_white_and_black_map_to_expected_range(self):
        white = np.full((480, 640, 3), 255, np.uint8)
        black = np.zeros((480, 640, 3), np.uint8)
        self.assertAlmostEqual(float(p.preprocess(white, (0, 0, 160, 160)).max()),
                               1.007874, places=5)
        self.assertAlmostEqual(float(p.preprocess(black, (0, 0, 160, 160)).min()), -1.0, places=6)

    def test_tiny_crop_rejected(self):
        with self.assertRaisesRegex(ValueError, "8 px"):
            p.preprocess(np.zeros((480, 640, 3), np.uint8), (10, 10, 14, 14))


class FakeDetector:
    def __init__(self, boxes):
        self.boxes = boxes
        self.calls = 0

    def detect(self, frame):
        index = self.calls
        self.calls += 1
        box = self.boxes[index] if index < len(self.boxes) else None
        return None if box is None else (box, 0.9)


class FakeModel:
    def __init__(self):
        self.calls = 0

    def forward(self, blob):
        self.calls += 1
        # A flat 68-point grid is enough here; geometry is tested separately.
        return [((i % 10) / 10.0, (i // 10) / 10.0) for i in range(68)]


def frames(count):
    return [np.zeros((480, 640, 3), np.uint8) for _ in range(count)]


class StreamTests(unittest.TestCase):
    def test_detect_and_hold_schedule(self):
        detector = FakeDetector([p.Box(200, 100, 400, 380)] * 4)
        records = list(p.process_stream(frames(4), detector, FakeModel(), fps=30.0,
                                        detect_every=2, max_hold=4, with_layout=False, pose_mode="none"))
        self.assertEqual([r["source"] for r in records], ["detect", "hold", "detect", "hold"])
        for record in records:
            self.assertTrue(record["face"])
            self.assertEqual(len(record["points"]), 68)
            self.assertIn("ear", record)
            self.assertIn("forehead", record)

    def test_no_face_is_recorded_not_invented(self):
        detector = FakeDetector([None, None])
        records = list(p.process_stream(frames(2), detector, FakeModel(), fps=30.0))
        self.assertEqual([r["reason"] for r in records], ["no_face", "no_face"])
        self.assertFalse(any(r["face"] for r in records))

    def test_hold_expires_after_max_hold(self):
        detector = FakeDetector([p.Box(200, 100, 400, 380)])
        records = list(p.process_stream(frames(3), detector, FakeModel(), fps=30.0,
                                        detect_every=10, max_hold=1, with_layout=False, pose_mode="none"))
        self.assertEqual(records[0]["source"], "detect")
        self.assertTrue(records[1]["face"])
        self.assertEqual(records[2]["reason"], "track_expired")

    def test_layout_flags_are_attached(self):
        detector = FakeDetector([p.Box(200, 100, 400, 380)])
        record = next(p.process_stream(frames(1), detector, FakeModel(), fps=30.0, pose_mode="none"))
        self.assertIn("layout", record)
        self.assertIn("eyes_side_by_side", record["layout"])


class SummaryTests(unittest.TestCase):
    def test_counts_and_no_board_claim(self):
        detector = FakeDetector([p.Box(200, 100, 400, 380), None])
        records = list(p.process_stream(frames(2), detector, FakeModel(), fps=30.0))
        summary = p.summarise(records, fps=30.0, source="unit", model_path="fake.onnx")
        self.assertEqual(summary["frames"], 2)
        self.assertEqual(summary["face_frames"], 1)
        self.assertEqual(summary["reasons"], {"no_face": 1})
        self.assertFalse(summary["board_run"])
        self.assertFalse(summary["accuracy_verified"])


if __name__ == "__main__":
    unittest.main()
