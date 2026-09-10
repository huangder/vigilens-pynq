"""websocket.py —— WebSocket 实时推送（《02》任务 B3）。

契约（docs/interface.md 第 1 节）：**1 帧/秒**，`ts` / `frame_id` 必须单调递增。
本服务三种数据源，切换只改一个参数 —— 这正是《02》说的"M2 换数据源开关"：

    --mode mock   内置 mock（B 线默认，不依赖 A）
    --mode file   回放 jsonl（A 线跑出来的 metrics/logs/stream.jsonl）
    --mode bus    转发 hub.HUB 里的实时帧（A 线在进程内 publish，M2 主用）

单独跑（不依赖 FastAPI）：
    python backend/websocket.py                 # ws://127.0.0.1:8765
    python backend/websocket.py --mode file --file metrics/logs/stream.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, AsyncIterator

try:
    from .config import load_config
    from .hub import HUB
    from .mock import mock_frame, mock_stream
    from .storage import load_jsonl
except ImportError:
    from config import load_config
    from hub import HUB
    from mock import mock_frame, mock_stream
    from storage import load_jsonl

try:
    import websockets  # type: ignore

    HAVE_WS = True
except ImportError:
    HAVE_WS = False


# ---------------------------------------------------------------------------
# 三种数据源 → 统一的 async 帧流
# ---------------------------------------------------------------------------

async def _stream_mock(hz: float) -> AsyncIterator[dict[str, Any]]:
    period = 1.0 / hz if hz > 0 else 0.0
    fid = 0
    while True:
        yield mock_frame(fid)
        fid += 1
        if period:
            await asyncio.sleep(period)


async def _stream_file(path: str, hz: float, loop: bool = True) -> AsyncIterator[dict[str, Any]]:
    frames = load_jsonl(path)
    if not frames:
        raise SystemExit(f"jsonl 里没有帧：{path}")
    period = 1.0 / hz if hz > 0 else 0.0
    while True:
        for fr in frames:
            yield fr
            if period:
                await asyncio.sleep(period)
        if not loop:
            return


async def _stream_bus(hz: float) -> AsyncIterator[dict[str, Any]]:
    """转发 hub 里的实时帧；总线上没数据时按 ws_disconnect_timeout_s 兜底 disconnected。"""
    q = HUB.subscribe()
    timeout = float(load_config()["ws_disconnect_timeout_s"])
    try:
        while True:
            try:
                yield q.get_nowait()
            except Exception:  # noqa: BLE001 —— queue.Empty，避免 import queue 只为一个异常
                await asyncio.sleep(min(timeout, 0.2))
                if HUB.latest is None and HUB.published == 0:
                    yield mock_frame(-1, status="disconnected", ts=None)
    finally:
        HUB.unsubscribe(q)


def make_stream(mode: str, hz: float, file: str | None) -> AsyncIterator[dict[str, Any]]:
    if mode == "mock":
        return _stream_mock(hz)
    if mode == "file":
        if not file:
            raise SystemExit("--mode file 需要 --file <jsonl>")
        return _stream_file(file, hz)
    if mode == "bus":
        return _stream_bus(hz)
    raise SystemExit(f"未知 --mode {mode!r}（可选 mock / file / bus）")


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

async def _handler(websocket: Any, mode: str, hz: float, file: str | None) -> None:
    peer = getattr(websocket, "remote_address", "?")
    print(f"[ws] 客户端接入 {peer}（mode={mode}, hz={hz}）")
    n = 0
    try:
        async for frame in make_stream(mode, hz, file):
            await websocket.send(json.dumps(frame, ensure_ascii=False))
            n += 1
    except Exception as exc:  # noqa: BLE001 —— 客户端断开是常态，不该打崩服务
        print(f"[ws] 客户端 {peer} 断开（已推 {n} 帧）：{type(exc).__name__}: {exc}")


async def serve(host: str, port: int, mode: str, hz: float, file: str | None) -> None:
    if not HAVE_WS:
        raise SystemExit(
            "缺少 websockets 包：pip install -r requirements.txt\n"
            "  （若只想看界面，可直接用浏览器打开 frontend/index.html —— 页面内置离线 mock 兜底）"
        )

    async def _h(ws: Any) -> None:
        await _handler(ws, mode, hz, file)

    async with websockets.serve(_h, host, port):
        print(f"[ws] 已启动：ws://{host}:{port}   mode={mode}  {hz} 帧/秒   Ctrl+C 停止")
        print(f"[ws] 前端连接地址：打开 frontend/index.html，把输入框里的地址填 ws://{host}:{port}")
        await asyncio.Future()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VigiLens WebSocket 推送服务（契约：1 帧/秒）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mode", default="mock", choices=["mock", "file", "bus"])
    ap.add_argument("--file", default=None, help="--mode file 时的 jsonl 路径")
    ap.add_argument("--hz", type=float, default=None, help="推送频率，默认取 config.yaml 的 ws_push_hz")
    args = ap.parse_args(argv)

    hz = float(load_config()["ws_push_hz"]) if args.hz is None else args.hz
    try:
        asyncio.run(serve(args.host, args.port, args.mode, hz, args.file))
    except KeyboardInterrupt:
        print("\n[ws] 已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
