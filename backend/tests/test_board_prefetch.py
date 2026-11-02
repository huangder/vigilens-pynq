"""Synthetic receiver scheduling tests, not board performance evidence."""
import threading
import pytest

from backend.board_source import BoardFrameSource


def test_receiver_overlaps_consumer_but_preserves_every_frame(monkeypatch):
    reached_three, reached_four = threading.Event(), threading.Event()

    def frames(self):
        for index in range(8):
            if index == 3:
                reached_three.set()
            if index == 4:
                reached_four.set()
            yield index

    monkeypatch.setattr(BoardFrameSource, "_read_frames", frames)
    source = BoardFrameSource("board:127.0.0.1:9000", motion_threshold=16)
    received = iter(source)
    assert next(received) == 0
    assert reached_three.wait(2)
    # Two pending items (1,2), one being offered (3); memory is bounded.
    assert not reached_four.is_set()
    assert list(received) == list(range(1, 8))
    assert source.error is None and source.prefetch_peak == 2


def test_early_close_cancels_a_full_pending_queue(monkeypatch):
    closed = threading.Event()

    def frames(self):
        try:
            yield from range(100)
        finally:
            closed.set()

    monkeypatch.setattr(BoardFrameSource, "_read_frames", frames)
    source = BoardFrameSource("board:127.0.0.1:9000", motion_threshold=16)
    received = iter(source)
    assert next(received) == 0
    received.close()
    assert closed.wait(1)
    assert not [t for t in threading.enumerate() if t.name == "board-receiver"]


def test_worker_exception_does_not_hide_already_received_frames(monkeypatch):
    def frames(self):
        yield 0
        yield 1
        raise RuntimeError("test decoder failure")

    monkeypatch.setattr(BoardFrameSource, "_read_frames", frames)
    source = BoardFrameSource("board:127.0.0.1:9000", motion_threshold=16)
    assert list(source) == [0, 1]
    assert source.error == "test decoder failure"
    assert not [t for t in threading.enumerate() if t.name == "board-receiver"]


def test_source_reuse_fails_instead_of_reconnecting_or_waiting_forever(monkeypatch):
    monkeypatch.setattr(BoardFrameSource, "_read_frames", lambda self: (x for x in [0]))
    source = BoardFrameSource("board:127.0.0.1:9000", motion_threshold=16)
    assert list(source) == [0]
    with pytest.raises(RuntimeError, match="single-use"):
        list(source)
