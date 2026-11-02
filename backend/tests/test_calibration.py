"""阈值标定模块的单元测试（`backend/calibration.py`）。

测试策略：**用已知分布的合成样本**驱动 —— 分界"干净"与"重叠"两种情形都要覆盖，
否则 Youden's J 的实现写错了也看不出来（只要排序对，J=1 的情形永远对）。
"""

from __future__ import annotations

import random
import math

import pytest

from backend.calibration import candidate_thresholds, fit_threshold, youden_j


def _gauss(seed: int, n: int, mu: float, sigma: float) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


def test_separable_groups_get_j_near_one() -> None:
    """两组完全分开时，J 必须接近 1，且阈值落在两簇之间。"""
    pos = _gauss(1, 200, 0.15, 0.01)     # 闭眼帧的 EAR
    neg = _gauss(2, 200, 0.35, 0.01)     # 睁眼帧的 EAR
    fit = fit_threshold(pos, neg, lower_is_positive=True)
    assert fit.direction == "lower_is_positive"
    assert 0.20 < fit.threshold < 0.30
    assert fit.youden_j > 0.99
    assert fit.tpr == 1.0 and fit.tnr == 1.0
    assert fit.n_pos == 200 and fit.n_neg == 200


def test_overlapping_groups_expose_the_overlap() -> None:
    """重叠时必须给出 0 < J < 1，并把两侧分位数如实报出来（不许假装干净）。"""
    pos = _gauss(3, 300, 0.18, 0.04)
    neg = _gauss(4, 300, 0.26, 0.04)
    fit = fit_threshold(pos, neg, lower_is_positive=True)
    assert 0.0 < fit.youden_j < 1.0
    assert 0.0 < fit.tpr < 1.0 and 0.0 < fit.tnr < 1.0
    assert fit.pos_quantiles["p05"] <= fit.pos_quantiles["p50"] <= fit.pos_quantiles["p95"]
    assert fit.neg_quantiles["p50"] > fit.pos_quantiles["p50"]


def test_higher_is_positive_for_mar() -> None:
    """MAR 是"越大越阳性"（哈欠），方向搞反会把判据整体翻过来。"""
    pos = _gauss(5, 120, 0.60, 0.03)     # 哈欠帧
    neg = _gauss(6, 120, 0.10, 0.02)     # 平静帧
    fit = fit_threshold(pos, neg, lower_is_positive=False)
    assert fit.direction == "higher_is_positive"
    assert 0.2 < fit.threshold < 0.5
    assert fit.youden_j > 0.99


def test_both_groups_required() -> None:
    """只有一组时必须报错，而不是给出"永远判阳性"的假阈值。"""
    with pytest.raises(ValueError):
        fit_threshold([0.1, 0.2], [], lower_is_positive=True)
    with pytest.raises(ValueError):
        fit_threshold([], [0.1, 0.2], lower_is_positive=False)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_samples_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="非有限"):
        fit_threshold([0.1, bad], [0.3], lower_is_positive=True)


def test_motion_unusable_is_higher_is_positive() -> None:
    from metrics.scripts.calibrate_thresholds import SPECS
    assert SPECS["motion"][1:4] == ("unusable", "usable", False)


def test_candidates_are_midpoints_and_cover_edges() -> None:
    """候选集必须含相邻不同取值的中点，并覆盖两端外侧（1D 的完备候选集）。"""
    cands = candidate_thresholds([0.1, 0.3], [0.5])
    assert any(abs(c - 0.2) < 1e-9 for c in cands)      # 0.1/0.3 的中点
    assert any(abs(c - 0.4) < 1e-9 for c in cands)      # 0.3/0.5 的中点
    assert cands[0] < 0.1 and cands[-1] > 0.5


def test_youden_counts_ties() -> None:
    """落在阈值上的样本数要如实报告：平局会让 TPR/TNR 同时虚高。"""
    j, tpr, tnr, ties = youden_j([0.2], [0.2], 0.2, lower_is_positive=True)
    assert ties == 2
    assert (tpr, tnr) == (0.0, 1.0)
    assert j == 0.0
