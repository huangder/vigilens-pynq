"""fatigue_coupling.py —— 年龄分层的**耦合判定模型**（纯算术，不碰 I/O）。

## 为什么不是"阈值 OR"

`backend/decision.py` 的六态判定是**规则融合**：任一指标越线就报 `fatigue_risk`。
它可解释、够用，但有两个问题：
  1. **证据强度不可加**：PERCLOS 刚好越线一点 与 大幅越线 + 长闭眼 + 哈欠，
     在 OR 语义下是同一个结论；
  2. **年龄不改权重**：而公开研究已经证明"眼部指标 ↔ 困倦"的耦合关系**随年龄改变**
     （Cai et al. 2021；见 `config.yaml` 的 `couple_eye_sensitivity_*` 注释）。
     统一阈值会对老年用户**系统性漏报** —— 漏报在安全方向上是更坏的错误。

本模块因此实现一个**证据累积**模型（对数几率形式，可解释、可加、可门控）：

```
logOdds_t = prior + Σ_i  sens_i(band) · w_quality · clip(gain_i · z_i)   +  耦合项
S_t       = (1-α)·S_{t-1} + α·logOdds_t          # 时序累积（指数记忆）
risk      = sigmoid(S_t)                          # 0~1
```

其中：
  · `z_i` = 该指标相对**同龄基线**的标准化偏离量（没有可用基线时**跳过该指标**，
    并在 reason 里如实写明"无可用基线"，绝不借用别的组的数字）；
  · `sens_i(band)` = 年龄敏感度权重（眼部证据在 60+ 组下调 —— 有文献依据；数值是占位值）；
  · `w_quality` = 质量收缩（质量越差证据越弱，而不是"照样报警"）；
  · 耦合项 = 显式的**指标交互**（高 PERCLOS + 长闭眼、高 PERCLOS + 眨眼率被抑制）；
  · α = 记忆系数（`couple_memory_alpha`）。

## 三条硬纪律

1. **先判断能不能测**：`status` 不在 `normal` / `fatigue_risk` 时（以及质量门控不过、
   人脸可见率不足时），直接输出 `comparable=False` + `risk=None` + `risk_level="unknown"`。
   **不报跳变数字**是本产品的承诺（与 `vital.*` 为 null 同一口径）。
2. **每个数字都要能说出出处**：每条证据都带 `baseline_source`（参照基线出处 /
   `config` 阈值 / `none`），比对结果带 `reference_version` 与 `reference_sources`。
3. **不许编数字**：`config.yaml` 里凡标 `⚠️ 占位值` 的常量都还没有标定，
   本模块的输出只能说"风险档位"，**不得**在报告里当作准确率或临床指标引用。

## 已知口径差异（诚实标注，不在本模块偷偷"修"）

· **PERCLOS 定义**：Dinges & Grace 1998 的原始描述强调它反映 **slow eyelid closures
  （droops）而不是 blinks**；而本项目 `behavior_metrics.py` 的 PERCLOS 是"EAR 低于阈值的
  时间占比"，**包含快速眨眼**。两者不完全等价 —— 见 `docs/27` 的未验证清单。
· **眨眼率的年龄分组**：文献**不支持**按年龄分眨眼率（Bentivoglio 1997、Sun 1997 均未发现
  年龄差异），但支持分**闭眼时长/PERCLOS**；且场景与性别的影响大于年龄
  （Doughty 2001：reading 1.4~14.4 vs conversation 10.5~32.5）。因此眨眼率用
  **全局基线 + 场景口径**（`age_scenario`），而不是年龄分组。
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

try:
    from .fatigue_db import Baseline, ReferenceNorms
except ImportError:  # 直接以脚本方式运行
    from fatigue_db import Baseline, ReferenceNorms

# 眼部证据项（受年龄敏感度权重影响）—— 划分依据见文件头与 config.yaml。
EYE_METRICS = ("perclos", "long_close_count", "blink_rate_per_min")
NON_EYE_METRICS = ("yawn_count", "pose_drift", "perclos_trend")

# 这些 status 下**不做比对**：`unreliable` / `adjust_posture` 是"这次测不了"，
# `disconnected` / `done` 根本没有新测量。与 decision.py 的优先级同源。
COMPARABLE_STATUSES = ("normal", "fatigue_risk")


def _clip(v: float, lim: float) -> float:
    return max(-lim, min(lim, v))


def sigmoid(x: float) -> float:
    """数值稳定版 sigmoid（x 很负时不溢出）。"""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class CoupledRiskModel:
    """一次会话对应一个实例（它带状态：时序记忆 + PERCLOS 趋势滑窗）。"""

    def __init__(self, cfg: dict[str, Any], norms: ReferenceNorms) -> None:
        self.cfg = cfg
        self.norms = norms

        self.prior_logit = float(cfg.get("couple_prior_logit", -2.2))
        self.gain = {
            "perclos": float(cfg.get("couple_gain_perclos", 1.6)),
            "long_close_count": float(cfg.get("couple_gain_long_close", 1.2)),
            "yawn_count": float(cfg.get("couple_gain_yawn", 1.0)),
            "blink_rate_per_min": float(cfg.get("couple_gain_blink_low", 0.9)),
            "pose_drift": float(cfg.get("couple_gain_pose_drift", 0.5)),
            "perclos_trend": float(cfg.get("couple_gain_trend", 0.8)),
        }
        self.inter_gain = {
            "perclos_x_longclose": float(cfg.get("couple_interaction_perclos_longclose", 0.7)),
            "perclos_x_blinksupp": float(cfg.get("couple_interaction_perclos_blinksupp", 0.5)),
        }
        self.weight_floor = float(cfg.get("couple_weight_floor", 0.15))
        self.clip = float(cfg.get("couple_clip", 3.0))
        self.alpha = float(cfg.get("couple_memory_alpha", 0.25))
        self.risk_medium = float(cfg.get("couple_risk_medium", 0.45))
        self.risk_high = float(cfg.get("couple_risk_high", 0.70))

        self.quality_min = float(cfg.get("quality_min_score", 0.60))
        self.face_visible_min = float(cfg.get("face_visible_min", 0.70))
        self.yaw_max = float(cfg.get("pose_yaw_max_deg", 30.0))
        self.pitch_max = float(cfg.get("pose_pitch_max_deg", 25.0))
        self.perclos_warn = float(cfg.get("perclos_warning", 0.25))
        self.long_close_warn = float(cfg.get("fatigue_long_close_count", 1))
        self.yawn_warn = float(cfg.get("fatigue_yawn_count", 2))
        self.scenario = str(cfg.get("age_scenario") or "reading")
        self.trend_ref = float(cfg.get("couple_trend_ref_per_min", 0.10))

        # 年龄敏感度：眼部 vs 非眼部（60+ 组下调眼部权重 —— Cai 2021）
        self.eye_sens_default = float(cfg.get("couple_eye_sensitivity_default", 1.0))
        self.eye_sens_older = float(cfg.get("couple_eye_sensitivity_older", 0.5))
        self.non_eye_sens_older = float(cfg.get("couple_non_eye_sensitivity_older", 1.2))

        # 时序状态
        self._memory: float | None = None
        self._perclos_hist: deque[tuple[float, float]] = deque(maxlen=4096)
        self.window_s = float(cfg.get("window_seconds", 30))
        self.fps = float(cfg.get("fps_nominal", 30))

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """清空记忆（换人 / 重开会话时必须调用，否则上一个人的趋势会串过来）。"""
        self._memory = None
        self._perclos_hist.clear()

    def sensitivity(self, band: str) -> tuple[float, float, str]:
        """返回 (眼部权重, 非眼部权重, 说明)。老年人以外的组都是 1.0 基准。"""
        if band == "older_adult":
            note = ("60+ 组：眼部证据权重下调（Cai et al. 2021：睡眠剥夺后老年组的眨眼时长/"
                    "长闭眼/PERCLOS 未升高，而驾驶损害仍出现）")
            ev = self.norms.evidence.get("age_dependent_ocular_sensitivity") or {}
            if ev.get("source"):
                note += f"｜依据出处：{ev['source']}"
            return (self.eye_sens_older, self.non_eye_sens_older, note)
        return (self.eye_sens_default, 1.0, "非老年组：眼部证据按基准权重")

    # ------------------------------------------------------------------
    def _baseline_for(self, metric: str, band: str, *,
                      config_fallback: tuple[float, float, str] | None = None
                      ) -> tuple[float | None, float | None, str, str, str | None]:
        """取 (typical, scale, 出处说明, 置信度, source)。

        解析顺序**必须**是这个顺序，且每一级都要如实回传出处：
          1. 该年龄参照组在基线 JSON 里的数值（带文献出处）；
          2. `global` 组（全人群）的数值；
          3. 项目 `config.yaml` 的阈值（占位值，标注 `config`）；
          4. 都没有 → 该指标**跳过**（返回全 None），reason 里写明"无可用基线"。
        """
        b: Baseline | None = self.norms.baseline(metric, band)
        if b is not None and b.usable:
            label = f"参照基线 {b.band}.{metric}"
            if b.source:
                label += f"（{b.source}）"
            return (b.typical, b.effective_scale, label, b.confidence, b.source)
        if config_fallback is not None:
            typical, scale, label = config_fallback
            return (typical, scale, f"config.yaml {label}（⚠️ 占位值，未标定）", "不确定", None)
        return (None, None, "无可用基线", "不确定", None)

    # ------------------------------------------------------------------
    def compare(self, *, behavior: dict, quality: dict, face: dict, band: str,
                status: str | None = None, frame_id: int | None = None,
                ts: float | None = None) -> dict[str, Any]:
        """返回 `age_contract.comparison` 段。**任何情况下都返回一个合法结构**。"""
        base = {
            "comparable": False, "band_applied": band, "risk": None,
            "risk_level": "unknown", "deviation": None,
            "quality_gate": self.quality_min, "contributions": [], "interactions": [],
            "reason": "", "reference_version": self.norms.version,
            "reference_sources": self.norms.sources(band) or self.norms.sources(),
        }

        # ---- 1) 先判断能不能测 ------------------------------------------
        if status is not None and status not in COMPARABLE_STATUSES:
            base["reason"] = (f"本次不做同龄比对：状态为 {status}"
                              f"（只有 {COMPARABLE_STATUSES} 才代表测量可用）。"
                              f"先判断能不能测、再决定测出什么 —— 不报跳变数字。")
            return base
        if quality.get("overall") is None or float(quality["overall"]) < self.quality_min:
            base["reason"] = (f"本次不做同龄比对：信号质量 {quality.get('overall')} 低于门控"
                              f" {self.quality_min:.2f}。")
            return base
        if face.get("visible") is None or float(face["visible"]) < self.face_visible_min:
            base["reason"] = (f"本次不做同龄比对：人脸可见率 {face.get('visible')} 低于下限"
                              f" {self.face_visible_min:.2f}。")
            return base

        # ---- 2) 质量收缩权重 --------------------------------------------
        q = float(quality["overall"])
        q_factor = min(1.0, max(0.0, (q - self.quality_min) / max(1e-9, 1.0 - self.quality_min)))
        v_factor = min(1.0, max(0.0, float(face["visible"]) / self.face_visible_min))
        w_quality = self.weight_floor + (1.0 - self.weight_floor) * q_factor * v_factor

        eye_sens, non_eye_sens, sens_note = self.sensitivity(band)
        contributions: list[dict[str, Any]] = []
        skipped: list[str] = []

        def add(metric: str, value: float | None, z: float | None, cause: str,
                baseline_label: str, confidence: str, source: str | None,
                sensitivity: float) -> float:
            """登记一条证据，返回其 log_odds 贡献（已含敏感度与质量收缩）。"""
            if z is None:
                skipped.append(f"{metric}（{cause}）")
                return 0.0
            gain = self.gain.get(metric, 0.0)
            log_odds = _clip(gain * z, self.clip) * sensitivity * w_quality
            contributions.append({
                "metric": metric, "value": value, "z": round(z, 4),
                "gain": gain, "sensitivity": round(sensitivity, 3),
                "weight": round(w_quality, 4), "log_odds": round(log_odds, 4),
                "baseline": baseline_label, "confidence": confidence, "source": source,
                "why": cause,
            })
            return log_odds

        # ---- 3) 眼部证据 ------------------------------------------------
        typ, scale, label, conf, src = self._baseline_for(
            "perclos", band, config_fallback=(self.perclos_warn, max(1e-9, self.perclos_warn),
                                              f"perclos_warning={self.perclos_warn}"))
        perclos = _f(behavior.get("perclos"))
        z_perclos = None if (perclos is None or typ is None or not scale) else (perclos - typ) / scale
        lr_perclos = add("perclos", perclos, z_perclos,
                         "闭眼时间占比高于同龄基线" if (z_perclos or 0) > 0 else "闭眼时间占比在基线内",
                         label, conf, src, eye_sens)

        typ, scale, label, conf, src = self._baseline_for(
            "long_close_count", band, config_fallback=(0.0, float(self.long_close_warn),
                                                       f"fatigue_long_close_count={self.long_close_warn:g}"))
        lc = _f(behavior.get("long_close_count"))
        z_lc = None if (lc is None or typ is None or not scale) else (lc - typ) / scale
        lr_lc = add("long_close_count", lc, z_lc,
                    "出现长闭眼（慢闭眼/微睡眠倾向）" if (z_lc or 0) > 0 else "无长闭眼",
                    label, conf, src, eye_sens)

        # 眨眼率：**全局基线 + 场景口径**（文献不支持按年龄分组，见文件头）
        typ, scale, label, conf, src = self._baseline_for("blink_rate_per_min", "global")
        br = _f(behavior.get("blink_rate_per_min"))
        z_br = None
        br_cause = "无可用基线"
        if br is not None and typ is not None and scale:
            if br <= 0:
                z_br, br_cause = None, "本窗口无有效眨眼，不参与（避免把'没人'算成'疲劳'）"
            elif br < typ:
                z_br, br_cause = (typ - br) / scale, f"眨眼率低于{self.scenario}场景基线"
            else:
                z_br, br_cause = 0.0, "眨眼率不低于基线（偏低方向以外的变化不单独计为疲劳）"
        lr_br = add("blink_rate_per_min", br, z_br, br_cause, label, conf, src, eye_sens)

        # ---- 4) 非眼部证据（老年组相对更重要）---------------------------
        typ, scale, label, conf, src = self._baseline_for(
            "yawn_count", band, config_fallback=(0.0, float(self.yawn_warn),
                                                 f"fatigue_yawn_count={self.yawn_warn:g}"))
        yc = _f(behavior.get("yawn_count"))
        z_yc = None if (yc is None or typ is None or not scale) else (yc - typ) / scale
        add("yawn_count", yc, z_yc, "窗口内出现打哈欠" if (z_yc or 0) > 0 else "无打哈欠",
            label, conf, src, non_eye_sens)

        pose = face.get("pose") or {}
        yaw, pitch = abs(_f(pose.get("yaw")) or 0.0), abs(_f(pose.get("pitch")) or 0.0)
        drift = max(yaw / max(1e-9, self.yaw_max), pitch / max(1e-9, self.pitch_max))
        z_drift = max(0.0, drift - 0.5)      # 半程以内的漂移不算证据（避免常态抖动就报警）
        add("pose_drift", round(drift, 4), z_drift,
            "头部姿态在阈值内持续偏移（非眼动的注意力代理量）" if z_drift > 0 else "姿态稳定",
            "config.yaml pose_yaw_max_deg / pose_pitch_max_deg（⚠️ 占位值）", "不确定", None,
            non_eye_sens)

        # ---- 5) 趋势项：PERCLOS 斜率 ------------------------------------
        z_trend, slope_per_min, trend_cause = None, None, "样本不足，未计算趋势"
        if perclos is not None and frame_id is not None:
            t = float(ts) if ts is not None else (float(frame_id) / max(1e-9, self.fps))
            self._perclos_hist.append((t, perclos))
            z_trend, slope_per_min, trend_cause = self._trend_z()
        # 报出去的是**斜率本身**（每分钟 PERCLOS 变化量，人体可读），
        # 不是 z×基准 —— 否则界面会显示一个"10.8"却不知道它是什么量。
        add("perclos_trend", None if slope_per_min is None else round(slope_per_min, 4),
            z_trend, trend_cause, "config.yaml couple_trend_ref_per_min（⚠️ 占位值）",
            "不确定", None, eye_sens)

        # ---- 6) 显式耦合项 ----------------------------------------------
        log_odds_terms = lr_perclos + lr_lc + lr_br
        log_odds_terms += sum(float(c["log_odds"]) for c in contributions
                              if c["metric"] in ("yawn_count", "pose_drift", "perclos_trend"))
        interactions: list[dict[str, Any]] = []
        if z_perclos is not None and z_perclos > 0 and z_lc is not None and z_lc > 0:
            # 单看 PERCLOS 或长闭眼都可能只是"正常范围内的波动"，同时出现才是
            # "闭眼—再闭合"的疲劳模式。这一项是**耦合**的核心表达。
            extra = (self.inter_gain["perclos_x_longclose"] * min(1.0, z_perclos)
                     * min(1.0, z_lc) * eye_sens * w_quality)
            log_odds_terms += extra
            interactions.append({"rule": "perclos_x_longclose", "gain": round(extra, 4),
                                 "why": "高 PERCLOS 与长闭眼同时出现（闭眼—再闭合模式）"})
        if z_perclos is not None and z_perclos > 0 and z_br is not None and z_br > 0:
            extra = (self.inter_gain["perclos_x_blinksupp"] * min(1.0, z_perclos)
                     * min(1.0, z_br) * eye_sens * w_quality)
            log_odds_terms += extra
            interactions.append({"rule": "perclos_x_blinksupp", "gain": round(extra, 4),
                                 "why": "闭眼占比升高的同时眨眼率被抑制"})

        # ---- 7) 先验 + 时序累积 + 档位 ----------------------------------
        instant = self.prior_logit + log_odds_terms
        self._memory = instant if self._memory is None \
            else (1.0 - self.alpha) * self._memory + self.alpha * instant
        risk = sigmoid(self._memory)
        level = "high" if risk >= self.risk_high else ("medium" if risk >= self.risk_medium else "low")

        # 偏离量 = **截断后**的证据之和 ÷ 有效增益之和 = "平均偏离多少个基线尺度"。
        # 用截断后的值算，否则一个陡峭的趋势项（z 可以到几十）会把汇总数字整个带飞。
        denom = sum(float(c["gain"]) * float(c["sensitivity"]) * float(c["weight"])
                    for c in contributions)
        dev = (sum(float(c["log_odds"]) for c in contributions) / denom) if denom > 0 else 0.0

        reason = self._reason(band=band, level=level, risk=risk, contributions=contributions,
                              interactions=interactions, skipped=skipped, sens_note=sens_note,
                              w_quality=w_quality, status=status)
        base.update({
            "comparable": True, "risk": round(risk, 4), "risk_level": level,
            "deviation": round(dev, 4), "contributions": contributions,
            "interactions": interactions, "reason": reason,
        })
        return base

    # ------------------------------------------------------------------
    def _trend_z(self) -> tuple[float | None, float | None, str]:
        """窗口内 PERCLOS 的最小二乘斜率 → (z, 每分钟斜率, 说明)。"""
        pts = [p for p in self._perclos_hist
               if p[0] >= self._perclos_hist[-1][0] - self.window_s]
        if len(pts) < 5:
            return None, None, "样本不足，未计算趋势"
        n = len(pts)
        mx = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        den = sum((p[0] - mx) ** 2 for p in pts)
        if den <= 1e-12:
            return None, None, "时间轴无跨度，未计算趋势"
        slope_per_s = sum((p[0] - mx) * (p[1] - my) for p in pts) / den
        slope_per_min = slope_per_s * 60.0
        z = max(0.0, slope_per_min / max(1e-9, self.trend_ref))
        return z, slope_per_min, (f"PERCLOS 每分钟上升 {slope_per_min:+.3f}"
                                  f"（基准 {self.trend_ref:.2f}/min 记为 1 个尺度）")

    def _reason(self, *, band: str, level: str, risk: float,
                contributions: list[dict], interactions: list[dict], skipped: list[str],
                sens_note: str, w_quality: float, status: str | None) -> str:
        band_txt = ("未确认年龄 → 使用全人群兜底基线（未分龄）" if band == "global"
                    else f"参照组 {band}")
        top = sorted(contributions, key=lambda c: -abs(float(c["log_odds"])))[:3]
        top_txt = "；".join(
            f"{c['metric']} 值 {c['value']}（偏离 z={c['z']:+.2f}，贡献 {c['log_odds']:+.3f}）"
            for c in top) or "无有效证据项"
        parts = [
            f"耦合风险 {risk:.2f}（{level}），{band_txt}",
            f"主要贡献：{top_txt}",
            f"质量收缩权重 {w_quality:.2f}（质量越差证据越弱）",
            sens_note,
        ]
        if interactions:
            parts.append("耦合项：" + "；".join(f"{i['rule']}（{i['why']}）" for i in interactions))
        if skipped:
            parts.append("未参与（缺基线/样本不足）：" + "；".join(skipped))
        parts.append("口径提醒：本项目 PERCLOS 含快速眨眼的低 EAR 帧，"
                     "与 Dinges & Grace 1998 强调的 slow closure 不完全等价（见 docs/27）")
        return "。".join(parts) + "。"


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":  # 自检：python backend/fatigue_coupling.py
    import json

    try:
        from .age_contract import validate_age_state, new_age_state
        from .config import load_config
        from .fatigue_db import load_reference
    except ImportError:
        from age_contract import validate_age_state, new_age_state
        from config import load_config
        from fatigue_db import load_reference

    cfg = load_config()
    norms = load_reference(cfg=cfg)
    good_q = {"overall": 0.92, "light_score": 0.9, "motion_score": 0.05}
    good_face = {"visible": 0.98, "pose": {"yaw": -2.0, "pitch": 1.5, "roll": 0.3}}
    calm = {"perclos": 0.06, "long_close_count": 0, "yawn_count": 0, "blink_rate_per_min": 18.2}
    tired = {"perclos": 0.34, "long_close_count": 2, "yawn_count": 3, "blink_rate_per_min": 6.0}
    bad_q = {"overall": 0.42, "light_score": 0.3, "motion_score": 0.7}

    print(f"参照基线版本：{norms.version}（组：{list(norms.bands)}）")
    for band in ("young_adult", "older_adult", "global"):
        for name, beh, q in (("正常", calm, good_q), ("疲劳", tired, good_q), ("质量差", tired, bad_q)):
            m = CoupledRiskModel(cfg, norms)
            for i in range(40):     # 跑满窗口，让趋势项与记忆生效
                beh_i = dict(beh)
                if name == "疲劳":
                    beh_i["perclos"] = 0.10 + 0.006 * i
                r = m.compare(behavior=beh_i, quality=q, face=good_face, band=band,
                              status="normal" if q is good_q else "unreliable",
                              frame_id=i, ts=i / 30.0)
            print(f"[{band:12s}] {name:4s} → comparable={str(r['comparable']):5s} "
                  f"risk={r['risk']} level={r['risk_level']:7s} 项数={len(r['contributions'])} "
                  f"耦合={[i['rule'] for i in r['interactions']]}")
            if band == "older_adult" and name == "疲劳":
                print("   理由：", r["reason"][:200], "…")
                side = new_age_state(ts=1.0, frame_id=1, comparison=r)
                print("   schema 校验：", validate_age_state(side) or "通过")
