"""Generate labeled synthetic geometry/statistics for native C replay.

No video, trained model, measured physiological value, BPM or accuracy evidence.
Usage: python board/autonomy/make_replay_fixture.py --output .tmp/autonomy/synthetic.txt
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config


def eye(value):
    return (0., 0., .25, value/2, .75, value/2, 1., 0., .75, -value/2, .25, -value/2)


def records(cfg, frames=900):
    """Return explicit synthetic fixtures; intentionally rejected records included.

    Frames span normal, blink, long eye closure, yawn, pose, dim light,
    missing face and high motion. Includes invalid PL valid/Q16 and ID/time
    rollback followed by valid records, to exercise rejection-state isolation.
    All scalars are test stimuli, never experimental measurements.
    """
    fps = float(cfg["fps_nominal"])
    if fps<=0 or not 1<=frames<=100000:
        raise ValueError("positive configured fps and 1..100000 records required")
    values = []
    for idx in range(frames):
        ts = idx/fps
        closed = 1.<=ts<1.25 or 3.<=ts<4. or 18.<=ts<18.25
        mouth_open = 5.<=ts<7. or 20.<=ts<22.
        visible = 0. if 15.<=ts<16. else 1.
        ear = float(cfg["ear_close_threshold"])*(.25 if closed else 1.5)
        mar = float(cfg["mar_threshold"])*(2. if mouth_open else .25)
        mean = 0. if 12.<=ts<14. else float(cfg["light_target"])
        yaw = float(cfg["pose_yaw_max_deg"])*2 if 8.<=ts<10. else 0.
        motion = 65536 if 24.<=ts<25. else 0
        valid = 0 if idx in (0, 100) else 1
        frame_id = idx-2 if idx==240 else idx
        if idx==241: ts = (idx-4)/fps
        if idx==200: motion = 65537
        row = [frame_id, ts, mean, motion, valid, 1, visible, 1, 1, 1, yaw, 0.]
        row += list(eye(ear))*2
        row += [0., 0., 1., 0., .5, 0., .5, mar]
        assert len(row)==44
        values.append(row)
    return values


def generate(output: Path, config: Path=ROOT/"config.yaml", frames: int=900):
    rows = records(load_config(config), frames)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = f"VIGILENS_REPLAY_V1 synthetic {len(rows)}\n"
    text += "".join(" ".join(str(v) if isinstance(v, int) else format(v, ".17g") for v in row)+"\n" for row in rows)
    output.write_text(text, encoding="ascii", newline="\n")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT/"config.yaml")
    parser.add_argument("--frames", type=int, default=900)
    args = parser.parse_args()
    generate(args.output, args.config, args.frames)
    print(f"synthetic fixture: {args.output}; records={args.frames}; no real measurement claim")
