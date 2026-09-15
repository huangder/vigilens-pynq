"""行为指标与端到端可复现性测试（《02》A3/A4/A5 验收口径）。

《02》铁律 3："任何'看起来能用'都不算完成。用黄金结果、重复运行一致性、
可复现脚本来定义'完成'。" —— 本文件就是这条纪律的自动化版本。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.behavior_metrics import BehaviorTracker
from backend.config import load_config
from backend.decision import DecisionEngine
from backend.face_landmark import StubLandmarker, ear, make_landmarker, mar
from backend.run_pipeline import main as run_pipeline_main

CFG = load_config()

# 契约 §0 的帧率（config.yaml 是唯一来源）。下面的用例一律**按秒数 × FPS** 推帧号，
# 不要再写死 30/45 —— 帧率在 2026-09-13 已经改过一次（30→45，契约 v1.1）。
FPS = float(CFG["fps_nominal"])


# ---------------------------------------------------------------------------
# 状态机 / 指标
# ---------------------------------------------------------------------------

def test_ear_formula_matches_definition() -> None:
    """EAR = (|p2-p6| + |p3-p5|) / (2*|p1-p4|)：手算一个已知例验证公式。"""
    eye = {"p1": (0.0, 0.0), "p2": (1.0, -1.0), "p3": (3.0, -1.0),
           "p4": (4.0, 0.0), "p5": (3.0, 1.0), "p6": (1.0, 1.0)}
    # |p2-p6| = 2, |p3-p5| = 2, |p1-p4| = 4  → (2+2)/(2*4) = 0.5
    assert ear(eye) == pytest.approx(0.5)


def test_mar_formula_matches_definition() -> None:
    mouth = {"left": (0.0, 0.0), "right": (10.0, 0.0), "top": (5.0, -3.0), "bottom": (5.0, 3.0)}
    assert mar(mouth) == pytest.approx(0.6)   # 6/10


def test_ear_zero_division_is_safe() -> None:
    assert ear({"p1": (0, 0), "p2": (0, 0), "p3": (0, 0), "p4": (0, 0), "p5": (0, 0), "p6": (0, 0)}) == 0.0
    assert mar({}) == 0.0


def test_stub_landmarker_is_deterministic() -> None:
    mk = StubLandmarker(pattern="blink")
    a = mk.detect(None, 42)
    b = mk.detect(None, 42)
    assert a.bbox == b.bbox
    assert ear(a.eyes["left"]) == pytest.approx(ear(b.eyes["left"]))


def test_blink_counting_is_reproducible() -> None:
    """同视频重复运行结果一致 —— A3 的核心验收口径。"""
    def run() -> dict:
        mk = StubLandmarker(pattern="blink", fps=FPS)
        trk = BehaviorTracker(CFG)
        last: dict = {}
        for fid in range(int(30 * FPS)):        # 30 秒（= window_seconds）
            obs = mk.detect(None, fid)
            last = trk.update(fid, fid / FPS, obs)
        return last

    a, b = run(), run()
    assert a == b, "两次运行结果不一致：时间戳或随机源泄漏进了指标计算"
    assert a["blink_count"] > 0, "眨眼视频应当数出眨眼，0 次说明状态机没工作"
    assert a["long_close_count"] >= 1, "pattern=blink 在第 20 秒埋了一次长闭眼，应被识别"


def test_blink_state_is_from_contract_enum() -> None:
    from backend.contract import BLINK_STATES

    mk = StubLandmarker(pattern="blink", fps=FPS)
    trk = BehaviorTracker(CFG)
    seen = set()
    for fid in range(int(10 * FPS)):
        seen.add(trk.update(fid, fid / FPS, mk.detect(None, fid))["blink_state"])
    assert seen <= set(BLINK_STATES), seen


def test_yawn_requires_min_duration() -> None:
    """打哈欠靠"持续时长去抖"，短促张口不该计数（A5 验收）。"""
    mk = StubLandmarker(pattern="yawn", fps=FPS)
    trk = BehaviorTracker(CFG)
    last = {}
    for fid in range(int(10 * FPS)):  # 10 秒，yawn pattern 每 6 秒一次 1.5 秒张口
        last = trk.update(fid, fid / FPS, mk.detect(None, fid))
    assert last["yawn_count"] >= 1

    # 短促张口：把阈值抬到 10 秒，任何张口都不该被记为哈欠
    strict = dict(CFG)
    strict["yawn_min_duration_ms"] = 10_000
    trk2 = BehaviorTracker(strict)
    last2 = {}
    for fid in range(int(10 * FPS)):
        last2 = trk2.update(fid, fid / FPS, mk.detect(None, fid))
    assert last2["yawn_count"] == 0


def test_face_lost_yields_zero_ear() -> None:
    """人脸完全出框时，检测器返回"没人脸"，EAR 归零 —— 不能被当成"睁眼正常"。"""
    mk = StubLandmarker(pattern="turn", fps=FPS)
    fid = int(6.0 * FPS)                # t = 6.0s，pattern=turn 到此完全出框
    obs = mk.detect(None, fid)
    assert obs.visible == 0.0
    assert obs.eyes == {} and obs.mouth == {}

    trk = BehaviorTracker(CFG)
    beh = trk.update(fid, 6.0, obs)
    assert beh["ear_left"] == 0.0
    assert beh["mar"] == 0.0


def test_turn_pattern_triggers_adjust_posture_condition() -> None:
    """转头阶段必须满足 adjust_posture 的触发条件（可见率不足或姿态越界）。"""
    mk = StubLandmarker(pattern="turn", fps=FPS)
    obs = mk.detect(None, int(4.0 * FPS))    # t = 4.0s，partial 可见阶段
    assert obs.eyes != {}, "此阶段仍有脸，只是可见率低/姿态越界"
    assert (obs.visible < CFG["face_visible_min"]
            or abs(obs.pose["yaw"]) > CFG["pose_yaw_max_deg"])


# ---------------------------------------------------------------------------
# 规则融合
# ---------------------------------------------------------------------------

GOOD_Q = {"overall": 0.9, "light_score": 0.9, "motion_score": 0.05}
BAD_Q = {"overall": 0.4, "light_score": 0.25, "motion_score": 0.7}
GOOD_FACE = {"visible": 0.98, "pose": {"yaw": -2.1, "pitch": 1.5, "roll": 0.3}}
BAD_FACE = {"visible": 0.35, "pose": {"yaw": 38.0, "pitch": 1.0, "roll": 0.0}}
CALM = {"perclos": 0.05, "long_close_count": 0, "yawn_count": 0, "blink_rate_per_min": 18.0}
TIRED = {"perclos": 0.33, "long_close_count": 2, "yawn_count": 3, "blink_rate_per_min": 11.0}


@pytest.mark.parametrize(
    "behavior,quality,face,expected",
    [
        (CALM, GOOD_Q, GOOD_FACE, "normal"),
        (TIRED, GOOD_Q, GOOD_FACE, "fatigue_risk"),
        (CALM, BAD_Q, GOOD_FACE, "unreliable"),
        (CALM, GOOD_Q, BAD_FACE, "adjust_posture"),
        # 顺序即产品语义：质量不过时**不**报疲劳，先拒绝测量
        (TIRED, BAD_Q, GOOD_FACE, "unreliable"),
        (TIRED, GOOD_Q, BAD_FACE, "adjust_posture"),
    ],
)
def test_decision_priority(behavior, quality, face, expected) -> None:
    eng = DecisionEngine(CFG)
    r = eng.decide(frame_id=1, behavior=behavior, quality=quality, face=face)
    assert r["status"] == expected
    assert r["reason"], "每条判定都必须给出可解释理由"
    assert r["triggers"], "每条判定都必须能追溯到触发的指标"


def test_status_is_always_in_contract_enum() -> None:
    from backend.contract import STATUS_VALUES

    eng = DecisionEngine(CFG)
    for beh, q, f in ((CALM, GOOD_Q, GOOD_FACE), (TIRED, GOOD_Q, GOOD_FACE),
                      (CALM, BAD_Q, GOOD_FACE), (CALM, GOOD_Q, BAD_FACE)):
        assert eng.decide(frame_id=1, behavior=beh, quality=q, face=f)["status"] in STATUS_VALUES


def test_gate_vitals_refuses_low_quality() -> None:
    eng = DecisionEngine(CFG)
    good = {"hr_bpm": 72.0, "hr_conf": 0.8, "rr_per_min": 15.0, "rr_conf": 0.7}
    assert eng.gate_vitals(0.9, good)["hr_bpm"] == 72.0
    assert all(v is None for v in eng.gate_vitals(0.4, good).values())


# ---------------------------------------------------------------------------
# 端到端可复现性（跑两遍，产物必须逐字节相同）
# ---------------------------------------------------------------------------

def test_run_pipeline_is_byte_reproducible(workdir: Path) -> None:
    """《02》第一周 A 线验收："对同一段回放视频连续跑两次，输出的 JSON 字段完整、数值一致"。"""

    def once(tag: str) -> tuple[str, str]:
        j = workdir / f"last_{tag}.json"
        c = workdir / f"metrics_{tag}.csv"
        rc = run_pipeline_main([
            "--source", "synthetic", "--pattern", "blink", "--seconds", "6",
            "--stub", "--quiet",
            "--json", str(j), "--csv", str(c),
        ])
        assert rc == 0
        return j.read_text(encoding="utf-8"), c.read_text(encoding="utf-8")

    j1, c1 = once("a")
    j2, c2 = once("b")
    assert j1 == j2, "两次运行的末帧 JSON 不一致 → 有非确定性来源（时间戳/随机数）泄漏"
    assert c1 == c2, "两次运行的 CSV 不一致"

    frame = json.loads(j1)
    assert frame["status"] in ("normal", "fatigue_risk", "adjust_posture", "unreliable")
    assert frame["behavior"]["blink_count"] > 0


def test_run_pipeline_writes_contract_valid_csv(workdir: Path) -> None:
    import csv

    from backend.storage import CSV_COLUMNS

    c = workdir / "m.csv"
    rc = run_pipeline_main(["--source", "synthetic", "--seconds", "3", "--stub", "--quiet",
                            "--json", str(workdir / "l.json"), "--csv", str(c),
                            "--summary", str(workdir / "s.json")])
    assert rc == 0
    with c.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        assert reader.fieldnames == CSV_COLUMNS

    # ⚠️ 帧数必须从 config.yaml 的 fps_nominal 推导，**不要硬编码 30/45**：
    #    契约 §0 的帧率已经改过一次（30→45，v1.1），硬编码会让改帧率时基线无故变红，
    #    那样真正的回归反而被淹没。本用例只关心"秒数 → 帧数"这条换算是否成立。
    expected = round(3 * float(CFG["fps_nominal"]))      # 3 秒 × 契约帧率
    assert len(rows) == expected
    assert rows[0]["blink_state"] in ("OPEN", "CLOSING", "CLOSED", "OPENING")

    summary = json.loads((workdir / "s.json").read_text(encoding="utf-8"))
    assert summary["frames_processed"] == expected
    assert summary["landmark_source"] == "stub"
    assert summary["logical_duration_s"] == pytest.approx(3.0)


def test_make_landmarker_falls_back_without_mediapipe() -> None:
    """没有 mediapipe 也必须能开工（B/C 线同理：谁都不许卡住别人）。"""
    mk = make_landmarker(force_stub=True)
    assert isinstance(mk, StubLandmarker)
