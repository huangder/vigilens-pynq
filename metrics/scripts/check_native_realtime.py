"""Fail-closed full-board realtime gate; no camera-only or host FPS substitution.

Read one complete board camera log and its exact contract JSONL. Thresholds
come from config.yaml. Passing this gate is throughput evidence, not accuracy
or cold-boot acceptance. Native camera mode is reported separately.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.contract import validate_frame

FIELD = re.compile(r"(\w+)=([^\s]+)")


def evaluate(log: str, contracts: list[dict], config: dict) -> dict:
    errors = []
    summaries = {}
    for name in ("AUTONOMY_SUMMARY", "AUDIT_SUMMARY", "SAMPLE_RESULT"):
        lines = [line for line in log.splitlines() if line.startswith(name + " ")]
        if len(lines) != 1:
            errors.append(f"expected one {name}, got {len(lines)}")
        else:
            summaries[name] = dict(FIELD.findall(lines[0]))

    def number(group, key):
        try:
            value = float(summaries[group][key])
            if not math.isfinite(value) or value < 0:
                raise ValueError
            return value
        except (KeyError, ValueError):
            errors.append(f"missing/invalid {group}.{key}")
            return None

    def require(group, key, expected):
        actual = number(group, key)
        if actual is not None and actual != expected:
            errors.append(f"{group}.{key}={actual}, required {expected}")

    fps = float(config["fps_nominal"])
    capture_min = fps * float(config["realtime_capture_ratio_min"])
    process_min = fps * float(config["realtime_process_ratio_min"])
    capture = number("AUTONOMY_SUMMARY", "camera_submit_fps")
    analysis = number("AUTONOMY_SUMMARY", "analysis_update_fps")
    for label, actual, minimum in (("capture", capture, capture_min), ("analysis", analysis, process_min)):
        if actual is not None and actual < minimum:
            errors.append(f"{label} {actual} fps below {minimum}")
    for key in ("skipped", "failed", "rc"):
        require("AUTONOMY_SUMMARY", key, 0)
    submitted = number("AUTONOMY_SUMMARY", "submitted")
    processed = number("AUTONOMY_SUMMARY", "processed")
    if submitted is not None:
        require("AUTONOMY_SUMMARY", "accepted", submitted)
        require("AUTONOMY_SUMMARY", "processed", submitted)
        require("AUDIT_SUMMARY", "frames", submitted)
        require("SAMPLE_RESULT", "kept", submitted)
    require("AUTONOMY_SUMMARY", "succeeded", len(contracts))
    rejected = number("AUTONOMY_SUMMARY", "rejected")
    if processed is not None and rejected is not None and processed != len(contracts) + rejected:
        errors.append("processed count does not match contracts + rejections")
    require("AUDIT_SUMMARY", "failures", 0)
    audits = number("AUDIT_SUMMARY", "audits")
    if audits is not None and audits < 1:
        errors.append("no hardware audits")
    if not any(line.startswith("CAMERA_PL_PASS ") for line in log.splitlines()):
        errors.append("hardware completion marker missing")
    if not contracts:
        errors.append("no contract frames")
    elif any(validate_frame(row) for row in contracts):
        errors.append("invalid contract frames")
    elif any(a["frame_id"] >= b["frame_id"] or a["ts"] >= b["ts"]
             for a, b in zip(contracts, contracts[1:])):
        errors.append("nonmonotonic contract frames")
    if contracts and not any(row.get("face", {}).get("visible", 0) > 0 for row in contracts):
        errors.append("no face-bearing workload")
    face_count=sum(row.get("face",{}).get("visible",0)>0 for row in contracts)
    face_ratio=face_count/len(contracts) if contracts else 0
    if contracts and face_ratio<float(config["face_visible_min"]):
        errors.append("face-bearing coverage below configured face_visible_min")
    sample_times = []
    for line in log.splitlines():
        if line.startswith("CAMERA_PL_FRAME "):
            fields = dict(FIELD.findall(line))
            try:
                sample_times.append(int(fields["ts_us"]) / 1e6)
                if int(fields["mismatch"]):
                    errors.append("per-frame hardware mismatch")
            except (KeyError, ValueError):
                errors.append("malformed hardware frame")
    if submitted is not None and len(sample_times) != submitted:
        errors.append("raw hardware frame count differs from summary")
    gaps = [b-a for a, b in zip(sample_times, sample_times[1:])]
    gap_p95 = None
    if len(gaps) < 1 or any(gap <= 0 for gap in gaps):
        errors.append("insufficient/nonmonotonic capture timestamps")
    else:
        capture_from_timestamps = (len(sample_times)-1)/(sample_times[-1]-sample_times[0])
        if capture_from_timestamps < capture_min:
            errors.append("raw capture timestamps fail configured throughput")
        # Binary summaries print six decimals; allow their rounding only.
        if capture is not None and abs(capture_from_timestamps-capture) > 1e-6:
            errors.append("reported capture FPS differs from raw timestamps")
        gaps.sort()
        rank = (len(gaps)-1)*0.95
        low = int(rank)
        gap_p95 = gaps[low] + (gaps[min(low+1, len(gaps)-1)]-gaps[low])*(rank-low)
        if gap_p95 > float(config["realtime_jitter_ratio_max"]) / fps:
            errors.append("capture interval p95 exceeds configured jitter limit")
    return dict(kind="native_full_board_realtime_gate", passed=not errors, errors=errors,
                capture_fps=capture, analysis_update_fps=analysis, capture_interval_p95_s=gap_p95,
                submitted=submitted, processed=processed, contract_frames=len(contracts),
                face_bearing_ratio=face_ratio,
                face_bearing_frames=face_count,
                face_bearing_update_fps=analysis*face_ratio if analysis is not None else None,
                native_camera_mode_verified=summaries.get("SAMPLE_RESULT", {}).get("native_30fps") == "yes",
                accuracy_verified=False, cold_boot_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("contracts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.contracts.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = evaluate(args.log.read_text(encoding="utf-8", errors="replace"), rows, load_config())
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
