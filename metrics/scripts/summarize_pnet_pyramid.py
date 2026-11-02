"""Aggregate PNet-only pyramid cost per minimum face size.

Input is the output of repeated `vg_pnet-host` runs (one line per run, see
`board/autonomy/pnet_probe.cpp`); the raw file is archived next to the JSON.
Host x86 numbers; they measure the pyramid width ratio, not Cortex-A9 time.

python metrics/scripts/summarize_pnet_pyramid.py metrics/evidence/x_raw.txt --output out.json
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
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = []
    for line in args.raw.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = dict(FIELD.findall(line))
        if "ms" not in fields:
            continue
        rows.append({"frame": int(fields["frame"]), "min_face": float(fields["min_face"]),
                     "ms": float(fields["ms"]), "boxes": int(fields["boxes"])})
    by_min_face = collections.defaultdict(list)
    for row in rows:
        by_min_face[row["min_face"]].append(row["ms"])
    summary = {
        "kind": "pnet_pyramid_width_benchmark",
        "hardware_test": False,
        "board_verified": False,
        "source": str(args.raw),
        "runs": len(rows),
        "min_face": {str(int(min_face)): {
            "runs": len(values),
            "median_ms": round(statistics.median(values), 3),
            "min_ms": round(min(values), 3),
            "max_ms": round(max(values), 3),
        } for min_face, values in sorted(by_min_face.items())},
        "candidates": [{"frame": frame, "min_face": min_face, "boxes": boxes}
                       for frame, min_face, boxes in
                       sorted({(r["frame"], r["min_face"], r["boxes"]) for r in rows})],
    }
    text = json.dumps(summary, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
