"""Compare two native vision replays (baseline vs tiered full-frame search).

Both inputs are produced by `.tmp/autonomy/.../vg_vision_replay_host`, i.e. the
board composition on a host CPU. This checks contract validity, per-frame
agreement and search-cost deltas; it is not a hardware or accuracy result.

python metrics/scripts/compare_native_detection.py \
    --base-log base_native.log --base-contract base_contract.jsonl \
    --fast-log fast_native.log --fast-contract fast_contract.jsonl \
    --output metrics/evidence/2026-11-01_native_detection_tiering.json
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.contract import validate_frame

FRAME = re.compile(r"(\w+)=([^\s]+)")


def frames(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("VISION_FRAME"):
            rows.append(dict(FRAME.findall(line)))
        elif line.startswith("VISION_SUMMARY"):
            summary = dict(FRAME.findall(line))
    return rows, summary


def contracts(path):
    raw = Path(path).read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    if not all(validate_frame(row) == [] for row in rows):
        raise SystemExit("invalid contract JSON")
    ids = [row["frame_id"] for row in rows]
    if ids != sorted(set(ids)) or any(a["ts"] >= b["ts"] for a, b in zip(rows, rows[1:])):
        raise SystemExit("contract order is not strictly increasing")
    return {row["frame_id"]: row for row in rows}, hashlib.sha256(raw).hexdigest()


def paths(rows):
    out = collections.defaultdict(lambda: [0, 0.0])
    for row in rows:
        entry = out[row.get("path", "-")]
        entry[0] += 1
        entry[1] += float(row["detect_ms"])
    return {key: {"frames": n, "detect_ms_total": round(total, 3),
                  "detect_ms_mean": round(total / n, 3) if n else 0.0}
            for key, (n, total) in sorted(out.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-log", type=Path, required=True)
    parser.add_argument("--base-contract", type=Path, required=True)
    parser.add_argument("--fast-log", type=Path, required=True)
    parser.add_argument("--fast-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base_rows, base_summary = frames(args.base_log)
    fast_rows, fast_summary = frames(args.fast_log)
    base, base_sha = contracts(args.base_contract)
    fast, fast_sha = contracts(args.fast_contract)
    common = sorted(set(base) & set(fast))
    both_visible = [f for f in common if base[f]["face"]["visible"] and fast[f]["face"]["visible"]]

    def delta(field, index=None, key=None):
        values = []
        for frame in both_visible:
            left, right = base[frame]["face"][field], fast[frame]["face"][field]
            if key is not None:
                left, right = left[key], right[key]
            elif index is not None:
                left, right = left[index], right[index]
            values.append(abs(left - right))
        return {"median": round(statistics.median(values), 3), "max": round(max(values), 3)}

    status_pairs = collections.Counter((base[f]["status"], fast[f]["status"]) for f in common)
    result = {
        "kind": "native_detection_tiering_ab",
        "hardware_test": False,
        "accuracy_verified": False,
        "board_verified": False,
        "purpose": "host replay of the board composition; board fps must be re-measured",
        "baseline": {
            "contract_frames": len(base),
            "visible_frames": sum(row["face"]["visible"] for row in base.values()),
            "rejected_frames": int(base_summary["rejected"]),
            "detect_ms_total": round(sum(float(r["detect_ms"]) for r in base_rows), 3),
            "contract_sha256": base_sha,
        },
        "tiered": {
            "contract_frames": len(fast),
            "visible_frames": sum(row["face"]["visible"] for row in fast.values()),
            "rejected_frames": int(fast_summary["rejected"]),
            "detect_ms_total": round(sum(float(r["detect_ms"]) for r in fast_rows), 3),
            "contract_sha256": fast_sha,
            "paths": paths(fast_rows),
        },
        "agreement": {
            "common_frames": len(common),
            "visible_in_both": len(both_visible),
            "baseline_only_visible": [f for f in common
                                      if base[f]["face"]["visible"] and not fast[f]["face"]["visible"]],
            "tiered_only_visible": [f for f in common
                                    if fast[f]["face"]["visible"] and not base[f]["face"]["visible"]],
            "byte_identical_rows": sum(base[f] == fast[f] for f in common),
            "bbox_x_delta_px": delta("bbox", 0),
            "bbox_y_delta_px": delta("bbox", 1),
            "bbox_w_delta_px": delta("bbox", 2),
            "pose_yaw_delta_deg": delta("pose", key="yaw"),
            "status_pairs": [list(pair) + [count] for pair, count in
                             sorted(status_pairs.items(), key=lambda kv: -kv[1])],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
