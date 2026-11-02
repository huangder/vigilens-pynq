"""Cap a live stream using real time slots, without padding or retiming frames."""
from __future__ import annotations

import math
from typing import Iterable, Iterator

try:
    from .capture import Frame
except ImportError:  # direct script entry
    from capture import Frame


class LiveSampler:
    """Keep one real frame per slot; slow sources leave empty slots.

    This is a rate cap, not interpolation or a claim of uniform sampling.
    Frame timestamps are preserved and output frame IDs are consecutive.
    """
    def __init__(self, frames: Iterable[Frame], *, fps: float,
                 limit: int | None = None, seconds: float | None = None):
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("sampling fps must be finite and positive")
        if limit is not None and limit <= 0:
            raise ValueError("sampling limit must be positive")
        if seconds is not None and (not math.isfinite(seconds) or seconds <= 0):
            raise ValueError("sampling seconds must be finite and positive")
        self.frames = frames
        self.fps, self.limit, self.seconds = fps, limit, seconds
        self.seen = self.kept = self.dropped = self.empty_slots = 0
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self.last_kept_ts: float | None = None
        self._slot = -1

    def __iter__(self) -> Iterator[Frame]:
        source = iter(self.frames)
        try:
            for frame in source:
                ts = float(frame.ts)
                if not math.isfinite(ts) or (self.last_ts is not None and ts <= self.last_ts):
                    raise ValueError("live capture timestamps must be finite and strictly increasing")
                if self.first_ts is None:
                    self.first_ts = ts
                elapsed = ts - self.first_ts
                if self.seconds is not None and elapsed >= self.seconds:
                    break
                self.last_ts = ts
                self.seen += 1
                # Numeric rounding only, to retain exact frame boundaries.
                slot = math.floor(elapsed * self.fps + 1e-6)
                if slot <= self._slot:
                    self.dropped += 1
                    continue
                self.empty_slots += max(0, slot - self._slot - 1)
                self._slot = slot
                self.last_kept_ts = ts
                fid = self.kept
                self.kept += 1
                yield Frame(frame_id=fid, ts=ts, image=frame.image)
                if self.limit is not None and self.kept >= self.limit:
                    break
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                close()

    def stats(self) -> dict:
        span = (self.last_kept_ts - self.first_ts
                if self.first_ts is not None and self.last_kept_ts is not None else 0.0)
        return {"mode": "source_timestamp_slots", "target_fps": self.fps,
                "input_frames": self.seen, "output_frames": self.kept,
                "dropped_frames": self.dropped, "empty_slots": self.empty_slots,
                "source_span_s": span,
                "output_fps": (self.kept - 1) / span if span > 0 else None,
                "native_camera_mode_verified": False}
