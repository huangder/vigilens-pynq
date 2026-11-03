"""Export the rPPG/vital configuration for native builds (config.yaml is the only source).

Kept separate from export_config.py so the existing measure header and its tests are
unchanged: that header describes vg_measure_config; this one describes vg_vital_config
and embeds the frozen FIR coefficients read from fpga/src/fir_coeffs_q15.h.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config, rppg_window_seconds
from backend.vital import load_fir_coeffs
from backend.hr_filter import HrFilterConfig


def build(config_path: Path, fir_header: Path | None = None, board_profile: bool = False) -> dict:
    cfg = load_config(config_path)
    if board_profile:
        board=cfg['board_vision'];cfg=dict(cfg)
        fps=float(board.get('vital_fps',cfg['fps_nominal']))
        samples=board.get('vital_min_samples')
        if not math.isfinite(fps) or fps<=0 or (samples is not None and (type(samples) is not int or samples<2)):
            raise ValueError('invalid board vital sampling profile')
        cfg['fps_nominal']=fps
        cfg['rppg_source_min_hz']=board.get('vital_source_min_hz',0)
        cfg['rppg_green_center_before_fir']=board.get('vital_green_center_before_fir',False)
        if samples is not None:cfg['rppg_window_seconds']=samples/fps
        if fir_header is None and board.get('vital_fir_header'):fir_header=ROOT/board['vital_fir_header']
    seconds=rppg_window_seconds(cfg)
    history=float(cfg.get('rppg_history_seconds',0))
    jitter=float(cfg.get('rppg_resample_jitter_ratio_max',cfg['realtime_jitter_ratio_max']))
    if not math.isfinite(history) or history<0 or not math.isfinite(jitter) or jitter<=0:
        raise ValueError('invalid rPPG history/resample jitter configuration')
    short_seconds=float(cfg.get('rppg_short_window_seconds',0))
    short_conf=float(cfg.get('rppg_short_window_min_conf',0))
    if (not math.isfinite(short_seconds) or not math.isfinite(short_conf) or
            not ((short_seconds==0 and short_conf==0) or (short_seconds>0 and .5<=short_conf<1))):
        raise ValueError('invalid rPPG short-window quality configuration')
    coeffs, shift, fs = load_fir_coeffs(fir_header or ROOT / "fpga" / "src" / "fir_coeffs_q15.h")
    if float(cfg["fps_nominal"]) != float(fs):
        raise ValueError("fps_nominal and the frozen FIR design rate disagree")
    if cfg['rppg_method'] not in ('pos','green'):raise ValueError('unsupported rppg_method')
    source_min=float(cfg.get('rppg_source_min_hz',0))
    if not math.isfinite(source_min) or not (source_min==0 or 2*float(cfg['hr_band_hz'][1])<source_min<=float(fs)):
        raise ValueError('invalid source sampling minimum')
    center=cfg.get('rppg_green_center_before_fir',False)
    if type(center) is not bool:raise ValueError('green_center_before_fir must be boolean')
    return {
        "fps": float(cfg["fps_nominal"]),
        "fs_tol": float(cfg["rppg_fs_tol"]),
        "window_seconds": seconds,
        "color_window_seconds": float(cfg["rppg_color_window_seconds"]),
        "f_lo": float(cfg["hr_band_hz"][0]),
        "f_hi": float(cfg["hr_band_hz"][1]),
        "harmonic_ratio_min": float(cfg["rppg_harmonic_ratio_min"]),
        "max_gap_s": float(cfg["rppg_max_gap_ms"]) / 1000.0,
        "jitter_ratio_max": jitter,
        "history_seconds": history,
        "source_min_hz": source_min,
        "green_center_before_fir": int(center),
        "recovery_s": float(cfg["rppg_recovery_ms"]) / 1000.0,
        "recovery_samples": int(cfg["rppg_recovery_samples"]),
        "method": 1 if cfg['rppg_method']=='green' else 0,
        "short_window_seconds": short_seconds,
        "short_window_min_conf": short_conf,
        "coeffs": coeffs,
        "shift": shift,
    }


def export(config_path: Path, output: Path, fir_header: Path | None = None, board_profile: bool = False) -> dict:
    values = build(config_path,fir_header,board_profile)
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
        f"  , VG_VITAL_COEFFS, {len(values['coeffs'])}, {values['shift']}, {values['recovery_samples']}, {values['method']}, " +
        float(values['short_window_seconds']).hex()+", "+float(values['short_window_min_conf']).hex()+", "+float(values['history_seconds']).hex()+", "+float(values['source_min_hz']).hex()+", "+str(values['green_center_before_fir']),
        "};",
        "static const vg_hr_filter_config vg_exported_hr_filter_config = {" +
        f"{int(stable.enabled)}, {stable.window}, " +
        ", ".join(float(getattr(stable,k)).hex() for k in ("alpha","interval_s","max_gap_s","max_spread_bpm")) + f", {stable.startup_samples}, {stable.min_bpm.hex()}" + "};",
        "#endif",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    selected_fir=fir_header or (ROOT/load_config(config_path)['board_vision']['vital_fir_header'] if board_profile and load_config(config_path)['board_vision'].get('vital_fir_header') else ROOT / "fpga" / "src" / "fir_coeffs_q15.h")
    manifest = {"config_sha256": digest, "fir_sha256":hashlib.sha256(selected_fir.read_bytes()).hexdigest(),
                "vital": {k: v for k, v in values.items() if k != "coeffs"},
                "coeffs": len(values["coeffs"]), "hr_filter": stable.__dict__, "thresholds_calibrated_by_export": False}
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fir-header",type=Path,help="Explicit experimental FIR; design rate must match configuration")
    parser.add_argument("--board-profile",action="store_true",help="Use explicit board_vision physiological rate/FIR/sample-count profile")
    args = parser.parse_args()
    print(json.dumps(export(args.config, args.output,args.fir_header,args.board_profile)))
