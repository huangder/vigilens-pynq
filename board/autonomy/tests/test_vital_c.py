"""C/Python parity for the native rPPG estimator.

The C library is compiled on the fly with the local compiler and driven through
ctypes; the Python side is the frozen reference `backend/rppg_color.PosRppg`.
A pass means the two produce the same accept/refuse decision and the same HR on
a synthetic RGB stream, on both the frame-id and the source-timestamp time base.
It says nothing about real-person accuracy.
"""
from __future__ import annotations

import ctypes as ct
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402
from backend.rppg_color import PosRppg  # noqa: E402
from backend.vital import load_fir_coeffs,GreenRppg,q15_from_roi_sum  # noqa: E402


class Config(ct.Structure):
    _fields_ = [("fps", ct.c_double), ("fs_tol", ct.c_double),
                ("window_seconds", ct.c_double), ("color_window_seconds", ct.c_double),
                ("f_lo", ct.c_double), ("f_hi", ct.c_double),
                ("harmonic_ratio_min", ct.c_double),
                ("max_gap_s", ct.c_double), ("jitter_ratio_max", ct.c_double),
                ("recovery_s", ct.c_double),
                ("coeffs", ct.POINTER(ct.c_short)), ("taps", ct.c_int), ("shift", ct.c_int),
                ("recovery_samples", ct.c_int),("method",ct.c_int),
                ("short_window_seconds",ct.c_double),("short_window_min_conf",ct.c_double),
                ("history_seconds",ct.c_double),("source_min_hz",ct.c_double),("green_center_before_fir",ct.c_int)]


class Result(ct.Structure):
    _fields_ = [("present", ct.c_int), ("hr_bpm", ct.c_double),
                ("hr_conf", ct.c_double), ("reject", ct.c_char_p)]


def build(destination: Path) -> Path:
    compiler = os.environ.get("CC") or shutil.which("gcc") or shutil.which("cc")
    if not compiler:
        raise unittest.SkipTest("no C compiler for the vital parity test")
    suffix = ".dll" if os.name == "nt" else ".so"
    library = destination / ("vgvital" + suffix)
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                    "-shared", "-fPIC", "-o", str(library),
                    str(ROOT / "board/autonomy/vital.c"), "-lm"],
                   check=True, capture_output=True, text=True)
    return library


def rgb_stream(frames: int, fps: float, bpm: float, count: int = 1_000):
    """A physiological-looking stream: baseline drift + 1.15 Hz colour pulse."""
    out = []
    for i in range(frames):
        t = i / fps
        pulse = 0.012 * math.sin(2 * math.pi * (bpm / 60.0) * t)
        drift = 0.004 * math.sin(2 * math.pi * 0.05 * t)
        base = (122.0, 118.0, 112.0)
        means = [base[0] * (1 + pulse + 0.3 * drift),
                 base[1] * (1 - pulse - 0.2 * drift),
                 base[2] * (1 + 0.5 * pulse)]
        out.append(([int(round(m * count)) for m in means], count))
    return out


def timestamp(i: int, fps: float = 30.0) -> float:
    """Deterministic jitter around the nominal grid: +-2 ms, below the config gap."""
    return i / fps + 0.002 * math.sin(i * 0.7)


def reference(stream, cfg, coeffs, shift, checkpoints, timestamps=False):
    estimator = PosRppg(fps=30.0, window_seconds=float(cfg["window_seconds"]),
                        color_window_seconds=float(cfg["rppg_color_window_seconds"]),
                        hr_band_hz=cfg["hr_band_hz"], coeffs=coeffs, shift=shift,
                        fs_tol=float(cfg["rppg_fs_tol"]),
                        jitter_ratio_max=float(cfg["realtime_jitter_ratio_max"]),
                        max_gap_ms=float(cfg["rppg_max_gap_ms"]))
    results = {}
    for index, (sums, count) in enumerate(stream):
        ts = timestamp(index) if timestamps else None
        estimator.push_rgb(sums, count, index, ts=ts)
        if index in checkpoints:
            row = estimator.estimate()
            results[index] = (bool(row["hr_bpm"] is not None), row["hr_bpm"],
                              row["hr_conf"], estimator.last_reject)
    return results


class VitalParity(unittest.TestCase):
    def test_expired_samples_do_not_fill_startup_including_during_pause(self):
        from backend.rppg_color import make_rppg
        for method in (0, 1):
            with self.subTest(method=method):
                handle = self.make(method=method, window_seconds=2.5, history_seconds=10)
                ref = make_rppg({**self.cfg, 'rppg_method': 'green' if method else 'pos'})
                sums, count = [122000, 118000, 112000], 1000
                for i in range(ref.need):
                    ts = 1 + i
                    self.assertEqual(self.push(handle, sums, count, i, ts), 0)
                    if method: ref.push(q15_from_roi_sum(sums[1], count), i, ts)
                    else: ref.push_rgb(sums, count, i, ts)
                    self.assertEqual(self.lib.vg_vital_samples(handle), ref.samples)
                    self.assertEqual(self.lib.vg_vital_history_samples(handle), len(ref._win))
                self.assertLess(ref.samples, ref.need)
                self.assertFalse(self.estimate(handle)[0])
                pause = ref.last_sample_ts + ref.recovery_s / 2
                self.invalidate(handle, 'quality_or_pose', pause, 1)
                ref.invalidate('quality_or_pose', pause, allow_recovery=True)
                self.assertEqual(self.lib.vg_vital_history_samples(handle), len(ref._win))
                self.assertEqual(self.lib.vg_vital_samples(handle), ref.samples)
                self.assertFalse(self.estimate(handle)[0])

    def test_growing_history_short_gap_and_expiry_match_python(self):
        from backend.rppg_color import make_rppg
        for method in (0,1):
            with self.subTest(method=method):
                handle=self.make(method=method,window_seconds=2.5,
                                 history_seconds=10,jitter_ratio=self.cfg['rppg_resample_jitter_ratio_max'])
                ref=make_rppg({**self.cfg,'rppg_method':'green' if method else 'pos'})
                for i,(sums,count) in enumerate(rgb_stream(900,30,69)):
                    if i%120 in (60,61,62):continue
                    ts=1+i/30
                    self.push(handle,sums,count,i,ts)
                    if method:ref.push(q15_from_roi_sum(sums[1],count),i,ts)
                    else:ref.push_rgb(sums,count,i,ts)
                    if i in (74,224,450,899):
                        actual,expected=self.estimate(handle),ref.estimate()
                        self.assertEqual(actual[0],expected['hr_bpm'] is not None)
                        self.assertEqual(self.lib.vg_vital_history_samples(handle),len(ref._win))
                        if actual[0]:
                            self.assertAlmostEqual(actual[1],expected['hr_bpm'],places=6)
                            self.assertAlmostEqual(actual[2],expected['hr_conf'],places=6)
                self.assertGreater(self.lib.vg_vital_history_samples(handle),75)
                self.assertLessEqual(self.lib.vg_vital_history_samples(handle),300)
                pause=1+900/30
                self.invalidate(handle,'quality_or_pose',pause,1)
                ref.invalidate('quality_or_pose',pause,allow_recovery=True)
                self.assertEqual(self.core_samples(handle),75)
                self.assertFalse(self.estimate(handle)[0])
                stream=rgb_stream(906,30,69)
                for i in (903,904,905):
                    sums,count=stream[i];ts=1+i/30
                    self.push(handle,sums,count,i,ts)
                    if method:ref.push(q15_from_roi_sum(sums[1],count),i,ts)
                    else:ref.push_rgb(sums,count,i,ts)
                    actual,expected=self.estimate(handle),ref.estimate()
                    self.assertEqual(actual[0],expected['hr_bpm'] is not None)
                    if i<905:self.assertEqual(actual[3],'recovering')
                    if actual[0]:self.assertAlmostEqual(actual[1],expected['hr_bpm'],places=6)
                self.assertTrue(actual[0],actual[3])
                self.invalidate(handle,'quality_or_pose',ts+.25,1)
                self.assertEqual(self.core_samples(handle),0)
                self.assertFalse(self.estimate(handle)[0])

    def test_75_sample_noise_guard_matches_python_without_relaxing_refusal(self):
        import numpy as np
        cfg={**load_config(),'rppg_window_seconds':2.5}
        ref=GreenRppg.from_config(cfg)
        handle=self.make(method=1,window_seconds=2.5)
        rng=np.random.default_rng(7)
        for i in range(75):
            value=int(max(-32768,min(32767,round(float(rng.normal(0,6000))))))
            ref.push(value,i)
            self.assertEqual(self.push(handle,[120*256,128*256+value,112*256],256,i),0)
        self.assertIsNone(ref.estimate()['hr_bpm'])
        actual=self.estimate(handle)
        self.assertFalse(actual[0])
        self.assertEqual(actual[3],'short_window_low_confidence')
        self.assertEqual(actual[3],ref.last_reject)
    def test_non_nominal_valid_source_rates_match_python(self):
        for method in (0,1):
            for rate in (27.5,28.5,29.95):
                with self.subTest(method=method,rate=rate):
                    handle=self.make(method=method)
                    options=dict(fps=30,window_seconds=float(self.cfg['window_seconds']),hr_band_hz=self.cfg['hr_band_hz'],coeffs=self.coeffs,shift=self.shift,
                        fs_tol=float(self.cfg['rppg_fs_tol']),jitter_ratio_max=float(self.cfg['realtime_jitter_ratio_max']),max_gap_ms=float(self.cfg['rppg_max_gap_ms']))
                    ref=GreenRppg(**options) if method else PosRppg(**options,color_window_seconds=float(self.cfg['rppg_color_window_seconds']))
                    for i,(sums,count) in enumerate(rgb_stream(1200,rate,69)):
                        ts=1+i/rate+.001*math.sin(i*.7)
                        if method:ref.push(q15_from_roi_sum(sums[1],count),i,ts=ts)
                        else:ref.push_rgb(sums,count,i,ts=ts)
                        self.assertEqual(self.push(handle,sums,count,i,ts),0)
                    actual=self.estimate(handle);expected=ref.estimate()
                    self.assertTrue(actual[0],actual[3]);self.assertIsNotNone(expected['hr_bpm'],ref.last_reject)
                    self.assertAlmostEqual(actual[1],expected['hr_bpm'],places=6)
                    self.assertAlmostEqual(actual[2],expected['hr_conf'],places=6)
    def test_green_method_matches_python_source_and_frame_id(self):
        for timestamps in (False,True):
            handle=self.make(method=1)
            ref=GreenRppg(fps=30,window_seconds=float(self.cfg['window_seconds']),hr_band_hz=self.cfg['hr_band_hz'],coeffs=self.coeffs,shift=self.shift,
                fs_tol=float(self.cfg['rppg_fs_tol']),jitter_ratio_max=float(self.cfg['realtime_jitter_ratio_max']),max_gap_ms=float(self.cfg['rppg_max_gap_ms']))
            checked=0
            for i,(sums,count) in enumerate(rgb_stream(1200,30,69)):
                ts=timestamp(i) if timestamps else None
                ref.push(q15_from_roi_sum(sums[1],count),i,ts=ts)
                self.assertEqual(self.push(handle,sums,count,i,ts if ts is not None else float('nan')),0)
                if i>=1020 and i%30==0:
                    actual=self.estimate(handle);expected=ref.estimate()
                    self.assertEqual(actual[0],expected['hr_bpm'] is not None)
                    if actual[0]:
                        self.assertAlmostEqual(actual[1],expected['hr_bpm'],places=6)
                        self.assertAlmostEqual(actual[2],expected['hr_conf'],places=6);checked+=1
            self.assertGreater(checked,0)
    @classmethod
    def setUpClass(cls):
        # A loaded Windows DLL cannot be unlinked; keep the folder and let the
        # OS reclaim it instead of failing teardown.
        cls.temp = Path(tempfile.mkdtemp(prefix="vg_vital_"))
        cls.lib = ct.CDLL(str(build(cls.temp)))
        cls.lib.vg_vital_create.restype = ct.c_void_p
        cls.lib.vg_vital_create.argtypes = [ct.POINTER(Config)]
        cls.lib.vg_vital_destroy.argtypes = [ct.c_void_p]
        cls.lib.vg_vital_push_rgb.restype = ct.c_int
        cls.lib.vg_vital_push_rgb.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint32),
                                             ct.c_uint32, ct.c_uint64, ct.c_double]
        cls.lib.vg_vital_estimate.restype = ct.c_int
        cls.lib.vg_vital_estimate.argtypes = [ct.c_void_p, ct.POINTER(Result)]
        cls.lib.vg_vital_invalidate.argtypes = [ct.c_void_p, ct.c_char_p, ct.c_double, ct.c_int]
        cls.lib.vg_vital_samples.argtypes = [ct.c_void_p]
        cls.lib.vg_vital_samples.restype = ct.c_int
        cls.lib.vg_vital_history_samples.argtypes = [ct.c_void_p]
        cls.lib.vg_vital_history_samples.restype = ct.c_int
        cls.cfg = load_config()
        cls.coeffs, cls.shift, cls.fs = load_fir_coeffs()
        cls.array = (ct.c_short * len(cls.coeffs))(*cls.coeffs)

    def make(self, fps=30.0, color_window=None,method=0, window_seconds=None, history_seconds=0, jitter_ratio=None):
        cfg = Config(fps=fps, fs_tol=float(self.cfg["rppg_fs_tol"]),
                     window_seconds=float(self.cfg["window_seconds"] if window_seconds is None else window_seconds),
                     color_window_seconds=float(color_window or self.cfg["rppg_color_window_seconds"]),
                     f_lo=float(self.cfg["hr_band_hz"][0]), f_hi=float(self.cfg["hr_band_hz"][1]),
                     harmonic_ratio_min=float(self.cfg["rppg_harmonic_ratio_min"]),
                     max_gap_s=float(self.cfg["rppg_max_gap_ms"]) / 1000.0,
                     jitter_ratio_max=float(self.cfg["realtime_jitter_ratio_max"] if jitter_ratio is None else jitter_ratio),
                     recovery_s=float(self.cfg["rppg_recovery_ms"]) / 1000.0,
                     coeffs=self.array, taps=len(self.coeffs), shift=self.shift,
                     recovery_samples=int(self.cfg["rppg_recovery_samples"]),method=method,
                     short_window_seconds=float(self.cfg['rppg_short_window_seconds']),
                     short_window_min_conf=float(self.cfg['rppg_short_window_min_conf']),history_seconds=history_seconds)
        handle = self.lib.vg_vital_create(ct.byref(cfg))
        self.assertTrue(handle, "vg_vital_create returned NULL")
        self.addCleanup(self.lib.vg_vital_destroy, handle)
        return handle

    def push(self, handle, sums, count, frame_id, ts=float("nan")):
        buffer = (ct.c_uint32 * 3)(*sums)
        return self.lib.vg_vital_push_rgb(handle, buffer, count, frame_id, ts)

    def estimate(self, handle):
        result = Result()
        code = self.lib.vg_vital_estimate(handle, ct.byref(result))
        self.assertEqual(code, 0)
        reject = result.reject.decode() if result.reject else None
        return (bool(result.present), result.hr_bpm, result.hr_conf, reject)

    def compare(self, timestamps: bool):
        stream = rgb_stream(1_200, 30.0, 69.0)
        checkpoints = set(range(960, 1_200, 30))
        expected = reference(stream, self.cfg, self.coeffs, self.shift, checkpoints, timestamps)
        handle = self.make()
        accepted = 0
        for index, (sums, count) in enumerate(stream):
            ts = timestamp(index) if timestamps else float("nan")
            self.assertEqual(self.push(handle, sums, count, index, ts), 0)
            if index in checkpoints:
                got = self.estimate(handle)
                want = expected[index]
                self.assertEqual(got[0], want[0], f"frame {index}: {got} vs {want}")
                self.assertEqual(got[3], want[3], f"frame {index}: {got} vs {want}")
                if want[0]:
                    accepted += 1
                    self.assertAlmostEqual(got[1], want[1], delta=0.11, msg=f"frame {index}")
                    self.assertAlmostEqual(got[2], want[2], delta=1e-4, msg=f"frame {index}")
        self.assertGreater(accepted, 0, "no checkpoint accepted; parity test is vacuous")

    def test_matches_python_on_frame_id_timebase(self):
        self.compare(timestamps=False)

    def test_matches_python_on_source_timestamps(self):
        self.compare(timestamps=True)

    def test_window_not_full_reject(self):
        handle = self.make()
        for index in range(10):
            sums, count = rgb_stream(1, 30.0, 69.0)[0]
            self.push(handle, sums, count, index)
        self.assertEqual(self.estimate(handle)[3], "window_not_full")

    def test_exported_short_window_matches_production_python_and_keeps_guards(self):
        from board.autonomy.export_vital_config import build as exported
        from backend.rppg_color import make_rppg
        values = exported(ROOT / "config.yaml")
        self.assertEqual(self.cfg["window_seconds"], 30)
        self.assertEqual(values["window_seconds"], 75/30)
        need = round(values["window_seconds"] * values["fps"])
        self.assertEqual(need, 75)
        for method in (0, 1):
            with self.subTest(method=method):
                handle = self.make(method=method, window_seconds=values["window_seconds"],
                                   history_seconds=values['history_seconds'],jitter_ratio=values['jitter_ratio_max'])
                ref = make_rppg({**self.cfg, "rppg_method": "green" if method else "pos"})
                self.assertEqual(ref.need, need)
                for i, (sums, count) in enumerate(rgb_stream(need + 150, 30, 69)):
                    ts = 1 + timestamp(i)
                    self.assertEqual(self.push(handle, sums, count, i, ts), 0)
                    if method:
                        ref.push(q15_from_roi_sum(sums[1], count), i, ts=ts)
                    else:
                        ref.push_rgb(sums, count, i, ts=ts)
                    if i == need - 2:
                        self.assertEqual(self.estimate(handle)[3], "window_not_full")
                        self.assertIsNone(ref.estimate()["hr_bpm"])
                actual, expected = self.estimate(handle), ref.estimate()
                self.assertTrue(actual[0], actual[3])
                self.assertIsNotNone(expected["hr_bpm"], ref.last_reject)
                self.assertAlmostEqual(actual[1], expected["hr_bpm"], places=6)
                self.assertAlmostEqual(actual[2], expected["hr_conf"], places=6)
                self.invalidate(handle, "vitals_blocked", ts + .2, 0)
                self.assertEqual(self.core_samples(handle), 0)
                self.assertFalse(self.estimate(handle)[0])
                slow = self.make(method=method, window_seconds=values["window_seconds"])
                for i, (sums, count) in enumerate(rgb_stream(need + 150, 24, 69)):
                    self.push(slow, sums, count, i, 1 + i / 24)
                self.assertEqual(self.estimate(slow)[3], "sampling_too_slow")

    def test_nonuniform_frame_ids_are_rejected(self):
        handle = self.make()
        stream = rgb_stream(1_000, 30.0, 69.0)
        for index, (sums, count) in enumerate(stream):
            self.push(handle, sums, count, index * 2)
        self.assertEqual(self.estimate(handle)[3], "nonuniform_frame_ids")

    def test_sampling_gap_is_rejected(self):
        handle = self.make()
        stream = rgb_stream(1_000, 30.0, 69.0)
        for index, (sums, count) in enumerate(stream):
            # Shift the tail by 15 frames so the series stays monotonic and the
            # only defect is a single 0.53 s hole at frame 950.
            ts = (index + (15 if index >= 950 else 0)) / 30.0
            self.push(handle, sums, count, index, ts)
        self.assertEqual(self.estimate(handle)[3], "sampling_gap")

    def test_invalid_roi_sums_are_rejected(self):
        handle = self.make()
        self.assertEqual(self.push(handle, [1, 2, 3], 0, 0), -1)
        self.assertEqual(self.push(handle, [256, 0, 0], 1, 0), -1)
        self.assertEqual(self.push(handle, [255, 0, 0], 1, 0), 0)

    def test_feed_rate_below_design_is_named(self):
        """A feed slower than fps*(1-fs_tol) must name the rate, not the window.

        The frozen FIR is designed for 30 Hz; the board analysis loop runs near
        1.7 Hz, so the estimator has to refuse explicitly instead of waiting for
        a window that can never fill.
        """
        for rate, expected in ((30.0, None), (24.0, "sampling_too_slow")):
            handle = self.make()
            stream = rgb_stream(1_200, rate, 72.0)
            for index, (sums, count) in enumerate(stream):
                self.assertEqual(self.push(handle, sums, count, index, index / rate), 0)
            present, hr, _conf, reject = self.estimate(handle)
            if expected is None:
                self.assertTrue(present, f"{rate} Hz should estimate, got {reject}")
                self.assertAlmostEqual(hr, 72.0, delta=1.5)
            else:
                self.assertFalse(present, f"{rate} Hz must refuse")
                self.assertEqual(reject, expected, f"{rate} Hz")
    def test_flat_signal_has_no_band_energy(self):
        handle = self.make()
        for index in range(1_000):
            self.push(handle, [120_000, 120_000, 120_000], 1_000, index)
        self.assertIn(self.estimate(handle)[3], ("color_projection_invalid", "no_band_energy", "no_dominant_peak"))


    def invalidate(self, handle, reason, ts, allow):
        self.lib.vg_vital_invalidate(handle, reason.encode(), ts, allow)

    def test_recovery_matches_python(self):
        estimator = PosRppg(fps=30.0, window_seconds=float(self.cfg["window_seconds"]),
                            color_window_seconds=float(self.cfg["rppg_color_window_seconds"]),
                            hr_band_hz=self.cfg["hr_band_hz"], coeffs=self.coeffs,
                            shift=self.shift, fs_tol=float(self.cfg["rppg_fs_tol"]),
                            jitter_ratio_max=float(self.cfg["realtime_jitter_ratio_max"]),
                            max_gap_ms=float(self.cfg["rppg_max_gap_ms"]),
                            recovery_ms=float(self.cfg["rppg_recovery_ms"]),
                            recovery_samples=int(self.cfg["rppg_recovery_samples"]))
        handle = self.make()
        stream = rgb_stream(1_000, 30.0, 69.0)
        for index, (sums, count) in enumerate(stream):
            ts = index / 30.0
            self.push(handle, sums, count, index, ts)
            estimator.push_rgb(sums, count, index, ts=ts)

        # A short quality/pose pause: keep the window but block estimation.
        ts = 999 / 30.0 + 0.05
        self.invalidate(handle, "adjust_posture", ts, 1)
        estimator.invalidate("adjust_posture", ts, allow_recovery=True)
        row = estimator.estimate()
        self.assertIsNone(row["hr_bpm"])
        self.assertEqual(estimator.last_reject, "adjust_posture")
        self.assertEqual(self.estimate(handle)[3], "adjust_posture")

        # Each push consumes one recovery sample; only then may estimation resume.
        for k in range(3):
            ts2 = (1_000 + k) / 30.0
            sums, count = rgb_stream(1, 30.0, 69.0)[0]
            self.push(handle, sums, count, 1_000 + k, ts2)
            estimator.push_rgb(sums, count, 1_000 + k, ts=ts2)
            row = estimator.estimate()
            got = self.estimate(handle)
            self.assertEqual(got[3], estimator.last_reject, f"recovery push {k}")
            if row["hr_bpm"] is not None:
                self.assertTrue(got[0], f"push {k}: python accepted but C refused")
                self.assertAlmostEqual(got[1], row["hr_bpm"], delta=0.11)

        # A long gap clears the window rather than resuming an old one.
        long_ts = 1_002 / 30.0 + 0.5
        self.invalidate(handle, "adjust_posture", long_ts, 1)
        estimator.invalidate("adjust_posture", long_ts, allow_recovery=True)
        estimator.estimate()
        self.assertEqual(self.estimate(handle)[3], estimator.last_reject)
        self.assertEqual(self.core_samples(handle), 0)

    def core_samples(self, handle):
        self.lib.vg_vital_samples.restype = ct.c_int
        self.lib.vg_vital_samples.argtypes = [ct.c_void_p]
        return self.lib.vg_vital_samples(handle)


if __name__ == "__main__":
    unittest.main()
