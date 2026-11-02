from array import array
import importlib.util
import json
from pathlib import Path
import sys
import pytest

spec = importlib.util.spec_from_file_location("compare_probe", Path(__file__).parents[1]/"compare_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def fixture(prefix, values):
    info = {"outputs": [{"slot": 0, "elements": len(values), "shape": [1, len(values)]}],
            "synthetic_input": True, "input_bytes": 4}
    Path(str(prefix)+".json").write_text(json.dumps(info))
    content = array("f", values)
    if sys.byteorder != "little":
        content.byteswap()
    Path(str(prefix) + ".output0.f32").write_bytes(content.tobytes())


def test_numeric_difference_is_reported_without_certifying_hardware(tmp_path):
    a, b = tmp_path/"host", tmp_path/"arm"
    fixture(a, [0, 1, 2]); fixture(b, [0, 1.5, 2])
    report = probe.compare(a, b)
    assert report["structural_match"]
    assert report["outputs"][0]["max_abs_difference"] == .5
    assert not report["outputs"][0]["identical"]
    assert not report["hardware_runtime_verified"]
    assert not report["accuracy_verified"]


def test_truncated_and_nonfinite_outputs_are_rejected(tmp_path):
    p = tmp_path/"bad"
    fixture(p, [float("nan")])
    with pytest.raises(ValueError, match="Nonfinite"):
        probe.read_outputs(p)
    Path(str(p)+".output0.f32").write_bytes(b"a")
    with pytest.raises(ValueError, match="size"):
        probe.read_outputs(p)


def test_different_layout_is_rejected(tmp_path):
    a, b = tmp_path/"a", tmp_path/"b"
    fixture(a, [1]); fixture(b, [1, 2])
    with pytest.raises(ValueError, match="layouts"):
        probe.compare(a, b)


def test_external_tensors_require_a_separate_input_binding(tmp_path):
    a, b = tmp_path/"a.with.dots", tmp_path/"b.with.dots"
    fixture(a, [1]); fixture(b, [1])
    for prefix in (a, b):
        path = Path(str(prefix)+".json")
        info = json.loads(path.read_text())
        info["synthetic_input"] = False
        path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="requires --zeros"):
        probe.compare(a, b)


def test_board_environment_label_does_not_replace_execution_evidence(tmp_path):
    a, b = tmp_path/"host", tmp_path/"target"
    fixture(a, [1]); fixture(b, [1])
    report = probe.compare(a, b, "cortex_a9_board")
    assert report["target_environment"] == "cortex_a9_board"
    assert "target" in report
    assert not report["hardware_runtime_verified"]
    with pytest.raises(ValueError, match="environment"):
        probe.compare(a, b, "unverified_platform")
