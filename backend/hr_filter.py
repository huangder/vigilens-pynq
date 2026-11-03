"""Bounded post-estimate stability filter; never repairs an invalid spectrum."""
from collections import deque
from dataclasses import dataclass
import math
import statistics


@dataclass(frozen=True)
class HrFilterConfig:
    enabled: bool = False
    window: int = 0
    alpha: float = 0.0
    interval_s: float = 0.0
    max_gap_s: float = 0.0
    max_spread_bpm: float = 0.0
    startup_samples: int = 0
    min_bpm: float = 0.0

    @classmethod
    def from_config(cls, cfg):
        minimum = float(cfg.get("hr_report_min_bpm", 0))
        if not math.isfinite(minimum) or minimum < 0:
            raise ValueError("invalid HR report minimum")
        enabled = cfg.get("hr_stabilizer_enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("hr_stabilizer_enabled must be boolean")
        if not enabled:
            return cls(min_bpm=minimum)
        window = cfg["hr_stabilizer_window"]
        startup = cfg.get("hr_stabilizer_startup_samples", window)
        values = {name: float(cfg["hr_stabilizer_" + name]) for name in (
            "alpha", "interval_s", "max_gap_s", "max_spread_bpm")}
        if (not isinstance(window, int) or isinstance(window, bool) or window < 3 or window > 9 or window % 2 != 1
                or not isinstance(startup, int) or isinstance(startup, bool) or not 1 <= startup <= window
                or not all(math.isfinite(v) for v in values.values())
                or not 0 < values["alpha"] <= 1 or values["interval_s"] <= 0
                or values["max_gap_s"] < values["interval_s"] or values["max_spread_bpm"] <= 0):
            raise ValueError("invalid HR stabilizer configuration")
        return cls(True, window, **values, startup_samples=startup, min_bpm=minimum)


class HrStabilizer:
    def __init__(self, config: HrFilterConfig):
        self.cfg = config
        self.reset()

    def reset(self):
        self.values = deque(maxlen=max(1, self.cfg.window))
        self.last_ts = None
        self.last_seen = None
        self.ema = None
        self.value = self.conf = None
        self.reason = "warming"

    def pause(self, ts):
        """Hide the current value while retaining bounded internal evidence.

        A pause never advances last_ts: repeated failures cannot keep old votes
        alive. Resuming still needs a fresh accepted estimate at interval_s.
        Hard losses must call reset(), not this method.
        """
        if (not self.cfg.enabled or ts is None or not math.isfinite(ts)
                or self.last_ts is None
                or (self.last_seen is not None and ts < self.last_seen)
                or ts - self.last_ts > self.cfg.max_gap_s):
            self.reset()
            return False
        self.last_seen = ts
        self.value = self.conf = None
        self.reason = "paused"
        return True

    def update(self, bpm, conf, ts):
        if bpm is not None and math.isfinite(bpm) and bpm < self.cfg.min_bpm:
            self.reset(); self.reason = "below_report_min"
            return None, None
        if not self.cfg.enabled:
            self.value, self.conf = bpm, conf
            self.reason = "disabled"
            return bpm, conf
        if (bpm is None or conf is None or ts is None or not all(math.isfinite(v) for v in (bpm, conf, ts))
                or bpm <= 0 or not 0 <= conf <= 1):
            self.reset(); self.reason = "invalid_input"
            return None, None
        if self.last_seen is not None and ts < self.last_seen:
            self.reset(); self.reason = "invalid_time"
            return None, None
        self.last_seen = ts
        if self.last_ts is not None and ts - self.last_ts > self.cfg.max_gap_s:
            self.reset(); self.last_seen = ts
        if self.last_ts is not None and ts - self.last_ts < self.cfg.interval_s:
            # Duplicate endpoint / high-rate caller cannot supply a new vote.
            if self.conf is not None:
                self.conf = min(conf, self.conf)
            return self.value, self.conf
        self.last_ts = ts
        self.values.append((bpm, conf))
        if len(self.values) < (self.cfg.startup_samples or self.cfg.window):
            self.reason = "warming"
            return None, None
        values = [v[0] for v in self.values]
        if max(values) - min(values) > self.cfg.max_spread_bpm:
            self.ema = self.value = self.conf = None
            self.reason = "unstable"
            return None, None
        median = statistics.median(values)
        self.ema = median if self.ema is None else self.ema + self.cfg.alpha * conf * (median - self.ema)
        self.value = math.floor(self.ema * 10 + .5) / 10
        self.conf = min(v[1] for v in self.values)
        self.reason = "accepted"
        return self.value, self.conf
