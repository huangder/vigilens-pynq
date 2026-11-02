"""一维阈值标定：从「带标签的特征」反推 `config.yaml` 里该填多少。

## 为什么单独一个模块

`config.yaml` 里 `ear_close_threshold` / `mar_threshold` / `pose_yaw_max_deg` /
`pose_pitch_max_deg` / `light_score_min` / `motion_score_max` 目前全是**占位值**
（每行都标了 ⚠️）。占位值的问题不是"不准"，而是**没人知道它们离真实分布有多远**：
EAR 0.19 与真实闭眼分界 0.24 之间差多少，看代码永远看不出来。

本模块只做一件事：**给两组带标签的样本，找使 Youden's J 最大的那个分界**，
并把"分界两侧还剩多少重叠"如实打印出来 —— 因为这些数字是要写进证据、写进
`docs/16` 的，不能拍脑袋。

## 边界（写清楚，免得被当成"已标定"）

- 这里算的是**该数据集上**的最优分界，不是"普适阈值"。换设备、换光照、
  换人群都会漂移，所以结果必须连同**数据来源**一起记，且不得覆盖 `config.yaml`：
  本模块**不写文件**，只返回候选值 + 证据数字，由人来改并写依据。
- 一维阈值只能表达"这条特征单独够不够"，表达不了"两条特征联合判"。
  联合门控（比如 `quality_weights`）需要多变量方法，不在本模块范围内。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math

__all__ = ["ThresholdFit", "fit_threshold", "youden_j", "candidate_thresholds"]


@dataclass
class ThresholdFit:
    """一次标定的结果。字段名与 `config.yaml` 的键名**不绑定**，由调用方映射。"""

    threshold: float                 # 建议阈值（候选里 J 最大的那个）
    youden_j: float                  # J = TPR + TNR - 1，越接近 1 分界越干净
    tpr: float                       # 阳性（闭眼/哈欠/偏头/不可用）判对的比例
    tnr: float                       # 阴性判对的比例
    n_pos: int                       # 阳性样本数
    n_neg: int                       # 阴性样本数
    direction: str                   # "lower_is_positive" / "higher_is_positive"
    pos_quantiles: dict = field(default_factory=dict)   # p05/p50/p95，给"重叠有多重"用
    neg_quantiles: dict = field(default_factory=dict)
    ties: int = 0                    # 落在阈值上的样本数（会同时算进两侧，故需报告）

    def as_dict(self) -> dict:
        return asdict(self)


def _quantile(sorted_vals: list[float], q: float) -> float:
    """线性插值分位数（样本量小，不需要 numpy 依赖）。"""
    if not sorted_vals:
        raise ValueError("空序列没有分位数")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


def _summarize(vals: list[float]) -> dict:
    s = sorted(vals)
    return {"p05": round(_quantile(s, 0.05), 4),
            "p50": round(_quantile(s, 0.50), 4),
            "p95": round(_quantile(s, 0.95), 4)}


def candidate_thresholds(pos: list[float], neg: list[float]) -> list[float]:
    """候选阈值 = 相邻不同取值的中点（含两端外侧各一个）。

    **不用等距网格**：特征只有几百个样本，网格会漏掉真正干净的分界；
    中点集合是 1D 情形的**完备**候选集（任何区间内 J 相同，取中点即可）。
    """
    uniq = sorted(set(pos) | set(neg))
    if not uniq:
        raise ValueError("阳性/阴性都为空，没法标定")
    if len(uniq) == 1:
        # 只有单一取值：没法分开。给一个"全判一侧"的阈值，并让 J=0 暴露真相。
        v = uniq[0]
        return [v - 1e-6, v + 1e-6]
    cands = [uniq[0] - 1e-6]
    for a, b in zip(uniq, uniq[1:]):
        cands.append((a + b) / 2.0)
    cands.append(uniq[-1] + 1e-6)
    return cands


def youden_j(pos: list[float], neg: list[float], threshold: float,
             *, lower_is_positive: bool) -> tuple[float, float, float, int]:
    """在给定阈值上算 (J, TPR, TNR, ties)。

    `lower_is_positive=True` 用于"值越小越阳性"的特征（EAR：闭眼时更小）；
    为 False 时用于"值越大越阳性"（MAR / |yaw| / motion_score）。
    """
    if lower_is_positive:
        tp = sum(1 for v in pos if v < threshold)
        tn = sum(1 for v in neg if v >= threshold)
        ties = sum(1 for v in pos + neg if v == threshold)
    else:
        tp = sum(1 for v in pos if v > threshold)
        tn = sum(1 for v in neg if v <= threshold)
        ties = sum(1 for v in pos + neg if v == threshold)
    tpr = tp / len(pos) if pos else 0.0
    tnr = tn / len(neg) if neg else 0.0
    return tpr + tnr - 1.0, tpr, tnr, ties


def fit_threshold(pos: list[float], neg: list[float], *,
                  lower_is_positive: bool = True) -> ThresholdFit:
    """找使 Youden's J 最大的阈值。

    `pos` = 阳性样本的特征值（闭眼帧的 EAR / 哈欠帧的 MAR / 偏头帧的 |yaw| …）；
    `neg` = 阴性样本（睁眼 / 平静 / 正视 …）。

    ⚠️ 两组都非空才允许调用：只有阳性没有阴性时，"最优阈值"会退化成"永远判阳性"，
    那种结果比占位值更危险（看起来有依据，其实是幻觉）。
    """
    if not pos or not neg:
        raise ValueError(f"阳性/阴性样本都必须非空（pos={len(pos)}, neg={len(neg)}）")
    if any(not math.isfinite(float(v)) for v in (*pos, *neg)):
        raise ValueError("样本包含非有限数值（NaN/Inf），拒绝拟合")

    best: tuple[float, float, float, float, int] | None = None   # (J, thr, tpr, tnr, ties)
    for thr in candidate_thresholds(pos, neg):
        j, tpr, tnr, ties = youden_j(pos, neg, thr, lower_is_positive=lower_is_positive)
        if best is None or j > best[0]:
            best = (j, thr, tpr, tnr, ties)
        # J 相同时取"离两组中位数更均衡"的一侧，避免把阈值推到分布边缘
        elif abs(j - best[0]) < 1e-12:
            mid = (_quantile(sorted(pos), 0.5) + _quantile(sorted(neg), 0.5)) / 2.0
            if abs(thr - mid) < abs(best[1] - mid):
                best = (j, thr, tpr, tnr, ties)

    assert best is not None
    j, thr, tpr, tnr, ties = best
    return ThresholdFit(
        threshold=round(float(thr), 4),
        youden_j=round(float(j), 4),
        tpr=round(float(tpr), 4),
        tnr=round(float(tnr), 4),
        n_pos=len(pos),
        n_neg=len(neg),
        direction="lower_is_positive" if lower_is_positive else "higher_is_positive",
        pos_quantiles=_summarize(pos),
        neg_quantiles=_summarize(neg),
        ties=int(ties),
    )
