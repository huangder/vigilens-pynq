"""Export config.yaml values for native PS modules without duplicating defaults."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.behavior_metrics import BehaviorTracker
from backend.config import load_config
from backend.decision import DecisionEngine
from backend.quality import QualityScorer


def config_values(path: Path):
    cfg = load_config(path)
    behavior, quality, decision = BehaviorTracker(cfg), QualityScorer(cfg), DecisionEngine(cfg)
    groups = {
        "behavior": dict(zip(("ear_close_threshold", "min_close_ms", "window_seconds", "long_close_ms",
                              "mar_threshold", "yawn_min_duration_ms"),
                             (behavior.ear_thr, behavior.min_close_ms, behavior.window_s, behavior.long_close_ms,
                              behavior.mar_thr, behavior.yawn_min_ms))),
        "quality": {"light_target": quality.light_target, "motion_score_gain": quality.motion_gain,
                    **{f"weight_{k}": quality.weights[k] for k in ("light", "motion", "face")}},
        "decision": dict(zip(("quality_min_score", "face_visible_min", "pose_yaw_max_deg", "pose_pitch_max_deg",
                              "perclos_warning", "fatigue_blink_rate_low", "vital_require_quality",
                              "fatigue_yawn_count", "fatigue_long_close_count", "pose_hold_frames"),
                             (decision.quality_min, decision.face_visible_min, decision.yaw_max, decision.pitch_max,
                              decision.perclos_warn, decision.blink_rate_low, decision.vital_require_quality,
                              decision.yawn_warn, decision.long_close_warn, decision.pose_hold_frames))),
    }
    for group in groups.values():
        for value in group.values():
            if not math.isfinite(value):
                raise ValueError("Nonfinite native config value")
    for key, bits in (("fatigue_yawn_count", 64), ("fatigue_long_close_count", 64), ("pose_hold_frames", 32)):
        if not 0 <= groups["decision"][key] < 2 ** bits:
            raise ValueError(f"Native integer config out of range: {key}")
    return groups


def export(path: Path, output: Path):
    # A missing YAML must not silently fall back to backend defaults.
    raw = path.read_bytes()
    groups = config_values(path)
    digest = hashlib.sha256(raw).hexdigest()
    pose_limit = float(load_config(path)["pose_reproj_max_frac"])
    if not math.isfinite(pose_limit) or pose_limit <= 0:
        raise ValueError("pose_reproj_max_frac must be finite and positive")
    lines = ["/* Generated from config.yaml; regenerate after every config change. */",
             "#ifndef VG_EXPORTED_CONFIG_H", "#define VG_EXPORTED_CONFIG_H",
             '#include "measure.h"', f'#define VG_CONFIG_SHA256 "{digest}"',
             f'#define VG_POSE_REPROJ_MAX_FRAC {pose_limit.hex()}',
             "static const vg_measure_config vg_exported_config = {"]
    for name, values in groups.items():
        constants = []
        for key, value in values.items():
            if isinstance(value, int):
                constants.append(str(value) + ("U" if key == "pose_hold_frames" else "ULL"))
            else:
                constants.append(float(value).hex())
        lines.append("  {" + ", ".join(constants) + "}, /* " + name + " */")
    lines.extend(["};", "#endif"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {"source_sha256": digest, "groups": groups,
                "thresholds_calibrated_by_export": False}
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.config, args.output)))
