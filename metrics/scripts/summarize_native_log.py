"""Summarize native vision logs (board camera runs and host replays).

Reads AUTONOMY_SAMPLE lines (board, `autonomy_camera.c`) and/or VISION_FRAME
lines (replay, `vision_replay.cpp`) and prints a JSON summary of per-search
costs. Timings come from the binaries themselves; this script only aggregates.

python metrics/scripts/summarize_native_log.py metrics/evidence/x.log --output out.json
"""
from __future__ import annotations
import argparse
import collections
import json
from pathlib import Path
import re
import statistics

FIELD = re.compile(r"(\w+)=([^\s]+)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = []
    for line in args.log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("AUTONOMY_SAMPLE") or line.startswith("VISION_FRAME"):
            fields = dict(FIELD.findall(line))
            if fields.get("detect_ms") is not None:
                rows.append(fields)
    ok = [r for r in rows if r.get("rc") == "0"]
    by_path = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for row in rows:
        entry = by_path[row.get("path", "-")]
        entry[0] += 1
        entry[1] += float(row["detect_ms"])
        entry[2] += float(row.get("landmark_ms", 0.0))
    detect = [float(r["detect_ms"]) for r in rows]
    total = [float(r.get("total_ms", 0.0)) for r in rows]
    summary = {
        "source": str(args.log),
        "frames": len(rows),
        "rc0_frames": len(ok),
        "detect_ms": {"sum": round(sum(detect), 3),
                      "mean": round(statistics.mean(detect), 3),
                      "median": round(statistics.median(detect), 3),
                      "max": round(max(detect), 3)},
        "total_ms": {"sum": round(sum(total), 3),
                     "mean": round(statistics.mean(total), 3)},
        "by_path": {path: {"frames": n, "detect_ms_sum": round(d, 3),
                            "detect_ms_mean": round(d / n, 3),
                            "landmark_ms_sum": round(l, 3)}
                     for path, (n, d, l) in sorted(by_path.items())},
    }
    text = json.dumps(summary, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
