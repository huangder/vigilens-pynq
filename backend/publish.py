"""publish.py —— A 线 → B 线的跨进程交接（里程碑 M2 / 开发步骤 P5，任务号 A9 的收尾）。

为什么需要它：
    契约里 A 线产出 JSON、B 线消费 JSON，但两者跑在**不同进程**里
    （`backend/run_pipeline.py` 做测量，`backend/api.py` 做服务）。
    `hub.py` 是**进程内**总线，跨不了进程；M2 的正式交接点是 B 线早就留好的
    `POST /api/ingest`（`api.py` 里那句注释写的就是 "A 线 run_pipeline --post"）。
    本文件把"该发哪几帧 / 怎么发 / 失败了怎么办"三件事收在一处，
    `run_pipeline.py` 只管逐帧调用。

三条设计决定（每条都是为了不在 M2 现场翻车）：

1. **推送节奏按逻辑时间，不按墙上时钟。**
   契约的 1 帧/秒指的是**业务时间**上的 1 Hz。回放管线比实时快几十倍，
   若按墙上时钟节流，30 秒的视频只会发出 1~2 帧。因此本文件用帧自带的 `ts`
   判断"距上次推送是否已过 1/hz 秒"——于是同一段视频跑两次，推出去的帧集合
   **完全一致**（与《02》A3 的可复现口径同源）。

2. **发之前先在本地过一遍契约校验。**
   B 线的 `/api/ingest` 会用 `validate_frame` 拒收坏帧（HTTP 422）。
   本地先校验一次，能直接说清是哪个字段坏了，而不是把问题丢给对面变成一个 422。

3. **失败必须出声。**
   连接类失败按 `retries` 重试；仍失败 → 抛 `PostError`，由 `run_pipeline`
   以非零退出码收场。**绝不静默丢帧**：M2 现场最难的故障就是"网页不动、日志无声"，
   宁可让管线当场报错，也不要演示时才发现少了一半数据。

用法（一般不用手敲，`run_pipeline.py --post` 内部调用）：

    from publish import FramePoster
    poster = FramePoster("http://127.0.0.1:8000/api/ingest", hz=1.0)
    if poster.maybe_post(frame):        # 按 hz 节流
        ...
    print(poster.stats())
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .contract import validate_frame
except ImportError:  # python backend/publish.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from contract import validate_frame

DEFAULT_INGEST_PATH = "/api/ingest"
_EPS = 1e-9


class PostError(RuntimeError):
    """推送失败：连接不通 / 对面 5xx / 对面以契约（422）拒收。

    属性都是给"人看日志"用的，不做控制流判断（契约不匹配用 `contract_mismatch`）。
    """

    def __init__(self, message: str, *, url: str, attempts: int = 0,
                 status: int | None = None, errors: list[str] | None = None,
                 contract_mismatch: bool = False) -> None:
        super().__init__(message)
        self.url = url
        self.attempts = attempts
        self.status = status
        self.errors = list(errors or [])
        self.contract_mismatch = contract_mismatch


def _http_post_json(url: str, payload: dict, timeout: float) -> tuple[int, str]:
    """POST 一个 JSON，返回 (HTTP 状态码, 响应正文)。4xx/5xx 走 HTTPError 分支。"""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "VigiLens-A-line/1.0 (+backend/publish.py)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 只允许本机/自定 URL
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:  # 4xx / 5xx：正文里通常有 B 线的 errors
        return int(e.code), e.read().decode("utf-8", errors="replace")


def _clean(frame: dict) -> dict:
    """去掉 `_` 开头的内部字段（`run_pipeline` 用它挂 triggers，不是契约字段）。"""
    return {k: v for k, v in frame.items() if not k.startswith("_")}


class FramePoster:
    """把契约帧按 hz 节流 POST 到 B 线 `/api/ingest`。"""

    def __init__(self, url: str, *, hz: float = 1.0, timeout: float = 2.0,
                 retries: int = 2, backoff: float = 0.2) -> None:
        if not url:
            raise ValueError("FramePoster 需要一个 URL")
        self.url = url
        self.hz = float(hz)
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.backoff = float(backoff)

        self.posted = 0            # 成功推送的帧数
        self.throttled = 0         # 因 hz 节流而跳过的帧数
        self.attempts = 0          # HTTP 请求次数（含重试）
        self.errors: list[str] = []   # 失败留下的原文（供摘要与排查）
        self.last_response: dict | None = None
        self._last_ts: float | None = None
        self._last_frame_id: int | None = None

    # ------------------------------------------------------------------ 节流
    @property
    def period(self) -> float:
        return 1.0 / self.hz if self.hz > 0 else 0.0

    def due(self, ts: float | None) -> bool:
        """按**逻辑时间戳**判断这一帧该不该发（第一帧总是发）。"""
        if self._last_ts is None:
            return True
        if self.period <= 0:
            return True
        if ts is None:  # 没有逻辑时间戳（--wall-clock）时退化为"每帧都发"
            return True
        return (float(ts) - self._last_ts) >= self.period - _EPS

    # ------------------------------------------------------------------ 发送
    def maybe_post(self, frame: dict) -> bool:
        """节流后发送；返回"这一帧是否真的发出去了"。"""
        if not self.due(frame.get("ts")):
            self.throttled += 1
            return False
        self.post(frame)
        return True

    def post(self, frame: dict) -> dict:
        """立刻发送一帧（不做节流）。失败抛 PostError。返回对面响应。"""
        clean = _clean(frame)
        errs = validate_frame(clean)
        if errs:
            # 本地就拦下：对面一定会 422，与其让对方报错不如这里说清楚
            msg = ("这一帧不符合契约，已拒绝发送：\n  - " + "\n  - ".join(errs))
            self.errors.append(msg)
            raise PostError(msg, url=self.url, errors=errs, contract_mismatch=True)

        payload = {"frame": clean}     # api.py 的 /api/ingest 兼容 {"frame": ...} 与裸帧
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 2):      # 首次 + retries 次重试
            self.attempts += 1
            try:
                status, body = _http_post_json(self.url, payload, self.timeout)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last_exc = e
                self.errors.append(f"第 {attempt} 次连接 {self.url} 失败：{e}")
                if attempt <= self.retries and self.backoff:
                    time.sleep(self.backoff * attempt)
                continue

            if 200 <= status < 300:
                self._last_ts = float(clean["ts"]) if clean.get("ts") is not None else None
                self._last_frame_id = clean.get("frame_id")
                self.posted += 1
                try:
                    self.last_response = json.loads(body) if body.strip() else {}
                except json.JSONDecodeError:
                    self.last_response = {"_raw": body}
                return self.last_response

            # 422 = 契约不匹配：重试没有意义，重试一百次对面还是拒收
            if status == 422:
                try:
                    remote = json.loads(body).get("errors") or []
                except json.JSONDecodeError:
                    remote = []
                msg = (f"B 线以 422 拒收这一帧（frame_id={clean.get('frame_id')}）：\n  - "
                       + "\n  - ".join(remote or [body[:400]]))
                self.errors.append(msg)
                raise PostError(msg, url=self.url, attempts=attempt,
                                status=status, errors=list(remote), contract_mismatch=True)

            last_exc = RuntimeError(f"HTTP {status}: {body[:200]}")
            self.errors.append(f"第 {attempt} 次推送返回 HTTP {status}：{body[:200]}")
            if attempt <= self.retries and self.backoff:
                time.sleep(self.backoff * attempt)

        msg = (f"推送失败：{self.url} 连续 {self.attempts} 次未成功（最后错误：{last_exc}）。"
               "请确认 B 线服务已启动（python backend/api.py --no-mock）且端口正确。")
        self.errors.append(msg)
        raise PostError(msg, url=self.url, attempts=self.attempts, errors=[])

    # ------------------------------------------------------------------ 汇总
    def stats(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "hz": self.hz,
            "posted": self.posted,
            "throttled": self.throttled,
            "http_attempts": self.attempts,
            "last_frame_id": self._last_frame_id,
            "errors": self.errors,
        }


def full_url(host: str = "127.0.0.1", port: int = 8000) -> str:
    """拼一个默认的 ingest 地址（给 CLI 的 `--post auto` 用）。"""
    return f"http://{host}:{port}{DEFAULT_INGEST_PATH}"


if __name__ == "__main__":  # 自检：python backend/publish.py
    from contract import new_frame  # 直接跑时走同目录导入

    fr = new_frame(
        ts=0.0, frame_id=0,
        face={"visible": 1.0, "bbox": [0, 0, 10, 10], "pose": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}},
        behavior={"ear_left": 0.3, "ear_right": 0.3, "blink_state": "OPEN", "blink_count": 0,
                  "blink_rate_per_min": 0.0, "perclos": 0.0, "long_close_count": 0,
                  "mar": 0.1, "yawn_count": 0},
        vital={"hr_bpm": None, "hr_conf": None, "rr_per_min": None, "rr_conf": None},
        quality={"light_score": 0.9, "motion_score": 0.0, "overall": 0.9},
        status="normal", advice="状态正常", reason="自检",
    )
    p = FramePoster(full_url(), hz=1.0, backoff=0.0)
    print("节流自检：ts=0 应发 ——", p.due(0.0))
    p._last_ts = 0.0
    print("节流自检：ts=0.5 应跳过 ——", not p.due(0.5), "| ts=1.0 应发 ——", p.due(1.0))
    print("本地契约校验：坏帧会被拦下 ——", end=" ")
    try:
        p.post({**fr, "status": "nonsense"})
        print("**没拦住（错）**")
    except PostError as e:
        print("拦住了：", e.contract_mismatch, len(e.errors), "条错误")
