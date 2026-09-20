"""rPPG 链路的单元测试（《02》任务 A 线增强项，契约 §3.6 的时间序列链路）。

测试策略：**用已知频率的合成信号驱动**，而不是等真实视频 ——
这样"算法对不对"与"数据有没有"解耦，真实视频到位后只需换输入源。

覆盖三层：
  1. 底层零件：Q1.15 量化、FIR 算术、ROI 几何与整数累加；
  2. 估计器行为：能解出已知频率、对噪声拒绝出数、窗口没满不出数；
  3. 设计承诺：漏帧时用有效采样率校正频率标度；`rr_*` 目前恒为 null（契约 §3.5 已知限制）。
"""

from __future__ import annotations

import math

import pytest

from backend.config import load_config
from backend.vital import (GreenRppg, forehead_roi, fir_process, load_fir_coeffs,
                           q15_from_roi_sum, roi_channel_sum)

CFG = load_config()
FPS = float(CFG["fps_nominal"])
WINDOW = float(CFG["window_seconds"])


def _feed(est: GreenRppg, freq_hz: float, *, amp: int = 6000, noise: float = 0.0,
          seed: int = 1, seconds: float | None = None, stride: int = 1) -> dict:
    """按已知频率喂正弦；`stride>1` 表示每 stride 帧才有一个有效样本（模拟漏帧）。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    n_valid = int(round((seconds if seconds is not None else WINDOW) * FPS))
    frame_id = 0
    pushed = 0
    while pushed < n_valid:
        if frame_id % stride == 0:
            v = amp * math.sin(2.0 * math.pi * freq_hz * frame_id / FPS)
            if noise:
                v += float(rng.normal(0.0, noise))
            est.push(int(max(-32768, min(32767, round(v)))), frame_id)
            pushed += 1
        frame_id += 1
    return est.estimate()


# ---------------------------------------------------------------------------
# 1) 底层零件
# ---------------------------------------------------------------------------

def test_q15_quantization_centers_at_128() -> None:
    """中灰（均值 128）映射到 0 —— 带通因此工作在零点附近（契约 4.6 性质 P2）。"""
    assert q15_from_roi_sum(128 * 1000, 1000) == 0
    assert q15_from_roi_sum(0, 100) == -32768
    # 合法均值永不裁剪：0 与 255 的极端
    assert q15_from_roi_sum(255 * 100, 100) <= 32512


def test_q15_rejects_empty_roi() -> None:
    """count==0 的帧必须整帧丢弃，不得产生样本（契约 4.6）。"""
    with pytest.raises(ValueError):
        q15_from_roi_sum(0, 0)


def test_fir_uses_frozen_coefficients_and_arithmetic() -> None:
    """带通必须用冻结系数（唯一来源是 C 线的头文件），且首样本符合 y=h[0]*x>>15。"""
    coeffs, shift, fs = load_fir_coeffs()
    # ⚠️ 不要硬编码 30/45（帧率已改过两次：30 → 45（v1.1）→ 30（v1.2 草案））。
    #    这里断言的是"C 线冻结头文件里的 fs 与契约 §0 的 fps_nominal 一致"，
    #    即抓住两个单一来源之间的漂移 —— 比写死一个数字更强。
    assert len(coeffs) == 63 and shift == 15
    assert fs == float(CFG["fps_nominal"]), (
        f"FIR 头文件 fs={fs} 与 config.yaml fps_nominal={CFG['fps_nominal']} 不一致："
        "系数与帧率必须同改（见 docs/interface.md §0/§3.5）"
    )
    ys, sat, _hist = fir_process([1000], coeffs, shift, None)
    assert ys[0] == (coeffs[0] * 1000) >> shift
    assert sat == 0


def test_fir_streaming_equals_batch() -> None:
    """逐样本喂 与 整段喂 必须逐样本一致（段语义：延迟线跨界保持）。

    ⚠️ 这条是**回归用例**：P1 的黄金参考只整段喂（段长都 >= 63），
    因此漏掉了"x 短于 N-1 时历史长度算错"这条路径 —— 逐样本喂 + hist=None 会直接报错。
    契约 §3.5 的 split 段测的是"整段接整段"，与"逐样本"是两条不同的路径。
    """
    coeffs, shift, _ = load_fir_coeffs()
    sig = [((i * 7919) % 20001) - 10000 for i in range(200)]   # 确定性伪随机，含正负

    batch, batch_sat, _ = fir_process(sig, coeffs, shift, None)

    streamed: list[int] = []
    hist = None
    for v in sig:
        ys, _sat, hist = fir_process([v], coeffs, shift, hist)
        streamed.append(ys[0])

    assert streamed == batch
    assert batch_sat == 0


def test_forehead_roi_is_inside_face_box_and_clamped() -> None:
    """额头 ROI 必须落在人脸框上半部，并裁剪到画面内。"""
    x0, y0, x1, y1 = forehead_roi((100, 200, 200, 300), 640, 480)
    assert 100 <= x0 < x1 <= 300 and 200 <= y0 < y1 <= 500
    assert y1 <= 200 + int(300 * 0.35) + 1          # 只取上部
    # 贴着画面右下角的人脸框：必须被裁进画面
    a0, b0, a1, b1 = forehead_roi((600, 460, 100, 100), 640, 480)
    assert 0 <= a0 <= a1 <= 640 and 0 <= b0 <= b1 <= 480
    # 无效框 -> 空 ROI
    assert forehead_roi((10, 10, 0, 50), 640, 480) == (0, 0, 0, 0)


def test_roi_channel_sum_is_half_open() -> None:
    """半开区间 [x0,x1)×[y0,y1)，越界裁剪，空 ROI 返回 (0,0)；绿通道下标在 RGB/BGR 下都是 1。"""
    import numpy as np

    img = np.zeros((10, 10, 3), dtype=np.uint8)
    img[2, 3] = [10, 20, 30]                        # (y=2, x=3)
    assert roi_channel_sum(img, (3, 2, 4, 3), channel=1) == (20, 1)     # 含 (3,2)、不含 (4,3)
    assert roi_channel_sum(img, (4, 3, 5, 4), channel=1) == (0, 1)
    assert roi_channel_sum(img, (3, 2, 3, 3), channel=1) == (0, 0)      # x0==x1 -> 空
    assert roi_channel_sum(img, (8, 8, 99, 99), channel=1) == (0, 4)    # 越界裁剪到 2×2
    assert roi_channel_sum("不是图像", (0, 0, 1, 1)) == (0, 0)          # 合成帧源


# ---------------------------------------------------------------------------
# 2) 估计器行为（合成信号驱动）
# ---------------------------------------------------------------------------

def test_rppg_recovers_on_bin_frequency() -> None:
    """落在频点上的正弦：误差应 <= 1 个频点间距（本窗口 = 2 BPM）。"""
    est = GreenRppg.from_config(CFG)
    r = _feed(est, 1.20)                            # 72 BPM
    assert r["hr_bpm"] is not None
    assert abs(r["hr_bpm"] - 72.0) <= 2.0
    assert r["hr_conf"] is not None and r["hr_conf"] > 0.5


def test_rppg_recovers_off_bin_frequency() -> None:
    """落在两个频点正中间（最坏情况）：误差应 <= 1 个频点间距。"""
    est = GreenRppg.from_config(CFG)
    r = _feed(est, 1.25)                            # 75 BPM，1.25/(45/1350) = 37.5 个频点
    assert r["hr_bpm"] is not None
    assert abs(r["hr_bpm"] - 75.0) <= 2.0


def test_rppg_rejects_pure_noise() -> None:
    """纯噪声不该给出心率 —— 宁可说"测不出"，也不报一个随机数（产品承诺）。"""
    est = GreenRppg.from_config(CFG)
    r = _feed(est, 0.0, amp=0, noise=6000, seed=7)
    assert r["hr_bpm"] is None and r["hr_conf"] is None


def test_rppg_needs_full_window() -> None:
    """窗口没填满一律不出数：没测够就是没测够。"""
    est = GreenRppg.from_config(CFG)
    assert not est.ready
    r = _feed(est, 1.20, seconds=WINDOW / 2.0)
    assert r["hr_bpm"] is None
    assert est.samples == est.need // 2


def test_rppg_constant_signal_gives_no_peak() -> None:
    """恒定输入（无脉动）不该报出心率。"""
    est = GreenRppg.from_config(CFG)
    r = _feed(est, 0.0, amp=0)
    assert r["hr_bpm"] is None


def test_rppg_is_deterministic() -> None:
    """同一串样本喂两次，结果必须逐字段相同（《02》A3 的确定性纪律）。"""
    a = _feed(GreenRppg.from_config(CFG), 1.20)
    b = _feed(GreenRppg.from_config(CFG), 1.20)
    assert a == b


def test_rppg_dropped_frames_keep_frequency_scale() -> None:
    """漏帧时用窗口首尾帧号反推有效采样率：不校正的话频率会被系统性抬高（≈stride 倍）。

    这里每 2 帧才给一个样本（有效 22.5 Hz）。若误用标称 45 Hz 做 FFT，
    1.2 Hz 会被算成 2.4 Hz（144 BPM）；校正后应仍在 72 BPM 附近。
    """
    est = GreenRppg.from_config(CFG)
    r = _feed(est, 1.20, stride=2)
    assert r["hr_bpm"] is not None
    assert abs(r["hr_bpm"] - 72.0) <= 2.0


def test_rppg_respiration_stays_null() -> None:
    """呼吸率必须保持 null：契约 §3.5 冻结"63 阶 @45 fps 做不了呼吸带"，
    需要 PS 侧降采样 + **另一组系数**，而那组系数尚未冻结。宁可空着，也不给假数字。"""
    r = _feed(GreenRppg.from_config(CFG), 1.20)
    assert r["rr_per_min"] is None and r["rr_conf"] is None


# ---------------------------------------------------------------------------
# 3) 接线：门控必须挡住低质量下的数字；端到端在无真实人脸时不得凭空出数
# ---------------------------------------------------------------------------

def test_gate_vitals_only_passes_high_quality() -> None:
    """`vital_require_quality` 之上才允许输出数字 —— 这是产品承诺，不是可选项。"""
    from backend.decision import DecisionEngine

    eng = DecisionEngine(CFG)
    est = {"hr_bpm": 72.0, "hr_conf": 0.8, "rr_per_min": None, "rr_conf": None}
    assert eng.gate_vitals(CFG["vital_require_quality"] + 0.1, est)["hr_bpm"] == 72.0
    assert eng.gate_vitals(CFG["vital_require_quality"] - 0.1, est)["hr_bpm"] is None
    assert eng.gate_vitals(0.99, None)["hr_bpm"] is None        # 没有估计 -> null


def test_run_pipeline_keeps_vital_null_without_real_face(workdir) -> None:
    """端到端：合成帧源没有真实图像 → ROI 为空 → 样本被丢弃 → vital.* 必须仍是 null。

    这条守住的是"rPPG 接进来之后，旧行为不能被悄悄改掉"：没有可信输入就不报数字。
    """
    import json

    from backend.run_pipeline import main as run_pipeline_main

    out = workdir / "v.json"
    rc = run_pipeline_main(["--source", "synthetic", "--seconds", "3", "--stub",
                            "--quiet", "--json", str(out)])
    assert rc == 0
    frame = json.loads(out.read_text(encoding="utf-8"))
    assert frame["vital"] == {"hr_bpm": None, "hr_conf": None,
                              "rr_per_min": None, "rr_conf": None}
