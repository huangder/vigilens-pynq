"""Live hybrid demo: board camera/PL -> PC landmarks -> board service -> contract.

Chain:
  board camera_pl (VIGI_STREAM_BIND) --VLP2/jpeg--> this script
      -> MediaPipe face detection + Peppa 68 + forehead RGB/quality stats
      -> board autonomy_service (TCP 9100, --pose pnp)
      -> contract JSON lines (validated here)

Why hybrid: 30 fps 720p decode+PL already saturates the two A9 cores, so the
landmark model runs on the PC for now. The board still owns PL, pose (PnP),
behaviour/quality/decision, vital gating and contract serialization.

Usage (PC, board camera already listening):
    python board/autonomy/live_hybrid_demo.py --board 169.254.73.29:9007 \
        --service 169.254.73.29:9100 --frames 900 --output metrics/logs/live_contract.jsonl
"""
from __future__ import annotations

import argparse
import ctypes
import json
import socket
import urllib.request
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2  # noqa: E402
import mediapipe as mp  # noqa: E402

from backend.board_source import BoardStreamEnd, read_packet  # noqa: E402
from backend.config import load_config  # noqa: E402
from backend.contract import validate_frame  # noqa: E402
from backend.quality import frame_diff_motion, to_gray  # noqa: E402

MODEL = ROOT / ".tmp/autonomy/candidates/peppa/slim_160_latest.onnx"
CROP_SCALE = 1.4
INPUT = 160


class Detector:
    def __init__(self):
        self.detector = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=0.5)

    def box(self, bgr):
        h, w = bgr.shape[:2]
        result = self.detector.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        if not result.detections:
            return None
        best = max(result.detections, key=lambda d: float(d.score[0]))
        box = best.location_data.relative_bounding_box
        x0 = max(0, int(box.xmin * w))
        y0 = max(0, int(box.ymin * h))
        x1 = min(w, int((box.xmin + box.width) * w))
        y1 = min(h, int((box.ymin + box.height) * h))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        return x0, y0, x1, y1


class Peppa:
    def __init__(self):
        self.net = cv2.dnn.readNetFromONNX(str(MODEL))

    def run(self, bgr, box):
        x0, y0, x1, y1 = box
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        fw = int((x1 - x0) * CROP_SCALE)
        fh = int((y1 - y0) * CROP_SCALE)
        cx0, cy0 = max(0, cx - fw // 2), max(0, cy - fh // 2)
        cx1 = min(bgr.shape[1], cx + fw // 2)
        cy1 = min(bgr.shape[0], cy + fh // 2)
        if cx1 - cx0 < 8 or cy1 - cy0 < 8:
            return None, None
        patch = bgr[cy0:cy1, cx0:cx1]
        resized = cv2.resize(patch, (INPUT, INPUT), interpolation=cv2.INTER_LINEAR)
        blob = ((resized.astype(np.float32) - 127.0) / 127.0).transpose(2, 0, 1)[None]
        self.net.setInput(np.ascontiguousarray(blob))
        raw = self.net.forward().reshape(-1)
        if raw.size != 143 or not np.isfinite(raw).all():
            return None, None
        pts = raw[:136].reshape(68, 2)
        pts[:, 0] = pts[:, 0] * (cx1 - cx0) + cx0
        pts[:, 1] = pts[:, 1] * (cy1 - cy0) + cy0
        return pts, (cx0, cy0, cx1, cy1)


def forehead_roi(pts, width, height):
    brow_y = min(pts[i][1] for i in range(17, 27))
    eye_y = max(pts[i][1] for i in range(36, 48))
    gap = eye_y - brow_y
    if gap <= 0:
        return None
    xs = [pts[i][0] for i in range(17, 27)]
    x0, x1 = min(xs), max(xs)
    dx = (x1 - x0) * 0.08
    x0, x1 = x0 + dx, x1 - dx
    y0 = brow_y - 0.65 * gap
    y1 = brow_y - 0.10 * gap
    x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
    x1, y1 = min(width, int(round(x1))), min(height, int(round(y1)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return x0, y0, x1, y1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, help="host:port of the camera stream")
    parser.add_argument("--service", required=True, help="host:port of the autonomy service")
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api", default=None, help="optional POST target, e.g. http://127.0.0.1:8000/api/ingest")
    parser.add_argument("--detect-every", type=int, default=1,
                        help="run the face detector every N frames and hold the box (>=1)")
    args = parser.parse_args(argv)
    cfg = load_config()

    board_host, board_port = args.board.rsplit(":", 1)
    stream = socket.create_connection((board_host, int(board_port)), timeout=20)
    stream.settimeout(20)
    svc_host, svc_port = args.service.rsplit(":", 1)
    service = socket.create_connection((svc_host, int(svc_port)), timeout=10)
    service.settimeout(10)
    reader = service.makefile("r", encoding="utf-8")
    writer = service.makefile("w", encoding="utf-8")
    detector, peppa = Detector(), Peppa()

    stats = Counter()
    statuses = Counter()
    errors = []
    previous_gray = None
    last_box = None
    started = time.monotonic()
    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    handle = out.open("w", encoding="utf-8")
    try:
        for index in range(args.frames):
            packet = read_packet(stream, int(cfg["motion_thresh_gray"]))
            if isinstance(packet, BoardStreamEnd) or packet is None:
                stats["stream_end"] += 1
                break
            meta, rgb = packet
            bgr = np.frombuffer(rgb, dtype=np.uint8).reshape(720, 1280, 3)[:, :, ::-1]
            gray = to_gray(bgr)
            motion = frame_diff_motion(previous_gray, gray, int(cfg["motion_thresh_gray"])) \
                if previous_gray is not None else {"motion_ratio_q16": 0}
            previous_gray = gray
            box = None
            if index % max(1, args.detect_every) == 0:
                box = detector.box(bgr)
            if box is None and last_box is not None:
                box = last_box
            pts = None
            if box is not None:
                pts, _crop = peppa.run(bgr, box)
                if pts is not None:
                    last_box = box
                    stats["face"] += 1
            ts = float(meta.get("source_ts_us", 0)) / 1e6
            fields = [str(index), f"{ts:.6f}", "1" if pts is not None else "0"]
            if pts is not None:
                x0, y0, x1, y1 = box
                fields += [str(x0), str(y0), str(x1 - x0), str(y1 - y0), "0", "0", "0"]
                roi = forehead_roi(pts, 1280, 720)
                if roi:
                    rx0, ry0, rx1, ry1 = roi
                    patch = bgr[ry0:ry1, rx0:rx1]
                    b, g, r = (int(patch[:, :, i].sum(dtype=np.int64)) for i in range(3))
                    sums = (r, g, b)
                    count = int((rx1 - rx0) * (ry1 - ry0))
                else:
                    sums, count = (0, 0, 0), 0
                mean_luma = float(gray.mean()) / 255.0
                fields += [f"{mean_luma:.6f}", str(int(motion["motion_ratio_q16"])),
                           "1" if index > 0 else "0", str(count),
                           str(sums[0]), str(sums[1]), str(sums[2])]
                for x, y in pts:
                    fields += [f"{x:.3f}", f"{y:.3f}"]
            else:
                mean_luma = float(gray.mean()) / 255.0
                fields += ["0", "0", "0", "0", "0", "0", "0",
                           f"{mean_luma:.6f}", str(int(motion["motion_ratio_q16"])),
                           "1" if index > 0 else "0", "0", "0", "0", "0"]
                fields += ["0", "0"] * 68
            writer.write(" ".join(fields) + "\n")
            writer.flush()
            reply = reader.readline().strip()
            if not reply or reply.startswith("ERR"):
                stats["service_error"] += 1
                if len(errors) < 5:
                    errors.append({"frame": index, "reply": reply})
                continue
            obj = json.loads(reply)
            problems = validate_frame(obj)
            if problems:
                stats["invalid"] += 1
                if len(errors) < 5:
                    errors.append({"frame": index, "errors": problems})
                continue
            statuses[obj["status"]] += 1
            stats["contract"] += 1
            if args.api:
                request = urllib.request.Request(
                    args.api, data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=3) as response:
                        stats["api_ok" if response.status == 200 else "api_bad"] += 1
                except Exception:  # noqa: BLE001
                    stats["api_error"] += 1
            handle.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
            if index % 60 == 0:
                print(f"frame={index} status={obj['status']} "
                      f"elapsed={time.monotonic() - started:.1f}s", flush=True)
    finally:
        handle.close()
        reader.close()
        writer.close()
        service.close()
        stream.close()
    elapsed = time.monotonic() - started
    summary = {"kind": "live_hybrid_contract", "frames_requested": args.frames,
               "contract_frames": stats["contract"], "face_frames": stats["face"],
               "status_counts": dict(statuses),
               "service_errors": stats["service_error"], "invalid": stats["invalid"],
               "elapsed_s": round(elapsed, 3),
               "throughput_fps": round(stats["contract"] / elapsed, 2) if elapsed else 0.0,
               "api": args.api, "api_ok": stats["api_ok"], "api_error": stats["api_error"],
               "errors": errors, "board_camera": args.board, "board_service": args.service,
               "landmarks_on_pc": True, "pose_on_board": True, "accuracy_verified": False}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
