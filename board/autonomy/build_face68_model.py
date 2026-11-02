"""Build a 68-point 3D reference for PnP from MediaPipe's own face mesh.

WHY THIS FILE EXISTS
--------------------
`docs/55` left PnP out because the 68 Peppa points had no 3D reference. This tool
builds one from local, licensed machinery only:

  1. run MediaPipe FaceMesh (468 landmarks with x, y, z) on the same clip;
  2. keep frames whose estimated |yaw| and |pitch| are small (a frontal pose makes
     the mesh's z meaningful; a profile frame would smear it);
  3. express every landmark in eye-centre coordinates scaled by inter-ocular
     distance, so the model is subject-scale free;
  4. take the per-landmark median over those frames;
  5. map each Peppa point to its nearest 468 landmark in the same normalised 2D
     space, and store the median distance as a per-point residual.

The result is a **subject-specific mean face from this clip**, not an anatomical
atlas. That is enough to recover head orientation (angles are insensitive to mild
shape differences) and must not be presented as a population-average model or as
metric depth.

Usage (repository root):

    python board/autonomy/build_face68_model.py \
        --source data/raw/blink.mp4 \
        --points metrics/logs/peppa_68_blink_full.jsonl \
        --output board/autonomy/face68_3d.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.face_landmark import (MP_LEFT_EYE, MP_RIGHT_EYE,  # noqa: E402
                                  MediaPipeLandmarker)

HEADER = ("X/Y from the 68-point model's own frontal median; Z from the mapped "
          "MediaPipe mesh depth, eye-centre/IOD normalised")
NOTE = ("Subject-specific reference from the calibration clip; x right, y down, z into "
        "the scene (MediaPipe convention: smaller z is closer). Units = inter-ocular "
        "distance. Not an anatomical atlas and not metric depth.")


def load_points(path: Path) -> dict[int, dict]:
    rows = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows[int(row["frame_id"])] = row
    return rows


def peppa_normalised(points) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    left = pts[36:42].mean(axis=0)
    right = pts[42:48].mean(axis=0)
    centre = 0.5 * (left + right)
    iod = float(np.linalg.norm(right - left))
    return (pts - centre) / iod


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-yaw", type=float, default=12.0)
    parser.add_argument("--max-pitch", type=float, default=10.0)
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument('--landmark-family',choices=('peppa','ert'),default='peppa')
    args = parser.parse_args(argv)

    import cv2
    import mediapipe as mp

    points = load_points(args.points)
    mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=1,
                                           refine_landmarks=False,
                                           min_detection_confidence=0.5,
                                           min_tracking_confidence=0.5)
    capture = cv2.VideoCapture(args.source)
    if not capture.isOpened():
        raise SystemExit(f"cannot open {args.source}")
    limit = args.frames or 10**9

    meshes: list[np.ndarray] = []
    peppa2d: list[np.ndarray] = []
    used = 0
    used_frame_ids=[]
    index = 0
    try:
        while used < limit:
            ok, frame = capture.read()
            if not ok:
                break
            height, width = frame.shape[:2]
            record = points.get(index)
            result = mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if (record and record.get("face") and record.get("geometry_valid")
                    and result.multi_face_landmarks):
                lm = result.multi_face_landmarks[0].landmark
                pose = MediaPipeLandmarker._pose_from_landmarks(lm, width, height)
                if abs(pose["yaw"]) < args.max_yaw and abs(pose["pitch"]) < args.max_pitch:
                    # Same definition as the Peppa normalisation: mean of the six
                    # eye-contour points, not the outer corners. Mixing the two
                    # definitions shifts every mapped point by ~15% of an IOD.
                    left = np.mean([[lm[i].x * width, lm[i].y * height] for i in MP_LEFT_EYE], axis=0)
                    right = np.mean([[lm[i].x * width, lm[i].y * height] for i in MP_RIGHT_EYE], axis=0)
                    cx, cy = 0.5 * (left[0] + right[0]), 0.5 * (left[1] + right[1])
                    iod = float(np.hypot(right[0] - left[0], right[1] - left[1]))
                    if iod > 1.0:
                        arr = np.asarray([[(p.x * width - cx) / iod,
                                           (p.y * height - cy) / iod,
                                           (p.z * width) / iod] for p in lm])
                        meshes.append(arr)
                        peppa2d.append(peppa_normalised(record["points"]))
                        used += 1
                        used_frame_ids.append(index)
            index += 1
    finally:
        capture.release()
        mesh.close()
    if len(meshes) < 10:
        raise SystemExit(f"only {len(meshes)} frontal frames; refusing to build a model")

    canonical = np.median(np.stack(meshes), axis=0)          # (468, 3)
    peppa_median = np.median(np.stack(peppa2d), axis=0)     # (68, 2)
    mapping = []
    residuals = []
    for point in peppa_median:
        distances = np.linalg.norm(canonical[:, :2] - point, axis=1)
        nearest = int(np.argmin(distances))
        mapping.append(nearest)
        residuals.append(float(distances[nearest]))

    # MediaPipe's z already follows the camera convention used here: smaller z is
    # closer, so the nose tip is negative relative to the eye corners. Keep the sign
    # (flipping it puts the nose behind the eyes and inverts yaw). X/Y come from the
    # 68-point model itself so the frontal model projects onto its own median layout.
    model = [[round(float(peppa_median[i][0]), 6), round(float(peppa_median[i][1]), 6),
              round(float(canonical[mapping[i]][2]), 6)] for i in range(68)]
    payload = {
        "kind": "face68_3d_reference",
        "landmark_family": args.landmark_family,
        "header": HEADER,
        "note": NOTE,
        "source_clip": str(args.source),
        "points_file": str(args.points),
        "frames_used": len(meshes),
        "frame_ids": used_frame_ids,
        "max_yaw_deg": args.max_yaw,
        "max_pitch_deg": args.max_pitch,
        "units": "inter-ocular distance",
        "axes": "x right, y down, z away from camera",
        "mapping_to_mediapipe": mapping,
        "mapping_residual_iod": [round(v, 6) for v in residuals],
        "points_68": model,
        "accuracy_verified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"frames_used": len(meshes),
                      "mapping": dict(zip(range(68), mapping)),
                      "max_residual": max(residuals),
                      "mean_residual": sum(residuals) / len(residuals)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
