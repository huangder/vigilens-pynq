"""behavior_metrics.py —— 行为指标（《02》任务 A3/A4/A5）。

本文件是**真实现**（不是 stub）：EAR、眨眼状态机、眨眼计数/频率、PERCLOS、
MAR、打哈欠事件都在这里算真值。M1 阶段唯一"占位"的是**上游关键点**
（face_landmark 的 StubLandmarker），而不是这里。

验收口径（《02》A3/A4/A5）：
  - 对眨眼视频能正确计数，**同视频重复运行结果一致** → 因此本文件不使用
    random / 时间戳以外的不确定输入，窗口算法纯函数式推进。
  - PERCLOS 在 30 秒窗口内数值稳定 → 窗口长度来自 config.yaml。
"""

from __future__ import annotations

from collections import deque
from typing import Any

try:  # 作为包 import（pytest / python -m backend.xxx）
    from .config import load_config
    from .face_landmark import ear, mar
except ImportError:  # 直接 python backend/behavior_metrics.py
    from config import load_config
    from face_landmark import ear, mar


class BehaviorTracker:
    """逐帧推进的行为指标跟踪器（有状态）。

    用法：
        trk = BehaviorTracker()
        beh = trk.update(frame.frame_id, frame.ts, observation)
    """

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        c = cfg or load_config()
        self.ear_thr: float = float(c["ear_close_threshold"])
        self.min_close_frames: int = int(c["min_close_frames"])
        self.window_s: float = float(c["window_seconds"])
        self.long_close_ms: float = float(c.get("long_close_ms", 500))
        self.mar_thr: float = float(c["mar_threshold"])
        self.yawn_min_ms: float = float(c["yawn_min_duration_ms"])

        # ---- 眨眼状态机（OPEN → CLOSING → CLOSED → OPENING → OPEN）----
        self.state: str = "OPEN"
        self._below = 0          # 连续低于阈值的帧数
        self._above = 0          # 连续高于阈值的帧数
        self._closed_since: float | None = None
        self.blink_count = 0
        self.long_close_count = 0

        # ---- 打哈欠状态机 ----
        self._mouth_open_since: float | None = None
        self._yawn_counted = False
        self.yawn_count = 0

        # ---- 滑窗 ----
        self._win: deque[tuple[float, bool]] = deque()      # (ts, 是否闭眼)
        self._blink_ts: deque[float] = deque()              # 眨眼发生时刻
        self._t0: float | None = None
        self._last_ts: float | None = None

    # ------------------------------------------------------------------
    def _evict(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._win and self._win[0][0] < cutoff:
            self._win.popleft()
        while self._blink_ts and self._blink_ts[0] < cutoff:
            self._blink_ts.popleft()

    def _step_blink(self, ts: float, closed_now: bool, open_now: bool) -> None:
        if closed_now:
            self._above = 0
            self._below += 1
            if self.state == "OPEN":
                self.state = "CLOSING"
            if self._below >= self.min_close_frames:
                if self.state != "CLOSED":
                    self.state = "CLOSED"
                    self._closed_since = ts
        elif open_now:
            self._below = 0
            self._above += 1
            if self.state in ("CLOSING", "CLOSED"):
                self.state = "OPENING"
            if self.state == "OPENING" and self._above >= self.min_close_frames:
                self.state = "OPEN"
            if self._closed_since is not None:
                # 一次闭眼事件结束：判定是"眨眼"还是"长闭眼"
                dur_ms = (ts - self._closed_since) * 1000.0
                if dur_ms >= self.long_close_ms:
                    self.long_close_count += 1
                else:
                    self.blink_count += 1
                    self._blink_ts.append(ts)
                self._closed_since = None

    def _step_yawn(self, ts: float, mouth_open: bool) -> None:
        if mouth_open:
            if self._mouth_open_since is None:
                self._mouth_open_since = ts
                self._yawn_counted = False
            elif not self._yawn_counted and (ts - self._mouth_open_since) * 1000.0 >= self.yawn_min_ms:
                # 持续张口超过阈值 → 记一次打哈欠（同一段只记一次）
                self.yawn_count += 1
                self._yawn_counted = True
        else:
            self._mouth_open_since = None
            self._yawn_counted = False

    # ------------------------------------------------------------------
    def update(self, frame_id: int, ts: float, obs: Any) -> dict[str, Any]:
        """推进一步，返回契约中的 behavior 子对象。"""
        if self._t0 is None:
            self._t0 = ts
        dt = 0.0 if self._last_ts is None else max(0.0, ts - self._last_ts)
        self._last_ts = ts

        has_face = bool(getattr(obs, "eyes", None)) and getattr(obs, "visible", 0.0) > 0.0
        if has_face:
            ear_l = ear(obs.eyes.get("left", {}))
            ear_r = ear(obs.eyes.get("right", {}))
            ear_avg = 0.5 * (ear_l + ear_r)
            mar_v = mar(getattr(obs, "mouth", {}) or {})
        else:
            # 没人脸时**保持上一状态的"未闭合"**并让 EAR 归零，
            # 而不是塞一个假的高 EAR（否则人脸丢失会被误判成"睁眼正常"）。
            ear_l = ear_r = ear_avg = 0.0
            mar_v = 0.0

        closed_now = has_face and ear_avg < self.ear_thr
        open_now = has_face and ear_avg >= self.ear_thr
        self._step_blink(ts, closed_now, open_now)
        self._step_yawn(ts, has_face and mar_v > self.mar_thr)

        # ---- 滑窗累计 ----
        self._win.append((ts, closed_now))
        self._evict(ts)
        total_dt = sum(1 for _ in self._win)
        closed_n = sum(1 for _, c in self._win if c)
        perclos = (closed_n / total_dt) if total_dt else 0.0

        elapsed = max(1e-6, ts - self._t0)
        rate_window = min(self.window_s, elapsed)
        blink_rate = (len(self._blink_ts) / rate_window) * 60.0 if rate_window > 0 else 0.0

        return {
            "ear_left": round(ear_l, 4),
            "ear_right": round(ear_r, 4),
            "blink_state": self.state,
            "blink_count": self.blink_count,
            "blink_rate_per_min": round(blink_rate, 2),
            "perclos": round(perclos, 4),
            "long_close_count": self.long_close_count,
            "mar": round(mar_v, 4),
            "yawn_count": self.yawn_count,
            "_dt": dt,          # 下划线开头 = 非契约字段，storage 落 CSV 时用，不写进 JSON
        }

    @staticmethod
    def strip_private(behavior: dict[str, Any]) -> dict[str, Any]:
        """去掉 `_` 开头的内部字段，得到严格符合契约的 behavior 对象。"""
        return {k: v for k, v in behavior.items() if not k.startswith("_")}


if __name__ == "__main__":  # 自检：python backend/behavior_metrics.py
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture import open_frame_source
    from face_landmark import make_landmarker

    # 合成帧源不是图像：自检用 StubLandmarker（真 MediaPipe 见 run_pipeline --source <视频>）
    mk = make_landmarker(pattern="blink", force_stub=True)
    trk = BehaviorTracker()
    cfg = load_config()
    fps = float(cfg["fps_nominal"])          # 契约 §0 的唯一来源，别写死 30
    window = float(cfg["window_seconds"])
    it, desc = open_frame_source("synthetic")
    last = None
    shown = 0
    for fr in it:
        if fr.frame_id > int(window * fps):  # 跑满一个滑窗（window_seconds 秒）
            break
        obs = mk.detect(fr.image, fr.frame_id)
        # 用 frame_id 推时间轴（合成源是瞬时产出，不代表实时）
        last = trk.update(fr.frame_id, fr.frame_id / fps, obs)
        if fr.frame_id % int(fps * 3) == 0 and shown < 12:      # 每 3 秒打一行
            print(f"  t={fr.frame_id/fps:5.1f}s state={last['blink_state']:8s} "
                  f"blinks={last['blink_count']:2d} rate={last['blink_rate_per_min']:5.1f}/min "
                  f"perclos={last['perclos']:.3f} yawns={last['yawn_count']} long_close={last['long_close_count']}")
            shown += 1
    print(f"{window:.0f} 秒回放结束（{fps:.0f} fps）：眨眼 {last['blink_count']} 次，打哈欠 {last['yawn_count']} 次，"
          f"长闭眼 {last['long_close_count']} 次，PERCLOS={last['perclos']:.3f}")
