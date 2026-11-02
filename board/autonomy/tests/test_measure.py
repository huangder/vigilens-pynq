"""Composition parity at geometry/statistics boundary, without a model/board.

python -m unittest discover -s board/autonomy/tests -p test_measure.py -v
"""
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_behavior as b
import test_quality as q
import test_decision as d
from backend.behavior_metrics import BehaviorTracker
from backend.quality import QualityScorer
from backend.decision import DecisionEngine
from backend.config import load_config


class Config(ct.Structure):
    _fields_ = [("behavior", b.Config), ("quality", q.Config), ("decision", d.Config)]


class Input(ct.Structure):
    _fields_ = [("frame_id", ct.c_uint64), ("ts", ct.c_double), ("mean_luma", ct.c_double),
                ("motion_q16", ct.c_uint32), ("motion_valid", ct.c_int),
                ("observation", b.Observation), ("face", d.Face)]


class Result(ct.Structure):
    _fields_ = [("frame_id", ct.c_uint64), ("ts", ct.c_double), ("behavior", b.Result),
                ("quality", q.Result), ("decision", d.Result), ("vital", d.Vitals)]


def native_config(cfg):
    return Config(b.Config(*(cfg[k] for k, _ in b.Config._fields_)),
                  q.Config(cfg["light_target"], cfg["motion_score_gain"],
                           *(cfg["quality_weights"][k] for k in ("light", "motion", "face"))),
                  d.Config(*(cfg[k] for k, _ in d.Config._fields_)))


def frame(frame_id, ts, value=.3, mouth=0., visible=1., yaw=0., pitch=0., mean=.5, motion=0):
    return Input(frame_id, ts, mean, motion, 1, b.native(b.observation(value, mouth, visible)),
                 d.Face(visible, yaw, pitch))


def decoded(r):
    beh = {k: getattr(r.behavior, k) for k in ("ear_left", "ear_right", "mar", "perclos", "blink_rate_per_min",
                                                   "blink_count", "long_close_count", "yawn_count")}
    beh.update(blink_state=("OPEN", "CLOSING", "CLOSED", "OPENING")[r.behavior.blink_state], _dt=r.behavior.dt)
    quality = {k: getattr(r.quality, k) for k in ("light_score", "motion_score", "overall")}
    quality.update(_mean_luma=r.quality.mean_luma, _motion_ratio=r.quality.motion_ratio, _mode="pixels")
    decision = {k: getattr(r.decision, k).decode() for k in ("status", "advice", "reason")}
    decision["triggers"] = [{k: getattr(t, k).decode() for k in ("rule", "metric", "verdict")}
                            | {"value": t.value, "threshold": t.threshold}
                            for t in r.decision.triggers[:r.decision.trigger_count]]
    return beh, quality, decision


class MeasureParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vg_measure_")
        library = Path(cls.temp.name)/("measure.dll" if os.name == "nt" else "measure.so")
        args = shlex.split(os.environ.get("CC", "gcc"))
        args += ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared"]
        args += ["-static-libgcc"] if os.name == "nt" else ["-fPIC"]
        args += [str(ROOT/f"board/autonomy/{name}.c") for name in ("measure", "behavior", "quality", "decision")]
        args += ["-lm", "-o", str(library)]
        subprocess.run(args, check=True, capture_output=True, text=True)
        cls.lib = ct.CDLL(str(library))
        cls.lib.vg_measure_create.argtypes = [ct.POINTER(Config)]
        cls.lib.vg_measure_create.restype = ct.c_void_p
        cls.lib.vg_measure_destroy.argtypes = [ct.c_void_p]
        cls.lib.vg_measure_update.argtypes = [ct.c_void_p, ct.POINTER(Input), ct.POINTER(Result)]
        cls.lib.vg_round_decimal.argtypes = [ct.c_double, ct.c_uint]
        cls.lib.vg_round_decimal.restype = ct.c_double
        cls.cfg = load_config()

    @classmethod
    def tearDownClass(cls):
        if os.name == "nt":
            import _ctypes
            _ctypes.FreeLibrary(cls.lib._handle)
        cls.lib = None
        cls.temp.cleanup()

    def engine(self):
        cfg = native_config(self.cfg)
        engine = self.lib.vg_measure_create(ct.byref(cfg))
        self.assertTrue(engine)
        return engine

    def compare_frames(self, frames):
        behavior, quality, decision = BehaviorTracker(self.cfg), QualityScorer(self.cfg), DecisionEngine(self.cfg)
        engine = self.engine()
        try:
            for i in frames:
                result = Result()
                self.assertEqual(self.lib.vg_measure_update(engine, ct.byref(i), ct.byref(result)), 0)
                def eye_dict(e):
                    return {f"p{k+1}": (p.x, p.y) for k, p in enumerate(e.p)} if e.present else {}
                obs = SimpleNamespace(visible=i.observation.visible,
                    eyes={"left": eye_dict(i.observation.left), "right": eye_dict(i.observation.right)} if i.observation.eyes_present else {},
                    mouth={k: (getattr(i.observation.mouth, k).x, getattr(i.observation.mouth, k).y)
                           for k in ("left", "right", "top", "bottom")} if i.observation.mouth.present else {})
                expected_behavior = behavior.update(i.frame_id, i.ts, obs)
                # Mirror statistics boundary without reimplementing pixel conversion.
                quality._prev_gray = None
                quality._last_motion["motion_ratio_q16"] = i.motion_q16
                with patch("backend.quality.to_gray", return_value=SimpleNamespace(mean=lambda: i.mean_luma*255.)):
                    expected_quality = quality.update(i.frame_id, i.ts, object(), obs)
                expected_decision = decision.decide(frame_id=i.frame_id, behavior=expected_behavior,
                    quality=expected_quality, face=d.face_dict(i.face))
                self.assertEqual(decoded(result), (expected_behavior, expected_quality, expected_decision), f"frame {i.frame_id}")
                self.assertEqual((result.frame_id, result.ts), (i.frame_id, i.ts))
                self.assertEqual(list(result.vital.present), [0]*4)
        finally:
            self.lib.vg_measure_destroy(engine)

    def test_decimal_round_matches_python_at_ties_neighbors_and_random(self):
        rng = random.Random(731)
        for digits in (0, 1, 2, 3, 4):
            values = [0., -0., 2.675, 1.23445, .00005, -.00005, 1e-310, -1e-310, 1e308, -1e308]
            for k in range(-100, 101):
                middle = (k+.5)/(10**digits)
                values += [math.nextafter(middle, -math.inf), middle, math.nextafter(middle, math.inf)]
            values += [rng.uniform(-1e5, 1e5) for _ in range(1000)]
            for value in values:
                actual = self.lib.vg_round_decimal(value, digits)
                expected = round(value, digits)
                self.assertEqual(actual, expected, (value, digits))
                if actual == 0.: self.assertEqual(math.copysign(1., actual), math.copysign(1., expected))
        self.assertTrue(math.isnan(self.lib.vg_round_decimal(1., 5)))

    def test_full_chain_seeded_irregular_sequences(self):
        rng = random.Random(119)
        ts = 0.
        frames = []
        for idx in range(3000):
            ts += rng.choice((.01, .033, .125, .5))
            frames.append(frame(idx, ts, value=rng.choice((.05, .21, .3)), mouth=rng.choice((0., .6, 1.)),
                visible=rng.choice((0., .7, 1.)), yaw=rng.choice((-50., 0., 50.)), pitch=rng.choice((0., 40.)),
                mean=rng.choice((0., .25, .5, .75, 1.)), motion=rng.choice((0, 1024, 16384, 65536))))
        self.compare_frames(frames)

    def test_rounding_before_decision_changes_boundary_result(self):
        # Overall just below its threshold can round up to the quality gate boundary.
        weights = self.cfg["quality_weights"]
        light = (self.cfg["quality_min_score"]*sum(weights.values()) - weights["motion"] - weights["face"])/weights["light"]
        mean = self.cfg["light_target"] - (1.-light)/2. - 1e-7
        engine = self.engine()
        try:
            result = Result()
            value = frame(1, 0., mean=mean)
            self.assertEqual(self.lib.vg_measure_update(engine, ct.byref(value), ct.byref(result)), 0)
            self.assertEqual(result.quality.overall, self.cfg["quality_min_score"])
            self.assertEqual(result.decision.status.decode(), "normal")
        finally:
            self.lib.vg_measure_destroy(engine)
        self.compare_frames([frame(1, 0., mean=mean, motion=0),
                             frame(2, .125, value=.21000001, mouth=.60000001, mean=.333333, motion=8191)])

    def test_timestamp_difference_overflow_is_rejected(self):
        engine = self.engine()
        try:
            out = Result()
            first = frame(1, -sys.float_info.max)
            bad = frame(2, sys.float_info.max)
            self.assertEqual(self.lib.vg_measure_update(engine, ct.byref(first), ct.byref(out)), 0)
            original = bytes(out)
            self.assertEqual(self.lib.vg_measure_update(engine, ct.byref(bad), ct.byref(out)), -1)
            self.assertEqual(bytes(out), original)
            valid = frame(2, math.nextafter(-sys.float_info.max, 0.))
            self.assertEqual(self.lib.vg_measure_update(engine, ct.byref(valid), ct.byref(out)), 0)
        finally:
            self.lib.vg_measure_destroy(engine)

    def test_rejected_frames_do_not_pollute_state_or_output(self):
        control, tested = self.engine(), self.engine()
        try:
            out = Result()
            first = frame(10, 1., value=.05, yaw=50)
            self.assertEqual(self.lib.vg_measure_update(tested, ct.byref(first), ct.byref(out)), 0)
            control_out = Result()
            self.assertEqual(self.lib.vg_measure_update(control, ct.byref(first), ct.byref(control_out)), 0)
            original = bytes(out)
            bad_frames = [(frame(10, 2.), -3), (frame(11, 1.), -3), (frame(9, 2.), -3),
                          (frame(11, .5), -3), (frame(11, math.nan), -1),
                          (frame(11, 2., motion=65537), -1)]
            invalid_motion = frame(11, 2., value=.05, yaw=50)
            invalid_motion.motion_valid = 0
            bad_frames.append((invalid_motion, -2))
            mismatch = frame(11, 2.)
            mismatch.observation.visible = .5
            bad_frames.append((mismatch, -1))
            nonfinite = frame(11, 2.)
            nonfinite.observation.left.p[0].x = math.nan
            bad_frames.append((nonfinite, -1))
            for bad, code in bad_frames:
                self.assertEqual(self.lib.vg_measure_update(tested, ct.byref(bad), ct.byref(out)), code)
                self.assertEqual(bytes(out), original)
            for good in (frame(11, 1.125, value=.05, yaw=50), frame(12, 1.25, value=.3, yaw=50), frame(13, 1.5)):
                self.assertEqual(self.lib.vg_measure_update(tested, ct.byref(good), ct.byref(out)), 0)
                self.assertEqual(self.lib.vg_measure_update(control, ct.byref(good), ct.byref(control_out)), 0)
                self.assertEqual(decoded(out), decoded(control_out))
        finally:
            self.lib.vg_measure_destroy(control)
            self.lib.vg_measure_destroy(tested)

    def test_finite_geometry_overflow_rejected_before_behavior_state_changes(self):
        tested, control = self.engine(), self.engine()
        try:
            first = frame(1, 0., value=.05)
            out, baseline = Result(), Result()
            for engine in (tested, control):
                self.assertEqual(self.lib.vg_measure_update(engine, ct.byref(first), ct.byref(out)), 0)
            original = bytes(out)
            bad = frame(2, .125, value=.05)
            bad.observation.left.p[0].x = -sys.float_info.max
            bad.observation.left.p[3].x = sys.float_info.max
            bad.observation.left.p[1].y = -sys.float_info.max
            bad.observation.left.p[5].y = sys.float_info.max
            self.assertEqual(self.lib.vg_measure_update(tested, ct.byref(bad), ct.byref(out)), -1)
            self.assertEqual(bytes(out), original)
            for good in (frame(2, .125, value=.05), frame(3, .25)):
                self.assertEqual(self.lib.vg_measure_update(tested, ct.byref(good), ct.byref(out)), 0)
                self.assertEqual(self.lib.vg_measure_update(control, ct.byref(good), ct.byref(baseline)), 0)
                self.assertEqual(decoded(out), decoded(baseline))
        finally:
            self.lib.vg_measure_destroy(tested)
            self.lib.vg_measure_destroy(control)


if __name__ == "__main__":
    unittest.main()
