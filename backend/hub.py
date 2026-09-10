"""hub.py —— 帧总线（A 线产出 → B 线消费的进程内交接点）。

> 说明：本文件**不在《04》第 3 节的目录清单里**，是三线并行需要的补充。
> 理由：`websocket.py`（B3）与 `api.py`（B1）都要"拿到最新一帧"，
> 如果各写一份缓存，M2 把 A 线真实输出接进来时会出现两个数据源不一致。
> 因此把"最新帧 + 订阅广播"抽成一个 40 行的小模块，两边共用。

线程模型：A 线在自己的线程/进程里 publish，B 线的 WebSocket 协程 subscribe。
用 threading.Lock + queue.Queue，不依赖 asyncio，因此 A 线的同步代码也能直接调。
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Iterator

DISCONNECT_HINT = {
    "status": "disconnected",
    "advice": "连接中断，请检查视频源或后端服务",
    "reason": "帧总线超过 %.1fs 未收到新帧",
}


class FrameHub:
    """保存最新一帧 + 向订阅者广播。"""

    def __init__(self, maxsize: int = 64) -> None:
        self._latest: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._maxsize = maxsize
        self._count = 0

    # ---- 生产者（A 线 / mock / API ingest）----
    def publish(self, frame: dict[str, Any]) -> None:
        with self._lock:
            self._latest = frame
            self._count += 1
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(frame)
            except queue.Full:
                # 订阅者积压：丢掉最旧一帧，保证它总能拿到"最新"
                try:
                    q.get_nowait()
                    q.put_nowait(frame)
                except queue.Empty:
                    pass

    # ---- 消费者（B 线 WebSocket）----
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.append(q)
            if self._latest is not None:
                q.put_nowait(self._latest)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    @property
    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    @property
    def published(self) -> int:
        with self._lock:
            return self._count

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def iter_subscription(self, q: queue.Queue, timeout: float = 3.0) -> Iterator[dict[str, Any]]:
        """阻塞式取帧；超时则 yield 一帧 disconnected（B 线兜底，不静默卡住）。"""
        while True:
            try:
                yield q.get(timeout=timeout)
            except queue.Empty:
                yield {"status": "disconnected", "advice": DISCONNECT_HINT["advice"],
                       "reason": DISCONNECT_HINT["reason"] % timeout, "frame_id": -1, "ts": None,
                       "_synthetic_disconnect": True}


# 进程内默认总线：api.py / websocket.py / run_pipeline.py 共享
HUB = FrameHub()


if __name__ == "__main__":  # 自检：python backend/hub.py
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mock import mock_frame

    h = FrameHub()
    q = h.subscribe()
    print("订阅后立刻收到最新帧：", "无" if q.empty() else "有（subscribe 会补发 latest，正确）")
    h.publish(mock_frame(0))
    print("publish 后订阅者拿到 status =", q.get_nowait()["status"])
    print("latest =", h.latest["status"], "| 已发布", h.published, "帧 | 订阅者", h.subscriber_count)
    it = h.iter_subscription(q, timeout=0.2)
    print("超时兜底产出：", next(it)["status"])
