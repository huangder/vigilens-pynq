"""Export the strictly validated fused NCNN FP32 Peppa graph to standard ONNX.

No retraining, quantization, epsilon pruning, original-ONNX equivalence claim,
or changes to weights. Uses the existing NCNN parser and channel/axis validator.
Requires onnx and numpy (isolated .tmp dependencies are sufficient).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prune_peppa_fp32 import infer_shapes, integer, parse_graph, require


def convolution_attributes(layer):
    """NCNN 20240410 convolution.cpp load_param: 4=L,15=R,14=T,16=B.

    Right defaults to left, top defaults to left, bottom defaults to top.
    Negative/SAME sentinels, pad_value, dynamic weights and activation params
    are already rejected by the shared parser. Only this model's CPU range.
    """
    kw = integer(layer, 1)
    kh = integer(layer, 11, kw)
    dw = integer(layer, 2, 1)
    dh = integer(layer, 12, dw)
    sw = integer(layer, 3, 1)
    sh = integer(layer, 13, sw)
    require(kw == kh and kw in (1, 3), "only square 1x1/3x3 kernels supported")
    require(dw == dh and dw in (1, 2) and sw == sh and sw in (1, 2), "unsupported dilation/stride")
    left = integer(layer, 4, 0)
    right = integer(layer, 15, left)
    top = integer(layer, 14, left)
    bottom = integer(layer, 16, top)
    require(min(left, right, top, bottom) >= 0, "negative/automatic padding unsupported")
    return {"kernel_shape": [kh, kw], "strides": [sh, sw], "dilations": [dh, dw],
            "pads": [top, left, bottom, right]}


def build_model(param_text, raw):
    layers, _ = parse_graph(param_text, raw)
    shapes = infer_shapes(layers)
    require(layers[0].tops == ["input1"] and layers[-1].tops == ["output1"], "expected Peppa input1/output1 bindings")
    require(shapes["input1"].channels == 3, "Peppa input must have three channels")
    nodes, initializers, audit = [], [], []
    used = set(shapes)

    def fresh(label):
        name = "__vg_" + label
        while name in used:
            name += "_"
        used.add(name)
        return name

    for layer in layers:
        kind = layer.kind
        attrs = {}
        if kind == "Input":
            continue
        if kind in {"Convolution", "ConvolutionDepthWise"}:
            attrs = convolution_attributes(layer)
            group = integer(layer, 7) if kind == "ConvolutionDepthWise" else 1
            source_channels = shapes[layer.bottoms[0]].channels
            out_channels = integer(layer, 0)
            require(source_channels % group == 0, "invalid group/input channels")
            kh, kw = attrs["kernel_shape"]
            weights = layer.weights.reshape(out_channels, source_channels//group, kh, kw)
            w_name, b_name, intermediate = (fresh(layer.name+suffix) for suffix in ("_weight", "_bias", "_pre_relu"))
            initializers += [numpy_helper.from_array(weights, w_name), numpy_helper.from_array(layer.bias, b_name)]
            nodes.append(helper.make_node("Conv", layer.bottoms+[w_name, b_name], [intermediate],
                                          name=layer.name+"/Conv", group=group, **attrs))
            nodes.append(helper.make_node("Relu", [intermediate], layer.tops, name=layer.name+"/Relu"))
            attrs["group"] = group
        elif kind == "Split":
            for index, top in enumerate(layer.tops):
                nodes.append(helper.make_node("Identity", layer.bottoms, [top], name=layer.name+"/Identity"+str(index)))
        elif kind == "Reduction":
            rank = len(shapes[layer.bottoms[0]].axes)
            raw_axes = list(map(int, layer.attrs[-23303].split(",")[1:]))
            axes = [1+(axis if axis >= 0 else axis+rank) for axis in raw_axes]
            # The shared validator proves every reduced label is H or W, not C.
            attrs = {"axes": axes, "keepdims": 0}
            nodes.append(helper.make_node("ReduceMean", layer.bottoms, layer.tops, name=layer.name+"/Mean", **attrs))
        elif kind == "Concat":
            # NCNN rank1 channel-vector axis0 -> ONNX rank2 [N,C] axis1.
            attrs = {"axis": 1}
            nodes.append(helper.make_node("Concat", layer.bottoms, layer.tops, name=layer.name+"/Concat", **attrs))
        elif kind == "InnerProduct":
            input_channels = shapes[layer.bottoms[0]].channels
            weights = layer.weights.reshape(143, input_channels)
            w_name, b_name = (fresh(layer.name+suffix) for suffix in ("_weight", "_bias"))
            initializers += [numpy_helper.from_array(weights, w_name), numpy_helper.from_array(layer.bias, b_name)]
            attrs = {"transB": 1}
            nodes.append(helper.make_node("Gemm", layer.bottoms+[w_name, b_name], layer.tops,
                                          name=layer.name+"/Gemm", **attrs))
        else:
            raise ValueError("unsupported operator after shared validation")
        audit.append({"name": layer.name, "ncnn_type": kind, "bottoms": layer.bottoms,
                      "tops": layer.tops, "onnx_attributes": attrs})
    graph = helper.make_graph(nodes, "PeppaFusedNcnnFp32", [helper.make_tensor_value_info("input1", TensorProto.FLOAT, [1, 3, 160, 160])],
                              [helper.make_tensor_value_info("output1", TensorProto.FLOAT, [1, 143])], initializers)
    model = helper.make_model(graph, producer_name="VigiLens fused NCNN FP32 export", opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    helper.set_model_props(model, {"source_format": "validated fused NCNN FP32", "original_onnx_equivalence_verified": "false"})
    onnx.checker.check_model(model, full_check=True)
    model = onnx.shape_inference.infer_shapes(model, strict_mode=True, data_prop=True)
    onnx.checker.check_model(model, full_check=True)
    # Fully concrete shapes rule out unknown flatten/rank or invalid spatial extents.
    values = {value.name: value for value in [*model.graph.input, *model.graph.value_info, *model.graph.output]}
    for node in model.graph.node:
        for output in node.output:
            require(output in values, "shape inference omitted an intermediate")
            dims = values[output].type.tensor_type.shape.dim
            require(dims and all(d.HasField("dim_value") and d.dim_value > 0 for d in dims), "nonconcrete/invalid inferred shape")
    require([dim.dim_value for dim in values["output1"].type.tensor_type.shape.dim] == [1, 143], "inferred output shape mismatch")
    return model, audit


def export(param, weights, output_dir):
    param, weights, output = Path(param).resolve(), Path(weights).resolve(), Path(output_dir).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("output directory must be absent or empty")
    text, raw = param.read_bytes(), weights.read_bytes()
    model, layers = build_model(text.decode("utf-8"), raw)
    serialized = model.SerializeToString()
    sha = lambda value: hashlib.sha256(value).hexdigest()
    report = {"kind": "fused_ncnn_fp32_to_standard_onnx", "onnx_version": onnx.__version__,
              "source_param_sha256": sha(text), "source_weights_sha256": sha(raw), "output_sha256": sha(serialized),
              "output_bytes": len(serialized), "input_shape_nchw": [1, 3, 160, 160], "output_shape": [1, 143],
              "opset": 13, "ir_version": 8, "checker_verified": True, "shape_inference_verified": True,
              "layers": layers, "weights_changed": False, "fp16": False, "quantized": False,
              "runtime_tensor_parity_verified": False, "original_onnx_equivalence_verified": False,
              "accuracy_verified": False, "full_pipeline_realtime_verified": False}
    output.mkdir(parents=True, exist_ok=True)
    (output/"peppa_pruned.onnx").write_bytes(serialized)
    (output/"export-audit.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--param", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = export(args.param, args.weights, args.output_dir)
    print(json.dumps({key: result[key] for key in ("output_sha256", "output_bytes", "checker_verified", "runtime_tensor_parity_verified")}))
