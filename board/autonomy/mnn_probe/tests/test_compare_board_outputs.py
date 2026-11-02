"""Offline checks for the logged-ARM-output extractor/comparator.

These tests use synthetic transcripts only: no board, no MNN binary, no real
model output. A passing suite proves the parser rejects malformed evidence and
computes differences correctly; it says nothing about model accuracy.
"""
import base64
import gzip
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import compare_board_outputs as cbo  # noqa: E402


def encode_block(name: str, raw: bytes) -> str:
    return (f"VIGI_FILE_BEGIN {name}\n"
            + base64.encodebytes(gzip.compress(raw, mtime=0)).decode("ascii")
            + "VIGI_FILE_END\n")


def probe_json(threads: int, **overrides) -> str:
    row = {"kind": "mnn_model_tensor_probe", "mnn_version": "3.6.1", "backend": "CPU",
           "precision": "Precision_High", "fp16": False, "int8": False,
           "input_kind": "file", "input_provenance_verified": False,
           "channel_order_verified": False, "landmark_pipeline_verified": False,
           "full_pipeline_realtime_verified": False, "accuracy_verified": False,
           "input_layout": "NCHW", "input_shape": [1, 3, 160, 160],
           "input_blob": "input1", "output_blob": "output1",
           "input_copy_each_iteration": True, "run_session_each_iteration": True,
           "timing_scope": "runSession_only", "warmup_iterations": 1,
           "iterations": 300, "threads_requested": threads,
           "mean_ms": 27.5, "p50_ms": 27.4, "p95_ms": 27.6, "max_rss_kib": 7040,
           "outputs": [{"elements": 143, "host_layout": "CAFFE",
                        "host_element_count": 143, "shape": [1, 143],
                        "storage": "little_endian_float32"}]}
    row.update(overrides)
    return json.dumps(row)


def transcript(name: str, values: list[float], *, threads=(1, 2),
               kind="original", extra: str = "") -> str:
    raw = struct.pack(f"<{len(values)}f", *values)
    text = "".join(probe_json(n) + "\n" for n in threads)
    frozen = cbo.FROZEN[kind]
    return (extra + text + f"{frozen['model_sha256']}\n{frozen['probe_sha256']}\n"
            + f"{cbo.INPUT_SHA256}  /tmp/peppa_input.f32\n" + encode_block(name, raw))


class ExtractTests(unittest.TestCase):
    def test_valid_block_round_trip(self):
        raw = struct.pack("<143f", *[0.25] * 143)
        found = cbo.extract_blocks("noise\n" + encode_block("peppa_mnn_t2.output0.f32", raw))
        self.assertEqual(found["peppa_mnn_t2.output0.f32"], raw)

    def test_unlisted_name_rejected(self):
        raw = b"\0" * cbo.OUTPUT_BYTES
        with self.assertRaisesRegex(cbo.BlockError, "whitelist"):
            cbo.extract_blocks(encode_block("attention_t2.output0.f32", raw))

    def test_duplicate_block_rejected(self):
        raw = b"\0" * cbo.OUTPUT_BYTES
        text = encode_block("peppa_mnn_t2.output0.f32", raw) * 2
        with self.assertRaisesRegex(cbo.BlockError, "duplicate"):
            cbo.extract_blocks(text)

    def test_oversize_payload_rejected(self):
        oversized = b"\0" * (cbo.MAX_UNCOMPRESSED + 1)
        with self.assertRaisesRegex(cbo.BlockError, "exceeds"):
            cbo.extract_blocks(encode_block("peppa_mnn_t2.output0.f32", oversized))

    def test_invalid_base64_rejected(self):
        text = ("VIGI_FILE_BEGIN peppa_mnn_t2.output0.f32\n"
                "!!!!not-base64!!!!\nVIGI_FILE_END\n")
        with self.assertRaisesRegex(cbo.BlockError, "base64"):
            cbo.extract_blocks(text)

    def test_wrong_payload_length_rejected(self):
        with self.assertRaisesRegex(cbo.BlockError, "expected 572 bytes"):
            cbo.extract_blocks(encode_block("peppa_mnn_t2.output0.f32", b"\0" * 568))

    def test_nonfinite_payload_rejected(self):
        raw = struct.pack("<143f", *([0.0] * 142 + [float("nan")]))
        found = cbo.extract_blocks(encode_block("peppa_mnn_t2.output0.f32", raw))
        with self.assertRaisesRegex(cbo.BlockError, "non-finite"):
            cbo.floats(found["peppa_mnn_t2.output0.f32"])


class ProbeTests(unittest.TestCase):
    def test_accepts_expected_metadata(self):
        rows = cbo.probe_records(probe_json(2))
        self.assertEqual(rows[0]["threads_requested"], 2)

    def test_rejects_low_precision(self):
        with self.assertRaisesRegex(cbo.BlockError, "Precision_High"):
            cbo.probe_records(probe_json(2, precision="Precision_Low"))

    def test_rejects_unearned_verification_claim(self):
        with self.assertRaisesRegex(cbo.BlockError, "accuracy_verified"):
            cbo.probe_records(probe_json(2, accuracy_verified=True))

    def test_rejects_wrong_output_metadata(self):
        outputs = [{"elements": 143, "host_layout": "CAFFE", "host_element_count": 143,
                    "shape": [1, 143], "storage": "big_endian_float32"}]
        with self.assertRaisesRegex(cbo.BlockError, "tensor metadata"):
            cbo.probe_records(probe_json(2, outputs=outputs))


class CompareTests(unittest.TestCase):
    def test_exact_differences(self):
        board = tuple([0.5] * 143)
        reference = tuple([0.5] * 136 + [0.25] * 7)
        row = cbo.compare(board, reference)
        self.assertEqual(row["elements"], 143)
        self.assertEqual(row["max_abs_difference"], 0.25)
        self.assertEqual(row["max_first136_abs_difference"], 0.0)

    def test_length_mismatch_rejected(self):
        with self.assertRaisesRegex(cbo.BlockError, "element count"):
            cbo.compare((0.0,), (0.0, 0.0))


class AnalyseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vg_cbo_")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.values = [0.125] * 143
        self.original = self.folder / "original.log"
        self.pruned = self.folder / "pruned.log"
        self.original.write_text(transcript("peppa_mnn_t2.output0.f32", self.values),
                                 encoding="utf-8")
        self.pruned.write_text(transcript("peppa_mnn_pruned_t2.output0.f32", self.values,
                                          kind="pruned"), encoding="utf-8")

    def test_report_binds_hashes_and_flags_identical_output(self):
        report = cbo.analyse([self.original, self.pruned], references={})
        self.assertEqual(report["board_outputs"]["peppa_mnn_t2.output0.f32"]["sha256"],
                         report["board_outputs"]["peppa_mnn_pruned_t2.output0.f32"]["sha256"])
        self.assertTrue(report["board_original_vs_pruned"]["byte_identical"])
        self.assertEqual(report["board_original_vs_pruned"]["max_abs_difference"], 0.0)
        self.assertEqual(report["references"], {})
        self.assertEqual(report["comparisons"], {})
        self.assertFalse(report["accuracy_verified"])

    def test_missing_frozen_hash_rejected(self):
        text = transcript("peppa_mnn_t2.output0.f32", self.values)
        self.original.write_text(text.replace(cbo.INPUT_SHA256, "0" * 64), encoding="utf-8")
        with self.assertRaisesRegex(cbo.BlockError, "frozen input hash"):
            cbo.analyse([self.original, self.pruned], references={})

    def test_missing_transcript_block_rejected(self):
        self.pruned.write_text(probe_json(1) + "\n" + probe_json(2) + "\n",
                               encoding="utf-8")
        with self.assertRaisesRegex(cbo.BlockError, "no VIGI_FILE block"):
            cbo.analyse([self.original, self.pruned], references={})

    def test_cli_writes_json(self):
        out = self.folder / "report.json"
        code = cbo.main(["--log", str(self.original), "--log", str(self.pruned),
                         "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["board_outputs"])


if __name__ == "__main__":
    unittest.main()
