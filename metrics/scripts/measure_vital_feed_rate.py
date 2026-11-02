"""Sweep the native rPPG feed rate and report the accept/refuse boundary.

The frozen FIR is designed for fps_nominal; `fs_tol` therefore also sets the
lowest feed rate the estimator can accept (fps*(1-fs_tol)). This measures that
boundary on synthetic data — it says nothing about real-person accuracy, and a
rate below it is a property of the caller, not of the model.

python metrics/scripts/measure_vital_feed_rate.py --output metrics/evidence/x.json
"""
from __future__ import annotations
import argparse
import ctypes as ct
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.vital import load_fir_coeffs


class Config(ct.Structure):
    _fields_ = [("fps", ct.c_double), ("fs_tol", ct.c_double),
                ("window_seconds", ct.c_double), ("color_window_seconds", ct.c_double),
                ("f_lo", ct.c_double), ("f_hi", ct.c_double),
                ("harmonic_ratio_min", ct.c_double),
                ("max_gap_s", ct.c_double), ("jitter_ratio_max", ct.c_double),
                ("recovery_s", ct.c_double),
                ("coeffs", ct.POINTER(ct.c_short)), ("taps", ct.c_int), ("shift", ct.c_int),
                ("recovery_samples", ct.c_int)]


class Result(ct.Structure):
    _fields_ = [("present", ct.c_int), ("hr_bpm", ct.c_double),
                ("hr_conf", ct.c_double), ("reject", ct.c_char_p)]


def build(folder: Path) -> Path:
    compiler = os.environ.get("CC") or shutil.which("gcc") or shutil.which("cc")
    if not compiler:
        raise SystemExit("no C compiler")
    suffix = ".dll" if os.name == "nt" else ".so"
    library = folder / ("vgvital" + suffix)
    subprocess.run([compiler, "-std=c11", "-O2", "-shared", "-fPIC",
                    "-o", str(library), str(ROOT / "board/autonomy/vital.c"), "-lm"],
                   check=True, capture_output=True, text=True)
    return library


def stream(samples: int, rate: float, bpm: float, count: int = 1000):
    for i in range(samples):
        t = i / rate
        pulse = 0.012 * math.sin(2 * math.pi * (bpm / 60.0) * t)
        drift = 0.004 * math.sin(2 * math.pi * 0.05 * t)
        base = (122.0, 118.0, 112.0)
        means = [base[0] * (1 + pulse + 0.3 * drift),
                 base[1] * (1 - pulse - 0.2 * drift),
                 base[2] * (1 + 0.5 * pulse)]
        yield [int(round(m * count)) for m in means], count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bpm", type=float, default=72.0)
    parser.add_argument("--samples", type=int, default=1200)
    args = parser.parse_args()
    cfg = load_config()
    coeffs, shift, _fs = load_fir_coeffs()
    fps = float(cfg["fps_nominal"])
    fs_tol = float(cfg["rppg_fs_tol"])
    minimum = fps * (1.0 - fs_tol)

    rows = []
    with tempfile.TemporaryDirectory(prefix="vg_rate_") as temp:
        folder = Path(temp)
        lib = ct.CDLL(str(build(folder)))
        lib.vg_vital_create.restype = ct.c_void_p
        lib.vg_vital_create.argtypes = [ct.POINTER(Config)]
        lib.vg_vital_destroy.argtypes = [ct.c_void_p]
        lib.vg_vital_push_rgb.restype = ct.c_int
        lib.vg_vital_push_rgb.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint32),
                                          ct.c_uint32, ct.c_uint64, ct.c_double]
        lib.vg_vital_estimate.restype = ct.c_int
        lib.vg_vital_estimate.argtypes = [ct.c_void_p, ct.POINTER(Result)]
        array = (ct.c_short * len(coeffs))(*coeffs)
        for rate in (30.0, 29.0, 28.0, 27.0, 26.0, 24.0, 15.0, 10.0, 5.0, 1.667):
            config = Config(fps=fps, fs_tol=fs_tol,
                            window_seconds=float(cfg["window_seconds"]),
                            color_window_seconds=float(cfg["rppg_color_window_seconds"]),
                            f_lo=float(cfg["hr_band_hz"][0]), f_hi=float(cfg["hr_band_hz"][1]),
                            harmonic_ratio_min=float(cfg["rppg_harmonic_ratio_min"]),
                            max_gap_s=float(cfg["rppg_max_gap_ms"]) / 1000.0,
                            jitter_ratio_max=float(cfg["realtime_jitter_ratio_max"]),
                            recovery_s=float(cfg["rppg_recovery_ms"]) / 1000.0,
                            coeffs=array, taps=len(coeffs), shift=shift,
                            recovery_samples=int(cfg["rppg_recovery_samples"]))
            handle = lib.vg_vital_create(ct.byref(config))
            if not handle:
                raise SystemExit("vg_vital_create failed")
            for index, (sums, count) in enumerate(stream(args.samples, rate, args.bpm)):
                buffer = (ct.c_uint32 * 3)(*sums)
                if lib.vg_vital_push_rgb(handle, buffer, count, index, index / rate):
                    raise SystemExit("push failed")
            result = Result()
            lib.vg_vital_estimate(handle, ct.byref(result))
            rows.append({"feed_hz": rate,
                         "seconds_of_audio": round(args.samples / rate, 2),
                         "present": bool(result.present),
                         "hr_bpm": round(result.hr_bpm, 2) if result.present else None,
                         "reject": result.reject.decode() if result.reject else None})
            lib.vg_vital_destroy(handle)
    summary = {"kind": "native_rppg_feed_rate_boundary",
               "hardware_test": False,
               "accuracy_verified": False,
               "design_fps": fps, "fs_tol": fs_tol,
               "lowest_accepted_hz": round(minimum, 3),
               "samples_per_case": args.samples, "synthetic_bpm": args.bpm,
               "rows": rows}
    text = json.dumps(summary, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
