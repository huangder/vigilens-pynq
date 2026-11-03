"""RGB POS extraction, independently implemented from Wang et al. (2017).

References and differences: docs/52_RGB_POS心率增强_20261030.md.
Only contract 4.6 step 1 changes. The shared engine still enforces time,
quality, Q1.15, FIR, spectral dominance and harmonic ambiguity guards.
"""
from __future__ import annotations

from collections import deque
import math
import numpy as np

try:
    from .vital import CENTER, SCALE, GreenRppg, fir_process, q15_from_roi_sum
except ImportError:
    from vital import CENTER, SCALE, GreenRppg, fir_process, q15_from_roi_sum


def pos_waveform(rgb, window_samples: int) -> np.ndarray:
    """Project uniform-time RGB means (N,3) with averaged window overlaps.

    Include every complete local window, including the latest input sample.
    Singular windows supply no support. Unsupported samples remain NaN.
    """
    colors = np.asarray(rgb, dtype=np.float64)
    if (colors.ndim != 2 or colors.shape[1] != 3 or window_samples < 2
            or len(colors) < window_samples or not np.isfinite(colors).all()
            or np.any(colors < 0) or np.any(colors > 255)):
        raise ValueError("POS requires finite RGB means in [0,255] and a full local window")
    blocks = np.lib.stride_tricks.sliding_window_view(colors, window_samples, axis=0)
    means = blocks.mean(axis=-1, keepdims=True)
    eps = np.finfo(np.float64).eps
    valid = (means[..., 0] > eps).all(axis=1)
    normalized = np.divide(blocks, means, out=np.zeros_like(blocks), where=means > eps)
    chroma = normalized[:, 1] - normalized[:, 2]
    orthogonal = normalized[:, 1] + normalized[:, 2] - 2 * normalized[:, 0]
    denominator = orthogonal.std(axis=1)
    valid &= denominator > eps
    ratio = np.divide(chroma.std(axis=1), denominator,
                      out=np.zeros_like(denominator), where=valid)
    pulses = chroma + ratio[:, None] * orthogonal
    pulses -= pulses.mean(axis=1, keepdims=True)
    pulses[~valid] = 0
    accumulated = np.zeros(len(colors), dtype=np.float64)
    support = np.zeros(len(colors), dtype=np.int64)
    # Vectorize across windows; loop only over the short local window's lag.
    for lag in range(window_samples):
        accumulated[lag:lag + len(blocks)] += pulses[:, lag]
        support[lag:lag + len(blocks)] += valid
    return np.divide(accumulated, support, out=np.full(len(colors), np.nan), where=support > 0)


def pos_q15(pulse) -> np.ndarray:
    """Equivalent mean=128*(1+POS), then frozen half-away((mean-128)*256).

    This is a defined scalar input, not measured green intensity. Refuse range
    violations instead of silently clipping an artificial periodic waveform.
    """
    equivalent_mean = CENTER * (1 + np.asarray(pulse, dtype=np.float64))
    if (not np.isfinite(equivalent_mean).all()
            or np.any(equivalent_mean < 0) or np.any(equivalent_mean > 255)):
        raise ValueError("POS scalar input unsupported or outside the Q1.15 mean range")
    value = (equivalent_mean - CENTER) * SCALE
    return np.where(value >= 0, np.floor(value + .5), np.ceil(value - .5)).astype(np.int64)


class PosRppg(GreenRppg):
    """Interpolate captured RGB before temporal projection, retaining warmup."""
    needs_rgb = True

    def __init__(self, *, color_window_seconds: float, **kwargs):
        fps = float(kwargs["fps"])
        if not math.isfinite(color_window_seconds) or color_window_seconds <= 0:
            raise ValueError("POS window must be finite and positive")
        self.color_window_samples = math.ceil(color_window_seconds * fps)
        if self.color_window_samples < 2:
            raise ValueError("POS local window needs at least two samples")
        super().__init__(**kwargs)
        if self.color_window_samples > self.analysis_capacity:
            raise ValueError("POS local window cannot exceed the measurement window")

    @property
    def warmup_samples(self) -> int:
        return super().warmup_samples + self.color_window_samples - 1

    @property
    def nominal_history_samples(self) -> int:
        return self.analysis_capacity + self.warmup_samples

    def reset(self, reason: str = "manual") -> None:
        super().reset(reason)
        self._rgb = deque()

    def push(self, q15, frame_id, ts=None):
        raise ValueError("POS requires push_rgb; a green scalar cannot substitute for RGB")

    def push_rgb(self, sums, count: int, frame_id: int, ts: float | None = None) -> None:
        if count <= 0 or len(sums) != 3 or any(not 0 <= s <= 255 * count for s in sums):
            raise ValueError("invalid RGB ROI sums/count")
        super().push(q15_from_roi_sum(sums[1], count), frame_id, ts)
        self._rgb.append(tuple(float(s) / count for s in sums))
        while len(self._rgb) > len(self._raw):
            self._rgb.popleft()

    def _color_filtered(self, colors, count):
        if len(colors) < self.color_window_samples:
            self._signal_error = "color_window_not_full"
            return None
        try:
            waveform = pos_waveform(colors, self.color_window_samples)
            if not np.isfinite(waveform).all():
                self._signal_error = "color_projection_invalid"
                return None
            quantized = pos_q15(waveform)
        except ValueError:
            self._signal_error = "color_signal_range"
            return None
        filtered, saturated, _ = fir_process(quantized, self.coeffs, self.shift)
        if saturated:
            self._signal_error = "color_fir_saturation"
            return None
        self.last_uniform_samples = count
        return np.asarray(filtered[-count:], dtype=np.float64)

    def _uniform_filtered(self, n: int):
        context = self._uniform_context(n)
        if context is None:
            return None
        first, times, grid, count = context
        colors = np.asarray(list(self._rgb)[first:], dtype=np.float64)
        sampled = np.column_stack([np.interp(grid, times, colors[:, c]) for c in range(3)])
        self.resample_calls += 1
        return self._color_filtered(sampled, count)

    def _nominal_filtered(self, n: int):
        if np.any(np.diff(self._fid) != 1):
            self._signal_error = "nonuniform_frame_ids"
            return None
        return self._color_filtered(list(self._rgb), n)

    def stats(self) -> dict:
        return {**super().stats(), "method": "pos", "color_window_samples": self.color_window_samples,
                "rgb_history_samples": len(self._rgb)}


def make_rppg(cfg, fps: float | None = None) -> GreenRppg:
    """Production method selection; preserve the existing scalar baseline API."""
    try:
        from .config import rppg_window_seconds
    except ImportError:
        from config import rppg_window_seconds
    method = cfg["rppg_method"]
    if method == "green":
        return GreenRppg.from_config(cfg, fps=fps)
    if method != "pos":
        raise ValueError(f"unsupported rppg_method: {method}")
    return PosRppg(fps=float(fps if fps is not None else cfg["fps_nominal"]),
                   window_seconds=rppg_window_seconds(cfg), hr_band_hz=cfg["hr_band_hz"],
                   fs_tol=float(cfg["rppg_fs_tol"]), jitter_ratio_max=float(cfg.get("rppg_resample_jitter_ratio_max",cfg["realtime_jitter_ratio_max"])),
                   max_gap_ms=float(cfg["rppg_max_gap_ms"]), recovery_ms=float(cfg["rppg_recovery_ms"]),
                   recovery_samples=int(cfg["rppg_recovery_samples"]),
                   harmonic_ratio_min=float(cfg["rppg_harmonic_ratio_min"]),
                   color_window_seconds=float(cfg["rppg_color_window_seconds"]),
                   short_window_seconds=cfg.get('rppg_short_window_seconds'),
                   short_window_min_conf=cfg.get('rppg_short_window_min_conf'),
                   history_seconds=float(cfg.get('rppg_history_seconds',0)),
                   source_min_hz=float(cfg.get('rppg_source_min_hz',0)))
