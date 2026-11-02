"""Export board_vision parameters; missing values fail instead of inventing defaults."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.contract import _TOP_KEYS

FLOATS = ("min_face", "scale_factor", "score_p", "score_r", "score_o",
          "nms_p_scale", "nms_p", "nms_r", "nms_o")
GEOMETRY = ("crop_scale", "forehead_inset", "forehead_top", "forehead_bottom", "redetect_seconds",
            "min_face_fast", "fast_side_margin", "deep_detect_seconds",
            "tracker_min_eigen", "tracker_max_error", "tracker_fb_error", "tracker_min_fraction",
            "tracker_max_motion", "tracker_model_hz", "tracker_redetect_seconds", "tracker_anchor_max_age_s",
            "tracker_epsilon", "ert_patch_min_std", "ert_patch_max_error")


def export(path: Path, output: Path):
    raw = path.read_bytes()
    config = load_config(path)
    values = config["board_vision"]
    if (values['camera_width'],values['camera_height']) not in ((1280,720),(640,480)):
        raise ValueError('unsupported board_vision camera geometry')
    if values['camera_format'] not in ('mjpg','yuyv') or (values['camera_format']=='yuyv' and (values['camera_width'],values['camera_height'])!=(640,480)):
        raise ValueError('unsupported board_vision camera format/rate')
    numbers = {key: float(values[key]) for key in FLOATS + GEOMETRY}
    if not all(math.isfinite(v) for v in numbers.values()):
        raise ValueError("board_vision parameters must be finite")
    for key in ("max_candidates", "threads", "detector_threads", "landmark_threads", "forehead_min_pixels"):
        if isinstance(values[key], bool) or int(values[key]) != values[key] or not 0 < values[key] <= 4096:
            raise ValueError(f"invalid board_vision.{key}")
    if numbers["min_face"] < 12 or not 0 < numbers["scale_factor"] < 1:
        raise ValueError("invalid detection pyramid")
    if any(not 0 <= numbers[key] <= 1 for key in FLOATS[2:]):
        raise ValueError("invalid detection score/NMS threshold")
    if numbers["crop_scale"] < 1 or not 0 <= numbers["forehead_inset"] < 0.5:
        raise ValueError("invalid landmark crop/ROI")
    if not numbers["forehead_top"] > numbers["forehead_bottom"] >= 0:
        raise ValueError("invalid forehead bounds")
    if numbers["redetect_seconds"] <= 0:
        raise ValueError("redetect_seconds must be positive")
    if numbers["min_face_fast"] < numbers["min_face"]:
        raise ValueError("min_face_fast must not be finer than min_face")
    if not 0 < numbers["fast_side_margin"] <= 1:
        raise ValueError("fast_side_margin must be in (0, 1]")
    if numbers["deep_detect_seconds"] <= 0:
        raise ValueError("deep_detect_seconds must be positive")
    for key, maximum in (("tracker_levels",8),("tracker_radius",7),("tracker_iterations",32)):
        value=values[key]
        if isinstance(value,bool) or int(value)!=value or not 1<=value<=maximum:
            raise ValueError(f"invalid board_vision.{key}")
    if any(numbers[key]<=0 for key in GEOMETRY if key.startswith("tracker_")):
        raise ValueError("tracker parameters must be positive")
    if numbers["tracker_min_fraction"]>1 or numbers["tracker_model_hz"]>float(config["fps_nominal"]):
        raise ValueError("invalid tracker fraction/model cadence")
    side=values["ert_patch_side"]
    if isinstance(side,bool) or int(side)!=side or not 2<=side<=128:
        raise ValueError("invalid board_vision.ert_patch_side")
    if not 0<numbers["ert_patch_min_std"]<=255 or not 0<numbers["ert_patch_max_error"]<=255:
        raise ValueError("invalid ERT appearance thresholds")
    history_size=numbers["tracker_anchor_max_age_s"]*float(config["fps_nominal"])
    if not math.isfinite(history_size) or not 1<=history_size<=4095:
        raise ValueError("invalid tracker history budget")
    digest = hashlib.sha256(raw).hexdigest()
    lines = ["/* Generated from config.yaml. Engineering initial values, not calibration. */",
             "#ifndef VG_EXPORTED_VISION_CONFIG_H", "#define VG_EXPORTED_VISION_CONFIG_H",
             '#include "face_detector.h"', f'#define VG_VISION_CONFIG_SHA256 "{digest}"',
             "static const vg_fd_params vg_exported_detector = {",
             "  " + ", ".join(numbers[k].hex() + "f" for k in FLOATS) + ",",
             f"  {int(values['max_candidates'])}", "};"]
    for key in GEOMETRY:
        lines.append(f"#define VG_{key.upper()} {numbers[key].hex()}")
    lines.append(f"#define VG_TRACKER_HISTORY_FRAMES {math.ceil(history_size)+1}")
    for key in ("threads", "detector_threads", "landmark_threads", "forehead_min_pixels",
                "tracker_levels", "tracker_radius", "tracker_iterations", "ert_patch_side"):
        lines.append(f"#define VG_{key.upper()} {int(values[key])}")
    if not isinstance(values["ncnn_packing"],bool):
        raise ValueError("board_vision.ncnn_packing must be boolean")
    lines.append(f"#define VG_NCNN_PACKING {int(values['ncnn_packing'])}")
    for key in ("analysis_nice","tracker_worker_nice"):
        nice=values[key]
        if isinstance(nice,bool) or int(nice)!=nice or not 0<=nice<=19:
            raise ValueError(f"{key} must be an integer in 0..19")
        lines.append(f"#define VG_{key.upper()} {int(nice)}")
    cadence = float(config["fps_nominal"]) / float(config["ws_push_hz"])
    if not math.isfinite(cadence) or cadence < 1:
        raise ValueError("invalid vital estimation cadence")
    lines.append(f"#define VG_VITAL_EVERY {int(round(cadence))}")
    lines += ["#endif", ""]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    if not 0 < len(_TOP_KEYS) < 32:
        raise ValueError("HTTP envelope key bitmap capacity exceeded")
    envelope = ["/* Generated from backend/contract.py; not another schema source. */",
                "#ifndef VG_HTTP_ENVELOPE_H", "#define VG_HTTP_ENVELOPE_H",
                "#define VG_HTTP_KEYS {" + ",".join(json.dumps(k) for k in _TOP_KEYS) + "}",
                f"#define VG_HTTP_KEY_COUNT {len(_TOP_KEYS)}", "#endif", ""]
    (output.parent/"vg_http_envelope.h").write_text("\n".join(envelope),encoding="utf-8")
    manifest = {"config_sha256": digest, "board_vision": values,
                "accuracy_verified": False}
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.config, args.output)))
