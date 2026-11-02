"""Synthetic geometry/state parity, not real-person accuracy or board timing.

Run: python -m unittest discover -s board/autonomy/tests -p test_behavior.py -v
Requires a native C compiler (CC or gcc); builds a temporary shared library.
"""
from __future__ import annotations

import ctypes as ct
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

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.behavior_metrics import BehaviorTracker
from backend.config import load_config
from backend.face_landmark import ear, mar


class Point(ct.Structure):
    _fields_ = [("x", ct.c_double), ("y", ct.c_double)]


class Eye(ct.Structure):
    _fields_ = [("present", ct.c_int), ("p", Point * 6)]


class Mouth(ct.Structure):
    _fields_ = [("present", ct.c_int)] + [(k, Point) for k in ("left", "right", "top", "bottom")]


class Observation(ct.Structure):
    _fields_ = [("eyes_present", ct.c_int), ("visible", ct.c_double),
                ("left", Eye), ("right", Eye), ("mouth", Mouth)]


class Config(ct.Structure):
    _fields_ = [(k, ct.c_double) for k in (
        "ear_close_threshold", "min_close_ms", "window_seconds", "long_close_ms",
        "mar_threshold", "yawn_min_duration_ms")]


class Result(ct.Structure):
    _fields_ = [(k, ct.c_double) for k in (
        "ear_left", "ear_right", "blink_rate_per_min", "perclos", "mar", "dt")]
    _fields_ += [("blink_state", ct.c_int)]
    _fields_ += [(k, ct.c_uint64) for k in ("blink_count", "long_close_count", "yawn_count")]


def eye(value):
    return {"p1": (0., 0.), "p2": (.25, value/2), "p3": (.75, value/2),
            "p4": (1., 0.), "p5": (.75, -value/2), "p6": (.25, -value/2)}


def observation(value, mouth=0., visible=1., missing_eyes=False):
    return SimpleNamespace(eyes={} if missing_eyes else {"left": eye(value), "right": eye(value)},
                           mouth={"left": (0., 0.), "right": (1., 0.),
                                  "top": (.5, 0.), "bottom": (.5, mouth)}, visible=visible)


def native(obs):
    def e(points):
        return Eye(bool(points), (Point*6)(*(Point(*points[f"p{i}"]) for i in range(1, 7)))) if points else Eye()
    m = obs.mouth
    return Observation(bool(obs.eyes), obs.visible, e(obs.eyes.get("left")), e(obs.eyes.get("right")),
                       Mouth(1, *(Point(*m[k]) for k in ("left", "right", "top", "bottom"))) if m else Mouth())


class BehaviorParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vg_behavior_")
        library = Path(cls.temp.name)/("behavior.dll" if os.name == "nt" else "behavior.so")
        command = shlex.split(os.environ.get("CC", "gcc"))
        command += ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared"]
        command += ["-static-libgcc"] if os.name == "nt" else ["-fPIC"]
        command += [str(ROOT/"board/autonomy/behavior.c"), "-lm", "-o", str(library)]
        subprocess.run(command, check=True, capture_output=True, text=True)
        cls.lib = ct.CDLL(str(library))
        cls.lib.vg_behavior_create.argtypes = [ct.POINTER(Config)]
        cls.lib.vg_behavior_create.restype = ct.c_void_p
        cls.lib.vg_behavior_destroy.argtypes = [ct.c_void_p]
        cls.lib.vg_behavior_update.argtypes = [ct.c_void_p, ct.c_double, ct.POINTER(Observation), ct.POINTER(Result)]
        cls.lib.vg_ear.argtypes = [ct.POINTER(Eye)]
        cls.lib.vg_ear.restype = ct.c_double
        cls.lib.vg_mar.argtypes = [ct.POINTER(Mouth)]
        cls.lib.vg_mar.restype = ct.c_double
        cls.cfg = load_config()

    @classmethod
    def tearDownClass(cls):
        # Windows keeps a loaded DLL locked until explicitly unloaded.
        if os.name == "nt":
            import _ctypes
            _ctypes.FreeLibrary(cls.lib._handle)
        cls.lib = None
        cls.temp.cleanup()

    def compare(self, frames, cfg=None):
        cfg = cfg or self.cfg
        py = BehaviorTracker(cfg)
        cc = Config(*(cfg[k] for k, _ in Config._fields_))
        tracker = self.lib.vg_behavior_create(ct.byref(cc))
        self.assertTrue(tracker)
        outputs = []
        try:
            for i, (ts, obs) in enumerate(frames):
                r = Result()
                self.assertEqual(self.lib.vg_behavior_update(tracker, ts, ct.byref(native(obs)), ct.byref(r)), 0)
                actual = {k: round(getattr(r, k), 2 if k == "blink_rate_per_min" else 4)
                          for k in ("ear_left", "ear_right", "blink_rate_per_min", "perclos", "mar")}
                actual.update({k: getattr(r, k) for k in ("blink_count", "long_close_count", "yawn_count")})
                actual["blink_state"] = ("OPEN", "CLOSING", "CLOSED", "OPENING")[r.blink_state]
                actual["_dt"] = r.dt
                expected = py.update(i, ts, obs)
                self.assertEqual(actual, expected, f"frame {i}, ts={ts}")
                outputs.append(actual)
        finally:
            self.lib.vg_behavior_destroy(tracker)
        return outputs

    def test_geometry_empty_degenerate_and_oblique(self):
        rng = random.Random(13)
        for _ in range(200):
            p = {f"p{i}": (rng.uniform(-100, 100), rng.uniform(-100, 100)) for i in range(1, 7)}
            obs = observation(.3)
            obs.eyes["left"] = p
            c = native(obs)
            self.assertAlmostEqual(self.lib.vg_ear(ct.byref(c.left)), ear(p), places=12)
            self.assertAlmostEqual(self.lib.vg_mar(ct.byref(c.mouth)), mar(obs.mouth), places=12)
        for width in (0., 1e-10, 1e-9, math.nextafter(1e-9, math.inf)):
            obs = observation(.3)
            obs.eyes["left"]["p4"] = (width, 0.)
            obs.mouth["right"] = (width, 0.)
            self.compare([(0., obs)])
        obs.eyes = {"right": {}}
        obs.mouth = {}
        self.compare([(0., obs)])

    def test_blink_short_and_long_at_two_rates(self):
        for fps in (30, 60):
            with self.subTest(fps=fps):
                result = self.compare([(i/fps, observation(.05 if 1<=i/fps<=1.2 or 2<=i/fps<=2.8 else .3))
                                       for i in range(fps*4)])
                self.assertEqual(result[-1]["blink_count"], 1)
                self.assertEqual(result[-1]["long_close_count"], 1)

    def test_exact_duration_boundaries(self):
        cfg = dict(self.cfg, min_close_ms=125., long_close_ms=500., yawn_min_duration_ms=250.)
        low = cfg["ear_close_threshold"]/2
        high = cfg["ear_close_threshold"]*2
        # Exactly min-close -> CLOSED, exactly long-close -> long count; yawn counts only once.
        r = self.compare([(0., observation(low, 1.)), (.125, observation(low, 1.)),
                          (.25, observation(low, 1.)), (.5, observation(high, 1.)),
                          (.625, observation(high, 1.))], cfg)
        self.assertEqual(r[1]["blink_state"], "CLOSED")
        self.assertEqual(r[-1]["long_close_count"], 1)
        self.assertEqual(r[-1]["yawn_count"], 1)

    def test_strict_ear_and_mar_thresholds(self):
        cfg = self.cfg
        r = self.compare([(0., observation(cfg["ear_close_threshold"], cfg["mar_threshold"])),
                          (2., observation(cfg["ear_close_threshold"], cfg["mar_threshold"]))])
        self.assertEqual(r[-1]["blink_state"], "OPEN")
        self.assertEqual(r[-1]["yawn_count"], 0)

    def test_nextafter_duration_and_reclosing_during_opening(self):
        cfg = dict(self.cfg, min_close_ms=125., long_close_ms=500.)
        below = math.nextafter(.125, -math.inf)
        # Reclosing while OPENING deliberately retains that state until CLOSED.
        r = self.compare([(0., observation(.05)), (below, observation(.05)),
                          (.125, observation(.05)), (.25, observation(.3)),
                          (.3125, observation(.05)), (.4375, observation(.05)),
                          (.5, observation(.3)), (.625, observation(.3))], cfg)
        self.assertEqual(r[1]["blink_state"], "CLOSING")
        self.assertEqual(r[2]["blink_state"], "CLOSED")
        self.assertEqual(r[4]["blink_state"], "OPENING")
        self.assertEqual(r[5]["blink_state"], "CLOSED")
        self.assertEqual(r[-1]["blink_count"], 2)

    def test_just_below_long_close_remains_blink(self):
        cfg = dict(self.cfg, min_close_ms=125., long_close_ms=500.)
        r = self.compare([(0., observation(.05)), (.125, observation(.05)),
                          (math.nextafter(.5, -math.inf), observation(.3))], cfg)
        self.assertEqual(r[-1]["blink_count"], 1)
        self.assertEqual(r[-1]["long_close_count"], 0)

    def test_face_loss_cancels_confirmed_closure_and_yawn(self):
        r = self.compare([(0., observation(.05, 1.)), (.2, observation(.05, 1.)),
                          (.4, observation(.05, 1., visible=0)),
                          (2., observation(.3)), (2.1, observation(.05)),
                          (2.3, observation(.05)), (2.4, observation(.3))])
        self.assertEqual(r[-1]["long_close_count"], 0)
        self.assertEqual(r[-1]["blink_count"], 1)
        self.assertEqual(r[-1]["yawn_count"], 0)

    def test_missing_eyes_is_face_loss(self):
        self.compare([(0., observation(.05)), (.2, observation(.05)),
                      (.3, observation(.05, missing_eyes=True)), (2., observation(.3))])

    def test_window_cutoff_inclusive_and_frame_weighting(self):
        cfg = dict(self.cfg, window_seconds=1.)
        r = self.compare([(0., observation(.05)), (.125, observation(.3)),
                          (1., observation(.3)), (math.nextafter(1., math.inf), observation(.3))], cfg)
        self.assertEqual(r[2]["perclos"], round(1/3, 4))
        self.assertEqual(r[3]["perclos"], 0.)

    def test_blink_timestamp_eviction_preserves_cumulative_count(self):
        cfg = dict(self.cfg, window_seconds=1., min_close_ms=125.)
        r = self.compare([(0., observation(.05)), (.125, observation(.05)), (.25, observation(.3)),
                          (1.25, observation(.3)), (1.5, observation(.3))], cfg)
        self.assertEqual(r[3]["blink_rate_per_min"], 60.)
        self.assertEqual(r[4]["blink_rate_per_min"], 0.)
        self.assertEqual(r[4]["blink_count"], 1)

    def test_yawn_rearms_and_zero_duration_still_needs_second_sample(self):
        cfg = dict(self.cfg, yawn_min_duration_ms=0.)
        r = self.compare([(0., observation(.3, 1.)), (0., observation(.3, 1.)),
                          (.1, observation(.3)), (.2, observation(.3, 1.)),
                          (.3, observation(.3, 1.))], cfg)
        self.assertEqual(r[0]["yawn_count"], 0)
        self.assertEqual(r[-1]["yawn_count"], 2)

    def test_seeded_irregular_long_stream_wraps_and_grows_queues(self):
        rng = random.Random(719)
        ts = 0.
        frames = []
        for _ in range(12000):
            ts += rng.choice((0., .01, .03, .125, .5, 3.))
            frames.append((ts, observation(rng.choice((.05, .21, .3)),
                                          rng.choice((0., .6, 1.)), rng.choice((0., .5, 1.)))))
        self.compare(frames, dict(self.cfg, window_seconds=2.))

    def test_invalid_config_and_backward_time_leave_output_unchanged(self):
        cfg = Config(*(self.cfg[k] for k, _ in Config._fields_))
        cfg.window_seconds = 0
        self.assertFalse(self.lib.vg_behavior_create(ct.byref(cfg)))
        cfg.window_seconds = self.cfg["window_seconds"]
        tracker = self.lib.vg_behavior_create(ct.byref(cfg))
        try:
            obs, result = native(observation(.3)), Result()
            self.assertEqual(self.lib.vg_behavior_update(tracker, 1., ct.byref(obs), ct.byref(result)), 0)
            original = bytes(result)
            for ts in (0., math.inf, math.nan):
                self.assertEqual(self.lib.vg_behavior_update(tracker, ts, ct.byref(obs), ct.byref(result)), -1)
                self.assertEqual(bytes(result), original)
            self.assertEqual(self.lib.vg_behavior_update(tracker, 2., ct.byref(obs), ct.byref(result)), 0)
            self.assertEqual(result.dt, 1.)
        finally:
            self.lib.vg_behavior_destroy(tracker)


if __name__ == "__main__":
    unittest.main()
