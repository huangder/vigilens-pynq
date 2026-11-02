"""Formula parity with Python QualityScorer; no pixel conversion or board timing.

python -m unittest discover -s board/autonomy/tests -p test_quality.py -v
Uses synthetic full-frame luminance statistics and HLS-format Q16 ratios.
"""
import ctypes as ct
import itertools
import math
import os
from pathlib import Path
import random
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.quality import QualityScorer


class Config(ct.Structure):
    _fields_ = [(k, ct.c_double) for k in ("light_target", "motion_score_gain", "weight_light", "weight_motion", "weight_face")]


class Result(ct.Structure):
    _fields_ = [(k, ct.c_double) for k in ("light_score", "motion_score", "overall", "mean_luma", "motion_ratio")]


class QualityParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vg_quality_")
        lib = Path(cls.temp.name)/("quality.dll" if os.name == "nt" else "quality.so")
        command = shlex.split(os.environ.get("CC", "gcc"))
        command += ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared"]
        command += ["-static-libgcc"] if os.name == "nt" else ["-fPIC"]
        command += [str(ROOT/"board/autonomy/quality.c"), "-lm", "-o", str(lib)]
        subprocess.run(command, check=True, capture_output=True, text=True)
        cls.lib = ct.CDLL(str(lib))
        cls.lib.vg_quality_from_stats.argtypes = [ct.POINTER(Config), ct.c_double, ct.c_double, ct.c_double, ct.POINTER(Result)]
        cls.lib.vg_quality_apply_motion_ratio.argtypes = [ct.POINTER(Config), ct.c_double, ct.c_double, ct.POINTER(ct.c_double), ct.POINTER(Result)]
        cls.lib.vg_quality_motion_q16.argtypes = [ct.c_uint32, ct.c_int, ct.POINTER(ct.c_double)]
        cls.cfg = load_config()

    @classmethod
    def tearDownClass(cls):
        if os.name == "nt":
            import _ctypes
            _ctypes.FreeLibrary(cls.lib._handle)
        cls.lib = None
        cls.temp.cleanup()

    def config(self, cfg):
        weights = cfg["quality_weights"]
        return Config(cfg["light_target"], cfg["motion_score_gain"], *(weights[k] for k in ("light", "motion", "face")))

    def compare_stats(self, gray_mean, q16, face, cfg=None):
        cfg = cfg or self.cfg
        c = self.config(cfg)
        result = Result()
        ratio = ct.c_double()
        self.assertEqual(self.lib.vg_quality_motion_q16(q16, 1, ct.byref(ratio)), 0)
        self.assertEqual(self.lib.vg_quality_from_stats(ct.byref(c), gray_mean/255., ratio.value, face, ct.byref(result)), 0)
        py = QualityScorer(cfg)
        py._last_motion["motion_ratio_q16"] = q16
        # to_gray is replaced only at the statistic boundary: the original
        # update method computes scoring/rounding from these identical inputs.
        gray = SimpleNamespace(mean=lambda: gray_mean, shape=(2, 2))
        with patch("backend.quality.to_gray", return_value=gray):
            expected = py.update(0, 0., object(), SimpleNamespace(visible=face))
        actual = {k: round(getattr(result, k), 4) for k in ("light_score", "motion_score", "overall")}
        actual.update(_mean_luma=round(result.mean_luma, 4), _motion_ratio=round(result.motion_ratio, 4), _mode="pixels")
        self.assertEqual(actual, expected)
        return actual

    def compare_override(self, light, ratio, face, cfg=None):
        cfg = cfg or self.cfg
        c = self.config(cfg)
        f = ct.c_double(face) if face is not None else None
        r = Result()
        self.assertEqual(self.lib.vg_quality_apply_motion_ratio(ct.byref(c), light, ratio, ct.byref(f) if f else None, ct.byref(r)), 0)
        py = QualityScorer(cfg)
        expected = py.apply_motion_ratio({"light_score": light}, ratio, face_score=face)
        actual = {k: round(getattr(r, k), 4) for k in ("light_score", "motion_score", "overall")}
        actual.update(_motion_ratio=round(r.motion_ratio, 4), _motion_source="pl_sim")
        self.assertEqual(actual, expected)
        return actual

    def test_pixel_stats_grid_and_q16_endpoints(self):
        for mean, q16, face in itertools.product((0., 63.5, 127.5, 255.), (0, 1, 16384, 65535, 65536), (0., .7, 1.)):
            self.compare_stats(mean, q16, face)

    def test_override_clamps_and_missing_face(self):
        for light, ratio, face in itertools.product((-.5, 0., .5, 1., 2.), (-1., 0., .25, 1., 2.), (None, -.5, 0., .7, 1., 2.)):
            self.compare_override(light, ratio, face)

    def test_weights_normalization_zero_sum_and_output_clamp(self):
        for light, motion, face in ((5., 3., 2.), (0., 0., 0.), (1., -1., 0.), (1e-12, 1e-12, 1e-12), (-1., -2., -3.)):
            cfg = dict(self.cfg, quality_weights=dict(light=light, motion=motion, face=face))
            self.compare_stats(100., 16000, .75, cfg)
            self.compare_override(.7843, .3, .75, cfg)

    def test_update_raw_face_differs_from_override_clamped_face(self):
        # This preserves the two Python APIs' existing distinct semantics.
        for face in (-1., 2.):
            self.compare_stats(127.5, 0, face)
            self.compare_override(1., 0., face)

    def test_seeded_stats_and_overrides(self):
        rng = random.Random(1549)
        for _ in range(2000):
            self.compare_stats(rng.uniform(0., 255.), rng.randrange(65537), rng.random())
            self.compare_override(round(rng.random(), 4), rng.uniform(-.1, 1.1), rng.random())

    def test_motion_gain_boundary(self):
        cfg = self.cfg
        boundary = 1./cfg["motion_score_gain"]
        for ratio in (math.nextafter(boundary, -math.inf), boundary, math.nextafter(boundary, math.inf)):
            self.compare_override(.5, ratio, 1.)

    def test_invalid_pl_result_never_becomes_a_zero_measurement(self):
        ratio = ct.c_double(.75)
        self.assertEqual(self.lib.vg_quality_motion_q16(0, 0, ct.byref(ratio)), -2)
        self.assertEqual(ratio.value, .75)
        self.assertEqual(self.lib.vg_quality_motion_q16(65537, 1, ct.byref(ratio)), -1)
        self.assertEqual(ratio.value, .75)

    def test_nonfinite_inputs_rejected_without_output_change(self):
        cfg = self.config(self.cfg)
        result = Result()
        self.assertEqual(self.lib.vg_quality_from_stats(ct.byref(cfg), .5, .1, 1., ct.byref(result)), 0)
        original = bytes(result)
        for value in (math.nan, math.inf, -math.inf):
            self.assertEqual(self.lib.vg_quality_from_stats(ct.byref(cfg), value, .1, 1., ct.byref(result)), -1)
            self.assertEqual(bytes(result), original)
            self.assertEqual(self.lib.vg_quality_apply_motion_ratio(ct.byref(cfg), .5, value, None, ct.byref(result)), -1)
            self.assertEqual(bytes(result), original)


if __name__ == "__main__":
    unittest.main()
