"""契约一致性测试（《04》第 3 节：接口契约是"唯一耦合点"）。

这批测试的作用不是"测代码写得对不对"，而是**在三人各自跑偏之前把契约盯住**：
  - A 线真实输出、B 线 Mock 都必须过同一套校验；
  - docs/02 里那份手写的示例 JSON 必须仍然合法 —— 一旦有人偷偷改字段，这里立刻红。
"""

from __future__ import annotations

import copy
import json

import pytest

from backend.contract import (
    BLINK_STATES,
    CONTRACT_VERSION,
    STATUS_VALUES,
    VITAL_KEYS,
    new_frame,
    validate_frame,
)
from backend.mock import DEMO_SEQUENCE, mock_frame, mock_stream

# 《02_三人分工与三线并行开发计划》4.1 节里手写的示例帧（逐字抄录，不许改）
DOC_EXAMPLE = {
    "ts": 1710000000.123,
    "frame_id": 12345,
    "face": {
        "visible": 0.98,
        "bbox": [320, 180, 180, 220],
        "pose": {"yaw": -2.1, "pitch": 1.5, "roll": 0.3},
    },
    "behavior": {
        "ear_left": 0.28, "ear_right": 0.29, "blink_state": "OPEN", "blink_count": 14,
        "blink_rate_per_min": 18.2, "perclos": 0.06, "long_close_count": 0,
        "mar": 0.12, "yawn_count": 1,
    },
    "vital": {"hr_bpm": None, "hr_conf": None, "rr_per_min": None, "rr_conf": None},
    "quality": {"light_score": 0.82, "motion_score": 0.05, "overall": 0.86},
    "status": "normal",
    "advice": "状态正常",
    "reason": "各项指标均在正常范围，信号质量良好",
}


def test_contract_version_is_declared() -> None:
    """契约版本必须显式声明，便于和 docs/interface.md 对账。"""
    assert CONTRACT_VERSION.startswith("v")


def test_doc_example_still_valid() -> None:
    """docs/02 4.1 的示例帧必须合法 —— 契约模块与冻结文档不许打架。"""
    assert validate_frame(DOC_EXAMPLE) == []


def test_doc_example_json_roundtrip() -> None:
    """示例帧能被序列化/反序列化后仍然合法（WebSocket 传的就是这个）。"""
    assert validate_frame(json.loads(json.dumps(DOC_EXAMPLE))) == []


def test_mock_covers_all_six_statuses() -> None:
    """B 线六态演示的前提：Mock 必须能产出全部 6 种 status。"""
    produced = {mock_frame(i)["status"] for i in range(len(DEMO_SEQUENCE))}
    assert produced == set(STATUS_VALUES)
    assert len(STATUS_VALUES) == 6


@pytest.mark.parametrize("status", STATUS_VALUES)
def test_mock_valid_for_each_status(status: str) -> None:
    for fid in range(5):
        frame = mock_frame(fid, status=status)
        assert validate_frame(frame) == [], f"{status} 第 {fid} 帧不合契约"


def test_mock_stream_is_contract_valid() -> None:
    for frame in mock_stream(realtime=False, limit=40):
        assert validate_frame(frame) == []


def test_mock_stream_is_reproducible() -> None:
    """同 seed 必得同序列（演示可复现、pytest 可断言）。

    用 logical_ts=True 把时间戳也变成确定值 —— 默认的墙上时钟模式
    对"实时演示"是对的，但会让逐字节比对永远失败。
    """
    a = [json.dumps(f, ensure_ascii=False)
         for f in mock_stream(realtime=False, logical_ts=True, limit=12, seed=7)]
    b = [json.dumps(f, ensure_ascii=False)
         for f in mock_stream(realtime=False, logical_ts=True, limit=12, seed=7)]
    assert a == b


def test_mock_stream_uses_wall_clock_by_default() -> None:
    """默认（实时演示）模式的时间戳必须是墙上时钟，否则前端趋势曲线的时间轴是假的。"""
    import time as _t

    before = _t.time()
    frames = list(mock_stream(realtime=False, limit=3))
    after = _t.time()
    # ts 会 round 到毫秒，所以留 1 ms 容差
    assert all(before - 0.001 <= f["ts"] <= after + 0.001 for f in frames)


def test_mock_frame_id_is_monotonic() -> None:
    ids = [f["frame_id"] for f in mock_stream(realtime=False, limit=20)]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_vital_is_null_when_quality_low() -> None:
    """产品承诺：质量不够时**不允许**输出心率/呼吸（否则退化成黑盒乱报）。"""
    for status in ("unreliable", "disconnected", "adjust_posture"):
        for fid in range(4):
            frame = mock_frame(fid, status=status)
            assert all(frame["vital"][k] is None for k in VITAL_KEYS), \
                f"{status} 不该有 vital 数值：{frame['vital']}"


def test_vital_present_when_quality_good() -> None:
    frame = mock_frame(0, status="normal")
    assert frame["quality"]["overall"] >= 0.75
    assert frame["vital"]["hr_bpm"] is not None
    assert 0.0 <= frame["vital"]["hr_conf"] <= 1.0


def test_status_and_behavior_are_self_consistent() -> None:
    """状态与数字必须自洽，否则演示时"写着疲劳、数字说正常"，一眼被看穿。"""
    tired = mock_frame(1, status="fatigue_risk")
    calm = mock_frame(0, status="normal")
    assert tired["behavior"]["perclos"] > calm["behavior"]["perclos"]
    assert tired["behavior"]["blink_rate_per_min"] < calm["behavior"]["blink_rate_per_min"]
    assert tired["behavior"]["yawn_count"] >= 2

    blind = mock_frame(2, status="adjust_posture")
    assert blind["face"]["visible"] < 0.7 or abs(blind["face"]["pose"]["yaw"]) > 30


# ---------------------------------------------------------------------------
# 校验器必须能抓到坏帧（否则它只是个装饰）
# ---------------------------------------------------------------------------

def _broken(**changes):
    f = copy.deepcopy(DOC_EXAMPLE)
    f.update(changes)
    return f


@pytest.mark.parametrize(
    "frame, keyword",
    [
        (_broken(status="tired"), "status"),
        (_broken(frame_id="12345"), "frame_id"),
        (_broken(advice=""), "advice"),
        (_broken(reason="   "), "reason"),
    ],
)
def test_validator_rejects_bad_top_level(frame: dict, keyword: str) -> None:
    errs = validate_frame(frame)
    assert errs and any(keyword in e for e in errs), errs


def test_validator_rejects_unknown_status() -> None:
    errs = validate_frame(_broken(status="sleeping"))
    assert errs and any("status" in e for e in errs)


def test_validator_rejects_extra_top_level_key() -> None:
    """契约不改就不许加字段（《02》铁律 1）。"""
    f = copy.deepcopy(DOC_EXAMPLE)
    f["debug"] = {"foo": 1}
    errs = validate_frame(f)
    assert errs and any("契约外" in e for e in errs), errs


def test_validator_rejects_missing_key() -> None:
    f = copy.deepcopy(DOC_EXAMPLE)
    del f["quality"]
    errs = validate_frame(f)
    assert errs and any("quality" in e for e in errs)


def test_validator_rejects_bad_bbox() -> None:
    f = copy.deepcopy(DOC_EXAMPLE)
    f["face"]["bbox"] = [320, 180, 180]          # 少一个
    assert validate_frame(f)
    f["face"]["bbox"] = ["320", 180, 180, 220]   # 字符串
    assert validate_frame(f)


def test_validator_rejects_out_of_range_scores() -> None:
    f = copy.deepcopy(DOC_EXAMPLE)
    f["face"]["visible"] = 1.4
    assert validate_frame(f)
    f = copy.deepcopy(DOC_EXAMPLE)
    f["quality"]["overall"] = -0.1
    assert validate_frame(f)
    f = copy.deepcopy(DOC_EXAMPLE)
    f["vital"]["hr_conf"] = 3.0
    assert validate_frame(f)


def test_validator_rejects_bad_blink_state() -> None:
    f = copy.deepcopy(DOC_EXAMPLE)
    f["behavior"]["blink_state"] = "SLEEPING"
    errs = validate_frame(f)
    assert errs and any("blink_state" in e for e in errs)
    assert "OPEN" in BLINK_STATES and len(BLINK_STATES) == 4


def test_validator_rejects_vital_missing_key() -> None:
    f = copy.deepcopy(DOC_EXAMPLE)
    del f["vital"]["hr_conf"]
    errs = validate_frame(f)
    assert errs and any("vital" in e for e in errs)


def test_new_frame_rejects_unknown_vital_field() -> None:
    with pytest.raises(ValueError):
        new_frame(ts=0.0, frame_id=0, face=DOC_EXAMPLE["face"], behavior=DOC_EXAMPLE["behavior"],
                  quality=DOC_EXAMPLE["quality"], status="normal", advice="a", reason="b",
                  vital={"spo2": 98})   # 血氧 = 契约明确排除的指标（docs/00 第 1.3 节）
