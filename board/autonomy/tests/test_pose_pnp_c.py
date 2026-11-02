"""C PnP parity: the A9 build must reproduce the Python reference on the same input.

Synthetic round-trips pin the solver; real frames (when the pipeline JSONL exists)
check that the C implementation finds the same minimum as OpenCV's solvePnP used
by peppa_geometry. No human ground truth is claimed.
"""
from __future__ import annotations

import ctypes as ct
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import peppa_geometry as g  # noqa: E402


class Pose(ct.Structure):
    _fields_ = [("yaw", ct.c_double), ("pitch", ct.c_double), ("roll", ct.c_double),
                ("t", ct.c_double * 3), ("reproj_px", ct.c_double),
                ("reproj_frac", ct.c_double)]

class Seed(ct.Structure):
    _fields_=[('R',ct.c_double*9),('t',ct.c_double*3),('width',ct.c_int),('height',ct.c_int),('valid',ct.c_int)]


Pts = (ct.c_double * 2) * 68
Mdl = (ct.c_double * 3) * 68
CAMERA = np.array([[640.0, 0.0, 320.0], [0.0, 640.0, 240.0], [0.0, 0.0, 1.0]])


def build(destination: Path) -> Path:
    compiler = os.environ.get("CC") or shutil.which("gcc") or shutil.which("cc")
    if not compiler:
        raise unittest.SkipTest("no C compiler for the PnP parity test")
    library = destination / ("vgpose" + (".dll" if os.name == "nt" else ".so"))
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                    "-shared", "-fPIC", "-I", str(ROOT / "board/autonomy"),
                    "-o", str(library), str(ROOT / "board/autonomy/pose_pnp.c"), "-lm"],
                   check=True, capture_output=True, text=True)
    return library


def rotation(yaw, pitch, roll):
    y, p, r = (math.radians(v) for v in (yaw, pitch, roll))
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return rz @ ry @ rx


class PoseParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = Path(tempfile.mkdtemp(prefix="vg_pose_"))
        cls.lib = ct.CDLL(str(build(cls.temp)))
        cls.lib.vg_pose_solve.restype = ct.c_int
        cls.lib.vg_pose_solve.argtypes = [ct.POINTER(Pts), ct.c_int, ct.c_int,
                                          ct.POINTER(Mdl), ct.c_double,
                                          ct.POINTER(Pose)]
        cls.model = g.load_face_model()
        cls.lib.vg_pose_track.restype=ct.c_int
        cls.lib.vg_pose_track.argtypes=[ct.POINTER(Pts),ct.c_int,ct.c_int,ct.POINTER(Mdl),ct.c_double,ct.POINTER(Seed),ct.POINTER(Seed),ct.POINTER(Pose)]

    def test_current_frame_warm_start_and_failure_invalidation(self):
        seed=Seed();mdl=Mdl(*[tuple(map(float,p)) for p in self.model]);out=Pose()
        for i in range(100):
            yaw=-20+i*.4;pitch=8*math.sin(i*.1);roll=4*math.cos(i*.1)
            rvec,_=cv2.Rodrigues(rotation(yaw,pitch,roll))
            points,_=cv2.projectPoints(self.model,rvec,np.array([.1,0,10.0]),CAMERA,None)
            array=Pts(*[tuple(map(float,p)) for p in points.reshape(-1,2)])
            self.assertEqual(self.lib.vg_pose_track(array,640,480,mdl,.25,ct.byref(seed),ct.byref(seed),ct.byref(out)),0)
            self.assertEqual(seed.valid,1)
            for actual,wanted in ((out.yaw,yaw),(out.pitch,pitch),(out.roll,roll)):self.assertAlmostEqual(actual,wanted,delta=.05)
        seed.R[0]=float('nan') # corrupted state must cold-recover
        self.assertEqual(self.lib.vg_pose_track(array,640,480,mdl,.25,ct.byref(seed),ct.byref(seed),ct.byref(out)),0)
        before=bytes(out)
        self.assertEqual(self.lib.vg_pose_track(Pts(),640,480,mdl,.25,ct.byref(seed),ct.byref(seed),ct.byref(out)),-1)
        self.assertEqual(seed.valid,0);self.assertEqual(bytes(out),before)

    def solve(self, points, limit=0.25):
        array = Pts()
        for i, (x, y) in enumerate(points):
            array[i][0], array[i][1] = float(x), float(y)
        model = Mdl()
        for i, row in enumerate(self.model):
            model[i][0], model[i][1], model[i][2] = (float(v) for v in row)
        out = Pose()
        code = self.lib.vg_pose_solve(array, 640, 480, model, limit, ct.byref(out))
        return code, out

    def test_synthetic_round_trip(self):
        for yaw in (-20, -8, 0, 8, 20):
            for pitch in (-10, 0, 10):
                for roll in (-15, 0, 15):
                    with self.subTest(yaw=yaw, pitch=pitch, roll=roll):
                        rvec, _ = cv2.Rodrigues(rotation(yaw, pitch, roll))
                        points, _ = cv2.projectPoints(self.model, rvec,
                                                      np.array([0.0, 0.0, 10.0]), CAMERA, None)
                        code, out = self.solve(points.reshape(-1, 2))
                        self.assertEqual(code, 0)
                        self.assertAlmostEqual(out.yaw, yaw, delta=0.05)
                        self.assertAlmostEqual(out.pitch, pitch, delta=0.05)
                        self.assertAlmostEqual(out.roll, roll, delta=0.05)

    def test_degenerate_and_gate(self):
        code, _ = self.solve(np.zeros((68, 2)))
        self.assertEqual(code, -1)
        rvec, _ = cv2.Rodrigues(rotation(5, 0, 0))
        points, _ = cv2.projectPoints(self.model, rvec, np.array([0.0, 0.0, 10.0]), CAMERA, None)
        rng = np.random.default_rng(3)
        noisy = points.reshape(-1, 2) + rng.normal(scale=8.0, size=(68, 2))
        code, _ = self.solve(noisy, limit=0.01)
        self.assertEqual(code, -2)

    def test_matches_python_on_real_frames(self):
        source = ROOT / "metrics/logs/peppa_68_blink_full.jsonl"
        if not source.exists():
            raise unittest.SkipTest("real-frame JSONL absent")
        rows = []
        with source.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("face") and row.get("geometry_valid"):
                    rows.append(np.asarray(row["points"], dtype=np.float64))
        rows = rows[::15][:40]
        self.assertGreater(len(rows), 5)
        deltas = []
        disagreements = 0
        for points in rows:
            reference = g.solve_pose_pnp(points, 640, 480)
            code, out = self.solve(points)
            if (reference is None) != (code != 0):
                disagreements += 1
                continue
            if reference is None:
                continue
            deltas.append((abs(reference["yaw"] - out.yaw),
                           abs(reference["pitch"] - out.pitch),
                           abs(reference["roll"] - out.roll)))
        self.assertGreater(len(deltas), 5)
        median = np.median(np.array(deltas), axis=0)
        self.assertLess(float(median[0]), 1.5, median)
        self.assertLess(float(median[1]), 1.5, median)
        self.assertLess(float(median[2]), 1.5, median)
        self.assertLessEqual(disagreements, max(2, len(rows) // 4))


if __name__ == "__main__":
    unittest.main()
