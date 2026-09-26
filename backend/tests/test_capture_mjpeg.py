"""test_capture_mjpeg.py —— 旁路 MJPEG 帧源（`--source mjpeg:URL`）。

两层：

1. **解析器**（`iter_mjpeg_jpegs`）离线可复现 —— 分块边界故意切在难处（头部中间、
   JPEG 中间、一字节一字节喂）。网络坏掉是难复现的，解析必须不是。
2. **整条帧源**用真的 `http.server` 起一个 MJPEG 流（不外连、不碰硬件），
   验证 `open_frame_source("mjpeg:http://127.0.0.1:<port>/video.mjpg")` 拿到的
   帧数、形状、以及**像素内容确实是我们推上去的那几张**（顺序 + 内容都验）。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.capture import iter_mjpeg_jpegs, open_frame_source

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")


def _jpeg(color: tuple[int, int, int], size: int = 32) -> bytes:
    """造一张纯色 JPEG（BGR）。"""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:, :] = color
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return bytes(buf.tobytes())


def _part(jpeg: bytes) -> bytes:
    """按 backend/api.py 的 /video.mjpg 格式包一个部件。"""
    return (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")


def _feed(data: bytes, cuts: list[int]):
    """把 data 按给定切点切成块——用来模拟 TCP 任意分块。"""
    out, prev = [], 0
    for c in cuts:
        out.append(data[prev:c])
        prev = c
    out.append(data[prev:])
    return [c for c in out if c]


# ---------------------------------------------------------------------------
# 1. 解析器
# ---------------------------------------------------------------------------


def test_parse_two_parts_whole():
    a, b = _jpeg((0, 0, 255)), _jpeg((0, 255, 0))
    got = list(iter_mjpeg_jpegs(iter([_part(a) + _part(b)])))
    assert got == [a, b]


@pytest.mark.parametrize("cuts", [
    [10],                       # 断在第一个头部里
    [25],                       # 断在 JPEG 载荷里
    None,                       # 一字节一字节喂（最坏情况）
    [7, 31, 60, 61, 200],       # 一堆碎块
])
def test_parse_survives_any_chunking(cuts):
    a, b = _jpeg((255, 0, 0)), _jpeg((128, 128, 128))
    data = _part(a) + _part(b)
    chunks = [bytes([x]) for x in data] if cuts is None else _feed(data, cuts)
    assert list(iter_mjpeg_jpegs(iter(chunks))) == [a, b]


def test_parse_resyncs_after_garbage():
    """流前面有垃圾 / 有个没有 Content-Length 的部件时不能崩，也不能吞掉后面的真帧。"""
    a = _jpeg((10, 20, 30))
    stream = b"garbage from a proxy\r\n\r\n--frame\r\nX-No-Length: 1\r\n\r\n" + _part(a)
    assert list(iter_mjpeg_jpegs(iter([stream]))) == [a]


def test_parse_holds_incomplete_tail():
    """载荷没到齐的那一帧不能被吐出来（宁可少一帧，不能给半张图）。"""
    a, b = _jpeg((1, 2, 3)), _jpeg((4, 5, 6))
    stream = _part(a) + _part(b)
    truncated = stream[: len(_part(a)) + 20]
    assert list(iter_mjpeg_jpegs(iter([truncated]))) == [a]


def test_parse_rejects_bogus_length():
    """长度离谱（超过上限）的部件要跳过，不能让 4 GB 的分配把进程打死。"""
    a = _jpeg((9, 9, 9))
    bogus = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 999999999\r\n\r\n"
    assert list(iter_mjpeg_jpegs(iter([bogus + _part(a)]))) == [a]


# ---------------------------------------------------------------------------
# 2. 真起一个 HTTP MJPEG 服务，走完整条帧源
# ---------------------------------------------------------------------------


class _MjpegHandler(BaseHTTPRequestHandler):
    body = b""
    hits = 0

    def do_GET(self):  # noqa: N802（BaseHTTPRequestHandler 的命名）
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):  # 静音：测试输出里不要 BaseHTTP 的访问日志
        pass


@pytest.fixture()
def mjpeg_server():
    # 三帧：BGR 纯红 / 纯绿 / 纯蓝，注意 OpenCV 编码进来、解码出来都应按 BGR 还原
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0)]
    _MjpegHandler.body = b"".join(_part(_jpeg(c, 48)) for c in colors)
    _MjpegHandler.hits = 0
    srv = HTTPServer(("127.0.0.1", 0), _MjpegHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/video.mjpg", colors
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_frame_source_end_to_end(mjpeg_server):
    url, colors = mjpeg_server
    it, desc = open_frame_source(f"mjpeg:{url}", limit=3)
    assert desc == f"mjpeg:{url}"
    frames = list(it)
    assert len(frames) == 3
    assert [f.frame_id for f in frames] == [0, 1, 2]
    for fr, want in zip(frames, colors):
        assert fr.shape == (48, 48, 3)
        # 中心像素应当就是推上去的那个颜色（JPEG 有损，逐通道给 ±12 容差）
        got = fr.image[24, 24].astype(int)
        assert all(abs(int(g) - int(w)) <= 12 for g, w in zip(got, want)), (got, want)


def test_frame_source_limit_stops_early(mjpeg_server):
    """limit=1 时只解一帧就停 —— 不能把整条无限流读完。"""
    url, _ = mjpeg_server
    it, _ = open_frame_source(f"mjpeg:{url}", limit=1)
    assert len(list(it)) == 1
