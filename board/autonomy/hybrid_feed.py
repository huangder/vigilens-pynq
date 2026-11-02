"""Feed per-frame measurements to the native autonomy service, one line per frame.

Two transports share one protocol:
  --stdio BINARY   spawn a local service (host validation, no sockets)
  --tcp HOST:PORT  send to the board service (board run)

The producer owns camera/landmarks/pose; the service owns measure/vital/contract.
Every answer is validated with backend/contract.py before it is written.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402
from backend.contract import validate_frame  # noqa: E402
from backend.quality import frame_diff_motion, to_gray  # noqa: E402


def load_points(path: Path):
    return {int(json.loads(line)["frame_id"]): json.loads(line)
            for line in path.open(encoding="utf-8")}


def roi_sums(frame_bgr, roi):
    x0, y0, x1, y1 = roi
    if x1 <= x0 or y1 <= y0:
        return None, 0
    patch = frame_bgr[y0:y1, x0:x1]
    b, g, r = (int(patch[:, :, i].sum(dtype=np.int64)) for i in range(3))
    return (r, g, b), int((x1 - x0) * (y1 - y0))


def line_for(index, frame, record, previous_gray, cfg):
    gray = to_gray(frame)
    motion = frame_diff_motion(previous_gray, gray, int(cfg["motion_thresh_gray"])) \
        if previous_gray is not None and gray is not None else \
        {"motion_ratio_q16": 0}
    face = bool(record.get("face")) and bool(record.get("geometry_valid"))
    fields = [str(index), f"{index / 30.0:.6f}", "1" if face else "0"]
    if face:
        box = record["box"]
        pose = record.get("pose") or {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}
        sums, count = roi_sums(frame, record["forehead"]) if record.get("forehead") else (None, 0)
        fields += [f"{box[0]}", f"{box[1]}", f"{box[2]-box[0]}", f"{box[3]-box[1]}",
                   f"{pose['yaw']:.4f}", f"{pose['pitch']:.4f}", f"{pose['roll']:.4f}"]
        mean_luma = float(gray.mean()) / 255.0 if gray is not None else 0.0
        fields += [f"{mean_luma:.6f}", str(int(motion["motion_ratio_q16"])),
                   "1" if index > 0 else "0", str(count)]
        fields += [str(v) for v in (sums or (0, 0, 0))]
        pts = record["points"]
        coords = []
        for x, y in pts:
            coords += [f"{x:.3f}", f"{y:.3f}"]
        fields += coords
    else:
        mean_luma = float(gray.mean()) / 255.0 if gray is not None else 0.0
        fields += ["0", "0", "0", "0", "0", "0", "0",
                   f"{mean_luma:.6f}", str(int(motion["motion_ratio_q16"])),
                   "1" if index > 0 else "0", "0", "0", "0", "0"]
        fields += ["0", "0"] * 68
    return " ".join(fields) + "\n", gray


class StdioTransport:
    def __init__(self, binary: Path, extra: list[str] | None = None):
        self.process = subprocess.Popen([str(binary)] + (extra or []), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, encoding="utf-8",
                                        errors="replace", bufsize=1)

    def exchange(self, line: str) -> str:
        self.process.stdin.write(line)
        self.process.stdin.flush()
        return self.process.stdout.readline()

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


class TcpTransport:
    def __init__(self, host: str, port: int):
        self.socket = socket.create_connection((host, port), timeout=10)
        self.reader = self.socket.makefile("r", encoding="utf-8")
        self.writer = self.socket.makefile("w", encoding="utf-8")

    def exchange(self, line: str) -> str:
        self.writer.write(line)
        self.writer.flush()
        return self.reader.readline()

    def close(self):
        self.writer.close()
        self.reader.close()
        self.socket.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--stdio", type=Path, default=None, help="local service binary")
    parser.add_argument("--stdio-args", default="", help="extra args for --stdio, e.g. '--pose pnp'")
    parser.add_argument("--tcp", default=None, help="host:port of the board service")
    args = parser.parse_args(argv)
    if bool(args.stdio) == bool(args.tcp):
        parser.error("choose exactly one of --stdio / --tcp")

    import cv2

    cfg = load_config()
    points = load_points(args.points)
    if args.stdio:
        transport = StdioTransport(args.stdio, args.stdio_args.split())
    else:
        host, port = args.tcp.rsplit(":", 1)
        transport = TcpTransport(host, int(port))
    capture = cv2.VideoCapture(args.source)
    if not capture.isOpened():
        raise SystemExit(f"cannot open {args.source}")
    stats = Counter()
    statuses = Counter()
    errors = []
    previous_gray = None
    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    handle = out.open("w", encoding="utf-8")
    try:
        for index in range(args.frames):
            ok, frame = capture.read()
            if not ok:
                break
            line, gray = line_for(index, frame, points.get(index, {}), previous_gray, cfg)
            previous_gray = gray
            reply = transport.exchange(line).strip()
            if not reply or reply.startswith("ERR"):
                stats["service_error"] += 1
                if len(errors) < 5:
                    errors.append({"frame": index, "reply": reply})
                continue
            try:
                obj = json.loads(reply)
            except json.JSONDecodeError:
                stats["bad_json"] += 1
                continue
            problems = validate_frame(obj)
            if problems:
                stats["invalid"] += 1
                if len(errors) < 5:
                    errors.append({"frame": index, "errors": problems})
                continue
            statuses[obj["status"]] += 1
            stats["contract_frames"] += 1
            handle.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    finally:
        handle.close()
        capture.release()
        transport.close()

    summary = {"kind": "hybrid_service_feed", "frames_requested": args.frames,
               "contract_frames": stats["contract_frames"], "status_counts": dict(statuses),
               "errors": errors, "stats": dict(stats), "board_service": bool(args.tcp),
               "pose_source": "pc_pnp", "accuracy_verified": False}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
