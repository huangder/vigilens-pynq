"""Build a small-face replay fixture (default 192x144) from a local clip.

Used to exercise the narrow/deep detection tiers: shrinking the clip puts the
face below board_vision.min_face_fast but above min_face, so a fixed narrow
search must fail where the adaptive one still finds it. Frames stay local.

python metrics/scripts/make_small_face_fixture.py --source data/raw/blink.mp4 \
    --output .tmp/autonomy/small_frames.rgbstream --frames 300
"""
from __future__ import annotations
import argparse
from pathlib import Path

import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/raw/blink.mp4"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--width", type=int, default=192)
    parser.add_argument("--height", type=int, default=144)
    parser.add_argument("--motion-threshold", type=int, default=16)
    parser.add_argument("--fps", type=float, default=None,
                        help="override the wall-clock rate written into the header")
    args = parser.parse_args()

    cap = cv2.VideoCapture(str(args.source))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.source}")
    fps = args.fps or cap.get(cv2.CAP_PROP_FPS) or 30.0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    written = 0
    with args.output.open("wb") as stream:
        for index in range(args.frames):
            ok, bgr = cap.read()
            if not ok:
                break
            small = cv2.resize(bgr, (args.width, args.height), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            motion = 0
            if previous is not None:
                diff = np.abs(gray.astype(np.int16) - previous.astype(np.int16)) > args.motion_threshold
                motion = int(round(diff.mean() * 65536))
            header = (f"{index} {index / fps:.12f} {args.width} {args.height} "
                      f"{gray.mean() / 255:.12f} {motion} {int(previous is not None)}\n")
            stream.write(header.encode("ascii"))
            stream.write(cv2.cvtColor(small, cv2.COLOR_BGR2RGB).tobytes())
            previous = gray
            written += 1
    cap.release()
    print(f"wrote {args.output} frames={written} fps={fps}")


if __name__ == "__main__":
    main()
