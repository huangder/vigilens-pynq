"""Synthetic checks for evidence integrity, not human accuracy evidence."""
import io
import json
import struct

import pytest
from PIL import Image

from metrics.scripts import human_session as human


class FragmentedSocket:
    def __init__(self, data):
        self.data = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def settimeout(self, _):
        pass

    def recv(self, size):
        return self.data.read(min(size, 997))


def sample_stream():
    from backend.tests.test_board_source import metadata, end_packet
    out = io.BytesIO()
    Image.new("RGB", (1280, 720)).save(out, format="JPEG")
    jpeg = out.getvalue()
    import zlib
    packets = []
    for i, ts in enumerate([1_000_000, 1_045_000, 1_110_000]):
        meta = metadata(source_sequence=i * 2, source_ts_us=ts, pl_frame_id=i,
                        motion_valid=i > 0, codec="jpeg-mjpeg", jpeg_bytes=len(jpeg),
                        jpeg_crc32=zlib.crc32(jpeg), jpeg_decoder="ifast-no-fancy")
        raw = json.dumps(meta).encode()
        packets.append(struct.pack("!4sIII", b"VLP2", len(raw), len(jpeg),
                                   human.bs.RGB_BYTES) + raw + jpeg)
    return b"".join(packets) + end_packet(3), packets, jpeg


def test_capture_preserves_every_wire_byte_timestamp_and_index(monkeypatch, workdir):
    data, packets, jpeg = sample_stream()
    monkeypatch.setattr(human.socket, "create_connection", lambda *a, **k: FragmentedSocket(data))
    session = human.Session(workdir, "board:127.0.0.1:9000", None)
    session.capture()
    assert session.state["status"] == "review"
    assert (workdir / "private.vlp").read_bytes() == data
    assert [x["ts"] for x in session.index] == [0, .045, .11]
    assert [x["offset"] for x in session.index] == [0, len(packets[0]), len(packets[0]) + len(packets[1])]
    assert human.jpeg_from_packet(packets[1]) == jpeg
    lock = json.loads((workdir / "capture.json").read_text(encoding="utf-8"))
    assert lock["wire_sha256"] == human.sha256(workdir / "private.vlp")
    assert lock["plan_is_ground_truth"] is False


def test_truncated_capture_cannot_become_reviewed_evidence(monkeypatch, workdir):
    data, _, _ = sample_stream()
    monkeypatch.setattr(human.socket, "create_connection", lambda *a, **k: FragmentedSocket(data[:-9]))
    session = human.Session(workdir, "board:127.0.0.1:9000", None)
    session.capture()
    assert session.state["status"] == "error"
    assert not (workdir / "capture.json").exists()
    with pytest.raises(ValueError, match="finish successfully"):
        session.save_review({"reviewer": "human", "reviewed_all": True, "rows": []})


@pytest.mark.parametrize("row", [
    {"start_frame": -1, "end_frame": 4, "event": "blink"},
    {"start_frame": 4, "end_frame": 3, "event": "blink"},
    {"start_frame": 0, "end_frame": 10, "event": "blink"},
    {"start_frame": True, "end_frame": 2, "event": "blink"},
    {"start_frame": 0, "end_frame": 2, "event": "inferred_fatigue"},
])
def test_annotations_refuse_invalid_events_or_frame_ranges(row):
    with pytest.raises(ValueError):
        human.validate_labels([row], 10)


def test_review_requires_human_confirmation_and_binds_actual_labels(monkeypatch, workdir):
    data, _, _ = sample_stream()
    monkeypatch.setattr(human.socket, "create_connection", lambda *a, **k: FragmentedSocket(data))
    session = human.Session(workdir, "board:127.0.0.1:9000", None)
    session.capture()
    request = {"reviewer": "", "reviewed_all": True,
               "rows": [{"start_frame": 0, "end_frame": 1, "event": "blink"}]}
    with pytest.raises(ValueError, match="human reviewer"):
        session.save_review(request)
    request["reviewer"] = "test fixture (synthetic)"
    request["reviewed_all"] = False
    with pytest.raises(ValueError):
        session.save_review(request)
    request["reviewed_all"] = True
    session.save_review(request)
    review = json.loads((workdir / "human_review.json").read_text(encoding="utf-8"))
    assert review["annotations_sha256"] == human.sha256(workdir / "annotations.csv")
    assert review["wire_sha256"] == human.sha256(workdir / "private.vlp")
    assert review["annotation_source"] == "human_manual"


def test_planned_cues_never_claim_to_be_observations():
    assert human.phase_at(0)["id"] == 0
    assert human.phase_at(10)["id"] == 1
    assert human.phase_at(sum(x[0] for x in human.PLAN))["id"] == len(human.PLAN)


def test_quick_review_keeps_unknown_counts_unknown_and_requires_human(monkeypatch, workdir):
    data, _, _ = sample_stream()
    monkeypatch.setattr(human.socket, "create_connection", lambda *a, **k: FragmentedSocket(data))
    session = human.Session(workdir, "board:127.0.0.1:9000", None)
    session.capture()
    request = {"reviewer": "synthetic test", "reviewed_all": True, "rows": [
        {"id": 0, "confirmed": True, "execution": "partial",
         "counts": {"blink": None, "long_close": 0, "yawn": 1}}]}
    session.save_quick_review(request)
    counts = json.loads((workdir / "quick_counts.json").read_text(encoding="utf-8"))
    assert counts[0]["counts"]["blink"] is None
    review = json.loads((workdir / "quick_human_review.json").read_text(encoding="utf-8"))
    assert review["frame_level_ground_truth"] is False
    assert review["annotation_source"] == "human_segment_counts"
    request["rows"][0]["counts"]["blink"] = True
    with pytest.raises(ValueError, match="非负整数"):
        session.save_quick_review(request)
