"""Strict NCNN->ONNX wiring and integer-network execution, no measured tolerance."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
DEPS = ROOT/".tmp/autonomy/onnx_py"
if DEPS.is_dir():
    sys.path.insert(0, str(DEPS))
try:
    import onnx
except ImportError:
    raise unittest.SkipTest("ONNX required; use isolated pinned dependencies")
SPEC = importlib.util.spec_from_file_location("export_ncnn", Path(__file__).resolve().parents[1]/"export_ncnn_fp32_onnx.py")
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def fixture():
    lines = ["Input input1 0 1 input1",
             "Convolution c 1 1 input1 c 0=4 1=1 5=1 6=12 9=1",
             "ConvolutionDepthWise dw 1 1 c d 0=4 1=1 5=1 6=4 7=4 9=1",
             "Split split 1 2 d a b",
             "Reduction aw 1 1 a aw 0=3 1=0 -23303=1,2 5=1",
             "Reduction ah 1 1 aw ah 0=3 1=0 -23303=1,1 5=1",
             "Reduction bw 1 1 b bw 0=3 1=0 -23303=1,2 5=1",
             "Reduction bh 1 1 bw bh 0=3 1=0 -23303=1,1 5=1",
             "Concat cat 2 1 ah bh cat",
             "InnerProduct fc 1 1 cat output1 0=143 1=1 2=1144"]
    w = np.array([[1, 2, 3], [-1, -1, -1], [0, 0, 0], [2, 0, 1]], dtype="<f4")
    bias = np.array([0, 1, -1, 2], dtype="<f4")
    dw = np.array([2, 3, 4, -1], dtype="<f4")
    dw_bias = np.array([1, 0, 0, 9], dtype="<f4")
    fc = (np.arange(1144).reshape(143, 8) % 5-2).astype("<f4")
    fc_bias = np.arange(143, dtype="<f4")
    raw = b"".join(b"\0"*4+x.tobytes()+b.tobytes() for x, b in ((w, bias), (dw, dw_bias), (fc, fc_bias)))
    text = "7767517\n10 11\n"+"\n".join(lines)+"\n"
    # Constant ones input -> exact integer outputs for all arithmetic here.
    feature = np.maximum(np.maximum(w.sum(axis=1)+bias, 0)*dw+dw_bias, 0)
    expected = fc @ np.concatenate([feature, feature]) + fc_bias
    return text, raw, expected.reshape(1, 143)


class ExportNcnnFp32(unittest.TestCase):
    def test_standard_ops_checker_rank_and_weight_layout(self):
        text, raw, _ = fixture()
        model, audit = exporter.build_model(text, raw)
        onnx.checker.check_model(model, full_check=True)
        self.assertEqual([d.dim_value for d in model.graph.input[0].type.tensor_type.shape.dim], [1, 3, 160, 160])
        self.assertEqual([d.dim_value for d in model.graph.output[0].type.tensor_type.shape.dim], [1, 143])
        self.assertTrue(all(not node.domain for node in model.graph.node))
        self.assertEqual([node.op_type for node in model.graph.node].count("Identity"), 2)
        means = [node for node in model.graph.node if node.op_type == "ReduceMean"]
        self.assertEqual([onnx.helper.get_attribute_value(node.attribute[0]) for node in means], [[3], [2], [3], [2]])
        convs = [node for node in model.graph.node if node.op_type == "Conv"]
        weights = {x.name: onnx.numpy_helper.to_array(x) for x in model.graph.initializer}
        self.assertEqual(weights[convs[0].input[1]].shape, (4, 3, 1, 1))
        self.assertEqual(weights[convs[1].input[1]].shape, (4, 1, 1, 1))
        self.assertEqual(next(x for x in audit if x["ncnn_type"] == "Concat")["onnx_attributes"], {"axis": 1})
        gemm = next(node for node in model.graph.node if node.op_type == "Gemm")
        self.assertEqual(weights[gemm.input[1]].shape, (143, 8))
        self.assertEqual(onnx.helper.get_attribute_value(gemm.attribute[0]), 1)
        layers, _ = exporter.parse_graph(text, raw)
        for layer in layers:
            if layer.kind not in {"Convolution", "ConvolutionDepthWise", "InnerProduct"}:
                continue
            node = next(node for node in model.graph.node if node.name == layer.name+
                        ("/Gemm" if layer.kind == "InnerProduct" else "/Conv"))
            self.assertEqual(weights[node.input[1]].reshape(-1).tobytes(), layer.weights.tobytes())
            self.assertEqual(weights[node.input[2]].tobytes(), layer.bias.tobytes())

    def test_ort_integer_reference_executes_relu_depthwise_branches_and_fc(self):
        try:
            import onnxruntime as ort
        except ImportError:
            self.skipTest("ORT required for real standard ONNX execution")
        text, raw, expected = fixture()
        model, _ = exporter.build_model(text, raw)
        options = ort.SessionOptions()
        options.log_severity_level = 3
        session = ort.InferenceSession(model.SerializeToString(), options, providers=["CPUExecutionProvider"])
        actual = session.run(["output1"], {"input1": np.ones((1, 3, 160, 160), dtype=np.float32)})[0]
        np.testing.assert_array_equal(actual, expected)

    def test_padding_defaults_and_asymmetric_ncnn_key_mapping(self):
        layer = exporter.parse_graph(*fixture()[:2])[0][1]
        layer.attrs.update({1: "3", 4: "1", 14: "2", 15: "3", 16: "4", 3: "2", 2: "2"})
        attrs = exporter.convolution_attributes(layer)
        self.assertEqual(attrs["pads"], [2, 1, 4, 3])
        self.assertEqual(attrs["strides"], [2, 2])
        self.assertEqual(attrs["dilations"], [2, 2])
        del layer.attrs[15]
        del layer.attrs[16]
        self.assertEqual(exporter.convolution_attributes(layer)["pads"], [2, 1, 2, 1])

    def test_unsupported_attributes_groups_axes_names_and_truncated_weights_rejected(self):
        text, raw, _ = fixture()
        for old, new in (("9=1", "9=2"), ("7=4", "7=2"), ("-23303=1,2", "-23303=1,0"),
                         ("1=1 5=1 6=12", "1=1 3=3 5=1 6=12"),
                         ("1=1 5=1 6=12", "1=1 18=1 5=1 6=12"),
                         ("Split split", "ShuffleChannel split"), ("input1", "wrong_input")):
            with self.subTest(new=new), self.assertRaises(ValueError):
                exporter.build_model(text.replace(old, new), raw)
        with self.assertRaises(ValueError):
            exporter.build_model(text, raw[:-1])

    def test_export_provenance_and_empty_directory_guard(self):
        text, raw, _ = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"source.param").write_text(text)
            (root/"source.bin").write_bytes(raw)
            report = exporter.export(root/"source.param", root/"source.bin", root/"out")
            self.assertTrue(report["checker_verified"])
            self.assertFalse(report["weights_changed"])
            self.assertFalse(report["original_onnx_equivalence_verified"])
            self.assertFalse(report["runtime_tensor_parity_verified"])
            self.assertEqual(json.loads((root/"out/export-audit.json").read_text()), report)
            with self.assertRaises(FileExistsError):
                exporter.export(root/"source.param", root/"source.bin", root/"out")


if __name__ == "__main__":
    unittest.main()
