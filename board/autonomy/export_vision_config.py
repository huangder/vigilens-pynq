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
    capture_fps=values.get('capture_fps',config['fps_nominal'])
    if type(capture_fps) not in (int,float) or not math.isfinite(capture_fps) or int(capture_fps)!=capture_fps or not 1<=capture_fps<=120:
        raise ValueError('invalid board_vision.capture_fps')
    if values['camera_format']=='yuyv' and capture_fps>30:
        raise ValueError('YUYV capture above 30 fps is not supported')
    workers=values.get('decode_workers',2)
    if type(workers) is not int or workers not in (1,2):
        raise ValueError('invalid board_vision.decode_workers')
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
    history_size=numbers["tracker_anchor_max_age_s"]*capture_fps
    if not math.isfinite(history_size) or not 1<=history_size<=4095:
        raise ValueError("invalid tracker history budget")
    digest = hashlib.sha256(raw).hexdigest()
    lines = ["/* Generated from config.yaml. Engineering initial values, not calibration. */",
             "#ifndef VG_EXPORTED_VISION_CONFIG_H", "#define VG_EXPORTED_VISION_CONFIG_H",
             '#include "face_detector.h"', f'#define VG_VISION_CONFIG_SHA256 "{digest}"',
             "static const vg_fd_params vg_exported_detector = {",
             "  " + ", ".join(numbers[k].hex() + "f" for k in FLOATS) + ",",
             f"  {int(values['max_candidates'])}", "};"]
    lines.append(f"#define VG_CAPTURE_FPS {int(capture_fps)}")
    preview_source=values.get('preview_source','analysis_rgb')
    if preview_source not in ('analysis_rgb','camera_mjpeg') or (preview_source=='camera_mjpeg' and values['camera_format']!='mjpg'):
        raise ValueError('invalid board_vision.preview_source')
    lines.append(f"#define VG_PREVIEW_CAMERA_MJPEG {int(preview_source=='camera_mjpeg')}")
    mode=values.get('tracker_image_mode','full_half')
    if mode not in ('full_half','roi_half'):
        raise ValueError('invalid board_vision.tracker_image_mode')
    expansion=float(values.get('tracker_region_expansion',values['crop_scale']))
    if not math.isfinite(expansion) or expansion<1:
        raise ValueError('invalid board_vision.tracker_region_expansion')
    lines.extend([f"#define VG_TRACKER_ROI_HALF {int(mode=='roi_half')}",
                  f"#define VG_TRACKER_REGION_EXPANSION {expansion.hex()}"])
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
    hr_hz=float(values.get('hr_estimate_hz',config['ws_push_hz']))
    if not math.isfinite(hr_hz) or not 0<hr_hz<=capture_fps:
        raise ValueError('invalid board_vision.hr_estimate_hz')
    lines.append(f"#define VG_HR_ESTIMATE_HZ {hr_hz.hex()}")
    cadence = capture_fps / hr_hz
    if not math.isfinite(cadence) or cadence < 1:
        raise ValueError("invalid vital estimation cadence")
    lines.append(f"#define VG_VITAL_EVERY {int(round(cadence))}")
    quality=config['video_jpeg_quality']
    if type(quality) is not int or not 1<=quality<=100:
        raise ValueError('video_jpeg_quality must be an integer in 1..100')
    lines.append(f"#define VG_PREVIEW_JPEG_QUALITY {quality}")
    preview_hz=float(values['preview_hz'])
    if not math.isfinite(preview_hz) or not 0<preview_hz<=float(config['fps_nominal']):
        raise ValueError('invalid board_vision.preview_hz')
    lines.append(f"#define VG_PREVIEW_INTERVAL_S {(1/preview_hz).hex()}")
    preview_async=values.get('preview_async',False)
    preview_nice=values.get('preview_worker_nice',0)
    if not isinstance(preview_async,bool) or type(preview_nice) is not int or not 0<=preview_nice<=19:
        raise ValueError('invalid board_vision asynchronous preview configuration')
    lines.extend([f"#define VG_PREVIEW_ASYNC {int(preview_async)}",f"#define VG_PREVIEW_WORKER_NICE {preview_nice}"])
    analysis_slots=values.get('analysis_slots',2)
    if type(analysis_slots) is not int or not 2<=analysis_slots<=8:
        raise ValueError('board_vision.analysis_slots must be an integer in 2..8')
    # Include two possible active decoders and PL current/history ownership.
    lines.extend([f"#define VG_ANALYSIS_SLOTS {analysis_slots}",
                  f"#define VG_RGB_POOL_BUFFERS {analysis_slots+4}"])
    blink_keys=('ear_close_ratio','ear_open_ratio','ear_baseline_tau_s',
                'blink_min_close_ms','blink_min_open_ms','blink_max_gap_ms','ear_baseline_reset_ms')
    blink=[float(values[k]) for k in blink_keys]
    if (not all(math.isfinite(v) for v in blink) or not 0<blink[0]<blink[1]<1 or
            any(v<=0 for v in blink[2:]) or blink[6]<blink[5]):
        raise ValueError('invalid board_vision EAR/blink policy')
    mode=values['ear_threshold_mode']
    if mode not in ('adaptive','absolute'):
        raise ValueError('invalid board_vision.ear_threshold_mode')
    absolute=[float(values[k]) for k in ('ear_close_absolute','ear_open_absolute')]
    if not all(math.isfinite(v) for v in absolute) or not 0<absolute[0]<=absolute[1]:
        raise ValueError('invalid board_vision absolute EAR thresholds')
    blink.extend(absolute if mode=='absolute' else [0.,0.])
    long_close=float(values['blink_long_close_ms'])
    if not math.isfinite(long_close) or long_close<blink[3]:
        raise ValueError('invalid board_vision.blink_long_close_ms')
    blink.append(long_close)
    lines.extend(['#include "behavior.h"',
        'static const vg_blink_config vg_exported_blink_config = {'+
        ', '.join(v.hex() for v in blink)+'};'])
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
