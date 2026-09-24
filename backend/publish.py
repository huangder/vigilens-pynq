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
import queue
import sys
import threading
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
DEFAULT_FRAME_PATH = "/api/frame"
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


def frame_url_from_ingest(ingest_url: str) -> str:
    """从 `/api/ingest` 推出旁路画面的 `/api/frame` 地址。

    为什么要从 --post 推导而不是再要一个参数：两个地址永远是同一台服务，
    分开配只会多一个能配错的地方（配错的表现是"画面没了但指标正常"，很难查）。
    """
    base = ingest_url.rstrip("/")          # 先吃掉尾斜杠，否则下面 endswith 会漏判
    if base.endswith(DEFAULT_INGEST_PATH):
        return base[: -len(DEFAULT_INGEST_PATH)] + DEFAULT_FRAME_PATH
    # 自定义路径：退回到"去掉最后一段"再拼，例如 /custom/ingest -> /custom/api/frame
    tail = base[len("https://"):] if base.startswith("https://") else base[len("http://"):]
    if "/" in tail:
        base = base.rsplit("/", 1)[0]
    return base.rstrip("/") + DEFAULT_FRAME_PATH


class VideoPusher:
    """旁路画面推送：把当前帧编码成 JPEG 推到 B 线 `/api/frame`。

    **它是"尽力而为"的，与 `FramePoster`（契约帧）的纪律刻意不同**：

    | | FramePoster（契约帧） | VideoPusher（旁路画面） |
    |---|---|---|
    | 丢了会怎样 | 测量结果缺失 → **必须报错、退出码 3** | 只是画面卡一下 → 计数并可见，**不改退出码** |
    | 节流 | 按契约 `ws_push_hz`，少推一帧都要记账 | 单槽队列，**满了直接丢最旧** |
    | 阻塞主流程 | 允许（测量必须等它推完） | **绝不**（编码与网络都在后台线程） |

    为什么这样分：画面是**给人看的**，指标是**测量结果**。让一个"预览画面"能把整段
    测量搞成失败退出，是本末倒置；但也不能静默 —— 所以计数、打印、进 summary，
    只是不改变退出码。
    """

    def __init__(self, url: str, *, hz: float = 8.0, quality: int = 80,
                 max_width: int = 640, timeout: float = 2.0) -> None:
        if not url:
            raise ValueError("VideoPusher 需要一个 URL")
        self.url = url
        self.hz = float(hz) if hz and hz > 0 else 8.0
        self.quality = int(min(100, max(1, quality)))
        self.max_width = int(max_width) if max_width and max_width > 0 else 0
        self.timeout = float(timeout)

        self.offered = 0        # 主流程交过来的帧数
        self.encoded = 0        # 成功编码的帧数
        self.posted = 0         # HTTP 成功次数
        self.dropped = 0        # 因单槽已满被丢掉的帧数（**预期行为**，不是错误）
        self.errors: list[str] = []
        self.last_frame_id: int | None = None
        self._cv2 = None
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_at = 0.0     # 节流（单调时钟）
        self._encode_failed_reason: str | None = None

    # ---------------------------------------------------------------- 编码
    def _ensure_cv2(self) -> bool:
        if self._cv2 is not None:
            return self._cv2 is not False
        try:
            import cv2  # type: ignore
            self._cv2 = cv2
        except Exception as e:  # noqa: BLE001
            self._cv2 = False
            self._encode_failed_reason = f"未安装 opencv-python（{type(e).__name__}）：无法编码 JPEG"
        return self._cv2 is not False

    def _encode(self, img) -> bytes | None:
        if not self._ensure_cv2():
            return None
        cv2 = self._cv2
        try:
            a = img
            shape = getattr(a, "shape", None)
            # ⚠️ 必须同时判 len(shape)>=2：非图像对象（如 capture.SyntheticImage）
            #    的 shape 可能是 (h, w, 3)，也可能压根不是元组 —— 直接取 [1] 会 IndexError，
            #    而那会变成一条"编码异常"，把"这不是图像"这个**预期情况**伪装成故障。
            if self.max_width and shape is not None and len(shape) >= 2 and shape[1] > self.max_width:
                scale = self.max_width / float(shape[1])
                a = cv2.resize(a, (self.max_width, max(1, int(round(shape[0] * scale)))),
                               interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", a, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
            if not ok:
                self._note_error("cv2.imencode 返回失败")
                return None
            return buf.tobytes()
        except Exception as e:  # noqa: BLE001
            self._note_error(f"编码异常：{type(e).__name__}: {e}")
            return None

    def _note_error(self, msg: str) -> None:
        if len(self.errors) < 20 and msg not in self.errors:
            self.errors.append(msg)

    # ---------------------------------------------------------------- 投递
    def offer(self, img, *, frame_id: int | None = None) -> bool:
        """主流程调用：把当前帧交给后台线程。**非阻塞，永不抛异常。**

        返回 True 表示已入队。返回 False 的两种原因（都可接受）：
          · 距上一帧不足一个周期（节流）—— 避免把高帧率源全量编码，白烧 CPU
          · 后台还在推上一帧（单槽已满）—— 丢掉旧帧，画面只慢不卡
        """
        self.offered += 1
        self.last_frame_id = frame_id
        now = time.monotonic()
        if now < self._next_at:
            self.dropped += 1
            return False
        self._next_at = now + 1.0 / self.hz
        try:
            self._q.put_nowait((img, frame_id))
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="vigilens-video-push", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                img, frame_id = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            jpeg = self._encode(img)
            if jpeg is None:
                continue
            self.encoded += 1
            url = self.url
            if frame_id is not None:
                url = f"{url}?frame_id={int(frame_id)}"
            try:
                req = urllib.request.Request(
                    url, data=jpeg, method="POST",
                    headers={
                        "Content-Type": "image/jpeg",
                        "Content-Length": str(len(jpeg)),
                        "User-Agent": "VigiLens-A-line/1.0 (+backend/publish.py video bypass)",
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    int(resp.status)
                self.posted += 1
            except Exception as e:  # noqa: BLE001 —— 画面推送失败**不允许**影响测量
                self._note_error(f"{type(e).__name__}: {e}")

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ---------------------------------------------------------------- 汇总
    def stats(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "hz": self.hz,
            "quality": self.quality,
            "max_width": self.max_width,
            "offered": self.offered,
            "encoded": self.encoded,
            "posted": self.posted,
            "dropped": self.dropped,
            "last_frame_id": self.last_frame_id,
            "errors": self.errors,
            # 刻意**不含** "ok"：画面是旁路，它的成败不参与"本次测量是否成功"的判定。
            "note": "旁路画面：失败只计数，不改变退出码（与 --post 的契约帧不同）",
        }


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
