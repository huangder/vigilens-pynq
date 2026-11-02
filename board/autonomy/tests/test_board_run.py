"""All generated logs here are synthetic validator fixtures, not board evidence."""
import base64
import gzip
import importlib.util
import json
import math
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "check_board_run.py"
spec = importlib.util.spec_from_file_location("check_board_run", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def write_replay(files, rows):
    raw = ("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n").encode()
    files["host_replay"].write_bytes(raw)
    files["board_replay"].write_bytes(raw)
    files["replay_log"].write_text(
        "/tmp/vg_measure_replay: OK\n/tmp/synthetic_replay.txt: OK\n"
        "AUTONOMY_CORE_BOARD_BEGIN\n900 /tmp/synthetic_replay_board.jsonl\n"
        + check.sha(raw) + "  /tmp/synthetic_replay_board.jsonl\nAUTONOMY_CORE_BOARD_END\n"
        "VIGI_FILE_BEGIN synthetic_replay_board.jsonl\n"
        + base64.b64encode(gzip.compress(raw)).decode() + "\nVIGI_FILE_END\n")


@pytest.fixture
def evidence(tmp_path):
    files = {name: tmp_path / name for name in ("probe_log", "replay_log", "host_replay", "board_replay", "config")}
    files["config"].write_text("fps_nominal: 30\n")
    config_hash = check.sha(files["config"].read_bytes())
    rows = [{"kind": "measure_replay", "synthetic": True, "config_sha256": config_hash,
             "record": i, "frame_id": i, "ts": i / 30, "rc": -2 if i == 0 else 0} for i in range(900)]
    for row in rows[1:]:
        row.update(behavior={"ear_left": 0.3}, quality={"overall": 1},
                   decision={"status": "normal"},
                   vital=dict.fromkeys(("hr_bpm", "hr_conf", "rr_per_min", "rr_conf")))
    write_replay(files, rows)
    lines = [*check.MODEL_HASHES, "/tmp/vg_model_probe: OK", "/tmp/attention.tflite: OK",
             "/tmp/detection.tflite: OK", "AUTONOMY_BOARD_PROBE_BEGIN", "armv7l"]
    for threads in (1, 2):
        for model, shapes in check.SHAPES.items():
            row = {"kind": "model_tensor_probe", "synthetic_input": True,
                   "landmark_pipeline_verified": False, "iterations": 30,
                   "threads": threads, "input_bytes": 442368 if model == "ATTENTION" else 196608,
                   "mean_ms": 100, "p50_ms": 99, "p95_ms": 101, "max_rss_kib": 5000,
                   "outputs": [{"slot": i, "elements": math.prod(shape), "shape": shape} for i, shape in enumerate(shapes)]}
            lines += [f"{model}_THREADS={threads}", f"MODEL_PROBE_PASS outputs={len(shapes)} synthetic=1 mean_ms=100", json.dumps(row)]
    lines += ["AUTONOMY_BOARD_PROBE_END"]
    files["probe_log"].write_text("\n".join(lines) + "\n")
    return files, rows


def test_complete_evidence_has_limited_claims(evidence):
    files, _ = evidence
    result = check.check_run(**files)
    assert result["hardware_model_tensor_execution_verified"] is True
    assert result["full_autonomy_verified"] is result["accuracy_verified"] is False
    assert result["full_pipeline_realtime_verified"] is False
    assert result["replay"]["reject_counts"] == {"-2": 1}
    assert result["replay"]["status_counts"] == {"normal": 899}
    assert result["single_inference_budget_ms"] == 1000 / 30
    assert not any(p["mean_within_single_inference_budget"] for p in result["baseline"]["probes"])


@pytest.mark.parametrize("old,new", [
    ("AUTONOMY_BOARD_PROBE_END", "> echo AUTONOMY_BOARD_PROBE_END"),
    ("armv7l", "x86_64"),
    ("/tmp/attention.tflite: OK", "/tmp/attention.tflite: FAILED"),
    ("ATTENTION_THREADS=2", "ATTENTION_THREADS=1"),
    ('"mean_ms": 100', '"mean_ms": NaN'),
    ('"mean_ms": 100', '"mean_ms": 1e999'),
    ('"threads": 2', '"threads": 1'),
    ('"elements": 1404', '"elements": 1403'),
    ("mean_ms=100", "mean_ms=99"),
])
def test_probe_tampering_rejected(evidence, old, new):
    files, _ = evidence
    path = files["probe_log"]
    path.write_text(path.read_text().replace(old, new, 1))
    with pytest.raises(ValueError):
        check.check_run(**files)


@pytest.mark.parametrize("case", ["synthetic", "config", "vital", "rejected_measurement", "nan", "record", "truncated"])
def test_bound_but_invalid_replay_rejected(evidence, case):
    files, rows = evidence
    if case == "synthetic":
        rows[1]["synthetic"] = False
    elif case == "config":
        rows[1]["config_sha256"] = "0" * 64
    elif case == "vital":
        rows[1]["vital"]["hr_bpm"] = 75
    elif case == "rejected_measurement":
        rows[0]["quality"] = {"overall": 1}
    elif case == "nan":
        rows[1]["behavior"]["ear_left"] = float("nan")
    elif case == "record":
        rows[1]["record"] = 2
    else:
        rows.pop()
    write_replay(files, rows)
    with pytest.raises(ValueError):
        check.check_run(**files)


def test_replay_hash_and_payload_binding(evidence):
    files, _ = evidence
    path = files["replay_log"]
    path.write_text(path.read_text().replace("900 /tmp/", "899 /tmp/", 1))
    with pytest.raises(ValueError, match="row count"):
        check.check_run(**files)


def test_optimized_requires_actual_delegate_nodes(evidence, tmp_path):
    files, _ = evidence
    path = tmp_path / "optimized.log"
    path.write_text(files["probe_log"].read_text())
    with pytest.raises(ValueError, match="delegate"):
        check.check_run(**files, optimized_probe_log=path)
    text = path.read_text().replace('"threads":', '"delegate_nodes": 1, "threads":')
    text = text.replace("synthetic=1 mean_ms", "synthetic=1 delegate_nodes=1 mean_ms")
    path.write_text(text)
    result = check.check_run(**files, optimized_probe_log=path)
    assert all(p["delegate_nodes"] == 1 for p in result["optimized"]["probes"])


def test_duplicate_json_and_cli_failure(evidence, tmp_path):
    with pytest.raises(ValueError, match="Duplicate"):
        check.strict_json('{"a":1,"a":2}')
    files, _ = evidence
    files["probe_log"].write_text("truncated")
    output = tmp_path / "out.json"
    arguments = [part for name, path in files.items() for part in ("--" + name.replace("_", "-"), str(path))]
    assert check.main(arguments + ["--out", str(output)]) == 1
    assert json.loads(output.read_text())["hardware_model_tensor_execution_verified"] is False
