"""Small numerical graphs test zero proofs, channel order and strict rejection."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

SPEC = importlib.util.spec_from_file_location("prune_peppa", Path(__file__).resolve().parents[1]/"prune_peppa_fp32.py")
pruner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pruner
SPEC.loader.exec_module(pruner)


def fixture(*, positive_dw=False, tiny=False, branch=True):
    rng = np.random.default_rng(17)
    w = np.zeros((8, 3), dtype="<f4")
    w[[0, 4, 7]] = rng.normal(size=(3, 3))
    if tiny:
        w[3, 0] = np.nextafter(np.float32(0), np.float32(1))
    bias = np.zeros(8, dtype="<f4")
    bias[5] = -2
    dw_bias = np.zeros(8, dtype="<f4")
    if positive_dw:
        dw_bias[2] = 1
    dw = np.arange(1, 9, dtype="<f4")
    lines = ["Input input 0 1 input", "Convolution c 1 1 input c 0=8 1=1 5=1 6=24 9=1",
             "ConvolutionDepthWise dw 1 1 c d 0=8 1=1 5=1 6=8 7=8 9=1"]
    blocks = [(w.reshape(-1), bias), (dw, dw_bias)]
    if branch:
        lines += ["Split split 1 2 d a b", "Reduction aw 1 1 a aw 0=3 1=0 -23303=1,2 5=1",
                  "Reduction ah 1 1 aw ah 0=3 1=0 -23303=1,1 5=1",
                  "Reduction bw 1 1 b bw 0=3 1=0 -23303=1,2 5=1",
                  "Reduction bh 1 1 bw bh 0=3 1=0 -23303=1,1 5=1",
                  "Concat cat 2 1 ah bh cat 0=0"]
        width, bottom = 16, "cat"
    else:
        lines += ["Reduction rw 1 1 d rw 0=3 1=0 -23303=1,2 5=1",
                  "Reduction rh 1 1 rw rh 0=3 1=0 -23303=1,1 5=1"]
        width, bottom = 8, "rh"
    lines += [f"InnerProduct fc 1 1 {bottom} output 0=143 1=1 2={143*width}"]
    blocks.append((rng.normal(size=143*width).astype("<f4"), rng.normal(size=143).astype("<f4")))
    blob_count = sum(int(line.split()[3]) for line in lines)
    text = "7767517\n"+f"{len(lines)} {blob_count}\n"+"\n".join(lines)+"\n"
    raw = b"".join(b"\0"*4+w.tobytes()+b.tobytes() for w, b in blocks)
    return text, raw


def evaluate(text, raw, value):
    """Independent tiny 1x1 CHW evaluator; no runtime/performance claim."""
    layers, _ = pruner.parse_graph(text, raw)
    tensors = {}
    for layer in layers:
        kind = layer.kind
        if kind == "Input":
            result = value.astype(np.float64)
        else:
            x = tensors[layer.bottoms[0]]
            if kind == "Convolution":
                w = layer.weights.reshape(int(layer.attrs[0]), x.shape[0]).astype(np.float64)
                result = np.maximum(np.einsum("oi,ihw->ohw", w, x)+layer.bias[:, None, None], 0)
            elif kind == "ConvolutionDepthWise":
                result = np.maximum(x*layer.weights[:, None, None]+layer.bias[:, None, None], 0)
            elif kind == "Split":
                result = x.copy()
            elif kind == "Reduction":
                axes = tuple(map(int, layer.attrs[-23303].split(",")[1:]))
                result = np.mean(x, axis=axes)
            elif kind == "Concat":
                result = np.concatenate([tensors[b] for b in layer.bottoms])
            elif kind == "InnerProduct":
                result = layer.weights.reshape(143, x.size).astype(np.float64) @ x + layer.bias
        tensors.update({top: result for top in layer.tops})
    return tensors["output"]


class PrunePeppa(unittest.TestCase):
    def test_alignment_split_mean_concat_fc_mapping_and_numerical_parity(self):
        text, raw = fixture()
        changed, weights, audit = pruner.transform(text, raw, 4)
        conv, dw = audit[1:3]
        self.assertEqual(conv["retained_original_output_channels"], [0, 1, 4, 7])
        self.assertEqual(dw["retained_original_output_channels"], [0, 1, 4, 7])
        self.assertEqual(audit[-1]["retained_original_fc_columns"], [0, 1, 4, 7, 8, 9, 12, 15])
        self.assertEqual(audit[-1]["output_channels_after"], 143)
        self.assertEqual(changed.splitlines()[1], text.splitlines()[1])
        self.assertLess(len(weights), len(raw))
        x = np.random.default_rng(8).uniform(-2, 2, (3, 2, 3))
        np.testing.assert_allclose(evaluate(text, raw, x), evaluate(changed, weights, x), rtol=1e-13, atol=1e-12)

    def test_positive_depthwise_bias_keeps_source_channel(self):
        text, raw = fixture(positive_dw=True)
        changed, weights, audit = pruner.transform(text, raw)
        self.assertEqual(audit[1]["positive_dw_bias_protected"], [2])
        self.assertIn(2, audit[1]["retained_original_output_channels"])
        self.assertEqual(audit[1]["retained_original_output_channels"], [0, 2, 4, 7])
        x = np.random.default_rng(9).normal(size=(3, 3, 2))
        np.testing.assert_allclose(evaluate(text, raw, x), evaluate(changed, weights, x), rtol=1e-13, atol=1e-12)

    def test_subnormal_is_not_exact_zero(self):
        text, raw = fixture(tiny=True)
        _, _, audit = pruner.transform(text, raw)
        self.assertNotIn(3, audit[1]["exact_zero_relu_candidates"])
        self.assertIn(3, audit[1]["retained_original_output_channels"])

    def test_positive_dw_bias_protection_crosses_split(self):
        text, raw = fixture(positive_dw=True)
        lines = text.splitlines()
        dw = next(line for line in lines if line.startswith("ConvolutionDepthWise"))
        lines.remove(dw)
        split_index = next(i for i, line in enumerate(lines) if line.startswith("Split"))
        lines[split_index] = "Split split 1 2 c a b"
        lines.insert(split_index+1, dw.replace("1 1 c d", "1 1 b d"))
        text = "\n".join(lines).replace("Reduction bw 1 1 b bw", "Reduction bw 1 1 d bw")+"\n"
        changed, weights, audit = pruner.transform(text, raw)
        self.assertEqual(audit[1]["positive_dw_bias_protected"], [2])
        x = np.random.default_rng(18).normal(size=(3, 2, 2))
        np.testing.assert_allclose(evaluate(text, raw, x), evaluate(changed, weights, x), atol=1e-12)

    def test_without_zero_filters_binary_is_unchanged(self):
        text, raw = fixture()
        data = bytearray(raw)
        data[4:4+24*4] = np.ones(24, dtype="<f4").tobytes()
        _, weights, audit = pruner.transform(text, bytes(data))
        self.assertEqual(weights, bytes(data))
        self.assertTrue(all(not row["removed_original_output_channels"] for row in audit))

    def test_positive_bias_on_zero_filter_is_live(self):
        text, raw = fixture()
        # First conv bias starts after tag + 24 FP32 weights.
        data = bytearray(raw)
        data[4+24*4+2*4:4+24*4+3*4] = np.float32(.25).tobytes()
        _, _, audit = pruner.transform(text, bytes(data))
        self.assertNotIn(2, audit[1]["exact_zero_relu_candidates"])
        self.assertIn(2, audit[1]["retained_original_output_channels"])

    def test_input_is_never_pruned_and_live_negative_input_is_not_assumed_zero(self):
        text, raw = fixture(branch=False)
        changed, weights, audit = pruner.transform(text, raw)
        self.assertEqual(audit[0]["retained_original_output_channels"], [0, 1, 2])
        for x in (np.zeros((3, 2, 2)), -np.ones((3, 2, 2)), np.ones((3, 2, 2))):
            np.testing.assert_allclose(evaluate(text, raw, x), evaluate(changed, weights, x), atol=1e-12)

    def test_reject_unknown_operator_attribute_activation_group_and_channel_mean(self):
        text, raw = fixture()
        changes = [("Split split", "Unknown split"), ("9=1", "9=2"),
                   ("7=8", "7=4"), ("-23303=1,2", "-23303=1,0"),
                   ("-23303=1,1", "-23303=1,0"), ("cat 0=0", "cat 0=1"),
                   ("6=24", "6=24 99=1"), ("5=1 6=24", "5=0 6=24")]
        for before, after in changes:
            with self.subTest(after=after), self.assertRaises(ValueError):
                pruner.transform(text.replace(before, after, 1), raw)

    def test_reject_bad_binary_nonfinite_and_counts(self):
        text, raw = fixture()
        bias_offset = 4+24*4
        for broken in (raw[:-1], raw+b"\0", b"\1"+raw[1:], raw[:4]+np.float32(np.nan).tobytes()+raw[8:],
                       raw[:bias_offset]+np.float32(np.inf).tobytes()+raw[bias_offset+4:]):
            with self.subTest(length=len(broken)), self.assertRaises(ValueError):
                pruner.transform(text, broken)
        with self.assertRaises(ValueError):
            pruner.transform(text.replace("0=143", "0=142"), raw)
        with self.assertRaises(ValueError):
            pruner.transform(text, raw, 3)

    def test_empty_output_guard_audit_and_hashes(self):
        text, raw = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            param, weights, output = root/"source.param", root/"source.bin", root/"out"
            param.write_text(text)
            weights.write_bytes(raw)
            report = pruner.prune(param, weights, output)
            self.assertEqual(json.loads((output/"audit.json").read_text()), report)
            self.assertFalse(report["accuracy_verified"])
            self.assertFalse(report["runtime_tensor_parity_verified"])
            self.assertFalse(report["original_onnx_equivalence_verified"])
            self.assertEqual(report["output"]["weights_bytes"], (output/"peppa.bin").stat().st_size)
            with self.assertRaises(FileExistsError):
                pruner.prune(param, weights, output)


if __name__ == "__main__":
    unittest.main()
