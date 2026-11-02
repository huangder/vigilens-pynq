"""Verify compatibility handling with generated, non-inference FlatBuffers."""
import hashlib
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "audit_models.py"
spec = importlib.util.spec_from_file_location("audit_models", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture
def schema():
    if not audit.DEFAULT_SCHEMA.exists():
        pytest.skip("Local MediaPipe schema not installed")
    pytest.importorskip("flatbuffers")
    return audit.load_schema(audit.DEFAULT_SCHEMA)


def write_model(path, schema, *, custom=False, invalid_opcode=False):
    import flatbuffers
    model = schema.ModelT()
    model.version = 3
    model.description = "Synthetic metadata fixture; not an inference model"
    code = schema.OperatorCodeT()
    # Exercise an older model that sets only DeprecatedBuiltinCode, and a
    # versioned custom op that must never be mistaken for a builtin ADD.
    code.deprecatedBuiltinCode = (schema.BuiltinOperator.CUSTOM if custom
                                  else schema.BuiltinOperator.PRELU)
    code.customCode = "FixtureCustom" if custom else None
    code.version = 2 if custom else 1
    model.operatorCodes = [code]
    graph = schema.SubGraphT()
    tensor = schema.TensorT()
    tensor.name = "image"
    tensor.shape = [1, 2, 2, 3]
    tensor.type = schema.TensorType.FLOAT32
    graph.tensors = [tensor]
    graph.inputs = [0]
    graph.outputs = [0]
    op = schema.OperatorT()
    op.opcodeIndex = 1 if invalid_opcode else 0
    op.inputs = [0]
    op.outputs = [0]
    graph.operators = [op, op]
    model.subgraphs = [graph]
    model.buffers = [schema.BufferT()]
    builder = flatbuffers.Builder(1024)
    offset = model.Pack(builder)
    builder.Finish(offset, file_identifier=b"TFL3")
    path.write_bytes(builder.Output())
    return path


def test_legacy_builtin_and_io_hash(tmp_path, schema):
    path = write_model(tmp_path / "builtin.tflite", schema)
    result = audit.audit_model(path, schema)
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["operator_codes"][0]["builtin_name"] == "PRELU"
    assert result["operator_codes"][0]["instances"] == 2
    assert result["subgraphs"][0]["inputs"][0]["shape"] == [1, 2, 2, 3]
    assert result["subgraphs"][0]["outputs"][0]["type"] == "FLOAT32"
    assert result["requires_custom_registration"] is False
    assert result["inference_verified"] is False


def test_custom_version_preserved(tmp_path, schema):
    path = write_model(tmp_path / "custom.tflite", schema, custom=True)
    result = audit.audit_model(path, schema)
    assert result["custom_operators"] == ["FixtureCustom"]
    assert result["requires_custom_registration"] is True
    assert result["operator_codes"][0]["version"] == 2
    assert result["operator_codes"][0]["instances"] == 2


def test_bad_opcode_rejected(tmp_path, schema):
    path = write_model(tmp_path / "bad_opcode.tflite", schema, invalid_opcode=True)
    with pytest.raises(ValueError, match="Invalid opcode"):
        audit.audit_model(path, schema)


def test_non_model_rejected(tmp_path):
    path = tmp_path / "bad.tflite"
    path.write_bytes(b"not a tflite file")
    with pytest.raises(ValueError, match="Not a TFL3"):
        audit.audit_model(path, None)
