"""ctypes bindings for the native PS core (measure + vital + contract writer).

This is the PC-side integration seam for the board migration: the same C files
that will run on the A9 are compiled into a shared library here and driven from
Python tests and the offline demo. It deliberately mirrors the C structs instead
of re-implementing the algorithms, so a field mismatch fails loudly at the
boundary rather than silently changing a number.

Not a board claim: compilation, timing and libc are this PC's.
"""
from __future__ import annotations

import ctypes as ct
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent
SOURCES = ("behavior.c", "quality.c", "decision.c", "measure.c", "vital.c", "contract_json.c")


class Point(ct.Structure):
    _fields_ = [("x", ct.c_double), ("y", ct.c_double)]


class Eye(ct.Structure):
    _fields_ = [("present", ct.c_int), ("p", Point * 6)]


class Mouth(ct.Structure):
    _fields_ = [("present", ct.c_int), ("left", Point), ("right", Point),
                ("top", Point), ("bottom", Point)]


class Observation(ct.Structure):
    _fields_ = [("eyes_present", ct.c_int), ("visible", ct.c_double),
                ("left", Eye), ("right", Eye), ("mouth", Mouth)]


class BehaviorConfig(ct.Structure):
    _fields_ = [(name, ct.c_double) for name in (
        "ear_close_threshold", "min_close_ms", "window_seconds",
        "long_close_ms", "mar_threshold", "yawn_min_duration_ms")]


class BehaviorResult(ct.Structure):
    _fields_ = [(name, ct.c_double) for name in (
        "ear_left", "ear_right", "blink_rate_per_min", "perclos", "mar", "dt")]
    _fields_ += [("blink_state", ct.c_int)]
    _fields_ += [(name, ct.c_uint64) for name in
                 ("blink_count", "long_close_count", "yawn_count")]


class QualityConfig(ct.Structure):
    _fields_ = [(name, ct.c_double) for name in (
        "light_target", "motion_score_gain", "weight_light", "weight_motion", "weight_face")]


class QualityResult(ct.Structure):
    _fields_ = [(name, ct.c_double) for name in (
        "light_score", "motion_score", "overall", "mean_luma", "motion_ratio")]


class Face(ct.Structure):
    _fields_ = [("visible", ct.c_double), ("yaw", ct.c_double), ("pitch", ct.c_double)]


class DecisionConfig(ct.Structure):
    _fields_ = [(name, ct.c_double) for name in (
        "quality_min_score", "face_visible_min", "pose_yaw_max_deg", "pose_pitch_max_deg",
        "perclos_warning", "fatigue_blink_rate_low", "vital_require_quality")]
    _fields_ += [("fatigue_yawn_count", ct.c_uint64),
                 ("fatigue_long_close_count", ct.c_uint64),
                 ("pose_hold_frames", ct.c_uint32)]


class Trigger(ct.Structure):
    _fields_ = [("rule", ct.c_char_p), ("metric", ct.c_char_p), ("verdict", ct.c_char_p),
                ("value", ct.c_double), ("threshold", ct.c_double)]


class DecisionResult(ct.Structure):
    _fields_ = [("status", ct.c_char * 24), ("advice", ct.c_char * 256),
                ("reason", ct.c_char * 2048), ("trigger_count", ct.c_uint),
                ("triggers", Trigger * 5)]


class Vital(ct.Structure):
    _fields_ = [("value", ct.c_double * 4), ("present", ct.c_int * 4)]


class VitalResult(ct.Structure):
    _fields_ = [("present", ct.c_int), ("hr_bpm", ct.c_double),
                ("hr_conf", ct.c_double), ("reject", ct.c_char_p)]


class MeasureConfig(ct.Structure):
    _fields_ = [("behavior", BehaviorConfig), ("quality", QualityConfig),
                ("decision", DecisionConfig)]


class MeasureInput(ct.Structure):
    _fields_ = [("frame_id", ct.c_uint64), ("ts", ct.c_double), ("mean_luma", ct.c_double),
                ("motion_q16", ct.c_uint32), ("motion_valid", ct.c_int),
                ("observation", Observation), ("face", Face)]


class MeasureResult(ct.Structure):
    _fields_ = [("frame_id", ct.c_uint64), ("ts", ct.c_double),
                ("behavior", BehaviorResult), ("quality", QualityResult),
                ("decision", DecisionResult), ("vital", Vital)]


class ContractFace(ct.Structure):
    _fields_ = [("bbox", ct.c_int * 4), ("yaw", ct.c_double), ("pitch", ct.c_double),
                ("roll", ct.c_double), ("visible", ct.c_double)]


class VitalConfig(ct.Structure):
    _fields_ = [(name, ct.c_double) for name in (
        "fps", "fs_tol", "window_seconds", "color_window_seconds",
        "f_lo", "f_hi", "harmonic_ratio_min", "max_gap_s", "jitter_ratio_max",
        "recovery_s")]
    _fields_ += [("coeffs", ct.POINTER(ct.c_short)), ("taps", ct.c_int), ("shift", ct.c_int),
                 ("recovery_samples", ct.c_int)]


def _compiler() -> list[str]:
    compiler = os.environ.get("CC") or shutil.which("gcc") or shutil.which("cc")
    if not compiler:
        raise RuntimeError("no C compiler found (set CC)")
    return compiler.split()


def build(destination: Path | None = None) -> Path:
    """Compile the native core; reuse a cached build when sources are unchanged."""
    destination = Path(destination or ROOT / ".tmp/autonomy/native")
    destination.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    for name in SOURCES:
        digest.update(name.encode())
        digest.update((SOURCE / name).read_bytes())
    digest.update((SOURCE / "measure.h").read_bytes())
    digest.update((SOURCE / "vital.h").read_bytes())
    digest.update((SOURCE / "contract_json.h").read_bytes())
    suffix = ".dll" if os.name == "nt" else ".so"
    library = destination / ("vgautonomy" + suffix)
    stamp = destination / "build.sha256"
    if library.exists() and stamp.exists() and stamp.read_text().strip() == digest.hexdigest():
        return library
    args = _compiler() + ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared"]
    args += ["-static-libgcc"] if os.name == "nt" else ["-fPIC"]
    args += ["-I", str(SOURCE)]
    args += [str(SOURCE / name) for name in SOURCES]
    args += ["-lm", "-o", str(library)]
    subprocess.run(args, check=True, capture_output=True, text=True)
    stamp.write_text(digest.hexdigest())
    return library


class NativeCore:
    """One loaded library plus the create/update/contract entry points."""

    def __init__(self, library: Path | None = None):
        self.lib = ct.CDLL(str(library or build()))
        lib = self.lib
        lib.vg_measure_create.restype = ct.c_void_p
        lib.vg_measure_create.argtypes = [ct.POINTER(MeasureConfig)]
        lib.vg_measure_destroy.argtypes = [ct.c_void_p]
        lib.vg_measure_update.restype = ct.c_int
        lib.vg_measure_update.argtypes = [ct.c_void_p, ct.POINTER(MeasureInput),
                                          ct.POINTER(MeasureResult)]
        lib.vg_vital_create.restype = ct.c_void_p
        lib.vg_vital_create.argtypes = [ct.POINTER(VitalConfig)]
        lib.vg_vital_destroy.argtypes = [ct.c_void_p]
        lib.vg_vital_push_rgb.restype = ct.c_int
        lib.vg_vital_push_rgb.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint32),
                                          ct.c_uint32, ct.c_uint64, ct.c_double]
        lib.vg_vital_reset.argtypes = [ct.c_void_p]
        lib.vg_vital_invalidate.argtypes = [ct.c_void_p, ct.c_char_p, ct.c_double, ct.c_int]
        lib.vg_vital_estimate.restype = ct.c_int
        lib.vg_vital_estimate.argtypes = [ct.c_void_p, ct.POINTER(VitalResult)]
        lib.vg_decision_create.restype = ct.c_void_p
        lib.vg_decision_create.argtypes = [ct.POINTER(DecisionConfig)]
        lib.vg_decision_destroy.argtypes = [ct.c_void_p]
        # Header order is (engine, quality, input, face, status, output).
        lib.vg_decision_gate_vitals.argtypes = [ct.c_void_p, ct.c_double,
                                                ct.POINTER(Vital), ct.POINTER(Face),
                                                ct.c_char_p, ct.POINTER(Vital)]
        lib.vg_contract_line.restype = ct.c_int
        lib.vg_contract_line.argtypes = [ct.c_char_p, ct.c_size_t,
                                         ct.POINTER(MeasureResult), ct.POINTER(ContractFace),
                                         ct.POINTER(Vital)]

    def contract_line(self, result: MeasureResult, face: ContractFace,
                      vital: Vital | None) -> str:
        buffer = ct.create_string_buffer(8192)
        code = self.lib.vg_contract_line(buffer, len(buffer), ct.byref(result),
                                         ct.byref(face), ct.byref(vital) if vital else None)
        if code != 0:
            raise RuntimeError("vg_contract_line failed")
        return buffer.value.decode("utf-8")


def measure_config(cfg: dict) -> MeasureConfig:
    weights = cfg["quality_weights"]
    decision = DecisionConfig(
        float(cfg["quality_min_score"]), float(cfg["face_visible_min"]),
        float(cfg["pose_yaw_max_deg"]), float(cfg["pose_pitch_max_deg"]),
        float(cfg["perclos_warning"]), float(cfg["fatigue_blink_rate_low"]),
        float(cfg["vital_require_quality"]),
        int(cfg["fatigue_yawn_count"]), int(cfg["fatigue_long_close_count"]),
        int(cfg["pose_hold_frames"]))
    return MeasureConfig(
        BehaviorConfig(float(cfg["ear_close_threshold"]), float(cfg["min_close_ms"]),
                       float(cfg["window_seconds"]), float(cfg["long_close_ms"]),
                       float(cfg["mar_threshold"]), float(cfg["yawn_min_duration_ms"])),
        QualityConfig(float(cfg["light_target"]), float(cfg["motion_score_gain"]),
                      float(weights["light"]), float(weights["motion"]), float(weights["face"])),
        decision)


def vital_config(cfg: dict, coeffs, shift: int) -> tuple[VitalConfig, ct.Array]:
    array = (ct.c_short * len(coeffs))(*coeffs)
    config = VitalConfig(
        float(cfg["fps_nominal"]), float(cfg["rppg_fs_tol"]),
        float(cfg["window_seconds"]), float(cfg["rppg_color_window_seconds"]),
        float(cfg["hr_band_hz"][0]), float(cfg["hr_band_hz"][1]),
        float(cfg["rppg_harmonic_ratio_min"]),
        float(cfg["rppg_max_gap_ms"]) / 1000.0, float(cfg["realtime_jitter_ratio_max"]),
        float(cfg["rppg_recovery_ms"]) / 1000.0,
        array, len(coeffs), shift, int(cfg["rppg_recovery_samples"]))
    return config, array


def observation(left, right, mouth, visible: float) -> Observation:
    """Build the C observation from six-point eyes and a four-point mouth."""
    obs = Observation()
    obs.visible = float(visible)
    obs.eyes_present = 1 if (left or right) else 0
    if left:
        obs.left.present = 1
        for index, key in enumerate(("p1", "p2", "p3", "p4", "p5", "p6")):
            obs.left.p[index].x, obs.left.p[index].y = left[key]
    if right:
        obs.right.present = 1
        for index, key in enumerate(("p1", "p2", "p3", "p4", "p5", "p6")):
            obs.right.p[index].x, obs.right.p[index].y = right[key]
    if mouth:
        obs.mouth.present = 1
        # Nested structs come back as copies, so write each one back explicitly.
        for name in ("left", "right", "top", "bottom"):
            x, y = mouth[name]
            target = getattr(obs.mouth, name)
            target.x, target.y = x, y
            setattr(obs.mouth, name, target)
    return obs
