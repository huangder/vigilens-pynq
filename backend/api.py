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
import queue
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

# 判定证据链（A 线 decision.py 的 triggers）：**不走契约帧**，走旁路。
# 形状：{"frame_id": 12, "items": [{"rule": ..., "metric": ..., "value": ..., "threshold": ..., "verdict": ...}, ...]}
# 为什么是旁路：契约 §1 规定帧的顶层字段只能是那 9 个，多一个就是非法帧；
# 而证据链是"给界面看的解释"，不是测量数据。详见 docs/08_B线给A线的接口请求.md。
_latest_triggers: dict[str, Any] | None = None


def ingest(frame: dict, *, source: str = "unknown", triggers: list | None = None) -> dict:
    """接收一帧（来自 mock 循环、A 线 POST、或进程内 publish）。"""
    global _last_status, _latest_triggers
    with _lock:
        _history.append(frame)
        if triggers is not None:
            _latest_triggers = {"frame_id": frame.get("frame_id"), "items": triggers}
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


def disconnect_frame(last: dict[str, Any] | None) -> dict[str, Any]:
    """断流兜底帧：**完整契约帧**，且序号不倒退。

    两个刻意的选择：
    · 用 `mock_frame(-1, status="disconnected")`（而不是 `hub.DISCONNECT_HINT` 那种提示字典）——
      契约 §1 的 schema 是给**所有**消费者的，发半截 dict 只有本页面能解析，
      第三方客户端会收到一帧"缺字段"的非法数据。本函数发出去的帧能过 `validate_frame`。
    · **沿用上一帧的 `frame_id` / `ts`**，只把 `status` 改成 `disconnected` ——
      契约 §1 要求 `ts`/`frame_id` 单调递增（供断点重连对齐）。沿用序号表达的是
      "自第 N 帧起没有新测量"，既不伪造一次不存在的测量，也不让序号倒退。
      副作用：断流期间的兜底帧序号相同，消费者据 `frame_id` 未变即可判断"没有新数据"。
    """
    frame = mock_frame(-1, status="disconnected", ts=None)
    if isinstance(last, dict):
        fid = last.get("frame_id")
        ts = last.get("ts")
        if isinstance(fid, int):
            frame["frame_id"] = fid
        if isinstance(ts, (int, float)):
            frame["ts"] = ts
    return frame


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

    def thresholds() -> dict[str, Any]:
        """门控阈值：**唯一来源是仓库根 `config.yaml`**，随 `/api/status` 下发给前端。

        为什么要有这个：前端是静态页面，读不到 yaml，只能在 `app.js` 里存一份副本。
        A 线标定后一改 `config.yaml`，那份副本就会**无声漂移**（症状：该报警却不报警，
        而且极难查）。把值下发出去后，前端优先用它，只有拿不到时才退回内置副本
        （双击 `index.html` 的 file:// 离线演示走这条路）。

        键名与 `config.yaml` 逐字一致，前端按同名键取值 —— 不做重命名，避免两套叫法。
        """
        keys = (
            "perclos_warning", "face_visible_min", "quality_min_score", "light_score_min",
            "motion_score_max", "vital_require_quality", "fatigue_long_close_count",
            "ws_disconnect_timeout_s",
        )
        return {k: cfg[k] for k in keys if k in cfg}

    @app.get("/api/status")
    def api_status() -> dict:
        latest = HUB.latest
        if latest is None:
            latest = mock_frame(-1, status="disconnected", ts=None)
        with _lock:
            triggers = _latest_triggers
        return {
            "ok": True,
            "source": "mock" if mock else "ingest",
            "published": HUB.published,
            "subscribers": HUB.subscriber_count,
            "frame": latest,
            "triggers": triggers,
            "thresholds": thresholds(),
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
        # 可选的旁路字段：判定证据链。**不塞进 frame**（那会让帧变成非法契约帧），
        # 与 frame 平级传进来。缺省即不带，行为与从前完全一致。
        triggers = payload.get("triggers")
        if triggers is not None and not isinstance(triggers, list):
            return JSONResponse(status_code=422, content={
                "ok": False,
                "errors": ["triggers 必须是数组（每项形如 {rule, metric, value, threshold, verdict}）"],
            })
        ingest(frame, source="ingest", triggers=triggers)
        return JSONResponse(content={"ok": True, "published": HUB.published})

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        q = HUB.subscribe()
        timeout = float(cfg["ws_disconnect_timeout_s"])
        try:
            while True:
                try:
                    # 在**线程里**带超时阻塞取帧（不能直接 q.get()：那会卡住事件循环）。
                    # 超时 = 数据中断，交给下面的兜底分支。
                    # ⚠️ 原实现用 get_nowait() + `continue`，一旦 HUB.latest 非空就
                    #    再也走不到兜底分支 —— 数据中断对第三方消费者是"静默"的
                    #    （A 线 2026-09-16 交接项，见 backend/A_LINE_DEV_STEPS.md §9 第 8 条）。
                    frame = await asyncio.to_thread(q.get, True, timeout)
                except queue.Empty:
                    frame = disconnect_frame(HUB.latest)
                await websocket.send_text(json.dumps(frame, ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            # RuntimeError：客户端已断开后继续 send 时 starlette 会抛它，不是服务端故障
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
