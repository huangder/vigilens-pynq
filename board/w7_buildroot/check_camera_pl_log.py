"""Validate actual camera/PL serial logs; hardware correctness != realtime."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config


def inspect_log(text: str, cfg: dict) -> dict:
    text = text.replace("\r", "")
    if re.search(r"(?m)^(CAMERA_PL_FAIL|CAPTURE_FAIL|PL_TIMEOUT|PL_PROFILE_FAIL|"
                 r"JPEG_DECODE_FAIL|BOARD_STREAM_FAIL|PIPELINE_START_FAIL)", text):
        raise ValueError("board reported a failure")
    records = []
    for line in text.splitlines():
        if line.startswith("CAMERA_PL_FRAME "):
            records.append(dict(re.findall(r"([a-z0-9_]+)=([^\s]+)", line)))
    match = re.search(r"(?m)^CAMERA_PL_PASS compared_frames=(\d+) native_camera_mode_verified=([01])$", text)
    if not match or len(records) < 2 or int(match[1]) != len(records):
        raise ValueError("missing/truncated frame results or completion marker")
    native = match[2] == "1"
    if f"CAMERA_PL_EXIT={0 if native else 2}" not in text:
        raise ValueError("missing expected probe exit status")
    count = int(cfg["tier_720p60_width"]) * int(cfg["tier_720p60_height"])
    ready = [dict(re.findall(r"([a-z0-9_]+)=([^\s]+)", line))
             for line in text.splitlines() if line.startswith("PL_READY ")]
    modern = bool(ready and "audit_every" in ready[0])
    if len(ready) > 1:
        raise ValueError("multiple board sessions in one log")
    if not modern and any("audit" in r for r in records):
        raise ValueError("audit frame lacks pipeline configuration")
    audit_every = int(ready[0]["audit_every"]) if modern else 1
    if not 0 <= audit_every <= 1000:
        raise ValueError("invalid audit interval")
    workers = int(ready[0].get("decode_workers", 1)) if modern else 1
    pl_wait = ready[0].get("pl_wait", "spin") if modern else "spin"
    if pl_wait not in ("spin", "yield"):
        raise ValueError("invalid PL wait policy")
    if workers not in (1, 2) or (modern and ready[0].get("pipeline") == "serial" and workers != 1):
        raise ValueError("invalid decoder worker count")
    decode_peak = None
    if modern and "decode_workers" in ready[0] and ready[0].get("pipeline") == "threaded":
        decodes = [dict(re.findall(r"([a-z0-9_]+)=([^\s]+)", line))
                   for line in text.splitlines() if line.startswith("DECODE_SUMMARY ")]
        if (len(decodes) != 1 or int(decodes[0]["workers"]) != workers or
                int(decodes[0]["frames"]) != len(records) or
                not 1 <= int(decodes[0]["peak_active"]) <= workers):
            raise ValueError("missing or inconsistent decoder summary")
        decode_peak = int(decodes[0]["peak_active"])
    audit_count = 0
    times, sequences = [], []
    for i, record in enumerate(records):
        expected_audit = int(bool(audit_every) and i % audit_every == 0)
        if modern and int(record.get("audit", -1)) != expected_audit:
            raise ValueError(f"frame {i+1} audit schedule differs")
        audit_count += expected_audit
        if (int(record["n"]) != i+1 or int(record["mismatch"]) != 0 or
                int(record["roi_count"]) != count or int(record["motion_valid"]) != int(i > 0) or
                not 0 <= int(record["motion_ratio_q16"]) <= 65536):
            raise ValueError(f"frame {i+1} failed its data/identity checks")
        times.append(int(record["ts_us"]) / 1e6)
        sequences.append(int(record["seq"]))
    if modern:
        audits = [dict(re.findall(r"([a-z0-9_]+)=([^\s]+)", line))
                  for line in text.splitlines() if line.startswith("AUDIT_SUMMARY ")]
        if (len(audits) != 1 or int(audits[0]["frames"]) != len(records) or
                int(audits[0]["audits"]) != audit_count or int(audits[0]["failures"]) != 0):
            raise ValueError("missing or failed audit summary")
    intervals = [b-a for a, b in zip(times, times[1:])]
    if min(intervals) <= 0 or any(b <= a for a, b in zip(sequences, sequences[1:])):
        raise ValueError("capture timestamps/sequences are not strictly increasing")
    fps = (len(times)-1)/(times[-1]-times[0])
    p95 = statistics.quantiles(intervals, n=100, method="inclusive")[94] if len(intervals)>1 else intervals[0]
    target = float(cfg["fps_nominal"])
    realtime = (fps >= target * float(cfg["realtime_capture_ratio_min"]) and
                p95 <= float(cfg["realtime_jitter_ratio_max"]) / target)
    return {"hardware_invariants_pass": True,
            "hardware_compare_pass": audit_count == len(records),
            "audit_compare_pass": audit_count > 0, "audit_frames": audit_count,
            "verification_mode": "every_frame" if audit_every == 1 else "audit",
            "decode_workers": workers, "decode_peak_active": decode_peak, "pl_wait": pl_wait,
            "compared_frames": len(records),
            "valid_motion_frames": len(records)-1, "native_camera_mode_verified": native,
            "source_span_s": times[-1]-times[0], "selected_fps": fps,
            "interval_p95_ms": p95*1000, "target_fps": target,
            "realtime_target_met": realtime,
            "timing_median_ms": {key: statistics.median(float(r[key]) for r in records)
                                 for key in ("decode_ms", "reference_ms", "transfer_pl_ms",
                                             "decode_cpu_ms", "transfer_pl_cpu_ms")
                                 if all(key in r for r in records)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--require-realtime", action="store_true")
    ap.add_argument("--require-native-mode", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = inspect_log(args.log.read_text(encoding="utf-8-sig"), load_config())
    except (ValueError, KeyError, OSError) as exc:
        print(f"CAMERA_PL_LOG_FAIL: {exc}", file=sys.stderr)
        return 1
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded+"\n", encoding="utf-8")
    return int((args.require_realtime and not result["realtime_target_met"]) or
               (args.require_native_mode and not result["native_camera_mode_verified"]))


if __name__ == "__main__":
    raise SystemExit(main())
