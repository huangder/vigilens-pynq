"""Cross-check board serial, received source identity, and business JSON.

No pixels are read or saved. Hardware correctness, transport completion,
algorithm connection, and achieved frame rate remain separate conclusions.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.contract import validate_frame
from board.w7_buildroot.check_camera_pl_log import inspect_log


def inspect_run(log: str, summary: dict, frames: list[dict], cfg: dict) -> dict:
    hardware = inspect_log(log, cfg)
    records = [dict(re.findall(r"([a-z0-9_]+)=([^\s]+)", line))
               for line in log.replace("\r", "").splitlines()
               if line.startswith("CAMERA_PL_FRAME ")]
    count = len(records)
    source = summary["board_source"]
    if (source.get("source") != "board_pl" or source.get("error") is not None or
            source.get("end_received") is not True or source.get("frames") != count or
            summary["frames_processed"] != count or source["valid_motion_frames"] != count-1):
        raise ValueError("board source did not finish the same complete run")
    end = re.search(r"(?m)^BOARD_STREAM_PASS sent_frames=(\d+)\r?$", log)
    if not end or int(end[1]) != count:
        raise ValueError("sender completion count differs")
    if source["first"]["source_sequence"] != int(records[0]["seq"]) or \
            source["last"]["source_sequence"] != int(records[-1]["seq"]):
        raise ValueError("received source identity differs from board log")
    if source["first"]["source_ts_us"] != int(records[0]["ts_us"]) or \
            source["last"]["source_ts_us"] != int(records[-1]["ts_us"]):
        raise ValueError("received capture timestamps differ from board log")
    if source["last"]["pl_frame_id"] - source["first"]["pl_frame_id"] != count-1:
        raise ValueError("PL frame identity is not continuous")
    if source["first"]["motion_threshold"] != int(cfg["motion_thresh_gray"]):
        raise ValueError("hardware motion threshold differs from config")
    if any(validate_frame(frame) for frame in frames):
        raise ValueError("invalid business JSON contract")
    measured = [frame for frame in frames if frame["status"] not in ("done", "disconnected")]
    if len(measured) != count:
        raise ValueError("measurement count differs from same-run hardware frames")
    first_ts = int(records[0]["ts_us"])
    offset = summary.get("frame_id_offset", 0)
    gain = float(cfg["motion_score_gain"])
    for index, (record, frame) in enumerate(zip(records, measured)):
        expected_ts = round((int(record["ts_us"])-first_ts)/1e6 + offset/float(cfg["fps_nominal"]), 3)
        if frame["frame_id"] != index+offset or frame["ts"] != expected_ts:
            raise ValueError(f"frame {index} capture-time/identity differs")
        expected_motion = round(min(1.0, int(record["motion_ratio_q16"])/65536*gain), 4) if index else 0.0
        if frame["quality"]["motion_score"] != expected_motion:
            raise ValueError(f"frame {index} does not use same-packet PL motion")
    if (measured[0]["status"] != "unreliable" or measured[0]["quality"]["overall"] != 0 or
            any(value is not None for value in measured[0]["vital"].values())):
        raise ValueError("undefined first-frame motion was not refused")
    for channel in ("post", "preview_post"):
        stats = summary.get(channel) or {}
        if not stats.get("ok") or stats.get("posted", 0) < 1 or stats.get("errors"):
            raise ValueError(f"{channel} did not deliver data successfully")
    if summary.get("landmark_source") != "mediapipe":
        raise ValueError("real algorithm connection requires MediaPipe, not stub")
    throughput = summary.get("throughput_fps")
    processing_target = float(cfg["fps_nominal"]) * float(cfg["realtime_process_ratio_min"])
    return {"network_closed_loop_pass": True, "same_packet_motion_checked_frames": count,
            "hardware": hardware, "metric_posts": summary["post"]["posted"],
            "preview_posts": summary["preview_post"]["posted"],
            "landmark_source": summary["landmark_source"],
            "face_visible_frames": sum(frame["face"]["visible"] > 0 for frame in measured),
            "pipeline_throughput_fps": throughput, "processing_target_fps": processing_target,
            "processing_target_met": None if throughput is None else throughput >= processing_target,
            "note": "Connection/correctness evidence; not fatigue accuracy, native 30fps, or standalone boot validation."}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board_log", type=Path)
    ap.add_argument("summary", type=Path)
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    try:
        result = inspect_run(args.board_log.read_text(encoding="utf-8-sig"),
                             json.loads(args.summary.read_text(encoding="utf-8-sig")),
                             [json.loads(line) for line in args.jsonl.read_text(encoding="utf-8-sig").splitlines() if line],
                             load_config())
    except (ValueError, KeyError, OSError) as exc:
        print(f"NETWORK_RUN_FAIL: {exc}", file=sys.stderr)
        return 1
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)
    if args.out:
        args.out.write_text(encoded+"\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
