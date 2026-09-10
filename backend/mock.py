"""mock.py —— 契约一致的假数据源（《02》任务 B2）。

存在意义（《02》第六节）：**B 线从第一天起就不需要等 A 线。**
只要 A 线还没产出真 JSON，B 线就用本文件在浏览器里看完整仪表盘。

设计要点（这是"好用"和"没用"的差别）：
  - **按 status 反推指标值**，而不是各字段独立乱随机。
    status=fatigue_risk 时 PERCLOS 就必须高、眨眼率就必须低；否则演示时
    "状态写着疲劳、数字却说正常"，评委一眼看穿。
  - 用 random.Random(seed) 局部实例，**不污染全局随机状态**，
    因此同 seed 必得同一序列 → pytest 可以断言，演示可复现。
  - 产出后强制过 contract.validate_frame()，保证与 A 线真实输出同构。

⚠️ 这里的数据是**假的**。报告/PPT 中凡是引用本文件产出的数字，必须标注"Mock 数据"。
"""

from __future__ import annotations

import argparse
import json
import random
import time
from typing import Any, Iterator

try:
    from .contract import STATUS_VALUES, assert_valid, new_frame
except ImportError:
    from contract import STATUS_VALUES, assert_valid, new_frame

# 六态演示时的推荐顺序（B6 验收：六种状态都能演示且样式区分清晰）
DEMO_SEQUENCE = ("normal", "fatigue_risk", "adjust_posture", "unreliable", "disconnected", "done")

_ADVICE = {
    "normal": ("状态正常", "各项指标均在正常范围，信号质量良好"),
    "fatigue_risk": ("疲劳风险升高，建议休息 5 分钟或远眺放松", "触发：PERCLOS 偏高；长闭眼次数增加"),
    "adjust_posture": ("请正对摄像头，保持面部完整出现在画面内", "人脸可见率或头部姿态超出允许范围"),
    "unreliable": ("信号不可靠，暂不输出测量结果", "光照/运动导致信号质量低于门控阈值"),
    "disconnected": ("连接中断，请检查视频源或后端服务", "超过 ws_disconnect_timeout_s 未收到数据帧"),
    "done": ("测量完成", "本次测量正常结束，结果已归档"),
}


def _rng(seed: int, frame_id: int) -> random.Random:
    """每次调用得到独立、可复现的随机源（不用全局 random）。"""
    return random.Random((seed * 1_000_003) ^ (frame_id * 2_654_435_761))


def mock_frame(
    frame_id: int,
    *,
    ts: float | None = None,
    status: str | None = None,
    seed: int = 20260910,
    statuses: tuple[str, ...] = DEMO_SEQUENCE,
    quality_gate: float = 0.75,
) -> dict[str, Any]:
    """生成一帧契约 JSON。status 为 None 时按 statuses 轮转。"""
    if status is None:
        status = statuses[frame_id % len(statuses)]
    if status not in STATUS_VALUES:
        raise ValueError(f"status 必须是 {STATUS_VALUES} 之一，实际 {status!r}")

    r = _rng(seed, frame_id)
    advice, reason = _ADVICE[status]

    # ---- 基线：一切正常 ----
    visible = min(0.999, 0.96 + r.uniform(-0.02, 0.02))
    bbox = (320 + r.randint(-2, 2), 180 + r.randint(-2, 2), 180 + r.randint(-3, 3), 220 + r.randint(-3, 3))
    yaw, pitch, roll = r.uniform(-3, 3), r.uniform(-2, 2), r.uniform(-2, 2)

    ear_l = 0.27 + r.uniform(-0.02, 0.02)
    ear_r = ear_l + r.uniform(-0.01, 0.01)
    blink_state = "OPEN"
    blink_count = 12 + frame_id // 20
    blink_rate = 17.0 + r.uniform(-2, 2)
    perclos = 0.05 + r.uniform(0, 0.03)
    long_close = 0
    mar = 0.10 + r.uniform(0, 0.05)
    yawn = 0
    light = 0.82 + r.uniform(-0.05, 0.05)
    motion = 0.03 + r.uniform(0, 0.03)
    overall = 0.86 + r.uniform(-0.04, 0.04)
    vital: dict[str, Any] = {}

    # ---- 按状态改写指标，保证"状态与数字自洽" ----
    if status == "fatigue_risk":
        perclos = 0.30 + r.uniform(0, 0.12)
        blink_rate = 9.0 + r.uniform(-2, 2)
        long_close = 1 + r.randint(0, 2)
        yawn = 2 + r.randint(0, 2)
        ear_l = 0.20 + r.uniform(-0.02, 0.03)
        ear_r = ear_l + r.uniform(-0.01, 0.01)
        blink_state = "CLOSED" if r.random() < 0.35 else "OPEN"
    elif status == "adjust_posture":
        visible = 0.35 + r.uniform(0, 0.25)
        yaw = 32.0 + r.uniform(0, 14) * (1 if r.random() < 0.5 else -1)
        pitch = r.uniform(-6, 6)
    elif status == "unreliable":
        light = 0.22 + r.uniform(0, 0.22)
        motion = 0.45 + r.uniform(0, 0.4)
        overall = 0.30 + r.uniform(0, 0.25)
        blink_state = "OPEN"
    elif status == "disconnected":
        visible = 0.0
        bbox = (0, 0, 0, 0)
        yaw = pitch = roll = 0.0
        ear_l = ear_r = 0.0
        blink_state = "OPEN"
        perclos = 0.0
        blink_rate = 0.0
        mar = 0.0
        light = motion = overall = 0.0
    elif status == "done":
        perclos = 0.08
        blink_rate = 16.0

    # ---- vital：质量不够就**必须**是 null（产品承诺，与 decision.gate_vitals 一致）----
    if status in ("normal", "fatigue_risk", "done") and overall >= quality_gate:
        hr = 68.0 + r.uniform(-4, 6)
        rr = 15.0 + r.uniform(-2, 2)
        vital = {
            "hr_bpm": round(hr, 1),
            "hr_conf": round(min(0.98, 0.62 + overall * 0.35 + r.uniform(-0.05, 0.05)), 3),
            "rr_per_min": round(rr, 1),
            "rr_conf": round(min(0.95, 0.55 + overall * 0.3 + r.uniform(-0.05, 0.05)), 3),
        }

    frame = new_frame(
        ts=ts if ts is not None else round(time.time(), 3),
        frame_id=frame_id,
        face={
            "visible": round(visible, 3),
            "bbox": [int(v) for v in bbox],
            "pose": {"yaw": round(yaw, 2), "pitch": round(pitch, 2), "roll": round(roll, 2)},
        },
        behavior={
            "ear_left": round(ear_l, 4),
            "ear_right": round(ear_r, 4),
            "blink_state": blink_state,
            "blink_count": blink_count,
            "blink_rate_per_min": round(blink_rate, 2),
            "perclos": round(perclos, 4),
            "long_close_count": long_close,
            "mar": round(mar, 4),
            "yawn_count": yawn,
        },
        vital=vital,
        quality={
            "light_score": round(max(0.0, min(1.0, light)), 4),
            "motion_score": round(max(0.0, min(1.0, motion)), 4),
            "overall": round(max(0.0, min(1.0, overall)), 4),
        },
        status=status,
        advice=advice,
        reason=reason,
    )
    return assert_valid(frame)


def mock_stream(
    hz: float = 1.0,
    *,
    seed: int = 20260910,
    statuses: tuple[str, ...] = DEMO_SEQUENCE,
    realtime: bool = True,
    limit: int | None = None,
    logical_ts: bool = False,
) -> Iterator[dict]:
    """按 hz 持续产出帧。

    realtime=True  → 按 hz 真实 sleep（给前端看的效果）
    realtime=False → 立刻产出（测试用）
    logical_ts=True→ 时间戳用 frame_id/hz 而不是墙上时钟 —— **可复现**，
                     回归测试与截图证据都用这个模式；实时演示用默认的墙上时钟。
    """
    period = 1.0 / hz if hz > 0 else 0.0
    fid = 0
    while limit is None or fid < limit:
        ts = round(fid / hz, 3) if (logical_ts and hz > 0) else None
        yield mock_frame(fid, ts=ts, status=statuses[fid % len(statuses)], seed=seed, statuses=statuses)
        fid += 1
        if realtime and period:
            time.sleep(period)


def main() -> int:
    ap = argparse.ArgumentParser(description="VigiLens 契约一致的 Mock 数据源（B 线用，A 线未就绪时不阻塞）")
    ap.add_argument("--frames", type=int, default=6, help="输出多少帧（默认 6，正好覆盖六态各一帧）")
    ap.add_argument("--hz", type=float, default=1.0, help="实时模式下的推送频率（默认 1 Hz，与契约一致）")
    ap.add_argument("--realtime", action="store_true", help="按 --hz 真实节流（默认不节流，立刻输出）")
    ap.add_argument("--status", default="all", help="all=六态轮转；或指定单个 status 反复输出")
    ap.add_argument("--seed", type=int, default=20260910, help="随机种子（同 seed 必得同序列，便于复现）")
    ap.add_argument("--logical-ts", action="store_true",
                    help="时间戳用 frame_id/hz（可复现，适合归档证据）而不是墙上时钟")
    ap.add_argument("--compact", action="store_true", help="单行紧凑 JSON（默认美化输出）")
    args = ap.parse_args()

    statuses = DEMO_SEQUENCE if args.status == "all" else (args.status,)
    for fr in mock_stream(args.hz, seed=args.seed, statuses=statuses, realtime=args.realtime,
                          limit=args.frames, logical_ts=args.logical_ts):
        if args.compact:
            print(json.dumps(fr, ensure_ascii=False, separators=(",", ":")))
        else:
            print(json.dumps(fr, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
