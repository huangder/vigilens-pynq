"""Validate recorded ARM tensor probes and synthetic measurement replay evidence.

This checks consistency of supplied evidence, not its cryptographic authenticity
or measurement accuracy. A tensor probe never proves the full camera pipeline.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SHAPES = {
    "ATTENTION": [[1, 1, 1, n] for n in (1404, 160, 142, 142, 10, 10, 1)],
    "DETECTION": [[1, 896, 16], [1, 896, 1]],
}
MODEL_HASHES = ("e06a804e0144f9929eda782122916b35d60c697c3c9344013ca2bbe76a6ce2b4",
                "bbff11cebd1eb27a1e004cae0b0e63ec8c551cbf34a4451148b4908b8db3eca8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")
    result = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
    def finite(value):
        if isinstance(value, float):
            require(math.isfinite(value), "Nonfinite JSON number")
        elif isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)
    finite(result)
    return result


def marked(lines, begin, end):
    require(lines.count(begin) == lines.count(end) == 1, f"Missing/duplicate markers: {begin}")
    first, last = lines.index(begin), lines.index(end)
    require(first < last, f"Reversed markers: {begin}")
    return lines[first + 1:last]


def positive(value, name):
    require(type(value) in (int, float) and math.isfinite(value) and value > 0, f"Invalid {name}")


def probe_report(path, budget_ms, optimized=False):
    text = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    body = marked(lines, "AUTONOMY_BOARD_PROBE_BEGIN", "AUTONOMY_BOARD_PROBE_END")
    require(body.count("armv7l") == 1, "Missing armv7l runtime architecture")
    for filename in ("attention.tflite", "detection.tflite"):
        require(lines.count(f"/tmp/{filename}: OK") == 1, f"Missing SHA256 OK: {filename}")
    require(any(re.fullmatch(r"/tmp/vg_model_probe(?:_[a-zA-Z0-9_]+)?: OK", line) for line in lines), "Missing probe executable SHA256 OK")
    require(all(value in text for value in MODEL_HASHES), "Model SHA256 declarations missing")
    sections = {}
    current = None
    for line in body:
        match = re.fullmatch(r"(ATTENTION|DETECTION)_THREADS=([12])", line)
        if match:
            current = (match[1], int(match[2]))
            require(current not in sections, "Duplicate probe section")
            sections[current] = {"pass": [], "json": []}
        elif line.startswith("MODEL_PROBE_PASS") or line.startswith("{"):
            require(current is not None, "Probe result before section header")
            sections[current]["pass" if line.startswith("MODEL_PROBE_PASS") else "json"].append(line)
        elif "MODEL_PROBE_FAIL" in line:
            raise ValueError("Probe failure marker")
    expected = {(model, n) for model in SHAPES for n in (1, 2)}
    require(set(sections) == expected, "Incomplete four-probe matrix")
    output = []
    for (model, threads), section in sections.items():
        require(len(section["pass"]) == len(section["json"]) == 1, "Missing/duplicate probe result")
        row = strict_json(section["json"][0])
        require(row.get("kind") == "model_tensor_probe" and row.get("synthetic_input") is True, "Probe is not explicitly synthetic")
        require(row.get("landmark_pipeline_verified") is False, "Tensor probe must not claim landmark pipeline verification")
        require(type(row.get("threads")) is int and row["threads"] == threads, "Probe thread mismatch")
        require(type(row.get("iterations")) is int and row["iterations"] == 30, "Incomplete probe iteration count")
        require(row.get("input_bytes") == (442368 if model == "ATTENTION" else 196608), "Probe input size mismatch")
        for field in ("mean_ms", "p50_ms", "p95_ms", "max_rss_kib"):
            positive(row.get(field), field)
        require(type(row["max_rss_kib"]) is int, "RSS must be integer KiB")
        require(row["p50_ms"] <= row["p95_ms"], "Invalid probe percentiles")
        outputs = row.get("outputs")
        require(isinstance(outputs, list) and len(outputs) == len(SHAPES[model]), "Wrong probe outputs")
        for slot, (tensor, shape) in enumerate(zip(outputs, SHAPES[model])):
            require(isinstance(tensor, dict) and type(tensor.get("slot")) is int
                    and type(tensor.get("elements")) is int
                    and isinstance(tensor.get("shape"), list)
                    and all(type(d) is int for d in tensor["shape"]), "Invalid tensor metadata types")
            require(tensor == {"slot": slot, "elements": math.prod(shape), "shape": shape}, "Probe tensor shape/elements mismatch")
        match = re.fullmatch(r"MODEL_PROBE_PASS outputs=(\d+) synthetic=1(?: delegate_nodes=(\d+))? mean_ms=([0-9.eE+-]+)", section["pass"][0])
        require(match is not None and int(match[1]) == len(outputs), "Invalid MODEL_PROBE_PASS summary")
        require(float(match[3]) == float(format(row["mean_ms"], ".6g")), "Probe PASS/JSON latency mismatch")
        delegates = row.get("delegate_nodes", 0)
        require(type(delegates) is int and delegates >= 0, "Invalid delegate count")
        if match[2] is not None:
            require(int(match[2]) == delegates, "Probe PASS/JSON delegate mismatch")
        if optimized:
            require(delegates > 0 and match[2] is not None, "Optimized evidence must execute delegate nodes")
        output.append({"model": model, "threads": threads,
                       **{key: row[key] for key in ("mean_ms", "p50_ms", "p95_ms", "max_rss_kib")},
                       "delegate_nodes": delegates,
                       "mean_within_single_inference_budget": row["mean_ms"] <= budget_ms,
                       "p95_within_single_inference_budget": row["p95_ms"] <= budget_ms})
    return {"path": str(path), "sha256": sha(path.read_bytes()), "probes": output}


def replay_report(log_path, host_path, board_path, config_hash):
    host, board = host_path.read_bytes(), board_path.read_bytes()
    require(host == board, "Host/board replay bytes differ")
    require(board.endswith(b"\n"), "Truncated replay: missing final newline")
    raw_rows = board.decode("utf-8").splitlines()
    require(len(raw_rows) == 900 and all(raw_rows), "Replay must contain exactly 900 records")
    lines = [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines()]
    body = marked(lines, "AUTONOMY_CORE_BOARD_BEGIN", "AUTONOMY_CORE_BOARD_END")
    for filename in ("vg_measure_replay", "synthetic_replay.txt"):
        require(lines.count(f"/tmp/{filename}: OK") == 1, f"Missing replay SHA256 OK: {filename}")
    require(body.count("900 /tmp/synthetic_replay_board.jsonl") == 1, "Replay log row count mismatch")
    require(body.count(f"{sha(board)}  /tmp/synthetic_replay_board.jsonl") == 1, "Replay log SHA256 mismatch")
    payload = marked(lines, "VIGI_FILE_BEGIN synthetic_replay_board.jsonl", "VIGI_FILE_END")
    require(gzip.decompress(base64.b64decode("".join(payload), validate=True)) == board, "Logged replay payload differs from board file")
    statuses, rejects = Counter(), Counter()
    for index, raw in enumerate(raw_rows):
        row = strict_json(raw)
        require(row.get("kind") == "measure_replay" and row.get("synthetic") is True, "Replay must be explicitly synthetic")
        require(row.get("config_sha256") == config_hash, "Replay config SHA256 mismatch")
        require(type(row.get("record")) is int and row["record"] == index, "Replay record sequence mismatch")
        require(type(row.get("frame_id")) is int and row["frame_id"] >= 0, "Invalid replay frame_id")
        require(type(row.get("ts")) in (int, float) and row["ts"] >= 0, "Invalid replay timestamp")
        require(type(row.get("rc")) is int and row["rc"] <= 0, "Invalid replay return code")
        if row["rc"] != 0:
            require(set(row) == {"kind", "synthetic", "config_sha256", "record", "frame_id", "ts", "rc"}, "Rejected record contains measurements")
            rejects[str(row["rc"])] += 1
        else:
            require(all(isinstance(row.get(key), dict) and row[key] for key in ("behavior", "quality", "decision")), "Accepted record lacks measurements")
            require(row.get("vital") == dict.fromkeys(("hr_bpm", "hr_conf", "rr_per_min", "rr_conf")), "Replay must not invent vital measurements")
            status = row["decision"].get("status")
            require(isinstance(status, str) and status, "Missing replay status")
            statuses[status] += 1
    return {"rows": 900, "sha256": sha(board), "host_board_byte_equal": True,
            "config_sha256": config_hash, "synthetic": True,
            "status_counts": dict(statuses), "reject_counts": dict(rejects)}


def check_run(probe_log, replay_log, host_replay, board_replay, config, optimized_probe_log=None):
    config_raw = config.read_bytes()
    match = re.search(r"^fps_nominal:\s*([^\s#]+)", config_raw.decode("utf-8-sig"), re.MULTILINE)
    require(match is not None, "Missing fps_nominal configuration")
    fps = float(match[1])
    positive(fps, "fps_nominal")
    budget = 1000 / fps
    result = {"hardware_model_tensor_execution_verified": True,
              "synthetic_measurement_replay_verified": True,
              "full_autonomy_verified": False, "accuracy_verified": False,
              "full_pipeline_realtime_verified": False,
              "fps_nominal": fps, "single_inference_budget_ms": budget,
              "budget_interpretation": "Necessary per-inference condition only; excludes capture, other inference, PL, metrics and service work.",
              "baseline": probe_report(probe_log, budget),
              "replay": replay_report(replay_log, host_replay, board_replay, sha(config_raw))}
    if optimized_probe_log is not None:
        result["optimized"] = probe_report(optimized_probe_log, budget, optimized=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-log", type=Path, default=ROOT / "metrics/logs/autonomy_board_probe.log")
    parser.add_argument("--replay-log", type=Path, default=ROOT / "metrics/logs/autonomy_board_replay.log")
    parser.add_argument("--host-replay", type=Path, default=ROOT / ".tmp/autonomy/synthetic_replay_host.jsonl")
    parser.add_argument("--board-replay", type=Path, default=ROOT / ".tmp/autonomy/board_outputs/synthetic_replay_board.jsonl")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--optimized-probe-log", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = check_run(args.probe_log, args.replay_log, args.host_replay, args.board_replay,
                           args.config, args.optimized_probe_log)
    except (ValueError, KeyError, TypeError, OSError, EOFError) as error:
        result = {"hardware_model_tensor_execution_verified": False,
                  "full_autonomy_verified": False, "accuracy_verified": False, "error": str(error)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return 0 if result["hardware_model_tensor_execution_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
