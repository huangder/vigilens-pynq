"""CLI / tensor I/O integration checks against a real built MNN probe/model.

Set VG_MNN_PROBE, VG_MNN_MODEL; optional VG_MNN_RUNNER='wsl.exe -d Ubuntu --'.
Tests do not assert timing budgets or landmark accuracy.
"""
import json
import math
import os
from pathlib import Path, PureWindowsPath
import shlex
import struct
import subprocess
import tempfile
import unittest


class MnnProbeIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = os.environ.get("VG_MNN_PROBE")
        cls.model = os.environ.get("VG_MNN_MODEL")
        if not cls.binary or not cls.model:
            raise unittest.SkipTest("Set explicit real MNN binary/model paths for integration checks")
        cls.runner = shlex.split(os.environ.get("VG_MNN_RUNNER", ""))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vg_mnn_test_")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.input = self.folder/"input.f32"
        self.input.write_bytes(struct.pack("<76800f", *([0.] * 76800)))
        self.prefix = self.folder/"output with spaces"

    def native(self, value):
        value = str(value)
        if os.name == "nt" and self.runner and len(value) > 2 and value[1] == ":":
            path = PureWindowsPath(value)
            return "/mnt/"+path.drive[0].lower()+"/"+"/".join(path.parts[1:])
        return value

    def invoke(self, iterations="2", threads="1", model=None):
        return subprocess.run(self.runner+[self.binary, model or self.model, self.native(self.input),
                               self.native(self.prefix), iterations, threads], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60)

    def test_valid_session_fp32_semantic_output_and_metadata(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MNN_MODEL_PROBE_PASS", result.stdout)
        report = json.loads(Path(str(self.prefix)+".json").read_text())
        self.assertEqual(report["precision"], "Precision_High")
        self.assertEqual(report["backend"], "CPU")
        self.assertEqual(report["input_shape"], [1, 3, 160, 160])
        self.assertEqual(report["outputs"][0]["elements"], 143)
        self.assertEqual(report["outputs"][0]["host_element_count"], 143)
        self.assertEqual(report["outputs"][0]["host_layout"], "CAFFE")
        self.assertTrue(report["run_session_each_iteration"])
        self.assertEqual(report["iterations"], 2)
        self.assertEqual(report["warmup_iterations"], 1)
        self.assertEqual(report["timing_scope"], "runSession_only")
        for key in ("fp16", "int8", "input_provenance_verified", "accuracy_verified", "full_pipeline_realtime_verified"):
            self.assertFalse(report[key])
        for key in ("mean_ms", "p50_ms", "p95_ms"):
            self.assertTrue(math.isfinite(report[key]) and report[key] >= 0)
        raw = Path(str(self.prefix)+".output0.f32").read_bytes()
        self.assertEqual(len(raw), 572)
        self.assertTrue(all(math.isfinite(v) for v in struct.unpack("<143f", raw)))

    def test_invalid_argument_bounds_and_missing_model(self):
        for iterations, threads in (("0", "1"), ("10001", "1"), ("-1", "1"), ("NaN", "1"),
                                    ("1", "0"), ("1", "3"), ("1junk", "1")):
            with self.subTest(iterations=iterations, threads=threads):
                self.assertNotEqual(self.invoke(iterations, threads).returncode, 0)
        self.assertNotEqual(self.invoke(model="missing-model.mnn").returncode, 0)

    def test_input_exact_length_and_finite_before_inference(self):
        raw = self.input.read_bytes()
        for invalid in (raw[:-4], raw+b"x", struct.pack("<f", float("nan"))+raw[4:],
                        struct.pack("<f", float("inf"))+raw[4:]):
            self.input.write_bytes(invalid)
            self.assertNotEqual(self.invoke().returncode, 0)
            self.assertFalse(Path(str(self.prefix)+".output0.f32").exists())

    def test_two_thread_request_and_changed_file_input(self):
        first = self.invoke("1", "2")
        self.assertEqual(first.returncode, 0, first.stderr)
        before = Path(str(self.prefix)+".output0.f32").read_bytes()
        self.input.write_bytes(struct.pack("<76800f", *([.5] * 76800)))
        second = self.invoke("1", "2")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(before, Path(str(self.prefix)+".output0.f32").read_bytes())
        self.assertEqual(json.loads(Path(str(self.prefix)+".json").read_text())["threads_requested"], 2)


if __name__ == "__main__":
    unittest.main()
