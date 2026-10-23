"""台账 BUG-014：mock 泵必须给真实数据让位（真实 ingest 一到就停）。

为什么这条测试值得存在：
`backend/api.py` 默认 `mock=True`，mock 帧与 A 线 POST 进来的真实帧进的是**同一条总线**。
两者混在一起时，网页上分不出哪个数字是测出来的 —— 那是《05》铁律 1 说的"编造数字"的近亲。
本文件的断言是：真实帧一到，`/api/status` 的 `source` 必须变成 `ingest`、`mock_active` 变 false，
且 `published` **不再增长**（泵真的停了，不是只改了个显示字段）。

走的是真实 HTTP（与 `test_preview_api.py` 同一范式），不 mock 掉 uvicorn ——
因为要验的正是"后台线程与请求线程共享状态"这件事。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from backend import api as api_mod
from backend.api import create_app
from backend.mock import mock_frame


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _get_json(url: str, timeout: float = 2.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _post_frame(base: str, frame: dict) -> dict:
    body = json.dumps({"frame": frame}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/api/ingest", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2.0) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _wait_ready(base: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _get_json(base + "/api/status", timeout=0.2)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.02)
    raise AssertionError(f"服务没有在 {timeout}s 内起来：{base}")


@pytest.fixture
def mock_api() -> Iterator[str]:
    """起一个**默认 mock 模式**的真服务（hz=50，让 mock 帧密集到能看出"停没停"）。"""
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(create_app(mock=True, hz=50.0, mount_frontend=False),
                            host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    _wait_ready(base)
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=3)


def _real_frame():
    """一帧**合法契约帧**（借 mock_frame 的骨架，但来源将被标成 ingest）。"""
    frame = mock_frame(9001)
    frame["status"] = "normal"
    return frame


def test_status_says_mock_before_any_real_frame(mock_api: str) -> None:
    """还没有真实数据时：`source=mock` 且 `mock_active=true` —— 页面上该有"这是 mock"的根据。"""
    st = _get_json(mock_api + "/api/status")
    assert st["mock_active"] is True
    assert st["source"] == "mock"


def test_real_frame_stops_mock_pump(mock_api: str) -> None:
    """POST 一帧真实帧后：source 变 ingest、mock_active 变 false、published 不再增长。"""
    got = _post_frame(mock_api, _real_frame())
    assert got["ok"] is True

    st = _get_json(mock_api + "/api/status")
    assert st["mock_active"] is False, "收到真实帧后 mock 必须让位（BUG-014）"
    assert st["source"] == "ingest"

    # 泵真的停了：等 5 个周期（hz=50 ⇒ 每帧 20 ms）再看计数
    before = st["published"]
    time.sleep(0.3)
    after = _get_json(mock_api + "/api/status")["published"]
    assert after == before, f"mock 泵还在推帧：published {before} → {after}"


def test_real_frame_wins_even_if_mock_pushed_first(mock_api: str) -> None:
    """顺序无关：先让 mock 推几帧，再推真实帧，结果必须一样（真实数据在后也不能被 mock 盖掉）。"""
    time.sleep(0.1)  # 让 mock 至少推过一帧
    first = _get_json(mock_api + "/api/status")
    assert first["source"] == "mock"

    _post_frame(mock_api, _real_frame())
    st = _get_json(mock_api + "/api/status")
    assert st["source"] == "ingest"
    assert st["mock_active"] is False


def test_stop_mock_pump_is_idempotent() -> None:
    """`stop_mock_pump()` 只报一次"从在推到已停"；重复调用返回 False（不刷屏、不改语义）。"""
    api_mod._MOCK_STOP.clear()
    api_mod._MOCK_YIELD_LOGGED.clear()
    try:
        assert api_mod.stop_mock_pump(reason="单测") is True
        assert api_mod._MOCK_STOP.is_set() is True
        assert api_mod.stop_mock_pump(reason="单测") is False
    finally:
        # 收尾：把闸松开，免得影响后续用例（它们各自 create_app 时也会清）
        api_mod._MOCK_STOP.clear()
        api_mod._MOCK_YIELD_LOGGED.clear()


def test_mock_off_never_reports_mock() -> None:
    """`--no-mock` 起服务时，`source` 恒为 ingest、`mock_active` 恒 false（不因没数据而说 mock）。"""
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(create_app(mock=False, mount_frontend=False),
                            host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        st = _get_json(base + "/api/status")
        assert st["mock_active"] is False
        assert st["source"] == "ingest"
    finally:
        server.should_exit = True
        thread.join(timeout=3)
