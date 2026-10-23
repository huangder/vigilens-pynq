"""rPPG 时间基准的回归测试 —— 对应 `docs/16` BUG-029 与 `docs/37` 风险表 R14。

**要钉住的行为**：
  1. 活帧源（摄像头/网络流）把**采集侧真实时间戳**交给 `GreenRppg.push(ts=...)` 后，
     频率轴按真实采样率换算：30 fps 下 1.2 Hz 信号 → 72 bpm；
  2. 真实采样率与 `fps_nominal` 偏差超过 `rppg_fs_tol` 时**一律不报数**（四项全 None），
     并在 `last_reject` 写成 `fs_mismatch` —— 这是"宁可 null，不报错数"的产品承诺；
  3. 离线回放（不传 ts）仍然走 `frame_id / fps_nominal`，且 `time_base` 如实标注为
     `fid_nominal`（那条路径恒等于标称 fps，不能拿来证明采样率正确）；
  4. `ts` 非单调（时钟回跳/数据错乱）时退回 `fid_nominal`，不抛异常。

输入是**人为构造的正弦**，用来暴露口径问题；不是"测出来的心率"。
"""

from __future__ import annotations

import math

import pytest

from backend import vital as vital_mod
from backend.capture import is_live_source
from backend.vital import GreenRppg

FPS = 30.0
WINDOW_S = 30.0
BAND = (0.7, 3.5)
F_TRUE = 1.2                    # 72 bpm
NEED = int(round(WINDOW_S * FPS))   # 900


def _feed(fs_real: float, *, with_ts: bool, samples: int = NEED,
          ts_scale: float = 1.0) -> GreenRppg:
    """按 `fs_real` 的真实采样率喂 `samples` 个样本。

    `ts_scale != 1` 用来构造"时间戳比真实间隔快/慢"的错乱场景（如时钟回跳用负数）。
    """
    est = GreenRppg(fps=FPS, window_seconds=WINDOW_S, hr_band_hz=BAND)
    for i in range(samples):
        v = int(round(3000.0 * math.sin(2.0 * math.pi * F_TRUE * i / fs_real)))
        ts = (i / fs_real) * ts_scale if with_ts else None
        est.push(v, i, ts=ts)
    return est


def test_true_rate_30fps_reports_72_bpm() -> None:
    est = _feed(30.0, with_ts=True)
    out = est.estimate()
    assert est.last_time_base == "source_ts"
    assert est.last_reject is None
    assert out["hr_bpm"] == pytest.approx(72.0, abs=1.5)
    assert out["hr_conf"] is not None and out["hr_conf"] > 0.5


@pytest.mark.parametrize("fs_bad", [13.0, 8.0, 3.7])
def test_sampling_rate_mismatch_refuses_to_report(fs_bad: float) -> None:
    """13 fps 曾经把 72 bpm 报成 166 bpm（conf 0.9998）—— 现在必须四项全 None。"""
    est = _feed(fs_bad, with_ts=True)
    out = est.estimate()
    assert est.last_time_base == "source_ts"
    assert est.last_reject == "fs_mismatch", f"fs_eff={est.last_fs_eff}"
    assert out == {"hr_bpm": None, "hr_conf": None, "rr_per_min": None, "rr_conf": None}
    assert out["hr_bpm"] is None


def test_measured_rate_30_14fps_is_still_accepted() -> None:
    """实测 30.14 fps vs 标称 30（+0.5%）在 10% 容差内，必须照常出数。

    这条防的是"护栏装过头"：真机实测帧率永远不是整整 30.000，
    如果容差写成 1e-3，真机就再也不出数了。
    """
    est = _feed(30.14, with_ts=True)
    out = est.estimate()
    assert est.last_reject is None
    assert out["hr_bpm"] == pytest.approx(72.0, abs=2.0)


def test_offline_source_keeps_frame_id_path() -> None:
    """不传 ts（离线回放）→ 走 frame_id/fps，且 time_base 如实写 fid_nominal。"""
    est = _feed(30.0, with_ts=False)
    out = est.estimate()
    assert est.last_time_base == "fid_nominal"
    assert out["hr_bpm"] == pytest.approx(72.0, abs=1.5)


def test_non_monotonic_timestamps_fall_back_instead_of_crashing() -> None:
    """时间戳回跳（NTP 校时 / 坏数据）不能炸，也不能据此算频率 → 退回 fid 路径。"""
    est = GreenRppg(fps=FPS, window_seconds=WINDOW_S, hr_band_hz=BAND)
    for i in range(NEED):
        v = int(round(3000.0 * math.sin(2.0 * math.pi * F_TRUE * i / FPS)))
        ts = (i / FPS) if i < NEED - 1 else 0.0     # 最后一个时间戳回跳到 0
        est.push(v, i, ts=ts)
    out = est.estimate()
    assert est.last_time_base == "fid_nominal"
    assert out["hr_bpm"] == pytest.approx(72.0, abs=1.5)


def test_jittered_timestamps_still_report_72() -> None:
    """真机时间戳永远带抖动（±20% 帧间隔），不能被护栏误杀。

    这条模拟"摄像头 30 fps 但每帧到达间隔抖一下"：`fs_eff` 会在 30 附近浮动，
    仍然落在 10% 容差内 ⇒ 必须正常出数。
    """
    import numpy as np

    rng = np.random.default_rng(7)
    est = GreenRppg(fps=FPS, window_seconds=WINDOW_S, hr_band_hz=BAND)
    t = 0.0
    for i in range(NEED):
        if i:
            t += (1.0 / FPS) * float(rng.uniform(0.8, 1.2))
        v = int(round(3000.0 * math.sin(2.0 * math.pi * F_TRUE * i / FPS)))
        est.push(v, i, ts=t)
    out = est.estimate()
    assert est.last_time_base == "source_ts"
    assert est.last_reject is None
    assert out["hr_bpm"] == pytest.approx(72.0, abs=2.0)


def test_tolerance_comes_from_config() -> None:
    """`from_config` 必须从 `config.yaml` 的 `rppg_fs_tol` 取容差（阈值不许写死在类里）。"""
    from backend.config import load_config

    cfg = load_config()
    est = GreenRppg.from_config(cfg, fps=FPS)
    assert est.fs_tol == pytest.approx(float(cfg["rppg_fs_tol"]))

    cfg2 = dict(cfg)
    cfg2["rppg_fs_tol"] = 0.001
    est2 = GreenRppg.from_config(cfg2, fps=FPS)
    assert est2.fs_tol == pytest.approx(0.001)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("synthetic", False),
        ("demo", False),
        ("data/raw/blink.mp4", False),
        ("0", True),
        ("2", True),
        ("uvc:0", True),
        ("mjpeg", True),
        ("mjpeg:http://127.0.0.1:8000/video.mjpg", True),
        ("http://127.0.0.1:8031/video.mjpg", True),
    ],
)
def test_is_live_source(source: str, expected: bool) -> None:
    """只有活帧源的 `Frame.ts` 才是真实到达时间，离线源用它反而不可复现。"""
    assert is_live_source(source) is expected


def test_fir_coeffs_are_the_frozen_ones() -> None:
    """护栏改的只是时间轴，不许顺手换滤波器（系数仍来自冻结表）。"""
    coeffs, shift, fs = vital_mod.load_fir_coeffs()
    assert shift == 15
    assert fs == pytest.approx(30.0)
    assert len(coeffs) in (63, 127)
