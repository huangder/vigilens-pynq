"""decision.py —— 规则融合与可解释建议（《02》任务 A8）。

《02》A8 验收："阈值写在 config.yaml，逻辑可解释"。
因此本文件的设计目标是**可追溯**：每个 status 都要能回答"是哪一条指标、
哪一个阈值、当前值多少触发了它"，这些理由会被写进 reason 与 triggers。

判决优先级（有意为之，顺序就是产品语义）：
  1. unreliable       —— 质量门控不过 → **先拒绝测量**，不再看疲劳指标
  2. adjust_posture   —— 人脸可见率不足 / 姿态越界
  3. fatigue_risk     —— PERCLOS / 长闭眼 / 打哈欠 任一告警
  4. normal

先质量、后疲劳，正是"先判断能不能测，再决定测出什么"的实现。
`disconnected` / `done` 不在本文件产生：前者是 B 线兜底，后者由 run_pipeline 收尾。
"""

from __future__ import annotations

import time
from typing import Any

try:
    from .config import load_config
except ImportError:
    from config import load_config

# 触发的每一条都长这样，理由可被前端逐条展示
Trigger = dict[str, Any]


class DecisionEngine:
    """规则融合。阈值全部来自 config.yaml，本文件不出现魔法数字。"""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        c = cfg or load_config()
        self.quality_min = float(c["quality_min_score"])
        self.face_visible_min = float(c["face_visible_min"])
        self.yaw_max = float(c["pose_yaw_max_deg"])
        self.pitch_max = float(c["pose_pitch_max_deg"])
        self.perclos_warn = float(c["perclos_warning"])
        self.yawn_warn = int(c.get("fatigue_yawn_count", 2))
        self.long_close_warn = int(c.get("fatigue_long_close_count", 1))
        self.blink_rate_low = float(c.get("fatigue_blink_rate_low", 10.0))
        self.vital_require_quality = float(c.get("vital_require_quality", 0.75))

    # ------------------------------------------------------------------
    def decide(self, *, frame_id: int, behavior: dict, quality: dict, face: dict) -> dict[str, Any]:
        """返回 {status, advice, reason, triggers}。"""
        triggers: list[Trigger] = []

        # ---- 1) 质量门控：先决定"能不能测" ----
        if quality["overall"] < self.quality_min:
            worst = min(
                (("light_score", quality["light_score"], "光照"),
                 ("motion_score", quality["motion_score"], "运动"),
                 ("face_visible", face["visible"], "人脸可见率")),
                key=lambda t: t[1],
            )
            triggers.append({
                "rule": "quality_gate",
                "metric": "quality.overall",
                "value": quality["overall"],
                "threshold": self.quality_min,
                "verdict": "fail",
            })
            return {
                "status": "unreliable",
                "advice": "信号不可靠，暂不输出测量结果，请改善光照或保持稳定",
                "reason": (f"信号质量 {quality['overall']:.2f} 低于门控阈值 {self.quality_min:.2f}"
                           f"（最弱项：{worst[2]} {worst[1]:.2f}）"),
                "triggers": triggers,
            }
        triggers.append({
            "rule": "quality_gate", "metric": "quality.overall",
            "value": quality["overall"], "threshold": self.quality_min, "verdict": "pass",
        })

        # ---- 2) 姿态 / 可见率 ----
        pose = face["pose"]
        if face["visible"] < self.face_visible_min:
            triggers.append({
                "rule": "face_visible", "metric": "face.visible",
                "value": face["visible"], "threshold": self.face_visible_min, "verdict": "fail",
            })
            return {
                "status": "adjust_posture",
                "advice": "请正对摄像头，保持面部完整出现在画面内",
                "reason": f"人脸可见率 {face['visible']:.2f} 低于下限 {self.face_visible_min:.2f}",
                "triggers": triggers,
            }
        if abs(pose["yaw"]) > self.yaw_max or abs(pose["pitch"]) > self.pitch_max:
            axis = "yaw" if abs(pose["yaw"]) > self.yaw_max else "pitch"
            limit = self.yaw_max if axis == "yaw" else self.pitch_max
            triggers.append({
                "rule": "pose_range", "metric": f"face.pose.{axis}",
                "value": pose[axis], "threshold": limit, "verdict": "fail",
            })
            return {
                "status": "adjust_posture",
                "advice": "头部偏离过大，请调整姿势正对摄像头",
                "reason": f"头部 {axis} = {pose[axis]:.1f}° 超出 ±{limit:.0f}°",
                "triggers": triggers,
            }

        # ---- 3) 疲劳风险 ----
        fired: list[str] = []
        if behavior["perclos"] > self.perclos_warn:
            fired.append(f"PERCLOS {behavior['perclos']:.2f} > {self.perclos_warn:.2f}")
            triggers.append({"rule": "perclos", "metric": "behavior.perclos",
                             "value": behavior["perclos"], "threshold": self.perclos_warn, "verdict": "warn"})
        if behavior["long_close_count"] >= self.long_close_warn:
            fired.append(f"长闭眼 {behavior['long_close_count']} 次 ≥ {self.long_close_warn}")
            triggers.append({"rule": "long_close", "metric": "behavior.long_close_count",
                             "value": behavior["long_close_count"], "threshold": self.long_close_warn,
                             "verdict": "warn"})
        if behavior["yawn_count"] >= self.yawn_warn:
            fired.append(f"打哈欠 {behavior['yawn_count']} 次 ≥ {self.yawn_warn}")
            triggers.append({"rule": "yawn", "metric": "behavior.yawn_count",
                             "value": behavior["yawn_count"], "threshold": self.yawn_warn, "verdict": "warn"})
        if 0.0 < behavior["blink_rate_per_min"] < self.blink_rate_low:
            fired.append(f"眨眼率 {behavior['blink_rate_per_min']:.1f}/min < {self.blink_rate_low:.0f}")
            triggers.append({"rule": "blink_rate_low", "metric": "behavior.blink_rate_per_min",
                             "value": behavior["blink_rate_per_min"], "threshold": self.blink_rate_low,
                             "verdict": "warn"})

        if fired:
            return {
                "status": "fatigue_risk",
                "advice": "疲劳风险升高，建议休息 5 分钟或远眺放松",
                "reason": "触发：" + "；".join(fired),
                "triggers": triggers,
            }

        return {
            "status": "normal",
            "advice": "状态正常",
            "reason": (f"各项指标均在正常范围（PERCLOS {behavior['perclos']:.2f}、"
                       f"眨眼率 {behavior['blink_rate_per_min']:.1f}/min），信号质量良好"),
            "triggers": triggers,
        }

    # ------------------------------------------------------------------
    def gate_vitals(self, quality_overall: float, vital: dict | None) -> dict:
        """质量不够时**不允许**输出心率/呼吸数值（vital.* 输出 null）。

        这条是产品承诺，不是可选项：门控不过还报数字，就退化成"黑盒乱报"了。
        """
        empty = {"hr_bpm": None, "hr_conf": None, "rr_per_min": None, "rr_conf": None}
        if not vital:
            return empty
        if quality_overall < self.vital_require_quality:
            return empty
        return {**empty, **vital}


def disconnected_frame(frame_id: int = -1) -> dict[str, Any]:
    """B 线兜底用的"连接中断"帧（《02》风险表：B 线必须能处理 disconnect）。

    不经过 DecisionEngine —— 视频源断了就没有指标可言。
    """
    return {
        "status": "disconnected",
        "advice": "连接中断，请检查视频源或后端服务",
        "reason": "超过 ws_disconnect_timeout_s 未收到数据帧",
        "triggers": [{"rule": "disconnect", "metric": "ts", "value": None,
                      "threshold": None, "verdict": "fail"}],
    }


if __name__ == "__main__":  # 自检：python backend/decision.py
    eng = DecisionEngine()
    good = {"overall": 0.9, "light_score": 0.9, "motion_score": 0.05}
    good_face = {"visible": 0.98, "pose": {"yaw": -2.1, "pitch": 1.5, "roll": 0.3}}
    calm = {"perclos": 0.06, "long_close_count": 0, "yawn_count": 0, "blink_rate_per_min": 18.2}
    tired = {"perclos": 0.31, "long_close_count": 2, "yawn_count": 3, "blink_rate_per_min": 11.0}
    bad_q = {"overall": 0.42, "light_score": 0.3, "motion_score": 0.7}
    bad_face = {"visible": 0.4, "pose": {"yaw": 34.0, "pitch": 1.0, "roll": 0.0}}

    for name, b, q, f in (("正常", calm, good, good_face),
                          ("疲劳", tired, good, good_face),
                          ("质量差", calm, bad_q, good_face),
                          ("姿势差", calm, good, bad_face)):
        r = eng.decide(frame_id=1, behavior=b, quality=q, face=f)
        print(f"[{name}] status={r['status']:15s} {r['reason']}")
    print("门控心率：质量 0.9 →", eng.gate_vitals(0.9, {"hr_bpm": 72.0, "hr_conf": 0.8}))
    print("门控心率：质量 0.5 →", eng.gate_vitals(0.5, {"hr_bpm": 72.0, "hr_conf": 0.8}))
