"""Delete proven zero ReLU channels from a fused NCNN FP32 Peppa graph.

No epsilon, quantization, training or ONNX rewrite. This proves zero-channel
algebra for the supplied NCNN graph, assuming finite intermediate values.
Changing channel packing / accumulation order still requires runtime parity.
NCNN 20240410 semantics: src/layer/{reduction,concat,convolutiondepthwise}.cpp.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np


SUPPORTED = {"Input", "Convolution", "ConvolutionDepthWise", "Split",
             "Reduction", "Concat", "InnerProduct"}
WEIGHTED = {"Convolution", "ConvolutionDepthWise", "InnerProduct"}


@dataclass
class Layer:
    kind: str
    name: str
    bottoms: list[str]
    tops: list[str]
    attrs: dict[int, str]
    weights: object = None
    bias: object = None


@dataclass(frozen=True)
class Shape:
    # C remains the original channel identity even when NCNN stores it in w/h.
    axes: tuple[str, ...]
    channels: int


def integer(layer, key, default=None):
    text = layer.attrs.get(key, default)
    if text is None:
        raise ValueError(f"{layer.name}: missing attribute {key}")
    try:
        return int(text)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{layer.name}: integer attribute {key} required") from error


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_graph(text, raw):
    """Read all records before writing anything; reject unknown layout/semantics."""
    lines = [line.split() for line in text.splitlines() if line.strip()]
    require(len(lines) >= 3 and lines[0] == ["7767517"], "invalid NCNN header")
    require(len(lines[1]) == 2, "invalid layer/blob counts")
    layer_count, blob_count = map(int, lines[1])
    layers, producers, names = [], {}, set()
    for fields in lines[2:]:
        require(len(fields) >= 4, "truncated layer")
        kind, name = fields[:2]
        require(kind in SUPPORTED and name not in names, "unknown operator or duplicate layer")
        names.add(name)
        nb, nt = map(int, fields[2:4])
        require(nb >= 0 and nt > 0 and len(fields) >= 4 + nb + nt, "invalid blob record")
        bottoms, tops = fields[4:4+nb], fields[4+nb:4+nb+nt]
        require(all(b in producers for b in bottoms), "graph is not topologically ordered")
        require(len(set(tops)) == nt and not any(t in producers for t in tops), "duplicate/inplace blob")
        attrs = {}
        for field in fields[4+nb+nt:]:
            require(field.count("=") == 1, "invalid attribute")
            key, value = field.split("=")
            key = int(key)
            require(key not in attrs, "duplicate attribute")
            attrs[key] = value
        layer = Layer(kind, name, bottoms, tops, attrs)
        allowed = {
            "Input": set(), "Split": set(), "Concat": {0},
            "Reduction": {0, 1, 2, -23303, 4, 5},
            "InnerProduct": {0, 1, 2, 8, 9},
            "Convolution": {0, 1, 2, 3, 4, 5, 6, 8, 9, 11, 12, 13, 14, 15, 16},
            "ConvolutionDepthWise": {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16},
        }[kind]
        require(set(attrs) <= allowed, f"{name}: unsupported attribute")
        require((kind == "Input" and nb == 0 and nt == 1) or
                (kind == "Split" and nb == 1 and nt >= 2) or
                (kind == "Concat" and nb >= 2 and nt == 1) or
                (kind not in {"Input", "Split", "Concat"} and nb == nt == 1), "unsupported arity")
        if kind in WEIGHTED:
            require(integer(layer, 8, 0) == 0, "quantized weights are unsupported")
            out = integer(layer, 0)
            require(out > 0, "invalid output channels")
            if kind == "InnerProduct":
                require(integer(layer, 1) == 1 and integer(layer, 9, 0) == 0,
                        "FC requires bias and no fused activation")
            else:
                require(integer(layer, 5) == 1 and integer(layer, 9) == 1,
                        "convolution requires bias and confirmed fused ReLU")
                kw = integer(layer, 1)
                kh = integer(layer, 11, kw)
                require(kw > 0 and kh > 0, "invalid kernel")
                for key in (2, 3, 12, 13):
                    require(integer(layer, key, 1) > 0, "invalid dilation/stride")
                for key in (4, 14, 15, 16):
                    require(integer(layer, key, 0) >= 0, "automatic/negative padding unsupported")
        layers.append(layer)
        producers.update({top: layer for top in tops})
    require(len(layers) == layer_count and len(producers) == blob_count, "layer/blob count mismatch")
    require(sum(l.kind == "Input" for l in layers) == 1 and layers[0].kind == "Input", "one initial Input required")
    require(sum(l.kind == "InnerProduct" for l in layers) == 1 and layers[-1].kind == "InnerProduct",
            "one terminal FC required")
    require(integer(layers[-1], 0) == 143, "Peppa FC must preserve 143 outputs")
    consumed = {b for layer in layers for b in layer.bottoms}
    require(set(producers) - consumed == set(layers[-1].tops), "non-FC graph output would change interface")

    offset = 0
    for layer in layers:
        if layer.kind not in WEIGHTED:
            continue
        count = integer(layer, 2 if layer.kind == "InnerProduct" else 6)
        out = integer(layer, 0)
        require(count > 0 and count % out == 0, "invalid weight count")
        end = offset + 4 + 4 * (count + out)
        require(end <= len(raw) and raw[offset:offset+4] == b"\0" * 4,
                f"{layer.name}: truncated/non-FP32 weight block")
        layer.weights = np.frombuffer(raw, dtype="<f4", count=count, offset=offset+4).copy()
        layer.bias = np.frombuffer(raw, dtype="<f4", count=out, offset=offset+4+count*4).copy()
        require(np.isfinite(layer.weights).all() and np.isfinite(layer.bias).all(), "nonfinite weight/bias")
        offset = end
    require(offset == len(raw), "trailing or misaligned weight bytes")
    return layers, producers


def input_channels(layer):
    kw = integer(layer, 1)
    kernel = kw * integer(layer, 11, kw)
    divisor = integer(layer, 0) * kernel
    require(layer.weights.size % divisor == 0, "weight size is not OIHW")
    return layer.weights.size // divisor


def infer_shapes(layers):
    shapes = {}
    first_input = layers[0].tops[0]
    consumers = [l for l in layers if first_input in l.bottoms]
    require(consumers and all(l.kind == "Convolution" for l in consumers),
            "shape-free Input must feed ordinary convolution directly")
    channels = {input_channels(l) for l in consumers}
    require(len(channels) == 1 and next(iter(channels)) > 0, "inconsistent inferred Input channels")
    for layer in layers:
        kind = layer.kind
        if kind == "Input":
            shape = Shape(("C", "H", "W"), next(iter(channels)))
        else:
            shape = shapes[layer.bottoms[0]]
            if kind in {"Convolution", "ConvolutionDepthWise"}:
                require(shape.axes == ("C", "H", "W"), "convolution requires spatial CHW input")
                if kind == "Convolution":
                    require(input_channels(layer) == shape.channels, "convolution input channel mismatch")
                else:
                    require(integer(layer, 0) == integer(layer, 7) == shape.channels and input_channels(layer) == 1,
                            "only depthwise group=C, multiplier=1 is supported")
                shape = Shape(shape.axes, integer(layer, 0))
            elif kind == "Reduction":
                require(integer(layer, 0) == 3 and integer(layer, 1) == 0 and integer(layer, 5) == 1,
                        "only modern explicit-axis spatial mean is supported")
                require(float(layer.attrs.get(2, "1")) == 1.0 and integer(layer, 4, 0) == 0,
                        "only coefficient-one, non-keepdims mean is supported")
                items = layer.attrs.get(-23303, "").split(",")
                require(len(items) > 1, "explicit axes required")
                count, *axes = map(int, items)
                require(count == len(axes) and count > 0, "invalid reduction axes array")
                axes = [a if a >= 0 else a + len(shape.axes) for a in axes]
                require(len(set(axes)) == len(axes) and all(0 <= a < len(shape.axes) for a in axes), "invalid reduction axis")
                require(all(shape.axes[a] in {"H", "W"} for a in axes), "channel reduction is forbidden")
                shape = Shape(tuple(label for i, label in enumerate(shape.axes) if i not in axes), shape.channels)
            elif kind == "Concat":
                require(integer(layer, 0, 0) == 0 and all(shapes[b].axes == ("C",) for b in layer.bottoms),
                        "Concat requires axis0 vectors after spatial mean")
                shape = Shape(("C",), sum(shapes[b].channels for b in layer.bottoms))
            elif kind == "InnerProduct":
                require(shape.axes == ("C",) and layer.weights.size == integer(layer, 0)*shape.channels,
                        "FC requires one value per channel, not unknown flattening")
                shape = Shape(("C",), integer(layer, 0))
        shapes.update({top: shape for top in layer.tops})
    return shapes


def protected_channels(layers, shapes):
    """Keep a dead source channel if any transparent path reaches positive DW bias."""
    protected = {top: set() for layer in layers for top in layer.tops}
    for layer in reversed(layers):
        needed = set().union(*(protected[t] for t in layer.tops))
        if layer.kind == "ConvolutionDepthWise":
            needed |= set(np.flatnonzero(layer.bias > 0).tolist())
            protected[layer.bottoms[0]].update(needed)
        elif layer.kind in {"Split", "Reduction"}:
            protected[layer.bottoms[0]].update(needed)
        elif layer.kind == "Concat":
            offset = 0
            for bottom in layer.bottoms:
                count = shapes[bottom].channels
                protected[bottom].update(i-offset for i in needed if offset <= i < offset+count)
                offset += count
    return protected


def transform(param_text, raw, alignment=4):
    require(isinstance(alignment, int) and not isinstance(alignment, bool) and
            alignment > 0 and alignment & (alignment-1) == 0, "alignment must be a positive power of two")
    layers, _ = parse_graph(param_text, raw)
    shapes = infer_shapes(layers)
    protected = protected_channels(layers, shapes)
    retained, output_lines, output_bytes, audit = {}, ["7767517", f"{len(layers)} {len(shapes)}"], bytearray(), []
    for layer in layers:
        kind = layer.kind
        original_out = shapes[layer.tops[0]].channels
        inputs = retained[layer.bottoms[0]] if layer.bottoms else []
        original_in = shapes[layer.bottoms[0]].channels if layer.bottoms else 0
        attrs = dict(layer.attrs)
        details = {"name": layer.name, "type": kind, "input_channels_before": original_in,
                   "input_channels_after": len(inputs), "output_channels_before": original_out}
        if kind == "Input":
            keep = list(range(original_out))
        elif kind == "Convolution":
            require(original_out % alignment == 0, "original convolution outputs must match requested alignment")
            rows = layer.weights.reshape(original_out, original_in, -1)
            exact_zero = set(np.flatnonzero(np.all(rows == 0, axis=(1, 2)) & (layer.bias <= 0)).tolist())
            blocked = exact_zero & protected[layer.tops[0]]
            removable = exact_zero - blocked
            live = set(range(original_out)) - removable
            target = min(original_out, max(alignment, ((len(live)+alignment-1)//alignment)*alignment))
            keep = sorted(live | set(sorted(removable)[:target-len(live)]))
            rows = rows[keep][:, inputs, :].reshape(-1)
            bias = layer.bias[keep]
            attrs[0], attrs[6] = str(len(keep)), str(rows.size)
            details.update(exact_zero_relu_candidates=sorted(exact_zero), positive_dw_bias_protected=sorted(blocked),
                           proof="All original filter weights compare == 0.0 exactly; finite bias <= 0; fused ReLU=1.")
        elif kind == "ConvolutionDepthWise":
            keep = list(inputs)
            removed = sorted(set(range(original_out))-set(keep))
            require(np.all(layer.bias[removed] <= 0), "removed zero input would become positive DW constant")
            rows = layer.weights.reshape(original_out, -1)[keep].reshape(-1)
            bias = layer.bias[keep]
            attrs[0], attrs[6], attrs[7] = str(len(keep)), str(rows.size), str(len(keep))
            details.update(proof="Only omitted upstream-proven zero channels; their finite DW bias <= 0; fused ReLU=1.")
        elif kind in {"Split", "Reduction"}:
            keep = list(inputs)
        elif kind == "Concat":
            keep, offset = [], 0
            for bottom in layer.bottoms:
                keep.extend(offset+i for i in retained[bottom])
                offset += shapes[bottom].channels
            details["input_channels_after"] = len(keep)
            details["input_channels_before"] = original_out
        elif kind == "InnerProduct":
            rows = layer.weights.reshape(original_out, original_in)[:, inputs].reshape(-1)
            bias = layer.bias
            attrs[2] = str(rows.size)
            keep = list(range(original_out))
            details["retained_original_fc_columns"] = list(inputs)
        else:
            raise AssertionError("unvalidated operator")
        retained.update({top: keep for top in layer.tops})
        details.update(output_channels_after=len(keep), retained_original_output_channels=keep,
                       removed_original_output_channels=sorted(set(range(original_out))-set(keep)))
        if kind in WEIGHTED:
            output_bytes.extend(b"\0"*4)
            output_bytes.extend(np.asarray(rows, dtype="<f4").tobytes())
            output_bytes.extend(np.asarray(bias, dtype="<f4").tobytes())
            details.update(weights_before=int(layer.weights.size), weights_after=int(rows.size))
        fields = [kind, layer.name, str(len(layer.bottoms)), str(len(layer.tops)), *layer.bottoms, *layer.tops]
        fields.extend(f"{key}={value}" for key, value in attrs.items())
        output_lines.append(" ".join(fields))
        audit.append(details)
    result = "\n".join(output_lines)+"\n"
    # Re-read both artifacts with the same strict parser and topology checks.
    checked, _ = parse_graph(result, bytes(output_bytes))
    infer_shapes(checked)
    return result, bytes(output_bytes), audit


def prune(param, weights, output_dir, alignment=4):
    param, weights, output = Path(param).resolve(), Path(weights).resolve(), Path(output_dir).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("output directory must be absent or empty")
    source_param, source_weights = param.read_bytes(), weights.read_bytes()
    text, raw, layers = transform(source_param.decode("utf-8"), source_weights, alignment)
    digest = lambda value: hashlib.sha256(value).hexdigest()
    report = {"kind": "peppa_ncnn_fp32_exact_zero_channel_pruning", "alignment": alignment,
              "source": {"param": str(param), "weights": str(weights), "param_sha256": digest(source_param),
                         "weights_sha256": digest(source_weights), "weights_bytes": len(source_weights)},
              "output": {"param_sha256": digest(text.encode("utf-8")), "weights_sha256": digest(raw), "weights_bytes": len(raw)},
              "layers": layers, "accuracy_verified": False, "full_pipeline_verified": False,
              "full_pipeline_realtime_verified": False,
              "runtime_tensor_parity_verified": False, "original_onnx_equivalence_verified": False,
              "proof_scope": "Supplied fused NCNN FP32 graph; exact zero filters and nonpositive biases under ReLU, no epsilon.",
              "limitations": ["Assumes CHW input and finite input/intermediate activations; zero times infinity is not covered.",
                              "Changed packing and summation order require actual runtime tensor comparison; not bitwise equivalence.",
                              "No claim about original ONNX subnormal values, landmarks, accuracy or end-to-end performance."]}
    output.mkdir(parents=True, exist_ok=True)
    (output/"peppa.param").write_text(text, encoding="utf-8", newline="\n")
    (output/"peppa.bin").write_bytes(raw)
    (output/"audit.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--param", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--alignment", type=int, default=4)
    args = parser.parse_args()
    report = prune(args.param, args.weights, args.output_dir, args.alignment)
    print(json.dumps({"source_bytes": report["source"]["weights_bytes"], "output_bytes": report["output"]["weights_bytes"],
                      "runtime_tensor_parity_verified": False, "accuracy_verified": False}))
