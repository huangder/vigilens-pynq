"""PC-side simulation of the *new* board pipeline (S1+S2+S3).

WHY THIS FILE EXISTS
--------------------
The 2026-10-29 board run spent 251 ms per 720p frame: 2.76 MB of raw RGB over a
100 Mbps link (221 ms) plus 68 ms of decode/reference/PL work. That is a
*verification* pipeline, not a runtime one. This module models the replacement
so the design can be measured on a PC before anything is flashed:

  S1  the camera's own MJPEG frame (~55 KB) is forwarded instead of raw RGB
      -> the wire stops being the bottleneck;
  S2  the per-frame CPU reference becomes an audit every N frames;
  S3  capture / decode / PL+send run as three stages with bounded queues
      -> frame time becomes max(decode, PL+send) instead of their sum.

WHAT IS REAL AND WHAT IS EMULATED (read before quoting numbers)
---------------------------------------------------------------
Real: JPEG bytes, the frozen gray formula and motion semantics, the VLP wire
format, the PC decoder, queueing/drops, wall-clock threading behaviour.
Emulated: per-stage *duration* (`decode_ms`/`pl_ms`/`audit_ms` default to the
2026-10-29 measured A9 numbers) and the link rate (`link_mbps`). The simulator
answers "does this architecture reach 30 fps on the measured board budget?",
never "the board is fast/slow". Board numbers must come from board logs.

PL fields (sums, gray size, diff_total, motion_pixels, Q16 ratio, first frame
invalid, strict `>` threshold) follow docs/interface.md and backend/quality.py.
"""
from __future__ import annotations

import json
import math
import queue
import socket
import statistics
import struct
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

WIDTH, HEIGHT = 1280, 720
GRAY_WIDTH, GRAY_HEIGHT = 480, 270
RGB_BYTES = WIDTH * HEIGHT * 3
PROFILE = "720p-3of8"
AUDIT_KEYS = ("sum_r", "sum_g", "sum_b", "diff_total", "motion_pixels", "motion_ratio_q16")


@dataclass
class SimConfig:
    codec: str = "jpeg"                 # jpeg | raw
    pipeline: str = "threaded"          # threaded | serial
    frames: int = 120
    camera_fps: float = 60.0
    decode_ms: float = 31.6             # A9 libjpeg, measured 2026-10-29
    pl_ms: float = 19.9                 # DMA + 3 IP + compare, measured
    audit_ms: float = 16.7              # scalar CPU reference, measured
    audit_every: int = 30               # S2: 0 disables the audit entirely
    link_mbps: float = 100.0            # emulated wire rate for the send stage
    emulate: bool = True                # False = no sleeps, PC upper bound
    motion_threshold: int = 16
    queue_depth: int = 4                # V4L2 buffer count on the board

    def __post_init__(self) -> None:
        if self.codec not in ("jpeg", "raw"):
            raise ValueError("codec must be jpeg or raw")
        if self.pipeline not in ("threaded", "serial"):
            raise ValueError("pipeline must be threaded or serial")
        if not 0 <= self.motion_threshold <= 255:
            raise ValueError("motion_threshold must be 0..255")
        if self.audit_every < 0:
            raise ValueError("audit_every must be >= 0")
        if self.frames < 2:
            raise ValueError("need at least 2 frames")
        if self.queue_depth < 1:
            raise ValueError("queue_depth must be >= 1")
        for name in ("camera_fps", "link_mbps"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("decode_ms", "pl_ms", "audit_ms"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"{name} must be finite and nonnegative")


def gray_720p(rgb: np.ndarray) -> np.ndarray:
    """Frozen 3/8 phase decimation + integer luma, byte-identical to HLS.

    Keep `x % 8 < 3` and `y % 8 < 3`: 1280x720 -> 480x270. Selection is done by
    reshaping into 8-wide groups, which is exactly "first 3 of every 8" and
    avoids building 2 M-element boolean masks per frame.
    """
    if rgb.shape != (HEIGHT, WIDTH, 3):
        raise ValueError(f"gray_720p needs {HEIGHT}x{WIDTH}x3 RGB")
    picked = rgb.reshape(HEIGHT // 8, 8, WIDTH // 8, 8, 3)[:, :3, :, :3, :]
    picked = picked.reshape(GRAY_HEIGHT, GRAY_WIDTH, 3)
    r = picked[:, :, 0].astype(np.uint32)
    g = picked[:, :, 1].astype(np.uint32)
    b = picked[:, :, 2].astype(np.uint32)
    return ((77 * r + 150 * g + 29 * b + 128) >> 8).astype(np.uint8)


def pl_frame(rgb: np.ndarray, prev_gray: np.ndarray | None,
             threshold: int) -> tuple[dict, np.ndarray]:
    """The three PL IPs' observable outputs for one RGB frame.

    uint32/uint16 are deliberate: channel sums max out at 235 M (fits uint32)
    and 77R+150G+29B maxes at 65280 (fits uint16), so the widening to 64-bit
    NumPy does by default would only cost time here.
    """
    sums = rgb.reshape(-1, 3).sum(axis=0, dtype=np.uint32)
    gray = gray_720p(rgb)
    if prev_gray is None:
        motion = {"diff_total": 0, "motion_pixels": 0, "motion_ratio_q16": 0}
    else:
        delta = np.abs(gray.astype(np.int16) - prev_gray.astype(np.int16))
        diff = int(delta.sum(dtype=np.uint32))
        pixels = int(np.count_nonzero(delta > threshold))  # strictly greater
        motion = {"diff_total": diff, "motion_pixels": pixels,
                  "motion_ratio_q16": (pixels << 16) // (GRAY_WIDTH * GRAY_HEIGHT)}
    out = {"sum_r": int(sums[0]), "sum_g": int(sums[1]), "sum_b": int(sums[2]),
           "count": WIDTH * HEIGHT, **motion}
    return out, gray


def jpeg_metadata(jpeg: bytes) -> dict:
    """Fields the board adds when forwarding MJPEG (S1)."""
    return {"codec": "jpeg-mjpeg", "jpeg_decoder": "islow-fancy", "jpeg_bytes": len(jpeg),
            "jpeg_crc32": zlib.crc32(jpeg) & 0xFFFFFFFF}


def packet(meta: dict, payload: bytes) -> bytes:
    raw = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    return struct.pack("!4sIII", b"VLP2", len(raw), len(payload), RGB_BYTES) + raw + payload


def end_packet(count: int) -> bytes:
    raw = json.dumps({"compared_frames": count}, separators=(",", ":")).encode("utf-8")
    return struct.pack("!4sII", b"VLE1", len(raw), 0) + raw


def _charge(result: "SimResult", name: str, started: float, budget_s: float,
            emulate: bool) -> float:
    """Sleep the unused part of a stage budget; count stages that overran it.

    An overrun means the PC did more real work than the board stage costs, so
    the modelled number is a *floor* for that stage. Counting them keeps the
    report honest instead of silently pretending the budget was met.
    """
    if emulate:
        left = budget_s - (time.perf_counter() - started)
        if left > 0:
            time.sleep(left)
    elapsed = time.perf_counter() - started
    if emulate and elapsed > budget_s + 0.002:
        result.stage_overruns[name] = result.stage_overruns.get(name, 0) + 1
    return elapsed


_WIN_TIMER_ON = False


def raise_timer_resolution() -> None:
    """Windows 默认 ~15.6 ms 调度粒度会把每次模拟耗时都撑大；仿真需要 1 ms。"""
    global _WIN_TIMER_ON
    if _WIN_TIMER_ON or sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)  # type: ignore[attr-defined]
        _WIN_TIMER_ON = True
    except Exception:  # noqa: BLE001 - 拿不到高精度计时也要能跑，只是数字偏慢
        pass


def encode_camera_frames(frames: Iterable[np.ndarray], quality: int = 75) -> list[bytes]:
    """Emulate the camera's own MJPG output (~55 KB/frame at 720p)."""
    import cv2
    out = []
    for bgr in frames:
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("cv2.imencode failed")
        out.append(buf.tobytes())
    return out


@dataclass
class _Item:
    index: int
    jpeg: bytes
    ts_us: int


@dataclass
class _Decoded:
    index: int
    jpeg: bytes
    rgb: np.ndarray
    ts_us: int


@dataclass
class SimResult:
    config: dict
    frames_sent: int = 0
    frames_dropped: int = 0
    wall_s: float = 0.0
    fps: float = 0.0
    decode_ms: list[float] = field(default_factory=list)
    pl_ms: list[float] = field(default_factory=list)
    send_ms: list[float] = field(default_factory=list)
    audit_frames: int = 0
    audit_mismatches: int = 0
    stage_overruns: dict = field(default_factory=dict)
    first_send_s: float | None = None
    last_send_s: float | None = None

    def summary(self) -> dict:
        def stats(values: list[float]) -> dict:
            if not values:
                return {"n": 0}
            ordered = sorted(values)
            return {"n": len(ordered),
                    "median": round(statistics.median(ordered), 3),
                    "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3)}

        return {"config": self.config, "frames_sent": self.frames_sent,
                "frames_dropped": self.frames_dropped, "wall_s": round(self.wall_s, 3),
                "fps": round(self.fps, 3),
                "decode_ms": stats(self.decode_ms), "pl_ms": stats(self.pl_ms),
                "send_ms": stats(self.send_ms), "audit_frames": self.audit_frames,
                "audit_mismatches": self.audit_mismatches,
                "stage_overruns": self.stage_overruns,
                "is_board_run": False, "mode": "pc_sim"}


def simulate(jpegs: list[bytes], cfg: SimConfig, *, sink=None) -> SimResult:
    """Run the modelled pipeline over camera JPEG frames.

    `sink` receives each finished packet's bytes (e.g. ``socket.sendall``).
    A None sink still exercises PL/metadata/serialisation, just no wire.
    """
    if len(jpegs) < cfg.frames:
        raise ValueError(f"need {cfg.frames} camera frames, got {len(jpegs)}")
    raise_timer_resolution()
    jpegs = jpegs[:cfg.frames]
    result = SimResult(config={"codec": cfg.codec, "pipeline": cfg.pipeline,
                               "frames": cfg.frames, "emulate": cfg.emulate,
                               "decode_ms": cfg.decode_ms, "pl_ms": cfg.pl_ms,
                               "audit_every": cfg.audit_every,
                               "link_mbps": cfg.link_mbps})
    link_bytes_per_s = cfg.link_mbps * 1_000_000 / 8.0
    state = {"prev_gray": None, "sent": 0}
    lock = threading.Lock()

    def send_stage(meta: dict, payload: bytes) -> None:
        started = time.perf_counter()
        blob = packet(meta, payload)
        if sink is not None:
            sink(blob)
        elapsed = _charge(result, "send", started, len(payload) / link_bytes_per_s, cfg.emulate)
        result.send_ms.append(elapsed * 1000)
        now = time.perf_counter()
        if result.first_send_s is None:
            result.first_send_s = now
        result.last_send_s = now

    def pl_stage(item: _Decoded) -> None:
        started = time.perf_counter()
        prev = state["prev_gray"]
        pl, gray = pl_frame(item.rgb, prev, cfg.motion_threshold)
        state["prev_gray"] = gray
        elapsed = _charge(result, "pl", started, cfg.pl_ms / 1000.0, cfg.emulate)
        result.pl_ms.append(elapsed * 1000)

        if cfg.audit_every and state["sent"] % cfg.audit_every == 0:
            # S2: recompute through an independent call and compare. A
            # disagreement is a hard failure, never averaged away.
            audit_started = time.perf_counter()
            again, _ = pl_frame(item.rgb, prev, cfg.motion_threshold)
            _charge(result, "audit", audit_started, cfg.audit_ms / 1000.0, cfg.emulate)
            result.audit_frames += 1
            if any(again[k] != pl[k] for k in AUDIT_KEYS):
                result.audit_mismatches += 1

        with lock:
            state["sent"] += 1
            sent_count = state["sent"]
        meta = {"profile": PROFILE, "width": WIDTH, "height": HEIGHT,
                "gray_width": GRAY_WIDTH, "gray_height": GRAY_HEIGHT,
                "source_sequence": item.index, "source_ts_us": item.ts_us,
                "pl_frame_id": sent_count, "motion_valid": True,
                "motion_ratio_q16": pl["motion_ratio_q16"],
                "motion_threshold": cfg.motion_threshold,
                "sum_r": pl["sum_r"], "sum_g": pl["sum_g"], "sum_b": pl["sum_b"],
                "count": pl["count"], "diff_total": pl["diff_total"],
                "motion_pixels": pl["motion_pixels"]}
        if prev is None:
            # The first frame the hardware ever sees cannot report motion:
            # BRAM history is undefined, so the zeros are declared, not measured.
            meta.update({"motion_valid": False, "motion_ratio_q16": 0,
                         "diff_total": 0, "motion_pixels": 0})
        if cfg.codec == "jpeg":
            meta.update(jpeg_metadata(item.jpeg))
            send_stage(meta, item.jpeg)
        else:
            send_stage(meta, item.rgb.tobytes())

    def decode_stage(item: _Item) -> _Decoded:
        import cv2
        started = time.perf_counter()
        image = cv2.imdecode(np.frombuffer(item.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape != (HEIGHT, WIDTH, 3):
            raise RuntimeError("simulated camera JPEG did not decode to 720p")
        rgb = image[:, :, ::-1].copy()
        elapsed = _charge(result, "decode", started, cfg.decode_ms / 1000.0, cfg.emulate)
        result.decode_ms.append(elapsed * 1000)
        return _Decoded(index=item.index, jpeg=item.jpeg, rgb=rgb, ts_us=item.ts_us)

    def item_for(index: int, jpeg: bytes) -> _Item:
        return _Item(index=index, jpeg=jpeg,
                     ts_us=1_000_000 + int(index * 1e6 / cfg.camera_fps))

    started_at = time.perf_counter()
    if cfg.pipeline == "serial":
        for index, jpeg in enumerate(jpegs):
            pl_stage(decode_stage(item_for(index, jpeg)))
        result.frames_sent = state["sent"]
    else:
        raw_q: queue.Queue = queue.Queue(maxsize=cfg.queue_depth)
        dec_q: queue.Queue = queue.Queue(maxsize=2)
        stopped = threading.Event()
        errors: list[Exception] = []

        def put(q, item) -> bool:
            while not stopped.is_set():
                try:
                    q.put(item, timeout=0.05)
                    return True
                except queue.Full:
                    pass
            return False

        def take(q):
            while not stopped.is_set():
                try:
                    return q.get(timeout=0.05)
                except queue.Empty:
                    pass
            return None

        def guarded(fn) -> None:
            try:
                fn()
            except Exception as exc:
                with lock:
                    errors.append(exc)
                stopped.set()

        def producer() -> None:
            for index, jpeg in enumerate(jpegs):
                delay = started_at + index / cfg.camera_fps - time.perf_counter()
                if stopped.wait(max(0, delay)):
                    return
                while not stopped.is_set():
                    try:
                        raw_q.put_nowait(item_for(index, jpeg))
                        break
                    except queue.Full:
                        try:
                            raw_q.get_nowait()      # V4L2 overwrites the oldest
                            result.frames_dropped += 1
                        except queue.Empty:
                            pass
            put(raw_q, None)

        def decoder() -> None:
            while True:
                item = take(raw_q)
                if item is None:
                    put(dec_q, None)
                    return
                if not put(dec_q, decode_stage(item)):
                    return

        def pl_sender() -> None:
            while True:
                item = take(dec_q)
                if item is None:
                    return
                pl_stage(item)

        threads = {
            "capture": threading.Thread(target=guarded, args=(producer,), name="capture", daemon=True),
            "decode": threading.Thread(target=guarded, args=(decoder,), name="decode", daemon=True),
            "pl": threading.Thread(target=guarded, args=(pl_sender,), name="pl", daemon=True),
        }
        for thread in threads.values():
            thread.start()
        try:
            deadline = time.monotonic() + cfg.frames / cfg.camera_fps + 120
            for thread in threads.values():
                thread.join(timeout=max(0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread in threads.values()):
                raise TimeoutError("simulated pipeline workers did not finish")
            if errors:
                raise RuntimeError("simulated pipeline worker failed") from errors[0]
        finally:
            stopped.set()
            for thread in threads.values():
                thread.join(timeout=1)
        result.frames_sent = state["sent"]

    result.wall_s = time.perf_counter() - started_at
    # Throughput is measured between first and last delivered packet: the
    # camera's start-up ramp is not a pipeline cost (same rule as the board's
    # output_fps = (kept-1)/(last_ts-first_ts)).
    if result.first_send_s is not None and result.last_send_s is not None:
        span = result.last_send_s - result.first_send_s
        result.fps = ((result.frames_sent - 1) / span
                      if result.frames_sent > 1 and span > 0 else 0.0)
    return result


def stream_to_client(jpegs: list[bytes], cfg: SimConfig, port: int,
                     *, host: str = "127.0.0.1", ready_timeout: float = 120.0) -> SimResult:
    """Serve one PC receiver, then send the simulated packets and VLE1."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(1)
    listener.settimeout(ready_timeout)
    print(f"SIM_LISTEN {host}:{port} codec={cfg.codec} pipeline={cfg.pipeline} "
          f"frames={cfg.frames}", flush=True)
    try:
        conn, peer = listener.accept()
    finally:
        listener.close()
    print(f"SIM_CONNECTED peer={peer[0]}:{peer[1]}", flush=True)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.settimeout(10.0)
    try:
        result = simulate(jpegs, cfg, sink=conn.sendall)
        conn.sendall(end_packet(result.frames_sent))
    finally:
        conn.close()
    return result
