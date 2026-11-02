"""PC reference implementation of the board landmark stage.

WHY THIS FILE EXISTS
--------------------
`docs/54` P1 asks for detection/tracking/crop -> 68 landmarks before any of it is
written in C. This module is that reference: it mirrors the *author's* ONNX
conventions (1.4x crop, BGR, 160x160 bilinear, `(pixel-127)/127`, NCHW float32,
first 136 outputs = 68 normalised XY) so the numbers match
`.tmp/autonomy/candidates/peppa/onnx_detector.py` rather than a re-invented
preprocessing.

What is real: the detector (MediaPipe face detection), the crop arithmetic, the
ONNX execution, the unprojection back to source pixels, and the timing on this
PC. What is emulated: the board has no such pipeline yet, so nothing here is a
Mizar-Z7020 latency, memory, or 30 fps claim.

Tracking is deliberately **not** claimed: to keep the board port small, the
"tracker" is a re-detect cadence plus a bounded hold of the last box. The record
marks `source="hold"` so a later optical-flow tracker can be compared honestly.

Usage (repository root):

    python board/autonomy/peppa_pipeline.py --source data/raw/blink.mp4 \
        --frames 300 --output metrics/logs/peppa_68_blink_300.jsonl \
        --summary metrics/logs/peppa_68_blink_300_summary.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / ".tmp/autonomy/candidates/peppa/slim_160_latest.onnx"
INPUT_SIZE = 160
CROP_SCALE = 1.4          # author: (1 + 2*0.2); do not "improve" without re-benchmarking
NORMALISE = 127.0
OUTPUT_ELEMENTS = 143
POINT_COUNT = 68

try:  # keep `import peppa_pipeline` working from tests and from the CLI
    from .peppa_geometry import (build_observation, eye_validity, forehead_roi,
                                 layout_checks, pose_proxy, solve_pose_pnp)
except ImportError:  # pragma: no cover - script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from peppa_geometry import (build_observation, eye_validity, forehead_roi,
                                layout_checks, pose_proxy, solve_pose_pnp)


@dataclass(frozen=True)
class Box:
    """Axis-aligned face box in source pixels, `[x0, y0, x1, y1]` (rounded ints)."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def as_list(self) -> list[int]:
        return [self.x0, self.y0, self.x1, self.y1]


def expanded_crop(box: Box, width: int, height: int, scale: float = CROP_SCALE):
    """Author's `crop_image`: centre-anchored `scale`x box, clipped to the frame.

    The clipping is what makes the unprojection interesting: a face at the frame
    edge gets a crop smaller than `scale`x, so the model's normalised coordinate
    must be scaled by the *clipped* crop size, not by `scale`.
    """
    bbox_x0, bbox_y0, bbox_x1, bbox_y1 = box.x0, box.y0, box.x1, box.y1
    bbox_width = bbox_x1 - bbox_x0
    bbox_height = bbox_y1 - bbox_y0
    face_width = scale * bbox_width
    face_height = scale * bbox_height
    center_x = (bbox_x0 + bbox_x1) // 2
    center_y = (bbox_y0 + bbox_y1) // 2
    x0 = max(0, int(center_x - face_width // 2))
    y0 = max(0, int(center_y - face_height // 2))
    x1 = min(width, int(center_x + face_width // 2))
    y1 = min(height, int(center_y + face_height // 2))
    return x0, y0, x1, y1


def preprocess(bgr, crop):
    """Crop -> 160x160 bilinear -> `(pixel-127)/127` -> NCHW float32 batch.

    Channel order is the author's BGR call convention. That is a *different*
    boundary from the PL's R-G-B pixel contract; converting one into the other
    here would silently change what the model sees.
    """
    import cv2
    import numpy as np

    x0, y0, x1, y1 = crop
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise ValueError("crop smaller than 8 px")
    patch = bgr[y0:y1, x0:x1]
    resized = cv2.resize(patch, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    normalised = (resized.astype(np.float32) - NORMALISE) / NORMALISE
    chw = np.transpose(normalised, (2, 0, 1))
    return np.ascontiguousarray(chw[None, ...], dtype=np.float32)


def unproject(points, crop):
    """Normalised crop coordinates -> source pixels (author's `detail` arithmetic)."""
    x0, y0, x1, y1 = crop
    crop_w, crop_h = x1 - x0, y1 - y0
    return [(float(px) * crop_w + x0, float(py) * crop_h + y0) for px, py in points]


class MediaPipeFaceDetector:
    """The same short-range 128x128 detector family the board measured.

    `model_selection=0` selects the short-range model (the 128 input in
    `docs/54` §3.2), not the full-range 192 input. It is a PC dependency only:
    the board still has to run its own TFLite detector.
    """

    def __init__(self, model_selection: int = 0, min_confidence: float = 0.5):
        import mediapipe as mp

        self._mp = mp
        self._detector = mp.solutions.face_detection.FaceDetection(
            model_selection=model_selection,
            min_detection_confidence=min_confidence,
        )

    def detect(self, bgr):
        import cv2

        height, width = bgr.shape[:2]
        result = self._detector.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        if not result.detections:
            return None
        # Deterministic pick: highest score, ties broken by left-most box.
        choice = min(result.detections,
                     key=lambda d: (-float(d.score[0]),
                                    d.location_data.relative_bounding_box.xmin))
        relative = choice.location_data.relative_bounding_box
        x0 = int(round(relative.xmin * width))
        y0 = int(round(relative.ymin * height))
        x1 = int(round((relative.xmin + relative.width) * width))
        y1 = int(round((relative.ymin + relative.height) * height))
        x0, x1 = max(0, min(x0, width - 1)), max(1, min(x1, width))
        y0, y1 = max(0, min(y0, height - 1)), max(1, min(y1, height))
        if x1 <= x0 or y1 <= y0:
            return None
        return Box(x0, y0, x1, y1), float(choice.score[0])

    def close(self):
        self._detector.close()


class Peppa68:
    """OpenCV-DNN execution of the frozen Peppa ONNX (no extra runtime)."""

    def __init__(self, model_path: Path = DEFAULT_MODEL):
        import cv2

        if not Path(model_path).exists():
            raise FileNotFoundError(f"Peppa model not found: {model_path}")
        self._net = cv2.dnn.readNetFromONNX(str(model_path))

    def forward(self, blob):
        self._net.setInput(blob)
        raw = self._net.forward()
        values = raw.reshape(-1)
        if values.size != OUTPUT_ELEMENTS:
            raise RuntimeError(f"expected {OUTPUT_ELEMENTS} outputs, got {values.size}")
        if not all(math.isfinite(float(v)) for v in values):
            raise RuntimeError("model produced a non-finite output")
        pairs = values[: POINT_COUNT * 2].reshape(POINT_COUNT, 2)
        return [(float(a), float(b)) for a, b in pairs]


def _rounded(values, digits=3):
    return [round(v, digits) for v in values]


def process_stream(frames, detector, model, *, fps: float, detect_every: int = 1,
                   max_hold: int = 8, crop_scale: float = CROP_SCALE,
                   with_layout: bool = True, pose_mode: str = "pnp"):
    """Yield one record per frame.

    `frames` is an iterable of BGR ndarrays. The generator keeps the last box for
    at most `max_hold` frames after a detection so a detector-every-N schedule
    degrades predictably (and visibly) instead of silently drifting.
    """
    last = None            # (box, score, frame_index)
    for index, frame in enumerate(frames):
        height, width = frame.shape[:2]
        detect_ms = model_ms = 0.0
        source = "hold"
        box = score = None
        if index % detect_every == 0 or last is None:
            started = time.perf_counter()
            found = detector.detect(frame)
            detect_ms = (time.perf_counter() - started) * 1000.0
            if found is None:
                last = None
            else:
                box, score = found
                last = (box, score, index)
                source = "detect"
        if last is None:
            yield {"frame_id": index, "ts": round(index / fps, 4), "face": False,
                   "reason": "no_face", "detect_ms": round(detect_ms, 3)}
            continue
        if source == "hold":
            held_box, held_score, since = last
            if index - since > max_hold:
                last = None
                yield {"frame_id": index, "ts": round(index / fps, 4), "face": False,
                       "reason": "track_expired", "detect_ms": round(detect_ms, 3)}
                continue
            box, score = held_box, held_score
        crop = expanded_crop(box, width, height, crop_scale)
        try:
            blob = preprocess(frame, crop)
        except ValueError:
            yield {"frame_id": index, "ts": round(index / fps, 4), "face": False,
                   "reason": "crop_too_small", "detect_ms": round(detect_ms, 3)}
            continue
        started = time.perf_counter()
        points = model.forward(blob)
        model_ms = (time.perf_counter() - started) * 1000.0
        pixels = unproject(points, crop)
        observation = build_observation(pixels)
        validity = eye_validity(pixels, box.width)
        record = {
            "frame_id": index,
            "ts": round(index / fps, 4),
            "face": True,
            "source": source,
            "box": box.as_list(),
            "score": round(score, 4),
            "crop": list(crop),
            "detect_ms": round(detect_ms, 3),
            "model_ms": round(model_ms, 3),
            "geometry_valid": validity["frame_ok"],
            "eye_valid": {"left": validity["left"], "right": validity["right"]},
            "ear": round(validity["ear"], 4) if validity["ear"] is not None else None,
            "ear_left": round(observation.ear_left, 4) if validity["left"] else None,
            "ear_right": round(observation.ear_right, 4) if validity["right"] else None,
            "mar": round(observation.mar, 4) if validity["frame_ok"] else None,
            "pose_proxy": pose_proxy(pixels),
            "forehead": list(forehead_roi(pixels, width, height) or ()),
            "points": [[round(x, 3), round(y, 3)] for x, y in pixels],
        }
        if with_layout:
            record["layout"] = layout_checks(pixels)
        if pose_mode == "pnp":
            record["pose"] = solve_pose_pnp(pixels, width, height) if observation.eyes else None
        yield record


def open_video(path: str):
    import cv2

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise SystemExit(f"cannot open video: {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    declared = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def frames():
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            yield frame

    return capture, frames(), float(fps) if math.isfinite(fps) and fps > 0 else 30.0, declared


def summarise(records: list[dict], *, fps: float, source: str, model_path: str) -> dict:
    faces = [r for r in records if r.get("face")]
    detect = [r["detect_ms"] for r in records if r.get("detect_ms", 0) > 0]
    model = [r["model_ms"] for r in faces if r.get("model_ms", 0) > 0]

    def stats(values):
        if not values:
            return {"n": 0}
        ordered = sorted(values)
        return {"n": len(ordered), "median": round(ordered[len(ordered) // 2], 3),
                "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
                "max": round(ordered[-1], 3)}

    reasons: dict[str, int] = {}
    for record in records:
        if not record.get("face"):
            reasons[record.get("reason", "unknown")] = reasons.get(record.get("reason", "unknown"), 0) + 1
    pose_ok = sum(1 for r in faces if r.get("pose"))
    layout_fail = {}
    for record in faces:
        for name, ok in (record.get("layout") or {}).items():
            if not ok:
                layout_fail[name] = layout_fail.get(name, 0) + 1
    valid_faces = [r for r in faces if r.get("geometry_valid")]
    ears = [r["ear"] for r in valid_faces if r.get("ear") is not None]
    return {
        "kind": "peppa68_pc_pipeline",
        "source": source,
        "model": model_path,
        "fps": fps,
        "frames": len(records),
        "face_frames": len(faces),
        "face_rate": round(len(faces) / len(records), 4) if records else 0.0,
        "geometry_valid_frames": len(valid_faces),
        "geometry_invalid_frames": len(faces) - len(valid_faces),
        "partial_eye_frames": sum(1 for r in faces if r.get("geometry_valid")
                                  and not (r["eye_valid"]["left"] and r["eye_valid"]["right"])),
        "ear": stats(ears),
        "reasons": reasons,
        "layout_failures": layout_fail,
        "pose_frames_solved": pose_ok,
        "pose_frames_failed": len(faces) - pose_ok,
        "detect_ms": stats(detect),
        "model_ms": stats(model),
        "board_run": False,
        "tracking": "re-detect cadence + bounded hold (no optical flow)",
        "accuracy_verified": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="video path")
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True, help="per-frame JSONL")
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--detector-model", type=int, default=0, choices=(0, 1))
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--detect-every", type=int, default=1)
    parser.add_argument("--max-hold", type=int, default=8)
    parser.add_argument("--crop-scale", type=float, default=CROP_SCALE)
    parser.add_argument("--pose", choices=("pnp", "none"), default="pnp",
                        help="pnp = 68-point solvePnP (default); none = skip")
    args = parser.parse_args(argv)
    if args.frames < 1 or args.detect_every < 1 or args.max_hold < 0:
        parser.error("frames >= 1, detect-every >= 1, max-hold >= 0")

    capture, frames, fps, declared = open_video(args.source)
    model = Peppa68(args.model)
    detector = MediaPipeFaceDetector(args.detector_model, args.min_confidence)
    try:
        limited = (frame for _, frame in zip(range(args.frames), frames))
        records = list(process_stream(limited, detector, model, fps=fps,
                                      detect_every=args.detect_every, max_hold=args.max_hold,
                                      crop_scale=args.crop_scale, pose_mode=args.pose))
    finally:
        detector.close()
        capture.release()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    summary = summarise(records, fps=fps, source=args.source, model_path=str(args.model))
    summary["declared_frames"] = declared
    summary["detect_every"] = args.detect_every
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
