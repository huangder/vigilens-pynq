"""Export the rPPG/vital configuration for native builds (config.yaml is the only source).

Kept separate from export_config.py so the existing measure header and its tests are
unchanged: that header describes vg_measure_config; this one describes vg_vital_config
and embeds the frozen FIR coefficients read from fpga/src/fir_coeffs_q15.h.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config, rppg_window_seconds
from backend.vital import load_fir_coeffs
from backend.hr_filter import HrFilterConfig


def build(config_path: Path) -> dict:
    cfg = load_config(config_path)
    coeffs, shift, fs = load_fir_coeffs(ROOT / "fpga" / "src" / "fir_coeffs_q15.h")
    if int(cfg["fps_nominal"]) != int(fs):
        raise ValueError("fps_nominal and the frozen FIR design rate disagree")
    if cfg['rppg_method'] not in ('pos','green'):raise ValueError('unsupported rppg_method')
    return {
        "fps": float(cfg["fps_nominal"]),
        "fs_tol": float(cfg["rppg_fs_tol"]),
        "window_seconds": rppg_window_seconds(cfg),
        "color_window_seconds": float(cfg["rppg_color_window_seconds"]),
        "f_lo": float(cfg["hr_band_hz"][0]),
        "f_hi": float(cfg["hr_band_hz"][1]),
        "harmonic_ratio_min": float(cfg["rppg_harmonic_ratio_min"]),
        "max_gap_s": float(cfg["rppg_max_gap_ms"]) / 1000.0,
        "jitter_ratio_max": float(cfg["realtime_jitter_ratio_max"]),
        "recovery_s": float(cfg["rppg_recovery_ms"]) / 1000.0,
        "recovery_samples": int(cfg["rppg_recovery_samples"]),
        "method": 1 if cfg['rppg_method']=='green' else 0,
        "coeffs": coeffs,
        "shift": shift,
    }


def export(config_path: Path, output: Path) -> dict:
    values = build(config_path)
    stable = HrFilterConfig.from_config(load_config(config_path))
    digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    coeffs = ", ".join(str(v) for v in values["coeffs"])
    lines = [
        "/* Generated from config.yaml + fpga/src/fir_coeffs_q15.h; do not edit. */",
        "#ifndef VG_EXPORTED_VITAL_CONFIG_H",
        "#define VG_EXPORTED_VITAL_CONFIG_H",
        '#include "vital.h"',
        '#include "hr_filter.h"',
        f'#define VG_VITAL_CONFIG_SHA256 "{digest}"',
        f"static const short VG_VITAL_COEFFS[{len(values['coeffs'])}] = {{{coeffs}}};",
        "static const vg_vital_config vg_exported_vital_config = {",
        "  " + ", ".join(float(values[k]).hex() for k in (
            "fps", "fs_tol", "window_seconds", "color_window_seconds", "f_lo", "f_hi",
            "harmonic_ratio_min", "max_gap_s", "jitter_ratio_max", "recovery_s")),
        f"  , VG_VITAL_COEFFS, {len(values['coeffs'])}, {values['shift']}, {values['recovery_samples']}, {values['method']}",
        "};",
        "static const vg_hr_filter_config vg_exported_hr_filter_config = {" +
        f"{int(stable.enabled)}, {stable.window}, " +
        ", ".join(float(getattr(stable,k)).hex() for k in ("alpha","interval_s","max_gap_s","max_spread_bpm")) + f", {stable.startup_samples}" + "};",
        "#endif",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {"config_sha256": digest, "vital": {k: v for k, v in values.items() if k != "coeffs"},
                "coeffs": len(values["coeffs"]), "hr_filter": stable.__dict__, "thresholds_calibrated_by_export": False}
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.config, args.output)))
