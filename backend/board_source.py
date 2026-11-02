"""Local board TCP stream: one verified RGB frame and its PL results per packet.

VLP1: magic + network uint32 JSON/RGB lengths + UTF-8 JSON + exact RGB888.
VLP2: magic + network uint32 JSON/wire/raw lengths + JSON with codec + payload.
codec=raw carries RGB; zlib-rgb-delta carries a complete zlib stream of byte
differences to the previous same-channel pixel, restarting at every row;
jpeg-mjpeg carries the camera's own MJPEG frame plus jpeg_bytes/jpeg_crc32 and
is decoded on the PC (S1, ~55 KB instead of 2.76 MB per 720p frame).
VLE1 is the explicit end marker for both. This private transport never changes
the business JSON contract. Callers always receive reconstructed RGB888.

For jpeg-mjpeg the byte-exact PL pledge is weaker on purpose: transport is
checked by CRC32 and decoded pixels are checked against the same-packet PL
channel sums with JPEG_SUM_TOLERANCE (different libjpeg builds upsample chroma
differently). Board audits compare the board decoder to PL. Only raw/VLP1
and lossless RGB transport preserve byte identity between PC and PL input.
"""
from __future__ import annotations

import json
import io
import queue
import socket
import struct
import time
import threading
import zlib
from dataclasses import dataclass
from typing import Any, Iterator

WIDTH, HEIGHT = 1280, 720
GRAY_WIDTH, GRAY_HEIGHT = 480, 270
RGB_BYTES = WIDTH * HEIGHT * 3
MAX_METADATA = 4096
PACKET_TIMEOUT = 10.0  # Transport deadline, not a measurement threshold.
CODECS = ("raw", "zlib-rgb-delta", "jpeg-mjpeg")
# PC and board may run different libjpeg builds (chroma upsampling/IDCT), so a
# decoded JPEG cannot promise byte equality. This aggregate check is approximate;
# similar frames/channel distributions can pass, so it is not pixel-identity proof.
JPEG_SUM_TOLERANCE = 0.005


class BoardProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class BoardStreamEnd:
    compared_frames: int


def parse_address(source: str) -> tuple[str, int]:
    parts = source.strip().split(":")
    if len(parts) != 3 or parts[0].lower() != "board" or not parts[1]:
        raise ValueError("board source must be board:IP:port")
    try:
        port = int(parts[2])
    except ValueError as exc:
        raise ValueError("board port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("board port must be in 1..65535")
    return parts[1], port


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise BoardProtocolError(f"duplicate metadata key: {key}")
        out[key] = value
    return out


def validate_metadata(meta: Any, threshold: int) -> dict:
    if not isinstance(meta, dict) or meta.get("profile") != "720p-3of8":
        raise BoardProtocolError("unsupported board profile")
    fixed = {"width": WIDTH, "height": HEIGHT, "gray_width": GRAY_WIDTH,
             "gray_height": GRAY_HEIGHT, "count": WIDTH * HEIGHT,
             "motion_threshold": threshold}
    for key, expected in fixed.items():
        if type(meta.get(key)) is not int or meta[key] != expected:
            raise BoardProtocolError(f"board {key} does not match profile/config")
    limits = {"source_sequence": 0xffffffff, "source_ts_us": 0xffffffffffffffff,
              "pl_frame_id": 0xffffffff, "motion_ratio_q16": 65536,
              "sum_r": WIDTH * HEIGHT * 255, "sum_g": WIDTH * HEIGHT * 255,
              "sum_b": WIDTH * HEIGHT * 255, "diff_total": GRAY_WIDTH * GRAY_HEIGHT * 255,
              "motion_pixels": GRAY_WIDTH * GRAY_HEIGHT}
    for key, maximum in limits.items():
        if type(meta.get(key)) is not int or not 0 <= meta[key] <= maximum:
            raise BoardProtocolError(f"board {key} outside integer range")
    if type(meta.get("motion_valid")) is not bool:
        raise BoardProtocolError("motion_valid must be boolean")
    if not meta["motion_valid"]:
        if any(meta[k] != 0 for k in ("motion_ratio_q16", "diff_total", "motion_pixels")):
            raise BoardProtocolError("invalid motion must be zeroed, never undefined BRAM values")
    elif meta["motion_ratio_q16"] != ((meta["motion_pixels"] << 16) // (GRAY_WIDTH * GRAY_HEIGHT)):
        raise BoardProtocolError("motion Q16 ratio disagrees with motion_pixels")
    return meta


def _recv_exact(sock, count: int, deadline: float, *, clean_eof: bool = False) -> bytes | None:
    out = bytearray()
    while len(out) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("board packet deadline exceeded")
        sock.settimeout(remaining)
        block = sock.recv(count - len(out))
        if not block:
            if clean_eof and not out:
                return None
            raise BoardProtocolError("truncated board packet")
        out.extend(block)
    return bytes(out)


def read_packet(sock, threshold: int) -> tuple[dict, bytes] | BoardStreamEnd | None:
    deadline = time.monotonic() + PACKET_TIMEOUT
    header = _recv_exact(sock, 12, deadline, clean_eof=True)
    if header is None:
        return None
    magic, json_size, rgb_size = struct.unpack("!4sII", header)
    if (magic not in (b"VLP1", b"VLP2", b"VLE1") or not 0 < json_size <= MAX_METADATA
            or (magic == b"VLP1" and rgb_size != RGB_BYTES)
            or (magic == b"VLE1" and rgb_size != 0)
            or (magic == b"VLP2" and not 0 < rgb_size <= RGB_BYTES)):
        raise BoardProtocolError("invalid board packet magic/length")
    if magic == b"VLP2":
        raw_size, = struct.unpack("!I", _recv_exact(sock, 4, deadline))
        if raw_size != RGB_BYTES:
            raise BoardProtocolError("invalid board decompressed length")
    raw = _recv_exact(sock, json_size, deadline)
    try:
        meta = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BoardProtocolError("invalid board JSON") from exc
    if magic == b"VLE1":
        if (not isinstance(meta, dict) or set(meta) != {"compared_frames"}
                or type(meta["compared_frames"]) is not int
                or not 0 <= meta["compared_frames"] <= 0xffffffff):
            raise BoardProtocolError("invalid board stream end metadata")
        return BoardStreamEnd(meta["compared_frames"])
    validate_metadata(meta, threshold)
    codec = meta.get("codec") if magic == b"VLP2" else "raw"
    if codec not in CODECS or (codec == "raw" and rgb_size != RGB_BYTES):
        raise BoardProtocolError("invalid board codec/length")
    rgb = _recv_exact(sock, rgb_size, deadline)
    if codec == "zlib-rgb-delta":
        decoder = zlib.decompressobj()
        try:
            rgb = decoder.decompress(rgb, RGB_BYTES + 1)
        except zlib.error as exc:
            raise BoardProtocolError("invalid board compressed RGB") from exc
        if (len(rgb) != RGB_BYTES or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail):
            raise BoardProtocolError("invalid board compressed RGB length/end")
        # Each row/channel starts afresh; uint8 cumulative sum wraps modulo 256.
        import numpy as np
        delta = np.frombuffer(rgb, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)
        rgb = np.cumsum(delta, axis=1, dtype=np.uint8).tobytes()
    elif codec == "jpeg-mjpeg":
        if type(meta.get("jpeg_bytes")) is not int or meta["jpeg_bytes"] != rgb_size:
            raise BoardProtocolError("board jpeg length disagrees with payload")
        if (type(meta.get("jpeg_crc32")) is not int or
                (zlib.crc32(rgb) & 0xFFFFFFFF) != meta["jpeg_crc32"]):
            raise BoardProtocolError("board jpeg checksum mismatch")
        mode = meta.get("jpeg_decoder", "islow-fancy")
        if mode not in ("islow-fancy", "ifast-no-fancy"):
            raise BoardProtocolError("unknown board JPEG decoder mode")
        rgb = decode_jpeg(rgb, mode=mode)
    return meta, rgb


def decode_jpeg(payload: bytes, *, mode: str = "islow-fancy") -> bytes:
    """Decode a board MJPEG frame to RGB888 on the PC (S1 path)."""
    if mode == "ifast-no-fancy":
        try:
            from PIL import Image
        except ImportError as exc:
            raise BoardProtocolError("fast board JPEG decoding needs Pillow on the PC") from exc
        try:
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "JPEG" or image.size != (WIDTH, HEIGHT) or image.mode != "RGB":
                    raise BoardProtocolError("board jpeg does not decode to 1280x720 RGB")
                # Pillow's JPEG decoder config is (scale, draft). Draft selects
                # JDCT_FASTEST (=IFAST) and disables fancy chroma upsampling,
                # matching camera_pl.c without changing measurement thresholds.
                # See Pillow src/libImaging/JpegDecode.c and docs/47 section 7.
                image.decoderconfig = (1, 1)
                image.load()
                return image.tobytes()
        except OSError as exc:
            raise BoardProtocolError("invalid board fast JPEG") from exc
    if mode != "islow-fancy":
        raise BoardProtocolError("unknown board JPEG decoder mode")
    import numpy as np
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - environment guard
        raise BoardProtocolError("jpeg-mjpeg needs opencv on the PC") from exc
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape != (HEIGHT, WIDTH, 3):
        raise BoardProtocolError("board jpeg does not decode to 1280x720 BGR")
    return image[:, :, ::-1].copy().tobytes()


def _channel_sums_ok(actual, meta: dict) -> bool:
    """Byte-exact for raw/zlib; tolerance-based for jpeg (see module docstring)."""
    if meta.get("codec") != "jpeg-mjpeg":
        return all(int(actual[c]) == meta[key]
                   for c, key in enumerate(("sum_r", "sum_g", "sum_b")))
    for c, key in enumerate(("sum_r", "sum_g", "sum_b")):
        allowed = max(1.0, float(meta[key]) * JPEG_SUM_TOLERANCE)
        if abs(int(actual[c]) - meta[key]) > allowed:
            return False
    return True


def _channel_sums(rgb_image):
    """Independent strided reductions; max 720p channel sum fits uint32.

    NumPy's three-column axis reduction is slower on this receiver than three
    single-channel reductions. Values and the same-packet check are unchanged.
    """
    import numpy as np
    return [int(rgb_image[:, :, c].sum(dtype=np.uint32)) for c in range(3)]


class BoardFrameSource:
    """Bounded receiver; any disconnect is visible in diagnostics and exit status.

    No reconnect or frame padding: those would hide capture gaps/restarts.
    Pixels stay in local process memory and are never saved by this source.
    """
    def __init__(self, source: str, *, motion_threshold: int, limit: int | None = None):
        self.address = parse_address(source)
        if type(motion_threshold) is not int or not 0 <= motion_threshold <= 255:
            raise ValueError("configured motion threshold must be an integer in 0..255")
        self.threshold, self.limit = motion_threshold, limit
        self.seconds: float | None = None
        self.error: str | None = None
        self.completed = False
        self.received = self.valid_motion = self.sequence_gaps = 0
        self.first_meta: dict | None = None
        self.last_meta: dict | None = None
        self.codecs: set[str] = set()
        self._cancel = threading.Event()
        self._socket = None
        self.prefetch_peak = 0
        self._iteration_started = False

    def __iter__(self) -> Iterator:
        if self._iteration_started:
            raise RuntimeError("board source is single-use; create a new source for another run")
        self._iteration_started = True
        # Keep reading while the caller runs MediaPipe, writes JSON or posts
        # metrics. Two pending frames bound memory/latency; none are dropped.
        pending: queue.Queue = queue.Queue(maxsize=2)
        end = object()

        def put(item) -> bool:
            while not self._cancel.is_set():
                try:
                    pending.put(item, timeout=0.05)
                    if item is not end:
                        self.prefetch_peak = max(self.prefetch_peak, pending.qsize())
                    return True
                except queue.Full:
                    pass
            return False

        def receive() -> None:
            frames = self._read_frames()
            try:
                for frame in frames:
                    if not put(frame):
                        break
            except Exception as exc:
                if not self._cancel.is_set():
                    self.error = str(exc)
                    print(f"[FAIL] board receiver: {exc}")
            finally:
                frames.close()
                put(end)

        worker = threading.Thread(target=receive, name="board-receiver", daemon=True)
        worker.start()
        try:
            while True:
                frame = pending.get()
                if frame is end:
                    break
                yield frame
        finally:
            self._cancel.set()
            active = self._socket
            if active is not None:
                try:
                    active.shutdown(socket.SHUT_RDWR)
                except (AttributeError, OSError):
                    pass
            worker.join(timeout=1.0)
            if worker.is_alive():
                self.error = "board receiver thread did not stop"

    def _read_frames(self) -> Iterator:
        import numpy as np
        try:
            from .capture import Frame
        except ImportError:
            from capture import Frame
        try:
            with socket.create_connection(self.address, timeout=PACKET_TIMEOUT) as sock:
                self._socket = sock
                while self.limit is None or self.received < self.limit:
                    packet = read_packet(sock, self.threshold)
                    if packet is None:
                        raise BoardProtocolError("board stream disconnected")
                    if isinstance(packet, BoardStreamEnd):
                        if self.received < 1 or packet.compared_frames != self.received:
                            raise BoardProtocolError("board stream end count disagrees with received frames")
                        self.completed = True
                        break
                    meta, rgb = packet
                    if self.last_meta is not None:
                        for key in ("source_sequence", "source_ts_us", "pl_frame_id"):
                            if meta[key] <= self.last_meta[key]:
                                raise BoardProtocolError(f"board {key} must strictly increase")
                        if not meta["motion_valid"]:
                            raise BoardProtocolError("motion became invalid after stream start")
                    elif meta["motion_valid"]:
                        raise BoardProtocolError("first board frame must mark motion invalid")
                    first = self.first_meta or meta
                    ts = (meta["source_ts_us"] - first["source_ts_us"]) / 1e6
                    if self.seconds is not None and ts >= self.seconds:
                        break
                    rgb_image = np.frombuffer(rgb, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)
                    channel_sums = _channel_sums(rgb_image)
                    if not _channel_sums_ok(channel_sums, meta):
                        raise BoardProtocolError(
                            "RGB payload does not match same-packet PL channel sums: "
                            f"codec={meta.get('codec', 'raw')} "
                            f"pc={[int(v) for v in channel_sums]} "
                            f"pl={[meta[k] for k in ('sum_r', 'sum_g', 'sum_b')]}")
                    if self.last_meta:
                        self.sequence_gaps += meta["source_sequence"] - self.last_meta["source_sequence"] - 1
                    self.first_meta = first
                    self.last_meta = meta
                    self.codecs.add(str(meta.get("codec", "raw")))
                    fid = self.received
                    self.received += 1
                    self.valid_motion += int(meta["motion_valid"])
                    image = rgb_image[:, :, ::-1].copy()
                    yield Frame(frame_id=fid, ts=ts, image=image, board_metadata=meta)
        except (OSError, ValueError) as exc:
            if not self._cancel.is_set():
                self.error = str(exc)
                print(f"[FAIL] board stream: {exc}")
        finally:
            self._socket = None

    def stats(self) -> dict:
        span = ((self.last_meta["source_ts_us"] - self.first_meta["source_ts_us"]) / 1e6
                if self.first_meta and self.last_meta else 0)
        return {"source": "board_pl", "address": f"{self.address[0]}:{self.address[1]}",
                "frames": self.received, "valid_motion_frames": self.valid_motion,
                "source_sequence_gaps": self.sequence_gaps, "source_span_s": span,
                "codecs": sorted(self.codecs),
                "receiver_prefetch_frames": 2, "receiver_prefetch_peak": self.prefetch_peak,
                "output_fps": (self.received - 1) / span if span > 0 else None,
                "first": self.first_meta, "last": self.last_meta, "error": self.error,
                "end_received": self.completed}


def apply_board_motion(scorer, quality: dict, meta: dict, *, face_score: float) -> dict:
    """Apply this packet's hardware motion, including explicit first-frame refusal."""
    out = scorer.apply_motion_ratio(quality, meta["motion_ratio_q16"] / 65536.0,
                                    face_score=face_score)
    out["_motion_source"] = "board_pl"
    out["_motion_valid"] = meta["motion_valid"]
    if not meta["motion_valid"]:
        out["overall"] = 0.0
    return out
