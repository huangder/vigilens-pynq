"""Local-video comparison of the existing 468/478 MediaPipe graph modes.

Model-output differences, not human accuracy, calibration or ARM performance.
No image/video output, upload, BPM estimate or threshold changes.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.behavior_metrics import BehaviorTracker
from backend.config import load_config
from backend.decision import DecisionEngine
from backend.face_landmark import (MediaPipeLandmarker, MP_LEFT_EYE, MP_RIGHT_EYE, MP_MOUTH,
                                   MP_FOREHEAD, MP_FACE_OVAL, ear, mar)
from backend.quality import QualityScorer


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024*1024), b""): digest.update(chunk)
    return digest.hexdigest()


def distribution(values):
    values = sorted(values)
    if not values: return {"n": 0, "mean": None, "p50": None, "p95": None, "max": None}
    def quantile(q):
        index = (len(values)-1)*q
        low = math.floor(index)
        high = math.ceil(index)
        return values[low] + (values[high]-values[low])*(index-low)
    return {"n": len(values), "mean": sum(values)/len(values), "p50": quantile(.5),
            "p95": quantile(.95), "max": values[-1]}


def roi_iou(a, b):
    if a is None or b is None: return None
    area_a = max(0, a[2]-a[0])*max(0, a[3]-a[1])
    area_b = max(0, b[2]-b[0])*max(0, b[3]-b[1])
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0]))*max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = area_a + area_b - intersection
    return intersection/union if union else None


def summarize(rows, cfg):
    summary = {"frames_compared": len(rows), "both_face_frames": 0, "visibility_disagreements": 0,
               "ear_closed_disagreements": 0, "mar_open_disagreements": 0,
               "decision_status_disagreements": 0, "vitals_gate_disagreements": 0,
               "forehead_presence_disagreements": 0, "modes": {}, "differences": {}}
    numeric = ("ear_left", "ear_right", "mar", "yaw", "pitch", "roll")
    for mode in ("base468", "attention478"):
        records = [r[mode] for r in rows]
        summary["modes"][mode] = {
            "detected_face_frames": sum(r["visible"]>0 for r in records),
            "lost_face_frames": sum(r["visible"]<=0 for r in records),
            "landmark_counts": dict(Counter(str(r["landmark_count"]) for r in records)),
            "required_point_failures": sum(not r["required_points_compatible"] for r in records if r["visible"]>0),
            "forehead_missing_frames": sum(r["forehead"] is None for r in records),
            "status_counts": dict(Counter(r["status"] for r in records)),
            "vitals_gate_allowed_frames": sum(r["vitals_allowed"] for r in records),
            "detect_ms_including_color_and_pose": distribution([r["detect_ms"] for r in records]),
            "last_behavior": records[-1]["behavior"] if records else None,
        }
    diffs = {key: [] for key in numeric}
    ious = []
    point_distances, point_normalized, point_frame_rms = [], [], []
    point_by_group = {"eyes": [], "mouth": []}
    for row in rows:
        a, b = row["base468"], row["attention478"]
        both = a["visible"]>0 and b["visible"]>0
        summary["both_face_frames"] += both
        summary["visibility_disagreements"] += (a["visible"]>0)!=(b["visible"]>0)
        summary["decision_status_disagreements"] += a["status"]!=b["status"]
        summary["vitals_gate_disagreements"] += a["vitals_allowed"]!=b["vitals_allowed"]
        summary["forehead_presence_disagreements"] += (a["forehead"] is None)!=(b["forehead"] is None)
        if both:
            for key in numeric: diffs[key].append(a[key]-b[key])
            summary["ear_closed_disagreements"] += ((a["ear_left"]+a["ear_right"])/2<cfg["ear_close_threshold"]) != ((b["ear_left"]+b["ear_right"])/2<cfg["ear_close_threshold"])
            summary["mar_open_disagreements"] += (a["mar"]>cfg["mar_threshold"]) != (b["mar"]>cfg["mar_threshold"])
            overlap = roi_iou(a["forehead"], b["forehead"])
            if overlap is not None: ious.append(overlap)
            if "eyes" in a and "eyes" in b:
                # Pose scale is MP33 to MP263: left p1 to right p4.
                scale = math.dist(b["eyes"]["left"]["p1"], b["eyes"]["right"]["p4"])
                frame_distances = []
                for group in ("eyes", "mouth"):
                    point_pairs = ((a[group][side][key], b[group][side][key]) for side in ("left", "right") for key in ("p1","p2","p3","p4","p5","p6")) if group=="eyes" else ((a[group][key], b[group][key]) for key in ("left","right","top","bottom"))
                    for pa, pb in point_pairs:
                        distance = math.dist(pa,pb)
                        frame_distances.append(distance)
                        point_distances.append(distance)
                        point_by_group[group].append(distance)
                        if scale>0: point_normalized.append(distance/scale)
                point_frame_rms.append(math.sqrt(sum(v*v for v in frame_distances)/len(frame_distances)))
    summary["differences"] = {key: {"signed_base_minus_attention": distribution(values),
                                      "absolute": distribution([abs(v) for v in values])}
                              for key, values in diffs.items()}
    summary["differences"]["forehead_roi_iou"] = distribution(ious)
    summary["differences"]["corresponding_point_distance_pixels"] = distribution(point_distances)
    summary["differences"]["eye_point_distance_pixels"] = distribution(point_by_group["eyes"])
    summary["differences"]["mouth_point_distance_pixels"] = distribution(point_by_group["mouth"])
    summary["differences"]["point_distance_normalized_by_attention_mp33_mp263"] = distribution(point_normalized)
    summary["differences"]["frame_point_rms_pixels"] = distribution(point_frame_rms)
    for mode in ("base468", "attention478"):
        audits = [r[mode].get("pose_audit") for r in rows if r[mode]["visible"]>0]
        audits = [audit for audit in audits if audit is not None]
        summary["modes"][mode]["pose_audit"] = {
            "frames_recomputed": len(audits), "solver_valid_frames": sum(a["valid"] for a in audits),
            "solver_rejected_frames": sum(not a["valid"] for a in audits),
            "reprojection_error_over_interocular": distribution([a["reprojection_error_over_interocular"] for a in audits if a["valid"]])}
    return summary


class AuditedLandmarker:
    def __init__(self, width, height, refine):
        self.detector = MediaPipeLandmarker(width, height, refine_landmarks=refine)
        self.last_count = 0
        self.last_landmarks = None
        original = self.detector._mesh.process
        def process(image):
            result = original(image)
            self.last_count = len(result.multi_face_landmarks[0].landmark) if result.multi_face_landmarks else 0
            self.last_landmarks = result.multi_face_landmarks[0].landmark if result.multi_face_landmarks else None
            return result
        self.detector._mesh.process = process

    def close(self):
        self.detector._mesh.close()


def pose_audit(landmarks, width, height):
    """Recompute the same backend solver outside the measured detect interval."""
    import cv2
    import numpy as np
    if landmarks is None: return None
    points = np.array([[landmarks[i].x*width, landmarks[i].y*height] for i in MediaPipeLandmarker._POSE_IDX], dtype="double")
    camera = np.array([[float(width),0,width/2],[0,float(width),height/2],[0,0,1]], dtype="double")
    result = MediaPipeLandmarker._solve_head_pose(points,camera)
    if result is None: return {"valid": False, "reprojection_error_over_interocular": None}
    rvec,tvec = result
    projected,_ = cv2.projectPoints(np.array(MediaPipeLandmarker._POSE_MODEL,dtype="double"),rvec,tvec,camera,np.zeros((4,1)))
    residual = float(np.linalg.norm(projected.reshape(-1,2)-points,axis=1).mean())
    scale = float(np.linalg.norm(points[2]-points[3]))
    return {"valid": True, "reprojection_error_pixels": residual,
            "reprojection_error_over_interocular": residual/scale, "positive_camera_z": float(tvec[2][0])}


def compare(source, output, *, start=0, frames=300, stride=1, rotate=0, config=ROOT/"config.yaml"):
    import cv2
    import mediapipe as mp
    import numpy as np
    if not source.is_file(): raise FileNotFoundError(source)
    if frames<=0 or frames>3000 or start<0 or stride<1: raise ValueError("invalid bounded frame selection")
    cfg = load_config(config)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened(): raise RuntimeError("cannot open local video")
    fps = capture.get(cv2.CAP_PROP_FPS)
    video_metadata = {"width": capture.get(cv2.CAP_PROP_FRAME_WIDTH), "height": capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
                      "frame_count": capture.get(cv2.CAP_PROP_FRAME_COUNT),
                      "orientation_metadata_deg": capture.get(cv2.CAP_PROP_ORIENTATION_META),
                      "orientation_auto": capture.get(cv2.CAP_PROP_ORIENTATION_AUTO), "backend": capture.getBackendName()}
    if not math.isfinite(fps) or fps<=0: capture.release(); raise ValueError("video has no usable FPS")
    detectors = {}
    rows = []
    index = 0
    required = sorted(set(MP_LEFT_EYE + MP_RIGHT_EYE + MP_MOUTH + MP_FOREHEAD + MP_FACE_OVAL + MediaPipeLandmarker._POSE_IDX))
    trackers = {key: BehaviorTracker(cfg) for key in ("base468", "attention478")}
    scorers = {key: QualityScorer(cfg) for key in trackers}
    decisions = {key: DecisionEngine(cfg) for key in trackers}
    output.mkdir(parents=True, exist_ok=True)
    try:
        with (output/"geometry.jsonl").open("w", encoding="utf-8") as raw:
            while len(rows)<frames:
                ok, image = capture.read()
                if not ok: break
                current = index
                index += 1
                if current<start or (current-start)%stride: continue
                if rotate: image = cv2.rotate(image, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rotate])
                if not detectors:
                    h, w = image.shape[:2]
                    detectors = {"base468": AuditedLandmarker(w,h,False), "attention478": AuditedLandmarker(w,h,True)}
                row = {"frame_index": current, "ts": current/fps, "shape_hw": list(image.shape[:2])}
                # Alternation reduces systematic first/second scheduling bias.
                order = list(detectors) if len(rows)%2==0 else list(reversed(detectors))
                for mode in order:
                    detector = detectors[mode]
                    t0 = time.perf_counter_ns()
                    obs = detector.detector.detect(image, current)
                    elapsed = (time.perf_counter_ns()-t0)/1e6
                    if obs.source!="mediapipe": raise RuntimeError("refusing stub output")
                    behavior = trackers[mode].update(current, current/fps, obs)
                    quality = scorers[mode].update(current, current/fps, image, obs)
                    face = {"visible": obs.visible, "pose": obs.pose}
                    decision = decisions[mode].decide(frame_id=current, behavior=behavior, quality=quality, face=face)
                    row[mode] = {"visible": obs.visible, "landmark_count": detector.last_count,
                        "required_points_compatible": detector.last_count>max(required),
                        "ear_left": ear(obs.eyes.get("left", {})), "ear_right": ear(obs.eyes.get("right", {})),
                        "mar": mar(obs.mouth), **obs.pose, "bbox": list(obs.bbox), "forehead": obs.forehead,
                        "pose_audit": pose_audit(detector.last_landmarks,image.shape[1],image.shape[0]),
                        "eyes": obs.eyes, "mouth": obs.mouth, "detect_ms": elapsed,
                        "behavior": behavior, "quality": quality, "status": decision["status"],
                        "triggers": decision["triggers"], "vitals_allowed": decisions[mode].vitals_allowed(quality["overall"], face, decision["status"])}
                rows.append(row)
                raw.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")
    finally:
        capture.release()
        for detector in detectors.values(): detector.close()
    if not rows: raise RuntimeError("no selected frames decoded")
    package = Path(mp.__file__).resolve().parent
    models = {name: sha256(package/"modules/face_landmark"/name) for name in ("face_landmark.tflite", "face_landmark_with_attention.tflite")}
    summary = {"scope": "local_model_output_difference_not_accuracy_or_arm_performance", "source_name": source.name,
               "source_sha256": sha256(source), "config_sha256": sha256(config), "fps_metadata": fps,
               "video_metadata": video_metadata, "decoded_shape_hw": rows[0]["shape_hw"],
               "frame_indices": [r["frame_index"] for r in rows], "rotation_clockwise_deg": rotate,
               "requested_frames": frames, "stride": stride, "required_point_indices": required,
               "required_indices_fit_468": max(required)<468, "model_sha256": models,
               "environment": {"python": sys.version, "platform": platform.platform(), "mediapipe": mp.__version__,
                               "opencv": cv2.__version__, "numpy": np.__version__},
               "timing_policy": "PC detect includes BGR->RGB, graph tracking and PnP; all frames include warm-up; alternating mode order; no ARM or concurrent capture inference claim",
               "limitations": ["No manual labels or reference tracker; differences are not accuracy.",
                   "Shared 468 point indices do not establish coordinate equivalence or threshold portability.",
                   "Thresholds unchanged and currently uncalibrated; no final threshold decision.",
                   "Pose solver failure currently returns zero angles; zero is not proof of frontal pose.",
                   "Large yaw differences may reflect PnP branch sensitivity; they do not identify the more accurate model.",
                   "Pose validity/reprojection audit reruns the same backend solver outside detect timing, without external pose labels.",
                   "ROI changes can alter the rPPG input; this run estimates no BPM.",
                   "Video tracking and any subsampling affect future graph state; reproduce exact frame indices.",
                   "PC mode comparison does not replace ARM model runtime/ROI pipeline validation."],
               **summarize(rows,cfg)}
    (output/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="use metrics/logs; raw geometry is not formal evidence")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--rotate", type=int, choices=(0,90,180,270), default=0)
    parser.add_argument("--config", type=Path, default=ROOT/"config.yaml")
    args = parser.parse_args()
    report = compare(args.source, args.output, start=args.start, frames=args.frames, stride=args.stride, rotate=args.rotate, config=args.config)
    print(json.dumps({"output": str(args.output), "frames": report["frames_compared"],
                      "both_face_frames": report["both_face_frames"], "scope": report["scope"]}))
