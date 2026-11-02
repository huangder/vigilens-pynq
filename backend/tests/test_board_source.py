"""Synthetic transport tests; these are not physical board/performance evidence."""
import io
import json
import struct
import zlib

import pytest

from backend import board_source as board
from backend.config import load_config


def metadata(**changes):
    out = {"profile": "720p-3of8", "width": 1280, "height": 720,
           "gray_width": 480, "gray_height": 270, "source_sequence": 10,
           "source_ts_us": 1_000_000, "pl_frame_id": 1, "motion_valid": False,
           "motion_ratio_q16": 0, "motion_threshold": load_config()["motion_thresh_gray"],
           "sum_r": 0, "sum_g": 0, "sum_b": 0, "count": 1280 * 720,
           "diff_total": 0, "motion_pixels": 0}
    out.update(changes)
    return out


def packet(meta=None, rgb=None):
    raw = json.dumps(meta or metadata()).encode("utf-8")
    rgb = bytes(board.RGB_BYTES) if rgb is None else rgb
    return struct.pack("!4sII", b"VLP1", len(raw), len(rgb)) + raw + rgb


def end_packet(count):
    raw = json.dumps({"compared_frames": count}).encode("utf-8")
    return struct.pack("!4sII", b"VLE1", len(raw), 0) + raw


def packet_v2(meta=None, rgb=None, *, codec="zlib-rgb-delta", payload=None,
              raw_size=board.RGB_BYTES, strategy=zlib.Z_DEFAULT_STRATEGY):
    import numpy as np
    meta = dict(meta or metadata(), codec=codec)
    raw = json.dumps(meta).encode("utf-8")
    rgb = bytes(board.RGB_BYTES) if rgb is None else rgb
    if payload is None:
        image = np.frombuffer(rgb, dtype=np.uint8).reshape(board.HEIGHT, board.WIDTH, 3)
        delta = image.copy()
        delta[:, 1:, :] -= image[:, :-1, :]
        if codec == "zlib-rgb-delta":
            compressor = zlib.compressobj(level=1, strategy=strategy)
            payload = compressor.compress(delta.tobytes()) + compressor.flush()
        else:
            payload = rgb
    return struct.pack("!4sIII", b"VLP2", len(raw), len(payload), raw_size) + raw + payload


@pytest.mark.parametrize("strategy", [zlib.Z_DEFAULT_STRATEGY, zlib.Z_RLE, zlib.Z_HUFFMAN_ONLY])
def test_vlp2_reconstructs_every_rgb_byte_and_row_boundary(strategy):
    import numpy as np
    rgb = np.random.default_rng(19).integers(0, 256, size=(board.HEIGHT, board.WIDTH, 3), dtype=np.uint8)
    # Random data may compress above the bounded wire maximum. Repeated rows
    # still exercise all values, modulo wrap and distinct first pixels.
    rgb[1:] = rgb[0]
    # Entropy-only strategies cannot exploit repeated rows, so restrict the
    # fixture's delta alphabet while retaining modulo-256 crossings.
    if strategy != zlib.Z_DEFAULT_STRATEGY:
        rgb = np.cumsum(rgb % 7, axis=1, dtype=np.uint8)
    data = packet_v2(rgb=rgb.tobytes(), strategy=strategy)
    _, decoded = board.read_packet(MemorySocket(data), load_config()["motion_thresh_gray"])
    assert decoded == rgb.tobytes()


@pytest.mark.parametrize("codec", ["raw", "zlib-rgb-delta"])
def test_vlp2_source_keeps_channel_sum_verification_and_end(monkeypatch, codec):
    rgb = bytes([11, 250, 128]) * (board.WIDTH * board.HEIGHT)
    meta = metadata(sum_r=11*board.WIDTH*board.HEIGHT,
                    sum_g=250*board.WIDTH*board.HEIGHT, sum_b=128*board.WIDTH*board.HEIGHT)
    stream, _ = source(monkeypatch, packet_v2(meta, rgb, codec=codec) + end_packet(1))
    frames = list(stream)
    assert len(frames) == 1 and stream.error is None and stream.completed
    assert frames[0].image[0, 0].tolist() == [128, 250, 11]
    stream, _ = source(monkeypatch, packet_v2(metadata(sum_g=1), codec=codec))
    assert list(stream) == [] and "channel sums" in stream.error


@pytest.mark.parametrize("payload", [
    b"not zlib", zlib.compress(bytes(board.RGB_BYTES))[:-1],
    zlib.compress(bytes(board.RGB_BYTES)) + b"trailing",
    zlib.compress(bytes(board.RGB_BYTES)) + zlib.compress(b"second stream"),
    zlib.compress(bytes(board.RGB_BYTES - 1)),
    zlib.compress(bytes(board.RGB_BYTES + 1)),
    zlib.compress(bytes(board.RGB_BYTES * 2)),
])
def test_vlp2_bad_compressed_stream_rejected(payload):
    with pytest.raises(board.BoardProtocolError, match="compressed RGB"):
        board.read_packet(MemorySocket(packet_v2(payload=payload)), load_config()["motion_thresh_gray"])


@pytest.mark.parametrize("kwargs", [
    {"raw_size": board.RGB_BYTES-1}, {"raw_size": board.RGB_BYTES+1},
    {"codec": "jpeg"}, {"codec": None}, {"codec": 1},
    {"payload": b""}, {"payload": bytes(board.RGB_BYTES+1)},
    {"codec": "raw", "payload": b"short"},
])
def test_vlp2_bad_header_codec_refused(kwargs):
    with pytest.raises(board.BoardProtocolError):
        board.read_packet(MemorySocket(packet_v2(**kwargs)), load_config()["motion_thresh_gray"])


def test_vlp2_truncation_and_mixed_vlp1_stream(monkeypatch):
    with pytest.raises(board.BoardProtocolError, match="truncated"):
        board.read_packet(MemorySocket(packet_v2()[:-1]), load_config()["motion_thresh_gray"])
    second = metadata(source_sequence=11, source_ts_us=1_100_000, pl_frame_id=2, motion_valid=True)
    stream, _ = source(monkeypatch, packet_v2() + packet(second) + end_packet(2))
    assert len(list(stream)) == 2 and stream.completed and stream.error is None


class MemorySocket:
    def __init__(self, data):
        self.data = io.BytesIO(data)
        self.closed = False

    def settimeout(self, timeout):
        assert timeout > 0

    def recv(self, count):
        return self.data.read(min(count, 65536))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


def source(monkeypatch, payload, **kwargs):
    sock = MemorySocket(payload)
    monkeypatch.setattr(board.socket, "create_connection", lambda *a, **k: sock)
    stream = board.BoardFrameSource("board:192.168.2.2:9000",
                                   motion_threshold=load_config()["motion_thresh_gray"], **kwargs)
    return stream, sock


@pytest.mark.parametrize("data", [b"V", packet()[:15], packet()[:-1]],
                         ids=["header", "metadata", "rgb"])
def test_truncated_packet_rejected(data):
    with pytest.raises(board.BoardProtocolError, match="truncated"):
        board.read_packet(MemorySocket(data), load_config()["motion_thresh_gray"])


@pytest.mark.parametrize("magic,jsize,rsize", [
    (b"BAD!", 2, board.RGB_BYTES), (b"VLP1", 4097, board.RGB_BYTES),
    (b"VLP1", 0, board.RGB_BYTES), (b"VLP1", 2, board.RGB_BYTES + 1),
])
def test_bad_header_refused_before_payload(magic, jsize, rsize):
    with pytest.raises(board.BoardProtocolError, match="magic/length"):
        board.read_packet(MemorySocket(struct.pack("!4sII", magic, jsize, rsize)), 16)


@pytest.mark.parametrize("change", [
    {"motion_valid": 1}, {"source_sequence": True}, {"source_sequence": -1},
    {"source_ts_us": 2**64}, {"pl_frame_id": 2**32}, {"motion_ratio_q16": 65537},
    {"width": 640}, {"gray_height": 288}, {"count": 1},
    {"motion_threshold": 999}, {"profile": "wrong"}, {"sum_g": 1280 * 720 * 256},
    {"motion_pixels": 129601}, {"diff_total": 129600 * 256},
    {"motion_ratio_q16": 1}, {"motion_valid": True, "motion_pixels": 1, "motion_ratio_q16": 100},
])
def test_metadata_boundaries(change):
    with pytest.raises(board.BoardProtocolError):
        board.validate_metadata(metadata(**change), load_config()["motion_thresh_gray"])


def test_duplicate_json_keys_are_rejected():
    raw = b'{"profile":"720p-3of8","profile":"720p-3of8"}'
    data = struct.pack("!4sII", b"VLP1", len(raw), board.RGB_BYTES) + raw
    with pytest.raises(board.BoardProtocolError, match="duplicate"):
        board.read_packet(MemorySocket(data), 16)


def test_sequence_gaps_allowed_time_preserved_rgb_converted(monkeypatch):
    second = metadata(source_sequence=15, source_ts_us=1_200_000, pl_frame_id=3,
                      motion_valid=True)
    rgb = bytes([20, 30, 40]) * (1280 * 720)
    first = metadata(sum_r=20 * 1280 * 720, sum_g=30 * 1280 * 720, sum_b=40 * 1280 * 720)
    stream, sock = source(monkeypatch, packet(first, rgb=rgb) + packet(second), limit=2)
    got = list(stream)
    assert stream.error is None and sock.closed
    assert [f.ts for f in got] == [0, 0.2]
    assert [f.frame_id for f in got] == [0, 1]
    assert got[0].image[0, 0].tolist() == [40, 30, 20]
    assert got[1].board_metadata["pl_frame_id"] == 3
    assert stream.stats()["source_sequence_gaps"] == 4


@pytest.mark.parametrize("change", [
    {"source_sequence": 10}, {"source_ts_us": 999999}, {"pl_frame_id": 1},
    {"motion_valid": False},
])
def test_backward_identity_and_invalid_history_stop_stream(monkeypatch, change):
    second = metadata(source_sequence=11, source_ts_us=1_100_000, pl_frame_id=2,
                      motion_valid=True)
    second.update(change)
    stream, sock = source(monkeypatch, packet() + packet(second))
    assert len(list(stream)) == 1
    assert stream.error and sock.closed


def test_first_motion_cannot_be_presented_as_valid(monkeypatch):
    stream, _ = source(monkeypatch, packet(metadata(motion_valid=True)))
    assert list(stream) == []
    assert "first board frame" in stream.error


def test_rgb_payload_must_match_same_packet_channel_sums(monkeypatch):
    stream, _ = source(monkeypatch, packet(metadata(sum_g=1)))
    assert list(stream) == []
    assert "channel sums" in stream.error


def test_disconnect_is_recorded_after_last_complete_frame(monkeypatch):
    stream, sock = source(monkeypatch, packet() + b"V")
    assert len(list(stream)) == 1
    assert "truncated" in stream.error and sock.closed


def test_explicit_end_count_matches_successful_complete_frames(monkeypatch):
    stream, sock = source(monkeypatch, packet() + end_packet(1))
    assert len(list(stream)) == 1
    assert stream.error is None and sock.closed
    assert stream.stats()["end_received"] is True


@pytest.mark.parametrize("count", [0, 2])
def test_end_count_mismatch_is_rejected(monkeypatch, count):
    stream, _ = source(monkeypatch, packet() + end_packet(count))
    assert len(list(stream)) == 1
    assert "end count" in stream.error


def test_end_without_any_frames_is_rejected(monkeypatch):
    stream, _ = source(monkeypatch, end_packet(0))
    assert list(stream) == []
    assert "end count" in stream.error


def test_truncated_end_is_failure(monkeypatch):
    stream, _ = source(monkeypatch, packet() + end_packet(1)[:-1])
    assert len(list(stream)) == 1
    assert "truncated" in stream.error


def test_eof_without_explicit_end_is_failure(monkeypatch):
    stream, _ = source(monkeypatch, packet())
    assert len(list(stream)) == 1
    assert "disconnected" in stream.error


def test_seconds_are_board_source_time(monkeypatch):
    second = metadata(source_sequence=11, source_ts_us=2_100_000, pl_frame_id=2, motion_valid=True)
    stream, _ = source(monkeypatch, packet() + packet(second))
    stream.seconds = 1
    assert len(list(stream)) == 1
    assert stream.error is None


def test_hardware_motion_reaches_contract_and_first_frame_is_refused(monkeypatch, workdir):
    from backend import run_pipeline as pipeline
    from backend.contract import validate_frame
    from backend.quality import QualityScorer

    # Intentionally keep CPU motion zero; packet motion must win. Test inputs
    # are fabricated fixtures and cannot serve as hardware correctness evidence.
    monkeypatch.setattr(QualityScorer, "update", lambda *a: {
        "light_score": 1.0, "motion_score": 0.0, "overall": 1.0})
    second = metadata(source_sequence=12, source_ts_us=1_200_000, pl_frame_id=2,
                      motion_valid=True, motion_pixels=129600 // 8,
                      motion_ratio_q16=8192, diff_total=129600)
    stream, _ = source(monkeypatch, packet() + packet(second))
    monkeypatch.setattr(pipeline, "open_frame_source", lambda *a, **k: (stream, "board:test"))
    output = workdir / "stream.jsonl"
    summary = workdir / "summary.json"
    rc = pipeline.main(["--source", "board:192.168.2.2:9000", "--limit", "2", "--stub",
                        "--quiet", "--no-done", "--json", str(workdir / "last.json"),
                        "--jsonl", str(output), "--summary", str(summary)])
    assert rc == 0
    got = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert got[0]["quality"]["overall"] == 0
    assert got[0]["status"] == "unreliable"
    assert all(v is None for v in got[0]["vital"].values())
    assert got[1]["quality"]["motion_score"] == round(min(1, .125 * load_config()["motion_score_gain"]), 4)
    assert got[1]["ts"] == .2
    assert all(not validate_frame(fr) for fr in got)
    report = json.loads(summary.read_text(encoding="utf-8"))
    assert report["board_source"]["valid_motion_frames"] == 1
    assert report["sampling"] is None


def test_pipeline_disconnect_keeps_outputs_and_exits_nonzero(monkeypatch, workdir):
    from backend import run_pipeline as pipeline
    stream, _ = source(monkeypatch, packet() + b"V")
    monkeypatch.setattr(pipeline, "open_frame_source", lambda *a, **k: (stream, "board:test"))
    output, summary = workdir / "stream.jsonl", workdir / "summary.json"
    rc = pipeline.main(["--source", "board:192.168.2.2:9000", "--stub", "--quiet",
                        "--json", str(workdir / "last.json"), "--jsonl", str(output),
                        "--summary", str(summary)])
    assert rc == 4
    got = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert got[-1]["status"] == "disconnected"
    assert (workdir / "last.json").exists()
    assert "truncated" in json.loads(summary.read_text(encoding="utf-8"))["board_source"]["error"]


def test_packet_timeout_records_failure_without_reconnecting(monkeypatch):
    class TimeoutSocket(MemorySocket):
        def recv(self, count):
            raise TimeoutError("test receive timeout")
    sock = TimeoutSocket(b"")
    monkeypatch.setattr(board.socket, "create_connection", lambda *a, **k: sock)
    stream = board.BoardFrameSource("board:192.168.2.2:9000", motion_threshold=16)
    assert list(stream) == []
    assert stream.error == "test receive timeout" and sock.closed


def test_connection_failure_writes_zero_frame_summary(monkeypatch, workdir):
    from backend import run_pipeline as pipeline
    def fail(*a, **k):
        raise ConnectionRefusedError("test refused")
    monkeypatch.setattr(board.socket, "create_connection", fail)
    summary = workdir / "summary.json"
    assert pipeline.main(["--source", "board:192.168.2.2:9000", "--stub", "--quiet",
                          "--summary", str(summary), "--json", str(workdir / "last.json")]) == 2
    report = json.loads(summary.read_text(encoding="utf-8"))
    assert report["frames_processed"] == 0
    assert report["board_source"]["error"] == "test refused"


def test_fast_channel_reduction_preserves_full_range_without_overflow():
    import numpy as np
    image = np.empty((board.HEIGHT, board.WIDTH, 3), dtype=np.uint8)
    image[:] = [255, 128, 0]
    assert board._channel_sums(image) == [board.WIDTH * board.HEIGHT * v for v in (255, 128, 0)]
    rng = np.random.default_rng(31)
    image[:] = rng.integers(0, 256, image.shape, dtype=np.uint8)
    assert board._channel_sums(image) == image.sum(axis=(0, 1), dtype=np.uint64).tolist()
