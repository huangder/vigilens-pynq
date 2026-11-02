"""Offline C/Python rule, explanation, trigger-order and vital-gating parity.

python -m unittest discover -s board/autonomy/tests -p test_decision.py -v
Synthetic inputs validate implementation parity, not fatigue accuracy.
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
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.decision import DecisionEngine


class Config(ct.Structure):
    _fields_ = [(k, ct.c_double) for k in ("quality_min_score", "face_visible_min", "pose_yaw_max_deg",
                  "pose_pitch_max_deg", "perclos_warning", "fatigue_blink_rate_low", "vital_require_quality")]
    _fields_ += [("fatigue_yawn_count", ct.c_uint64), ("fatigue_long_close_count", ct.c_uint64), ("pose_hold_frames", ct.c_uint32)]


class Face(ct.Structure):
    _fields_ = [(k, ct.c_double) for k in ("visible", "yaw", "pitch")]


class Input(ct.Structure):
    _fields_ = [("perclos", ct.c_double), ("blink_rate_per_min", ct.c_double),
                ("long_close_count", ct.c_uint64), ("yawn_count", ct.c_uint64)]
    _fields_ += [(k, ct.c_double) for k in ("overall", "light_score", "motion_score")]
    _fields_ += [("face", Face)]


class Trigger(ct.Structure):
    _fields_ = [(k, ct.c_char_p) for k in ("rule", "metric", "verdict")]
    _fields_ += [("value", ct.c_double), ("threshold", ct.c_double)]


class Result(ct.Structure):
    _fields_ = [("status", ct.c_char*24), ("advice", ct.c_char*256), ("reason", ct.c_char*2048),
                ("trigger_count", ct.c_uint), ("triggers", Trigger*5)]


class Vitals(ct.Structure):
    _fields_ = [("value", ct.c_double*4), ("present", ct.c_int*4)]


def sample(**overrides):
    values = dict(perclos=.1, blink_rate_per_min=18., long_close_count=0, yawn_count=0,
                  overall=.9, light_score=.9, motion_score=.1, visible=1., yaw=0., pitch=0.)
    values.update(overrides)
    return Input(*(values[k] for k, _ in Input._fields_[:-1]), Face(*(values[k] for k in ("visible", "yaw", "pitch"))))


def face_dict(f):
    return {"visible": f.visible, "pose": {"yaw": f.yaw, "pitch": f.pitch, "roll": 0.}}


class DecisionParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vg_decision_")
        lib = Path(cls.temp.name)/("decision.dll" if os.name == "nt" else "decision.so")
        args = shlex.split(os.environ.get("CC", "gcc"))
        args += ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared"]
        args += ["-static-libgcc"] if os.name == "nt" else ["-fPIC"]
        args += [str(ROOT/"board/autonomy/decision.c"), "-lm", "-o", str(lib)]
        subprocess.run(args, check=True, capture_output=True, text=True)
        cls.lib = ct.CDLL(str(lib))
        cls.lib.vg_decision_create.argtypes = [ct.POINTER(Config)]
        cls.lib.vg_decision_create.restype = ct.c_void_p
        cls.lib.vg_decision_destroy.argtypes = [ct.c_void_p]
        cls.lib.vg_decision_decide.argtypes = [ct.c_void_p, ct.POINTER(Input), ct.POINTER(Result)]
        cls.lib.vg_decision_vitals_allowed.argtypes = [ct.c_void_p, ct.c_double, ct.POINTER(Face), ct.c_char_p]
        cls.lib.vg_decision_gate_vitals.argtypes = [ct.c_void_p, ct.c_double, ct.POINTER(Vitals),
                                                  ct.POINTER(Face), ct.c_char_p, ct.POINTER(Vitals)]
        cls.cfg = load_config()

    @classmethod
    def tearDownClass(cls):
        if os.name == "nt":
            import _ctypes
            _ctypes.FreeLibrary(cls.lib._handle)
        cls.lib = None
        cls.temp.cleanup()

    def engine(self, cfg):
        c = Config(*(cfg[k] for k, _ in Config._fields_))
        e = self.lib.vg_decision_create(ct.byref(c))
        self.assertTrue(e)
        return e

    def compare(self, samples, cfg=None):
        cfg = cfg or self.cfg
        py = DecisionEngine(cfg)
        engine = self.engine(cfg)
        outputs = []
        try:
            for frame_id, value in enumerate(samples):
                r = Result()
                self.assertEqual(self.lib.vg_decision_decide(engine, ct.byref(value), ct.byref(r)), 0)
                actual = {k: getattr(r, k).decode() for k in ("status", "advice", "reason")}
                actual["triggers"] = [{k: getattr(t, k).decode() for k in ("rule", "metric", "verdict")}
                                      | {"value": t.value, "threshold": t.threshold}
                                      for t in r.triggers[:r.trigger_count]]
                expected = py.decide(frame_id=frame_id,
                    behavior={k: getattr(value, k) for k in ("perclos", "blink_rate_per_min", "long_close_count", "yawn_count")},
                    quality={k: getattr(value, k) for k in ("overall", "light_score", "motion_score")}, face=face_dict(value.face))
                self.assertEqual(actual, expected, f"frame {frame_id}")
                outputs.append(actual)
                for status in (None, actual["status"], "disconnected", "done"):
                    self.assertEqual(bool(self.lib.vg_decision_vitals_allowed(engine, value.overall, ct.byref(value.face),
                                      status.encode() if status else None)),
                                     py.vitals_allowed(value.overall, face_dict(value.face), status))
        finally:
            self.lib.vg_decision_destroy(engine)
        return outputs

    def test_priority_and_all_fatigue_triggers(self):
        r = self.compare([sample(perclos=.9, long_close_count=3, yawn_count=4, blink_rate_per_min=2),
                          sample(overall=.1, perclos=.9, visible=.1), sample(visible=.1, perclos=.9)])
        self.assertEqual([x["status"] for x in r], ["fatigue_risk", "unreliable", "adjust_posture"])
        self.assertEqual(len(r[0]["triggers"]), 5)

    def test_quality_weakest_motion_complement_and_ties(self):
        self.compare([sample(overall=.1, light_score=.2, motion_score=.9),
                      sample(overall=.1, light_score=.5, motion_score=.5, visible=.5),
                      sample(overall=.1, light_score=.5, motion_score=.5, visible=.1)])

    def test_exact_boundaries_and_neighbors(self):
        c = self.cfg
        frames = []
        for key, threshold in (("overall", c["quality_min_score"]), ("visible", c["face_visible_min"]),
                               ("yaw", c["pose_yaw_max_deg"]), ("pitch", c["pose_pitch_max_deg"]),
                               ("perclos", c["perclos_warning"]), ("blink_rate_per_min", c["fatigue_blink_rate_low"])):
            for value in (math.nextafter(threshold, -math.inf), threshold, math.nextafter(threshold, math.inf)):
                frames.append(sample(**{key: value}))
        frames += [sample(blink_rate_per_min=0), sample(blink_rate_per_min=-1),
                   sample(yawn_count=c["fatigue_yawn_count"]), sample(long_close_count=c["fatigue_long_close_count"])]
        self.compare(frames, dict(c, pose_hold_frames=1))

    def test_pose_hysteresis_and_early_return_freeze(self):
        cfg = dict(self.cfg, pose_hold_frames=3)
        out = sample(yaw=-50)
        calm = sample()
        r = self.compare([out, sample(overall=.1), out, sample(visible=.1), out,
                          calm, sample(overall=.1), calm, calm,
                          sample(pitch=40), sample(yaw=50, pitch=40), sample(pitch=40)], cfg)
        self.assertEqual(r[4]["status"], "adjust_posture")
        self.assertEqual(r[5]["status"], "adjust_posture")
        self.assertEqual(r[8]["status"], "normal")
        self.assertIn("pitch", r[-1]["reason"])

    def test_pose_hold_zero_clamps_to_one(self):
        self.compare([sample(yaw=50), sample()], dict(self.cfg, pose_hold_frames=0))

    def test_seeded_rule_sequences(self):
        rng = random.Random(1007)
        frames = [sample(overall=rng.choice((.1, .6, .9)), visible=rng.choice((.1, .7, 1.)),
                         light_score=rng.random(), motion_score=rng.random(),
                         yaw=rng.uniform(-70, 70), pitch=rng.uniform(-60, 60),
                         perclos=rng.random(), blink_rate_per_min=rng.uniform(0, 30),
                         long_close_count=rng.randrange(3), yawn_count=rng.randrange(4)) for _ in range(5000)]
        self.compare(frames)

    def test_vital_null_mask_and_optional_face_status(self):
        c = self.cfg
        engine = self.engine(c)
        py = DecisionEngine(c)
        keys = ("hr_bpm", "hr_conf", "rr_per_min", "rr_conf")
        try:
            for q, f, status, mask in itertools.product(
                    (.1, c["vital_require_quality"], .99),
                    (None, Face(1., 0., 0.), Face(.1, 0., 0.), Face(1., 60., 0.)),
                    (None, "normal", "adjust_posture", "unreliable", "disconnected", "done"),
                    range(16)):
                vital = {k: 1.25+i for i, k in enumerate(keys) if mask & (1<<i)}
                inv = Vitals((ct.c_double*4)(*(vital.get(k, 0.) for k in keys)),
                             (ct.c_int*4)(*(int(k in vital) for k in keys)))
                result = Vitals()
                self.lib.vg_decision_gate_vitals(engine, q, ct.byref(inv), ct.byref(f) if f else None,
                                               status.encode() if status else None, ct.byref(result))
                actual = {k: result.value[i] if result.present[i] else None for i, k in enumerate(keys)}
                self.assertEqual(actual, py.gate_vitals(q, vital, face=face_dict(f) if f else None, status=status))
        finally:
            self.lib.vg_decision_destroy(engine)

    def test_invalid_input_does_not_change_result(self):
        engine = self.engine(self.cfg)
        try:
            r = Result()
            self.assertEqual(self.lib.vg_decision_decide(engine, ct.byref(sample()), ct.byref(r)), 0)
            original = bytes(r)
            self.assertEqual(self.lib.vg_decision_decide(engine, ct.byref(sample(overall=math.nan)), ct.byref(r)), -1)
            self.assertEqual(bytes(r), original)
        finally:
            self.lib.vg_decision_destroy(engine)


if __name__ == "__main__":
    unittest.main()
