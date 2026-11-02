"""PC end-to-end demo of the board-autonomy path, entirely on the local machine.

    68 points (peppa_pipeline JSONL) + video
        -> same-frame forehead RGB sums and frozen gray/motion statistics
        -> native C measure (behavior/quality/decision)
        -> native C POS/FIR/spectrum (1 Hz estimate, gated)
        -> native C contract JSON line, validated by backend/contract.py

What is real here: the C code paths, the contract schema, the frozen gray/motion
formulas, the POS estimator, and the synchronization of every statistic to one
frame. What is a stand-in: **pose** comes from MediaPipe until the 68-point PnP is
migrated (there is no 3D reference for those points yet), and quality/pose
soft-pause recovery in vital is not wired. Output stays in `metrics/logs/`; no
face image is written or uploaded.

Usage (repository root):

    python board/autonomy/pc_autonomy_demo.py \
        --points metrics/logs/peppa_68_blink_full.jsonl \
        --source data/raw/blink.mp4 --frames 656 \
        --output metrics/logs/pc_contract_blink.jsonl \
        --summary metrics/logs/pc_contract_blink_summary.json
"""
from __future__ import annotations

import argparse
import ctypes as ct
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import vg_native as vn  # noqa: E402
from backend.config import load_config  # noqa: E402
from backend.contract import validate_frame  # noqa: E402
from backend.quality import frame_diff_motion, to_gray  # noqa: E402
from backend.vital import load_fir_coeffs  # noqa: E402


def load_points(path: Path) -> dict[int, dict]:
    records = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            records[int(row["frame_id"])] = row
    return records


def roi_sums(frame_bgr, roi):
    """RGB sums over the forehead ROI; channel order is R,G,B for the contract."""
    x0, y0, x1, y1 = roi
    if x1 <= x0 or y1 <= y0:
        return None, 0
    patch = frame_bgr[y0:y1, x0:x1]
    b, g, r = (int(patch[:, :, i].sum(dtype=np.int64)) for i in range(3))
    return (r, g, b), int((x1 - x0) * (y1 - y0))


def make_pose_provider(kind: str):
    if kind == "zero":
        return lambda frame, index: (0.0, 0.0, 0.0), None
    if kind == "pnp":
        # Pose comes from the same 68 points as the metrics: no extra model, no
        # second detector. A failed solve falls back to zeros and is counted.
        def pose(frame, index, record=None):
            data = (record or {}).get("pose")
            return ((data["yaw"], data["pitch"], data["roll"]) if data else (0.0, 0.0, 0.0))
        return pose, None
    from backend.face_landmark import MediaPipeLandmarker

    landmarker = MediaPipeLandmarker(640, 480, refine_landmarks=False)

    def pose(frame, index):
        observation = landmarker.detect(frame, index)
        return (observation.pose["yaw"], observation.pose["pitch"], observation.pose["roll"])

    return pose, landmarker


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--frames", type=int, default=656)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--pose", choices=("pnp", "mediapipe", "zero"), default="pnp",
                        help="pnp = 68-point solvePnP (default); mediapipe = old stand-in")
    parser.add_argument("--vital-every", type=int, default=30, help="estimate cadence in frames")
    args = parser.parse_args(argv)

    import cv2

    cfg = load_config()
    points = load_points(args.points)
    capture = cv2.VideoCapture(args.source)
    if not capture.isOpened():
        raise SystemExit(f"cannot open video: {args.source}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or cfg["fps_nominal"])
    if not fps > 0:
        fps = float(cfg["fps_nominal"])
    pose, landmarker = make_pose_provider(args.pose)

    core = vn.NativeCore()
    config = vn.measure_config(cfg)
    engine = core.lib.vg_measure_create(ct.byref(config))
    if not engine:
        raise SystemExit("vg_measure_create failed")
    coeffs, shift, _fs = load_fir_coeffs()
    vital_cfg, coeffs_array = vn.vital_config(cfg, coeffs, shift)
    vital = core.lib.vg_vital_create(ct.byref(vital_cfg))
    if not vital:
        raise SystemExit("vg_vital_create failed")
    gate = core.lib.vg_decision_create(ct.byref(config.decision))
    if not gate:
        raise SystemExit("vg_decision_create failed")

    stats = Counter()
    hours = []
    errors = []
    previous_gray = None
    raw_vital = vn.Vital()        # last accepted estimate before per-frame gating
    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    handler = out.open("w", encoding="utf-8")
    try:
        for index in range(args.frames):
            ok, frame_bgr = capture.read()
            if not ok:
                break
            record = points.get(index, {})
            face_present = bool(record.get("face")) and bool(record.get("geometry_valid"))
            gray = to_gray(frame_bgr)
            motion = frame_diff_motion(previous_gray, gray, int(cfg["motion_thresh_gray"])) \
                if previous_gray is not None and gray is not None else \
                {"motion_ratio_q16": 0, "motion_pixels": 0, "diff_total": 0}
            previous_gray = gray

            input_frame = vn.MeasureInput()
            input_frame.frame_id = index
            input_frame.ts = index / fps
            input_frame.motion_q16 = int(motion["motion_ratio_q16"])
            input_frame.motion_valid = 1 if index > 0 else 0
            input_frame.mean_luma = float(gray.mean()) / 255.0 if gray is not None else 0.0

            sums = None
            if face_present:
                pts = record["points"]
                left = {f"p{k + 1}": tuple(pts[36 + k]) for k in range(6)}
                right = {f"p{k + 1}": tuple(pts[42 + k]) for k in range(6)}
                mouth = {"left": tuple(pts[48]), "right": tuple(pts[54]),
                         "top": tuple(pts[51]), "bottom": tuple(pts[57])}
                input_frame.observation = vn.observation(left, right, mouth, 1.0)
                input_frame.face.visible = 1.0
                yaw, pitch, roll = pose(frame_bgr, index, record)
                input_frame.face.yaw, input_frame.face.pitch = yaw, pitch
                if record.get("forehead"):
                    sums = roi_sums(frame_bgr, record["forehead"])
                box = record["box"]
            else:
                input_frame.observation = vn.observation(None, None, None, 0.0)
                input_frame.face.visible = 0.0
                yaw = pitch = roll = 0.0
                box = [0, 0, 0, 0]

            result = vn.MeasureResult()
            rc = core.lib.vg_measure_update(engine, ct.byref(input_frame), ct.byref(result))
            if rc != 0:
                stats["measure_rejected"] += 1
                continue
            stats["frames"] += 1

            if sums and sums[1] > 0:
                rgb, count = sums
                buffer = (ct.c_uint32 * 3)(*rgb)
                core.lib.vg_vital_push_rgb(vital, buffer, count, index, index / fps)
            else:
                # Lost face/ROI: clear the window instead of estimating across the gap.
                core.lib.vg_vital_reset(vital)
                raw_vital = vn.Vital()

            if index % args.vital_every == 0:
                estimate = vn.VitalResult()
                core.lib.vg_vital_estimate(vital, ct.byref(estimate))
                if estimate.present:
                    stats["vital_accepted"] += 1
                    raw_vital = vn.Vital()
                    raw_vital.value[0], raw_vital.value[1] = estimate.hr_bpm, estimate.hr_conf
                    raw_vital.present[0] = raw_vital.present[1] = 1
                else:
                    stats["vital_not_ready"] += 1

            # The C gate is stateless: re-apply it every frame so a quality/pose
            # drop hides the held value instead of publishing a stale one.
            gated = vn.Vital()
            if raw_vital.present[0]:
                core.lib.vg_decision_gate_vitals(
                    gate, result.quality.overall, ct.byref(raw_vital),
                    ct.byref(input_frame.face), result.decision.status, ct.byref(gated))
                if not gated.present[0]:
                    stats["vital_gated_out"] += 1
                elif index % args.vital_every == 0:
                    hours.append(gated.value[0])

            face = vn.ContractFace()
            face.bbox[:] = [int(v) for v in box]
            face.yaw, face.pitch, face.roll = yaw, pitch, roll
            face.visible = float(input_frame.face.visible)
            line = core.contract_line(result, face, gated if gated.present[0] else None)
            obj = json.loads(line)
            problems = validate_frame(obj)
            if problems:
                errors.append({"frame_id": index, "errors": problems})
                stats["contract_invalid"] += 1
                continue
            stats[obj["status"]] += 1
            handler.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    finally:
        handler.close()
        capture.release()
        if landmarker is not None:
            landmarker.close() if hasattr(landmarker, "close") else None
        core.lib.vg_measure_destroy(engine)
        core.lib.vg_vital_destroy(vital)
        core.lib.vg_decision_destroy(gate)

    summary = {
        "kind": "pc_autonomy_demo",
        "source": args.source,
        "points": str(args.points),
        "pose": args.pose,
        "vital_every": args.vital_every,
        "fps": fps,
        "stats": dict(stats),
        "contract_errors": errors[:5],
        "hr_accepted_samples": len(hours),
        "hr_min": round(min(hours), 2) if hours else None,
        "hr_max": round(max(hours), 2) if hours else None,
        "board_run": False,
        "pose_migrated": args.pose == "pnp",
        "accuracy_verified": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
