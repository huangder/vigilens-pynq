"""Run the board's native vision composition on local video, without hardware claims.

Python only decodes test inputs and computes scalar PL-reference metadata. The
executable performs detection, landmarks, PnP, measurement, gating and contract
serialization. Injected black frames test face-loss and recovery; no accuracy
is inferred from agreement with a detector or from these developer samples.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.contract import validate_frame
from backend.quality import to_gray, frame_diff_motion


def wsl_path(path):
    path = Path(path).resolve()
    return f"/mnt/{path.drive[0].lower()}/{path.as_posix()[3:]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/raw/blink.mp4")
    parser.add_argument("--start", type=int, default=60)
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--work", type=Path, default=ROOT / ".tmp/autonomy/native_validation")
    parser.add_argument("--executable", type=Path, default=ROOT / ".tmp/autonomy/native_vision/vg_vision_replay_host")
    args = parser.parse_args()
    cfg = load_config()
    cap = cv2.VideoCapture(str(args.source))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened() or not fps > 0 or args.frames < 2:
        raise ValueError("video/fps/frame count is invalid")
    cap.set(cv2.CAP_PROP_POS_FRAMES, args.start)
    args.work.mkdir(parents=True, exist_ok=True)
    fixture = args.work / "frames.rgbstream"
    records = []
    previous = None
    with fixture.open("wb") as stream:
        for index in range(args.frames + 2):
            if index in (args.frames // 2, args.frames // 2 + 1):
                if previous is None:
                    raise ValueError("black-frame injection needs a prior real frame")
                bgr = np.zeros_like(last_bgr)
                kind = "black"
            else:
                ok, bgr = cap.read()
                if not ok:
                    raise ValueError("video ended before test fixture was complete")
                last_bgr = bgr
                kind = "real"
            gray = to_gray(bgr)
            motion = frame_diff_motion(previous, gray, int(cfg["motion_thresh_gray"])) if previous is not None else {"motion_ratio_q16": 0}
            h, w = bgr.shape[:2]
            header = f"{index} {index/fps:.12f} {w} {h} {gray.mean()/255:.12f} {motion['motion_ratio_q16']} {int(previous is not None)}\n"
            stream.write(header.encode("ascii"))
            stream.write(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).tobytes())
            records.append({"frame_id": index, "kind": kind})
            previous = gray
    cap.release()
    output = args.work / "contract.jsonl"
    diagnostics = args.work / "native.log"
    command = ["wsl.exe", "-d", "Ubuntu", "--", wsl_path(args.executable),
               wsl_path(ROOT / ".tmp/autonomy/mtcnn"),
               wsl_path(ROOT / ".tmp/autonomy/deploy/peppa_pruned.mnn"), wsl_path(output)]
    started = time.monotonic()
    with fixture.open("rb") as inp:
        run = subprocess.run(command, stdin=inp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    diagnostics.write_bytes(run.stdout + run.stderr)
    if run.returncode:
        raise RuntimeError(f"native process failed rc={run.returncode}; see {diagnostics}")
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    if not rows:
        raise AssertionError("native composition emitted no contract frames")
    ids = [row["frame_id"] for row in rows]
    assert ids == sorted(set(ids)), "contract frame ids must strictly increase"
    assert all(validate_frame(row) == [] for row in rows), "invalid contract JSON"
    assert all(a["ts"] < b["ts"] for a, b in zip(rows, rows[1:])), "nonmonotonic time"
    indexed = {row["frame_id"]: row for row in rows}
    black_ids = [r["frame_id"] for r in records if r["kind"] == "black"]
    for frame_id in black_ids:
        row = indexed[frame_id]
        assert row["face"]["visible"] == 0, "black frame reused a face"
        assert all(v is None for v in row["vital"].values()), "black frame reused vitals"
        assert row["status"] in ("unreliable", "adjust_posture")
    assert any(row["face"]["visible"] > 0 for row in rows if row["frame_id"] > black_ids[-1]), "face did not recover"
    summary = {"kind": "native_vision_offline_development", "input_frames": len(records),
               "contract_frames": len(rows), "face_frames": sum(row["face"]["visible"] > 0 for row in rows),
               "black_ids": black_ids, "black_frames_clear_face_and_vitals": True,
               "contract_valid": True, "recovery_observed": True,
               "elapsed_s_including_launch": round(time.monotonic()-started, 3),
               "fps_source": fps, "hardware_test": False, "accuracy_verified": False,
               "config_sha256": __import__("hashlib").sha256((ROOT/"config.yaml").read_bytes()).hexdigest()}
    (args.work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
