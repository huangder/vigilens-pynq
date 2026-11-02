"""Native contract writer: schema, escaping, vital gating and refusal paths.

Builds the real C core through `vg_native`, drives it with synthetic geometry, and
validates every emitted line with the project's own `backend.contract`. Synthetic
only: this proves the wiring and the schema, not measurement accuracy.
"""
from __future__ import annotations

import ctypes as ct
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vg_native as vn  # noqa: E402
from backend.config import load_config  # noqa: E402
from backend.contract import validate_frame  # noqa: E402
from backend.vital import load_fir_coeffs  # noqa: E402


def eye(value: float):
    return {"p1": (0.0, 0.0), "p2": (0.25, value / 2), "p3": (0.75, value / 2),
            "p4": (1.0, 0.0), "p5": (0.75, -value / 2), "p6": (0.25, -value / 2)}


MOUTH = {"left": (0.0, 0.0), "right": (1.0, 0.0), "top": (0.5, -0.15), "bottom": (0.5, 0.15)}


class Frame:
    def __init__(self, index: int, *, visible=1.0, ear=0.3, mean_luma=0.5,
                 motion_q16=1000, motion_valid=1, yaw=0.0, pitch=0.0,
                 bbox=(100, 100, 200, 200)):
        self.input = vn.MeasureInput()
        self.input.frame_id = index
        self.input.ts = index / 30.0
        self.input.mean_luma = mean_luma
        self.input.motion_q16 = motion_q16
        self.input.motion_valid = motion_valid
        self.input.observation = vn.observation(
            eye(ear) if visible else None, eye(ear) if visible else None,
            MOUTH if visible else None, visible)
        self.input.face.visible = visible
        self.input.face.yaw = yaw
        self.input.face.pitch = pitch
        self.bbox = bbox
        self.yaw, self.pitch, self.roll = yaw, pitch, 0.0


class ContractNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = vn.NativeCore()
        cls.cfg = load_config()
        cls.coeffs, cls.shift, _ = load_fir_coeffs()

    def engine(self):
        config = vn.measure_config(self.cfg)
        handle = self.core.lib.vg_measure_create(ct.byref(config))
        self.assertTrue(handle)
        self.addCleanup(self.core.lib.vg_measure_destroy, handle)
        return handle

    def update(self, engine, frame: Frame):
        result = vn.MeasureResult()
        rc = self.core.lib.vg_measure_update(engine, ct.byref(frame.input), ct.byref(result))
        return rc, result

    def contract(self, result, frame: Frame, vital=None):
        face = vn.ContractFace()
        face.bbox[:] = [int(v) for v in frame.bbox]
        face.yaw, face.pitch, face.roll = frame.yaw, frame.pitch, frame.roll
        face.visible = frame.input.face.visible
        return self.core.contract_line(result, face, vital)

    def test_emitted_line_is_a_valid_frame(self):
        engine = self.engine()
        for index in range(1, 6):
            rc, result = self.update(engine, Frame(index))
            self.assertEqual(rc, 0)
            obj = json.loads(self.contract(result, Frame(index)))
            self.assertEqual(validate_frame(obj), [])
            self.assertEqual(obj["frame_id"], index)
            self.assertEqual(obj["face"]["bbox"], [100, 100, 200, 200])
            self.assertIn(obj["behavior"]["blink_state"],
                          ("OPEN", "CLOSING", "CLOSED", "OPENING"))
            self.assertEqual(set(obj["vital"]), {"hr_bpm", "hr_conf", "rr_per_min", "rr_conf"})

    def test_advice_and_reason_are_nonempty_and_escaped(self):
        engine = self.engine()
        rc, result = self.update(engine, Frame(1, visible=0.0))
        self.assertEqual(rc, 0)
        line = self.contract(result, Frame(1, visible=0.0))
        obj = json.loads(line)
        self.assertEqual(validate_frame(obj), [])
        self.assertTrue(obj["advice"].strip())
        self.assertTrue(obj["reason"].strip())
        self.assertNotIn("\n", obj["advice"])

    def test_motion_invalid_is_refused_before_state(self):
        engine = self.engine()
        rc, _ = self.update(engine, Frame(0, motion_valid=0))
        self.assertEqual(rc, -2)

    def test_nonmonotonic_frame_is_refused(self):
        engine = self.engine()
        self.assertEqual(self.update(engine, Frame(5))[0], 0)
        self.assertEqual(self.update(engine, Frame(5))[0], -3)

    def test_insufficient_buffer_and_invalid_face(self):
        engine = self.engine()
        rc, result = self.update(engine, Frame(1))
        self.assertEqual(rc, 0)
        face = vn.ContractFace()
        face.bbox[:] = [0, 0, 10, 10]
        face.visible = 1.0
        buffer = ct.create_string_buffer(16)
        self.assertEqual(self.core.lib.vg_contract_line(buffer, 16, ct.byref(result),
                                                        ct.byref(face), None), -1)
        face.visible = 1.5
        big = ct.create_string_buffer(8192)
        self.assertEqual(self.core.lib.vg_contract_line(big, 8192, ct.byref(result),
                                                        ct.byref(face), None), -1)

    def test_vital_is_gated_then_serialized(self):
        engine = self.engine()
        vital_cfg, array = vn.vital_config(self.cfg, self.coeffs, self.shift)
        self.assertTrue(array)
        vital = self.core.lib.vg_vital_create(ct.byref(vital_cfg))
        self.addCleanup(self.core.lib.vg_vital_destroy, vital)
        self.assertTrue(vital)
        gate = self.core.lib.vg_decision_create(ct.byref(vn.measure_config(self.cfg).decision))
        self.addCleanup(self.core.lib.vg_decision_destroy, gate)
        self.assertTrue(gate)

        last_result = None
        last_frame = None
        accepted = 0
        for index in range(1, 1001):
            frame = Frame(index, mean_luma=float(self.cfg["light_target"]),
                          motion_q16=0, motion_valid=1)
            rc, result = self.update(engine, frame)
            self.assertEqual(rc, 0)
            pulse = 0.012 * math.sin(2 * math.pi * 1.2 * index / 30.0)
            base = (120.0, 118.0, 112.0)
            sums = [int(round(base[c] * (1 + pulse * (1, -1, 0.5)[c]) * 1000)) for c in range(3)]
            buffer = (ct.c_uint32 * 3)(*sums)
            self.assertEqual(self.core.lib.vg_vital_push_rgb(vital, buffer, 1000, index,
                                                             index / 30.0), 0)
            last_result, last_frame = result, frame
            if index == 1000:
                estimate = vn.VitalResult()
                self.assertEqual(self.core.lib.vg_vital_estimate(vital, ct.byref(estimate)), 0)
                self.assertTrue(estimate.present, estimate.reject)
                candidate = vn.Vital()
                candidate.value[0], candidate.value[1] = estimate.hr_bpm, estimate.hr_conf
                candidate.present[0] = candidate.present[1] = 1
                gated = vn.Vital()
                self.core.lib.vg_decision_gate_vitals(
                    gate, result.quality.overall, ct.byref(candidate),
                    ct.byref(frame.input.face), result.decision.status, ct.byref(gated))
                if gated.present[0]:
                    accepted += 1
                    self.assertAlmostEqual(gated.value[0], estimate.hr_bpm, places=6)
        self.assertEqual(accepted, 1, "the synthetic window should pass the C vital gate")
        obj = json.loads(self.contract(last_result, last_frame, gated))
        self.assertEqual(validate_frame(obj), [])
        self.assertIsNotNone(obj["vital"]["hr_bpm"])
        self.assertIsNotNone(obj["vital"]["hr_conf"])
        self.assertIsNone(obj["vital"]["rr_per_min"])
        self.assertTrue(0.0 <= obj["vital"]["hr_conf"] <= 1.0)


if __name__ == "__main__":
    unittest.main()
