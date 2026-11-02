"""Synthetic preview concurrency tests; no physical frame-rate evidence."""
import threading
import urllib.error

import numpy as np
import pytest

from backend import publish


def metadata(fid):
    return dict(frame_id=fid, ts=fid / 10, bbox=[fid, 0, 1, 1], source_width=2,
                source_height=2, face_visible=1.0, status="normal", detector="mediapipe")


def test_slow_preview_keeps_only_latest_pixels_with_matching_metadata(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    sent = []

    def http(url, data, headers, timeout):
        if not sent:
            entered.set()
            assert release.wait(3), "test did not release the blocked HTTP request"
        sent.append((data, headers))
        return 200, "{}"

    monkeypatch.setattr(publish, "_http_post_bytes", http)
    poster = publish.LatestPreviewPoster("http://local/api/preview", hz=0,
                                        encoder=lambda image: bytes([int(image[0, 0, 0])]))
    try:
        assert poster.offer(np.zeros((2, 2, 3), np.uint8), **metadata(0))
        assert entered.wait(2)
        frame = np.ones((2, 2, 3), np.uint8)
        assert poster.offer(frame, **metadata(1))
        frame[:] = 2
        meta = metadata(2)
        assert poster.offer(frame, **meta)
        frame[:] = 99
        meta["bbox"][0] = 99
    finally:
        release.set()
        poster.stop()
    assert [data for data, _ in sent] == [b"\x00", b"\x02"]
    assert [headers["X-VigiLens-Frame-Id"] for _, headers in sent] == ["0", "2"]
    assert sent[-1][1]["X-VigiLens-Bbox-X"] == "2"
    stats = poster.stats()
    assert stats["offered"] == 3 and stats["replaced"] == 1
    assert stats["posted"] == stats["encoded"] == 2
    assert not stats["worker_alive"] and not stats["errors"]


def test_preview_is_throttled_before_encoding(monkeypatch):
    encoded = []
    monkeypatch.setattr(publish, "_http_post_bytes", lambda *a: (200, "{}"))
    with publish.LatestPreviewPoster("http://local/api/preview", hz=1,
            encoder=lambda image: encoded.append(1) or b"jpeg") as poster:
        assert poster.offer(np.zeros((2, 2, 3), np.uint8), **metadata(0))
        assert not poster.offer(np.zeros((2, 2, 3), np.uint8), **metadata(1))
        assert not poster.offer(np.zeros((2, 2, 3), np.uint8), **metadata(2))
    assert len(encoded) == 1
    assert poster.stats()["throttled"] == 2 and poster.posted == 1
    assert not poster.offer(np.zeros((2, 2, 3), np.uint8), **metadata(3))


@pytest.mark.parametrize("failure", ["http", "encoder"])
def test_background_failure_is_recorded_and_worker_stops(monkeypatch, failure):
    def http(*args):
        raise urllib.error.URLError("test offline")
    monkeypatch.setattr(publish, "_http_post_bytes", http)
    with publish.LatestPreviewPoster("http://local/api/preview",
            encoder=lambda image: None if failure == "encoder" else b"jpeg") as poster:
        poster.offer(np.zeros((2, 2, 3), np.uint8), **metadata(0))
    assert poster.errors and poster.posted == 0
    assert not poster.stats()["worker_alive"]
    assert poster.image_available is (failure != "encoder")
