"""同源 JPEG 预览旁路的真实 HTTP 回归测试。"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from backend.api import PREVIEW_MAX_BYTES, PREVIEW_STORE, create_app, validate_preview


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


@pytest.fixture
def preview_api() -> Iterator[str]:
    import uvicorn

    PREVIEW_STORE.clear()
    port = _free_port()
    config = uvicorn.Config(create_app(mock=False, mount_frontend=False), host="127.0.0.1",
                            port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/status", timeout=0.2):  # noqa: S310
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.02)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("预览 API 测试服务启动超时")
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        PREVIEW_STORE.clear()


def _headers(frame_id: int = 7, *, bbox: tuple[int, int, int, int] = (10, 20, 100, 120)) -> dict[str, str]:
    x, y, w, h = bbox
    return {
        "Content-Type": "image/jpeg",
        "X-VigiLens-Frame-Id": str(frame_id),
        "X-VigiLens-Ts": str(frame_id / 45),
        "X-VigiLens-Bbox-X": str(x),
        "X-VigiLens-Bbox-Y": str(y),
        "X-VigiLens-Bbox-W": str(w),
        "X-VigiLens-Bbox-H": str(h),
        "X-VigiLens-Source-Width": "640",
        "X-VigiLens-Source-Height": "480",
        "X-VigiLens-Face-Visible": "0.91",
        "X-VigiLens-Status": "normal",
        "X-VigiLens-Detector": "mediapipe",
    }


def _request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310
            return int(resp.status), {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), {k.lower(): v for k, v in e.headers.items()}, e.read()


def test_preview_endpoint_roundtrip_etag_and_latest_only(preview_api: str) -> None:
    code, _, body = _request(f"{preview_api}/api/preview/latest")
    assert code == 404 and "尚未收到" in body.decode("utf-8")

    first = b"\xff\xd8first\xff\xd9"
    code, _, body = _request(f"{preview_api}/api/preview", data=first, headers=_headers(7))
    assert code == 200 and json.loads(body)["preview"]["frame_id"] == 7

    code, response_headers, body = _request(f"{preview_api}/api/preview/latest")
    assert code == 200 and body == first
    assert response_headers["x-vigilens-frame-id"] == "7"
    assert response_headers["x-vigilens-detector"] == "mediapipe"
    etag = response_headers["etag"]

    code, _, body = _request(f"{preview_api}/api/preview/latest", headers={"If-None-Match": etag})
    assert code == 304 and body == b""

    second = b"\xff\xd8second\xff\xd9"
    assert _request(f"{preview_api}/api/preview", data=second, headers=_headers(8))[0] == 200
    code, response_headers, body = _request(f"{preview_api}/api/preview/latest")
    assert code == 200 and body == second
    assert response_headers["x-vigilens-frame-id"] == "8"

    status, _, status_body = _request(f"{preview_api}/api/status")
    preview = json.loads(status_body)["preview"]
    assert status == 200 and preview["frame_id"] == 8 and preview["bytes"] == len(second)


@pytest.mark.parametrize(
    ("data", "headers", "expected"),
    [
        (b"not jpeg", {**_headers(), "Content-Type": "application/octet-stream"}, 415),
        (b"not jpeg", _headers(), 422),
        (b"\xff\xd8x\xff\xd9", {k: v for k, v in _headers().items()
                                   if k != "X-VigiLens-Frame-Id"}, 422),
        (b"\xff\xd8x\xff\xd9", _headers(bbox=(600, 20, 100, 120)), 422),
    ],
)
def test_preview_endpoint_rejects_bad_input(preview_api: str, data: bytes,
                                            headers: dict[str, str], expected: int) -> None:
    code, _, body = _request(f"{preview_api}/api/preview", data=data, headers=headers)
    assert code == expected
    assert json.loads(body)["errors"]


def test_preview_size_limit_is_checked_without_allocating_server_history() -> None:
    with pytest.raises(ValueError, match="超过上限"):
        validate_preview(b"\xff\xd8" + b"x" * PREVIEW_MAX_BYTES + b"\xff\xd9",
                         "image/jpeg", _headers())


def test_preview_detector_header_is_validated(preview_api: str) -> None:
    headers = {**_headers(), "X-VigiLens-Detector": "not-a-detector"}
    code, _, body = _request(f"{preview_api}/api/preview", data=b"\xff\xd8x\xff\xd9", headers=headers)
    assert code == 422 and "detector" in json.loads(body)["errors"][0]
