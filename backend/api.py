"""api.py —— FastAPI REST + WebSocket + 静态前端（《02》任务 B1）。

对外接口（契约 docs/interface.md 第 1 节）：
    GET  /api/status    最新一帧 + 总线状态
    GET  /api/metrics   最新一帧 + 最近 N 帧历史（趋势曲线用）
    GET  /api/events    状态变化事件日志（事件列表用）
    POST /api/ingest    **M2 集成点**：A 线把契约帧推进来，B 线立即广播
    WS   /ws            1 帧/秒推送
    GET  /              直接托管 frontend/（省掉跨域与 file:// 的麻烦）

数据源开关（《02》M2 只改这一处）：
    python backend/api.py                 # mock 模式：B 线独立开发
    python backend/api.py --no-mock       # 只广播 ingest 进来的真实帧：M2 集成后
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

try:
    from .config import REPO_ROOT, load_config
    from .hub import HUB
    from .mock import mock_frame, mock_stream
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import REPO_ROOT, load_config
    from hub import HUB
    from mock import mock_frame, mock_stream

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

HISTORY_MAX = 600          # 最近 10 分钟（1 Hz）
EVENTS_MAX = 200

_history: deque[dict] = deque(maxlen=HISTORY_MAX)
_events: deque[dict] = deque(maxlen=EVENTS_MAX)
_lock = threading.Lock()
_last_status: str | None = None


def ingest(frame: dict, *, source: str = "unknown") -> dict:
    """接收一帧（来自 mock 循环、A 线 POST、或进程内 publish）。"""
    global _last_status
    with _lock:
        _history.append(frame)
        if frame.get("status") != _last_status:
            _events.append({
                "ts": frame.get("ts"),
                "frame_id": frame.get("frame_id"),
                "from": _last_status,
                "to": frame.get("status"),
                "advice": frame.get("advice"),
                "reason": frame.get("reason"),
                "source": source,
            })
            _last_status = frame.get("status")
    HUB.publish(frame)
    return frame


def _mock_pump(hz: float) -> None:
    """后台线程：按 hz 把 mock 帧推进总线。用线程而不是 asyncio，A 线同步代码也能复用。"""
    period = 1.0 / hz if hz > 0 else 0.0
    fid = 0
    while True:
        ingest(mock_frame(fid), source="mock")
        fid += 1
        if period:
            time.sleep(period)


def create_app(*, mock: bool = True, hz: float = 1.0, mount_frontend: bool = True) -> Any:
    if not HAVE_FASTAPI:
        raise SystemExit(
            "缺少 fastapi / uvicorn：pip install -r requirements.txt\n"
            "  （只想看界面的话不用装：直接浏览器打开 frontend/index.html，页面内置离线 mock）"
        )

    cfg = load_config()
    app = FastAPI(title="VigiLens API", version="0.1.0",
                  description="知倦 / VigiLens —— 无接触疲劳与生命体征趋势监测终端（后端接口）")

    if mock:
        threading.Thread(target=_mock_pump, args=(hz,), daemon=True).start()

    @app.get("/api/status")
    def api_status() -> dict:
        latest = HUB.latest
        if latest is None:
            latest = mock_frame(-1, status="disconnected", ts=None)
        return {
            "ok": True,
            "source": "mock" if mock else "ingest",
            "published": HUB.published,
            "subscribers": HUB.subscriber_count,
            "frame": latest,
            "server_time": round(time.time(), 3),
        }

    @app.get("/api/metrics")
    def api_metrics(limit: int = 120) -> dict:
        with _lock:
            hist = list(_history)[-max(1, min(limit, HISTORY_MAX)):]
        return {
            "ok": True,
            "count": len(hist),
            "latest": hist[-1] if hist else None,
            "history": [
                {"ts": f.get("ts"), "frame_id": f.get("frame_id"),
                 "blink_rate_per_min": f["behavior"]["blink_rate_per_min"],
                 "perclos": f["behavior"]["perclos"],
                 "hr_bpm": f["vital"]["hr_bpm"],
                 "quality_overall": f["quality"]["overall"],
                 "status": f.get("status")}
                for f in hist
            ],
        }

    @app.get("/api/events")
    def api_events(limit: int = 50) -> dict:
        with _lock:
            evs = list(_events)[-max(1, min(limit, EVENTS_MAX)):]
        return {"ok": True, "count": len(evs), "events": evs}

    @app.post("/api/ingest")
    async def api_ingest(payload: dict) -> JSONResponse:
        """M2 集成点：A 线 run_pipeline --post 或任意客户端把契约帧推到这里。"""
        try:
            from .contract import validate_frame
        except ImportError:
            from contract import validate_frame

        frame = payload.get("frame", payload)
        errs = validate_frame(frame)
        if errs:
            return JSONResponse(status_code=422,
                               content={"ok": False, "errors": errs})
        ingest(frame, source="ingest")
        return JSONResponse(content={"ok": True, "published": HUB.published})

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        q = HUB.subscribe()
        timeout = float(cfg["ws_disconnect_timeout_s"])
        try:
            while True:
                try:
                    frame = q.get_nowait()
                except Exception:  # noqa: BLE001 —— queue.Empty
                    await asyncio.sleep(min(timeout, 0.2))
                    if HUB.latest is None:
                        frame = mock_frame(-1, status="disconnected", ts=None)
                    else:
                        continue
                await websocket.send_text(json.dumps(frame, ensure_ascii=False))
        except WebSocketDisconnect:
            pass
        finally:
            HUB.unsubscribe(q)

    if mount_frontend:
        fe = REPO_ROOT / "frontend"
        if fe.is_dir():
            app.mount("/", StaticFiles(directory=str(fe), html=True), name="frontend")

    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VigiLens 后端服务（REST + WebSocket + 静态前端）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-mock", action="store_true", help="不产生 mock 数据，只广播 POST /api/ingest 进来的真实帧")
    ap.add_argument("--hz", type=float, default=None)
    ap.add_argument("--no-frontend", action="store_true", help="不托管 frontend/")
    args = ap.parse_args(argv)

    hz = float(load_config()["ws_push_hz"]) if args.hz is None else args.hz
    if not HAVE_FASTAPI:
        print("缺少 fastapi / uvicorn。安装：pip install -r requirements.txt\n"
              "若只想看界面：直接用浏览器打开 frontend/index.html（内置离线 mock 兜底）。",
              file=sys.stderr)
        return 1

    import uvicorn  # type: ignore

    app = create_app(mock=not args.no_mock, hz=hz, mount_frontend=not args.no_frontend)
    print(f"[api] http://{args.host}:{args.port}/          ← 仪表盘（托管 frontend/）")
    print(f"[api] http://{args.host}:{args.port}/api/status")
    print(f"[api] ws://{args.host}:{args.port}/ws           ← 1 帧/秒推送")
    print(f"[api] 数据源：{'mock（B 线独立开发）' if not args.no_mock else 'ingest（M2 集成）'}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
