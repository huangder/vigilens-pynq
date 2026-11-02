"""Read local TFLite model metadata without loading MediaPipe or invoking inference.

Requires flatbuffers and a compatible generated TensorFlow Lite schema. By default
the schema and models come from the local MediaPipe installation in .tools.
This is an operator inventory, not a runtime compatibility/performance test.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODULES = ROOT / ".tools/python312/Lib/site-packages/mediapipe/modules"
DEFAULT_SCHEMA = DEFAULT_MODULES.parent / "tasks/metadata/schema_py_generated.py"


def load_schema(path: Path):
    """Load the standalone generated file, avoiding mediapipe.__init__ side effects."""
    spec = importlib.util.spec_from_file_location("autonomy_tflite_schema", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load schema: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enum_names(enum):
    return {value: name for name, value in vars(enum).items()
            if not name.startswith("_") and isinstance(value, int)}


def decode(value):
    return value.decode("utf-8") if value is not None else None


def tensor_info(graph, index, schema):
    if index < 0 or index >= graph.TensorsLength():
        raise ValueError(f"Invalid I/O tensor index: {index}")
    tensor = graph.Tensors(index)
    quant = tensor.Quantization()
    return {
        "index": index,
        "name": decode(tensor.Name()),
        "shape": [tensor.Shape(j) for j in range(tensor.ShapeLength())],
        "type": enum_names(schema.TensorType).get(tensor.Type(), f"UNKNOWN_{tensor.Type()}"),
        "quantization": None if quant is None else {
            "scale": [quant.Scale(j) for j in range(quant.ScaleLength())],
            "zero_point": [quant.ZeroPoint(j) for j in range(quant.ZeroPointLength())],
            "quantized_dimension": quant.QuantizedDimension(),
        },
    }


def audit_model(path: Path, schema):
    raw = path.read_bytes()
    if len(raw) < 8 or raw[4:8] != b"TFL3":
        raise ValueError(f"Not a TFL3 FlatBuffer: {path}")
    model = schema.Model.GetRootAsModel(raw, 0)
    names = enum_names(schema.BuiltinOperator)
    codes = []
    for index in range(model.OperatorCodesLength()):
        code = model.OperatorCodes(index)
        # Old models only set the deprecated int8 field. New codes beyond 127
        # use the int32 field and the placeholder in the deprecated field.
        builtin = max(code.BuiltinCode(), code.DeprecatedBuiltinCode())
        custom = decode(code.CustomCode())
        if builtin == schema.BuiltinOperator.CUSTOM and not custom:
            raise ValueError(f"Custom operator {index} has no name")
        codes.append({"index": index, "builtin_code": builtin,
                      "builtin_name": names.get(builtin, f"UNKNOWN_{builtin}"),
                      "custom_name": custom, "version": code.Version(),
                      "instances": 0})
    subgraphs = []
    for index in range(model.SubgraphsLength()):
        graph = model.Subgraphs(index)
        counts = Counter()
        for j in range(graph.OperatorsLength()):
            code_index = graph.Operators(j).OpcodeIndex()
            if code_index >= len(codes):
                raise ValueError(f"Invalid opcode index: {code_index}")
            codes[code_index]["instances"] += 1
            counts[code_index] += 1
        subgraphs.append({
            "index": index, "name": decode(graph.Name()),
            "tensor_count": graph.TensorsLength(),
            "operator_count": graph.OperatorsLength(),
            "inputs": [tensor_info(graph, graph.Inputs(j), schema)
                       for j in range(graph.InputsLength())],
            "outputs": [tensor_info(graph, graph.Outputs(j), schema)
                        for j in range(graph.OutputsLength())],
            "opcode_instances": {str(k): v for k, v in sorted(counts.items())},
        })
    custom = sorted({c["custom_name"] for c in codes
                     if c["builtin_code"] == schema.BuiltinOperator.CUSTOM and c["instances"]})
    return {
        "path": path.resolve().as_posix(), "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema_version": model.Version(), "description": decode(model.Description()),
        "operator_codes": codes, "custom_operators": custom, "subgraphs": subgraphs,
        "requires_custom_registration": bool(custom),
        "inference_verified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="*", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    paths = args.models or sorted([
        *DEFAULT_MODULES.glob("face_detection/*.tflite"),
        *DEFAULT_MODULES.glob("face_landmark/*.tflite"),
    ])
    if not paths:
        parser.error("No models found; specify local .tflite paths")
    schema = load_schema(args.schema)
    result = {
        "audit_kind": "flatbuffer_metadata_only",
        "schema_path": args.schema.resolve().as_posix(),
        "schema_sha256": hashlib.sha256(args.schema.read_bytes()).hexdigest(),
        "models": [audit_model(path, schema) for path in paths],
        "limitations": [
            "No inference, model accuracy, speed or memory benchmark was performed.",
            "Builtin-only inventory does not prove target runtime compatibility.",
            "Raw model outputs require the MediaPipe preprocessing and postprocessing graph.",
        ],
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
