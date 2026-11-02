"""PC 仿真流水线（S1 JPEG 透传 / S2 审计帧 / S3 三线程）的契约式自检。

这些用例证明的是**仿真器与协议自洽**：灰度公式与 A 线冻结口径逐字节相同、
PL 语义（首帧无效、严格大于、Q16 向下取整）一致、VLP 包能被 `board_source`
原样接收。它们**不是**板端性能证据，也不能替代上板日志。
"""
from __future__ import annotations

import io
import json
import struct
import zlib

import numpy as np
import pytest

from backend import board_sim as sim
from backend import board_source as board
from backend import quality
from backend.config import load_config


def solid_jpeg(color=(30, 120, 200), size=(sim.WIDTH, sim.HEIGHT)) -> bytes:
    import cv2
    image = np.zeros((size[1], size[0], 3), np.uint8)
    image[:] = color
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return buf.tobytes()


def test_gray_formula_matches_a_line_frozen_reference():
    rng = np.random.default_rng(11)
    rgb = rng.integers(0, 256, size=(sim.HEIGHT, sim.WIDTH, 3), dtype=np.uint8)
    got = sim.gray_720p(rgb)
    # A 线 to_gray 吃 BGR，档位 3/8；两条路径必须逐字节相同。
    want = quality.to_gray(rgb[:, :, ::-1], decim=(8, 3))
    assert got.shape == (sim.GRAY_HEIGHT, sim.GRAY_WIDTH)
    assert np.array_equal(got, want)


def test_first_pl_frame_is_declared_invalid_and_zeroed():
    rgb = np.full((sim.HEIGHT, sim.WIDTH, 3), 7, np.uint8)
    pl, gray = sim.pl_frame(rgb, None, 16)
    assert pl["diff_total"] == 0 and pl["motion_pixels"] == 0 and pl["motion_ratio_q16"] == 0
    assert pl["count"] == sim.WIDTH * sim.HEIGHT
    assert gray.shape == (sim.GRAY_HEIGHT, sim.GRAY_WIDTH)


def test_motion_uses_strictly_greater_threshold_and_floor_division():
    def uniform(level: int) -> np.ndarray:
        return np.full((sim.HEIGHT, sim.WIDTH, 3), level, np.uint8)

    _, prev = sim.pl_frame(uniform(10), None, 16)          # luma 10
    equal, _ = sim.pl_frame(uniform(26), prev, 16)         # delta 恰好 16
    assert equal["diff_total"] == 16 * sim.GRAY_WIDTH * sim.GRAY_HEIGHT
    assert equal["motion_pixels"] == 0                     # 严格大于才算
    assert equal["motion_ratio_q16"] == 0

    over, _ = sim.pl_frame(uniform(200), prev, 16)         # delta 190 > 16
    assert over["motion_pixels"] == sim.GRAY_WIDTH * sim.GRAY_HEIGHT
    assert over["motion_ratio_q16"] == 65536


def test_jpeg_packet_round_trips_through_the_real_receiver():
    jpeg = solid_jpeg()
    rgb = np.zeros((sim.HEIGHT, sim.WIDTH, 3), np.uint8)
    rgb[:] = (200, 120, 30)       # RGB 顺序
    sums = rgb.reshape(-1, 3).sum(axis=0, dtype=np.uint32)
    meta = {"profile": sim.PROFILE, "width": sim.WIDTH, "height": sim.HEIGHT,
            "gray_width": sim.GRAY_WIDTH, "gray_height": sim.GRAY_HEIGHT,
            "source_sequence": 0, "source_ts_us": 1, "pl_frame_id": 1,
            "motion_valid": False, "motion_ratio_q16": 0,
            "motion_threshold": load_config()["motion_thresh_gray"],
            "sum_r": int(sums[0]), "sum_g": int(sums[1]), "sum_b": int(sums[2]),
            "count": sim.WIDTH * sim.HEIGHT, "diff_total": 0, "motion_pixels": 0,
            **sim.jpeg_metadata(jpeg)}
    blob = sim.packet(meta, jpeg)
    got_meta, decoded = board.read_packet(MemorySocket(blob), meta["motion_threshold"])
    assert got_meta["codec"] == "jpeg-mjpeg"
    assert len(decoded) == board.RGB_BYTES
    image = np.frombuffer(decoded, np.uint8).reshape(sim.HEIGHT, sim.WIDTH, 3)
    # JPEG 是有损的：同色块解出来允许 ±2，关键是**通道序**仍然是 R-G-B。
    assert np.abs(image[0, 0].astype(int) - np.array([200, 120, 30])).max() <= 2


@pytest.mark.parametrize("change", [
    {"jpeg_crc32": 1}, {"jpeg_bytes": 2}, {"jpeg_bytes": 10 ** 6},
    {"jpeg_crc32": float(zlib.crc32(solid_jpeg()) & 0xffffffff)},
    {"jpeg_bytes": float(len(solid_jpeg()))},
])
def test_metadata_crc_or_length_mismatch_is_rejected(change):
    jpeg = solid_jpeg()
    meta = {"profile": sim.PROFILE, "width": sim.WIDTH, "height": sim.HEIGHT,
            "gray_width": sim.GRAY_WIDTH, "gray_height": sim.GRAY_HEIGHT,
            "source_sequence": 0, "source_ts_us": 1, "pl_frame_id": 1,
            "motion_valid": False, "motion_ratio_q16": 0,
            "motion_threshold": load_config()["motion_thresh_gray"],
            "sum_r": 0, "sum_g": 0, "sum_b": 0, "count": sim.WIDTH * sim.HEIGHT,
            "diff_total": 0, "motion_pixels": 0, **sim.jpeg_metadata(jpeg), **change}
    with pytest.raises(board.BoardProtocolError):
        board.read_packet(MemorySocket(sim.packet(meta, jpeg)),
                          load_config()["motion_thresh_gray"])


def test_truncated_jpeg_payload_fails_decoding():
    jpeg = solid_jpeg()
    jpeg = jpeg[: len(jpeg) // 2]          # CRC 按截断后的字节算，所以先过 CRC
    meta = {"profile": sim.PROFILE, "width": sim.WIDTH, "height": sim.HEIGHT,
            "gray_width": sim.GRAY_WIDTH, "gray_height": sim.GRAY_HEIGHT,
            "source_sequence": 0, "source_ts_us": 1, "pl_frame_id": 1,
            "motion_valid": False, "motion_ratio_q16": 0,
            "motion_threshold": load_config()["motion_thresh_gray"],
            "sum_r": 0, "sum_g": 0, "sum_b": 0, "count": sim.WIDTH * sim.HEIGHT,
            "diff_total": 0, "motion_pixels": 0,
            "codec": "jpeg-mjpeg", "jpeg_bytes": len(jpeg),
            "jpeg_crc32": zlib.crc32(bytes(jpeg)) & 0xFFFFFFFF}
    with pytest.raises(board.BoardProtocolError):
        board.read_packet(MemorySocket(sim.packet(meta, bytes(jpeg))),
                          load_config()["motion_thresh_gray"])


def test_sum_tolerance_accepts_1lsb_drift_but_refuses_a_channel_swap():
    rgb = np.zeros((sim.HEIGHT, sim.WIDTH, 3), np.uint8)
    rgb[:] = (200, 120, 30)
    sums = [int(x) for x in rgb.reshape(-1, 3).sum(axis=0, dtype=np.uint32)]
    ok = {"sum_r": sums[0] + 1000, "sum_g": sums[1] - 1000, "sum_b": sums[2] + 1}
    bad = {"sum_r": sums[1], "sum_g": sums[0], "sum_b": sums[2]}
    assert board._channel_sums_ok(sums, {**ok, "codec": "jpeg-mjpeg"})
    assert not board._channel_sums_ok(sums, {**bad, "codec": "jpeg-mjpeg"})
    # raw/zlib 仍然逐字节严格
    assert not board._channel_sums_ok(sums, {**ok, "codec": "raw"})


def test_serial_simulation_emits_the_ladder_and_invalid_first_frame():
    jpegs = [solid_jpeg((i * 7 % 256, 120, 30)) for i in range(6)]
    cfg = sim.SimConfig(codec="jpeg", pipeline="serial", frames=6, emulate=False,
                        audit_every=2)
    result = sim.simulate(jpegs, cfg, sink=None)
    assert result.frames_sent == 6
    assert result.audit_frames == 3 and result.audit_mismatches == 0
    assert result.fps > 0


def test_threaded_simulation_delivers_every_frame_when_paced_slowly():
    jpegs = [solid_jpeg((i * 7 % 256, 120, 30)) for i in range(6)]
    cfg = sim.SimConfig(codec="jpeg", pipeline="threaded", frames=6, emulate=False,
                        camera_fps=5.0, audit_every=3, queue_depth=4)
    result = sim.simulate(jpegs, cfg, sink=None)
    assert result.frames_sent == 6
    assert result.frames_dropped == 0
    assert result.audit_frames == 2
    assert result.stage_overruns == {}


class MemorySocket:
    def __init__(self, data):
        self.data = io.BytesIO(data)

    def settimeout(self, timeout):
        assert timeout > 0

    def recv(self, count):
        return self.data.read(min(count, 65536))


def test_unknown_codec_is_still_refused():
    meta = {"profile": sim.PROFILE, "width": sim.WIDTH, "height": sim.HEIGHT,
            "gray_width": sim.GRAY_WIDTH, "gray_height": sim.GRAY_HEIGHT,
            "source_sequence": 0, "source_ts_us": 1, "pl_frame_id": 1,
            "motion_valid": False, "motion_ratio_q16": 0,
            "motion_threshold": load_config()["motion_thresh_gray"],
            "sum_r": 0, "sum_g": 0, "sum_b": 0, "count": sim.WIDTH * sim.HEIGHT,
            "diff_total": 0, "motion_pixels": 0, "codec": "jpeg"}
    blob = sim.packet(meta, solid_jpeg())
    with pytest.raises(board.BoardProtocolError):
        board.read_packet(MemorySocket(blob), load_config()["motion_thresh_gray"])


def test_stream_end_marker_shape():
    blob = sim.end_packet(7)
    assert struct.unpack("!4sII", blob[:12])[0] == b"VLE1"
    assert json.loads(blob[12:].decode("utf-8")) == {"compared_frames": 7}


@pytest.mark.parametrize("where", ["decode", "send"])
def test_threaded_worker_failure_propagates_without_leaving_workers(where):
    import threading
    cfg = sim.SimConfig(frames=10, emulate=False, camera_fps=1000, queue_depth=1)
    jpeg = solid_jpeg()

    def broken_sink(blob):
        raise OSError("test receiver disconnected")

    before = {t.ident for t in threading.enumerate()}
    with pytest.raises(RuntimeError, match="worker failed"):
        sim.simulate([b"not a jpeg" if where == "decode" else jpeg] * 10, cfg,
                     sink=broken_sink if where == "send" else None)
    assert not [t for t in threading.enumerate()
                if t.ident not in before and t.name in ("capture", "decode", "pl")]


def test_threaded_audits_follow_processed_frames_after_capture_drops():
    jpeg = solid_jpeg()
    cfg = sim.SimConfig(frames=20, camera_fps=1000, queue_depth=1,
                        decode_ms=10, pl_ms=10, audit_ms=1, audit_every=3)
    result = sim.simulate([jpeg] * cfg.frames, cfg)
    assert result.frames_dropped > 0
    assert result.audit_frames == (result.frames_sent - 1) // cfg.audit_every + 1


@pytest.mark.parametrize("change", [
    {"camera_fps": 0}, {"camera_fps": float("nan")},
    {"link_mbps": -1}, {"link_mbps": float("inf")},
    {"decode_ms": -1}, {"pl_ms": float("nan")}, {"audit_ms": -1},
])
def test_invalid_timing_budget_is_rejected(change):
    with pytest.raises(ValueError):
        sim.SimConfig(**change)


def test_fast_jpeg_mode_matches_declared_decoder_without_relaxing_sums():
    from PIL import Image
    import cv2
    # Textured synthetic input exposes the IFAST/ISLOW difference; no face data.
    rng = np.random.default_rng(24)
    image = rng.integers(0, 256, (sim.HEIGHT, sim.WIDTH, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode('.jpg', image)
    assert ok
    jpeg = encoded.tobytes()
    with Image.open(io.BytesIO(jpeg)) as decoded:
        decoded.decoderconfig = (1, 1)
        want = decoded.tobytes()
    rgb = np.frombuffer(want, np.uint8).reshape(sim.HEIGHT, sim.WIDTH, 3)
    pl, _ = sim.pl_frame(rgb, None, 16)
    meta = {"profile": sim.PROFILE, "width": sim.WIDTH, "height": sim.HEIGHT,
            "gray_width": sim.GRAY_WIDTH, "gray_height": sim.GRAY_HEIGHT,
            "source_sequence": 0, "source_ts_us": 1, "pl_frame_id": 1,
            "motion_valid": False, "motion_threshold": 16, **pl,
            **sim.jpeg_metadata(jpeg), "jpeg_decoder": "ifast-no-fancy"}
    received, pixels = board.read_packet(MemorySocket(sim.packet(meta, jpeg)), 16)
    assert pixels == want
    assert board._channel_sums_ok(rgb.reshape(-1, 3).sum(0), received)
    assert board.decode_jpeg(jpeg) != want


@pytest.mark.parametrize("mode", ["ifast-no-fancy", "islow-fancy"])
def test_jpeg_decoder_rejects_wrong_geometry(mode):
    with pytest.raises(board.BoardProtocolError):
        board.decode_jpeg(solid_jpeg(size=(640, 480)), mode=mode)


def test_unknown_jpeg_decoder_mode_is_rejected():
    with pytest.raises(board.BoardProtocolError):
        board.decode_jpeg(solid_jpeg(), mode="typo")
