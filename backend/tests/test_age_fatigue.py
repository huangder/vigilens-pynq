"""test_age_fatigue.py —— 年龄分层疲劳功能（年龄档案 / 收录 / 数据库 / 耦合判定）的回归测试。

覆盖什么（每条都对应一个**可被违反的承诺**，而不是"跑一遍不报错"）：

  1. `age_contract` 的 schema 铁律：锁定后不许再推断、图像推断不得自我确认、
     不可比时不许有数字、占位引擎必须自我标注、schema 外字段不许出现；
  2. `FaceSignature`：确定性、不可逆签名的形状与距离、退化输入必须被拒绝；
  3. `AgeProfileStore`：收录窗口到点自动判定、低质量帧不计样本、用户表态优先、
     一次确认后推断停用（且违规会被 schema 抓住）；
  4. `CoupledRiskModel`：门控不过必须"不可比 + 无数字"、年龄敏感度方向正确、
     每条证据都带得出处、同一输入必得同一结果；
  5. `FatigueDB`：逐帧字段齐全、会话汇总、**stub 会话不进人群统计**、
     签名以字节存（不是图片）、参照基线带出处入库；
  6. B 线 `/api/age`：用户决定优先于流水线观测、未知 action 返回 422。

纪律（AGENTS.md §1）：本文件不依赖摄像头、网络、模型权重，全部可离线复现；
不确定的推理不写成断言（例如不对年龄推断精度做任何断言 —— 它没有精度证据）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

import age_contract as ac           # noqa: E402
import age_infer as ai              # noqa: E402
import fatigue_coupling as fc       # noqa: E402
import fatigue_db as fdb           # noqa: E402
from config import load_config      # noqa: E402
from mock import mock_frame         # noqa: E402


# ---------------------------------------------------------------------------
# 固件
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg() -> dict:
    return load_config()


@pytest.fixture
def norms() -> fdb.ReferenceNorms:
    return fdb.load_reference()


@pytest.fixture
def face_image() -> np.ndarray:
    """一张确定性的"有纹理"假人脸图（不需要真人脸：签名只用到灰度统计）。"""
    rng = np.random.default_rng(20261001)
    img = rng.integers(40, 220, size=(480, 640, 3), dtype=np.uint8)
    return img


def _good_quality() -> dict:
    return {"overall": 0.92, "light_score": 0.90, "motion_score": 0.05}


def _good_face() -> dict:
    return {"visible": 0.98, "bbox": [200, 120, 180, 220],
            "pose": {"yaw": -2.0, "pitch": 1.5, "roll": 0.3}}


def _calm() -> dict:
    return {"perclos": 0.06, "long_close_count": 0, "yawn_count": 0, "blink_rate_per_min": 18.0}


def _tired() -> dict:
    return {"perclos": 0.34, "long_close_count": 2, "yawn_count": 3, "blink_rate_per_min": 6.0}


# ---------------------------------------------------------------------------
# 1) schema 铁律
# ---------------------------------------------------------------------------

def test_empty_sidecar_is_valid():
    assert ac.validate_age_state(ac.new_age_state(ts=1.0, frame_id=0)) == []


def test_band_for_age_boundaries():
    assert ac.band_for_age(12) == "child"
    assert ac.band_for_age(13) == "teen"
    assert ac.band_for_age(17) == "teen"
    assert ac.band_for_age(18) == "young_adult"
    assert ac.band_for_age(25) == "young_adult"
    assert ac.band_for_age(26) == "adult"
    assert ac.band_for_age(45) == "adult"
    assert ac.band_for_age(46) == "middle_aged"
    assert ac.band_for_age(59) == "middle_aged"
    assert ac.band_for_age(60) == "older_adult"
    # 落在所有区间之外**必须**返回 None（不许硬塞进某一组）
    assert ac.band_for_age(3) is None
    assert ac.band_for_age(200) is None


def test_locked_profile_must_not_call_inference():
    """产品承诺：一次正式确认年龄后不再采用年龄推测机制。"""
    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["profile"].update({"age_years": 31, "band": "adult", "source": "manual_input",
                         "confirmed": True, "confirmed_by": "user_manual",
                         "confirmed_at": 1.0, "locked": True,
                         "inference_calls_after_lock": 1})
    errs = ac.validate_age_state(s)
    assert any("inference_calls_after_lock" in e for e in errs)


def test_image_estimate_cannot_self_confirm():
    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["profile"].update({"age_years": 24, "band": "young_adult", "source": "image_estimate",
                         "confirmed": True, "confirmed_by": "user_manual",
                         "confirmed_at": 1.0, "locked": True})
    errs = ac.validate_age_state(s)
    assert any("user_accepted_estimate" in e for e in errs)


def test_incomparable_must_not_carry_numbers():
    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["comparison"].update({"comparable": False, "risk": 0.5, "risk_level": "unknown"})
    errs = ac.validate_age_state(s)
    assert any("comparable=False" in e for e in errs)

    s2 = ac.new_age_state(ts=1.0, frame_id=1)
    s2["comparison"].update({"comparable": True, "risk": None, "band_applied": "adult"})
    assert any("risk 不许为 null" in e for e in ac.validate_age_state(s2))


def test_placeholder_engine_must_self_label():
    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["estimate"].update({"engine": "heuristic", "placeholder": False,
                          "band": "adult", "age_years": 40.0})
    assert any("placeholder=True" in e for e in ac.validate_age_state(s))


def test_engine_none_must_not_carry_values():
    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["estimate"].update({"engine": "none", "band": "adult"})
    assert any("engine=none" in e for e in ac.validate_age_state(s))


def test_extra_top_level_field_is_rejected():
    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["debug"] = 1
    assert any("schema 外顶层字段" in e for e in ac.validate_age_state(s))


def test_heuristic_engine_marks_itself_placeholder(face_image):
    eng = ai.HeuristicAgeEngine()
    if not eng.available():
        pytest.skip("需要 cv2/numpy")
    est = eng.estimate(face_image, (200, 120, 180, 220))
    assert est is not None
    assert est.placeholder is True
    assert est.engine == "heuristic"
    # 置信度被硬压在诚实上限之下（不许看起来像一次可信推断）
    assert est.confidence is not None and est.confidence <= ai.PLACEHOLDER_CONFIDENCE_CAP


def test_cv_band_mapping_covers_all_eight():
    assert set(ac.CV_BAND_TO_AGE_BAND) == set(ac.CV_AGE_BANDS)
    assert all(v in ac.AGE_BANDS for v in ac.CV_BAND_TO_AGE_BAND.values())


# ---------------------------------------------------------------------------
# 2) 人脸签名（不是照片）
# ---------------------------------------------------------------------------

def test_signature_is_deterministic_and_sized(face_image):
    a = ai.FaceSignature.from_image(face_image, (200, 120, 180, 220), 16)
    b = ai.FaceSignature.from_image(face_image, (200, 120, 180, 220), 16)
    assert a is not None and a == b
    assert len(a) == 256          # 16x16 int8 —— 不是一张能被认出来的脸
    assert ai.FaceSignature.distance(a, b) == 0.0


def test_signature_distance_grows_for_different_faces(face_image):
    a = ai.FaceSignature.from_image(face_image, (200, 120, 180, 220), 16)
    rng = np.random.default_rng(7)
    other = rng.integers(0, 255, size=(480, 640, 3), dtype=np.uint8)
    b = ai.FaceSignature.from_image(other, (200, 120, 180, 220), 16)
    d = ai.FaceSignature.distance(a, b)
    assert d is not None and d > 0.0


def test_signature_rejects_degenerate_inputs(face_image):
    assert ai.FaceSignature.from_image(face_image, (10, 10, 2, 2), 16) is None   # 框太小
    flat = np.full((200, 200, 3), 128, dtype=np.uint8)                          # 纯色无区分度
    assert ai.FaceSignature.from_image(flat, (0, 0, 100, 100), 16) is None
    assert ai.FaceSignature.from_image("not an image", (0, 0, 10, 10), 16) is None


def test_subject_id_is_stable_and_anonymous(face_image):
    sig = ai.FaceSignature.from_image(face_image, (200, 120, 180, 220), 16)
    sid = ai.FaceSignature.subject_id(sig)
    assert sid.startswith("sub-") and len(sid) == 16      # sub- + 12 hex
    assert ai.FaceSignature.subject_id(sig) == sid         # 同一张脸必得同一 id


# ---------------------------------------------------------------------------
# 3) 收录窗口与"确认后停用推断"
# ---------------------------------------------------------------------------

def _store(cfg, **over) -> ai.AgeProfileStore:
    c = dict(cfg)
    c.update(over)
    return ai.AgeProfileStore(c)


def test_enrollment_window_auto_decides(cfg, face_image):
    st = _store(cfg, age_enroll_window_s=20.0, age_enroll_min_samples=12)
    for i in range(25):
        st.observe(frame_id=i, ts=float(i), image=face_image, bbox=(200, 120, 180, 220),
                   quality_overall=0.9, face_visible=1.0)
    assert st.enrollment["state"] == "enrolled"
    assert st.enrollment["samples"] >= 12
    assert st.profile["subject"] and st.profile["subject"].startswith("sub-")
    kinds = [e["kind"] for e in st.events]
    assert "enroll_observe_start" in kinds and "enroll_decided" in kinds


def test_low_quality_frames_do_not_count(cfg, face_image):
    st = _store(cfg, age_enroll_min_quality=0.8)
    for i in range(30):
        st.observe(frame_id=i, ts=float(i), image=face_image, bbox=(200, 120, 180, 220),
                   quality_overall=0.4, face_visible=1.0)
    assert st.enrollment["samples"] == 0
    assert st.enrollment["state"] != "enrolled"


def test_user_can_refuse_enrollment(cfg, face_image):
    st = _store(cfg)
    st.observe(frame_id=0, ts=0.0, image=face_image, bbox=(200, 120, 180, 220),
               quality_overall=0.9, face_visible=1.0)
    st.enroll_decision(False, ts=1.0)
    assert st.enrollment["state"] == "rejected"
    assert st.profile["subject"] is None


def test_confirm_stops_inference_and_tripwire_fires(cfg, face_image):
    st = _store(cfg)
    assert st.inference_allowed() is True
    st.set_manual(31, ts=1.0)
    assert st.locked() is True
    assert st.inference_allowed() is False
    # 正常调用方**先问再调**，于是不会踩到绊线：
    if st.inference_allowed():
        st.maybe_infer(frame_id=1, logical_t=0.0, image=face_image,
                       bbox=(200, 120, 180, 220), ts=2.0)
    assert st.profile["inference_calls_after_lock"] == 0
    assert ac.validate_age_state(st.sidecar(ts=2.0, frame_id=1)) == []
    # 而"没问就调"的调用方会被留痕，并被 schema 当场抓住：
    st.maybe_infer(frame_id=2, logical_t=1.0, image=face_image,
                   bbox=(200, 120, 180, 220), ts=3.0)
    assert st.profile["inference_calls_after_lock"] == 1
    assert any("inference_calls_after_lock" in e
               for e in ac.validate_age_state(st.sidecar(ts=3.0, frame_id=2)))


def test_reset_re_enables_inference(cfg):
    st = _store(cfg)
    st.set_manual(31, ts=1.0)
    assert st.inference_allowed() is False
    st.reset(ts=2.0)
    assert st.profile["confirmed"] is False and st.profile["locked"] is False
    assert st.inference_allowed() is (st.engine is not None)
    assert "profile_reset" in [e["kind"] for e in st.events]


def test_enrollment_stops_once_locked(cfg, face_image):
    st = _store(cfg)
    st.set_manual(70, ts=1.0)
    st.observe(frame_id=0, ts=2.0, image=face_image, bbox=(200, 120, 180, 220),
               quality_overall=0.9, face_visible=1.0)
    assert st.enrollment["state"] == "locked"
    assert st.profile["band"] == "older_adult"


def test_apply_remote_state_never_overwrites_user_decision(cfg, face_image):
    st = _store(cfg)
    st.set_manual(31, ts=1.0)
    remote = ac.new_age_state(ts=2.0, frame_id=5)
    remote["profile"].update({"age_years": 70, "band": "older_adult", "confirmed": True,
                              "confirmed_by": "user_manual", "confirmed_at": 2.0,
                              "locked": True, "source": "manual_input"})
    remote["enrollment"].update({"state": "observing", "reason": "远端说在观察",
                                 "samples": 9, "observed_s": 9.0, "progress": 0.45})
    remote["estimate"].update({"engine": "opencv_caffe", "band": "young_adult",
                               "age_years": 24.0, "confidence": 0.4})
    st.apply_remote_state(remote)
    # 决定不被覆盖：
    assert st.profile["age_years"] == 31 and st.profile["band"] == "adult"
    # 观测被吸收：
    assert st.estimate is not None and st.estimate.band == "young_adult"


# ---------------------------------------------------------------------------
# 4) 耦合判定模型
# ---------------------------------------------------------------------------

def test_refuses_when_measurement_unusable(cfg, norms):
    m = fc.CoupledRiskModel(cfg, norms)
    r = m.compare(behavior=_tired(), quality=_good_quality(), face=_good_face(),
                  band="adult", status="adjust_posture", frame_id=0, ts=0.0)
    assert r["comparable"] is False
    assert r["risk"] is None and r["risk_level"] == "unknown"
    assert "adjust_posture" in r["reason"]

    m2 = fc.CoupledRiskModel(cfg, norms)
    bad_q = {"overall": 0.3, "light_score": 0.2, "motion_score": 0.8}
    r2 = m2.compare(behavior=_tired(), quality=bad_q, face=_good_face(),
                    band="adult", status="normal", frame_id=0, ts=0.0)
    assert r2["comparable"] is False and r2["risk"] is None


def test_tired_scores_higher_than_calm(cfg, norms):
    def run(beh):
        m = fc.CoupledRiskModel(cfg, norms)
        out = None
        for i in range(40):
            out = m.compare(behavior=beh, quality=_good_quality(), face=_good_face(),
                            band="adult", status="normal", frame_id=i, ts=i / 30.0)
        return out
    calm, tired = run(_calm()), run(_tired())
    assert calm["comparable"] and tired["comparable"]
    assert tired["risk"] > calm["risk"]
    assert calm["risk_level"] == "low"
    assert tired["risk_level"] == "high"


def test_older_adults_eye_evidence_is_downweighted(cfg, norms):
    """Cai 2021 的方向：老年组的眼动证据不敏感 → 同样输入下风险更低，
    且由非眼部证据（哈欠/姿态）相对补位。这条方向是**有文献依据的**。"""
    def run(band):
        m = fc.CoupledRiskModel(cfg, norms)
        eye_only = {"perclos": 0.34, "long_close_count": 2, "yawn_count": 0,
                    "blink_rate_per_min": 6.0}
        out = None
        for i in range(40):
            out = m.compare(behavior=eye_only, quality=_good_quality(), face=_good_face(),
                            band=band, status="normal", frame_id=i, ts=i / 30.0)
        return out
    young, older = run("young_adult"), run("older_adult")
    assert older["risk"] < young["risk"]
    sens = {c["metric"]: c["sensitivity"] for c in older["contributions"]}
    assert sens["perclos"] < sens["yawn_count"]      # 眼部被压、非眼部不被压


def test_evidence_carries_provenance(cfg, norms):
    m = fc.CoupledRiskModel(cfg, norms)
    r = None
    for i in range(10):
        r = m.compare(behavior=_calm(), quality=_good_quality(), face=_good_face(),
                      band="adult", status="normal", frame_id=i, ts=i / 30.0)
    by_metric = {c["metric"]: c for c in r["contributions"]}
    # 眨眼率有真实可核实的全局基线（Doughty 2001），标签里必须写明
    assert "blink_rate_per_min" in by_metric
    assert "blink_rate_per_min" in by_metric["blink_rate_per_min"]["baseline"]
    # PERCLOS 没有可用基线 → 必须如实标注来自 config 的占位阈值
    assert "perclos" in by_metric
    assert "占位值" in by_metric["perclos"]["baseline"]
    # 参照组与实际应用组都要写出来（未确认年龄时是 global 兜底）
    assert r["band_applied"] in ac.AGE_BANDS + (ac.GLOBAL_BAND,)
    assert r["reference_version"]


def test_unknown_band_falls_back_to_global_baseline(cfg, norms):
    m = fc.CoupledRiskModel(cfg, norms)
    r = m.compare(behavior=_calm(), quality=_good_quality(), face=_good_face(),
                  band=ac.GLOBAL_BAND, status="normal", frame_id=0, ts=0.0)
    assert r["comparable"] is True and r["band_applied"] == ac.GLOBAL_BAND


def test_coupling_is_deterministic(cfg, norms):
    def run():
        m = fc.CoupledRiskModel(cfg, norms)
        out = []
        for i in range(20):
            out.append(m.compare(behavior=_tired(), quality=_good_quality(),
                                 face=_good_face(), band="adult", status="normal",
                                 frame_id=i, ts=i / 30.0)["risk"])
        return out
    assert run() == run()


def test_interaction_requires_both_conditions(cfg, norms):
    """耦合项必须真的"耦合"：只有 PERCLOS 高、或只有长闭眼时不该触发。"""
    def run(beh):
        m = fc.CoupledRiskModel(cfg, norms)
        out = None
        for i in range(40):
            out = m.compare(behavior=beh, quality=_good_quality(), face=_good_face(),
                            band="adult", status="normal", frame_id=i, ts=i / 30.0)
        return out
    only_perclos = run({"perclos": 0.34, "long_close_count": 0, "yawn_count": 0,
                        "blink_rate_per_min": 18.0})
    both = run({"perclos": 0.34, "long_close_count": 2, "yawn_count": 0,
                "blink_rate_per_min": 18.0})
    assert [i["rule"] for i in only_perclos["interactions"]] == []
    assert "perclos_x_longclose" in [i["rule"] for i in both["interactions"]]


def test_all_risk_levels_are_declared(cfg, norms):
    m = fc.CoupledRiskModel(cfg, norms)
    r = m.compare(behavior=_calm(), quality=_good_quality(), face=_good_face(),
                  band="adult", status=None, frame_id=0, ts=0.0)
    assert r["risk_level"] in ac.RISK_LEVELS


# ---------------------------------------------------------------------------
# 5) 数据库
# ---------------------------------------------------------------------------

def test_frames_cover_all_measured_fields(cfg):
    """用户要求"包含所有测量出来的相关疲劳数据" —— 契约帧的每个测量字段都要有列。"""
    with fdb.FatigueDB(":memory:", cfg=cfg) as db:
        db.start_session(session_id="s1", started_at=0.0)
        db.add_frame("s1", mock_frame(0))
        row = next(db.iter_frames("s1"))
    for col in ("ear_left", "ear_right", "mar", "perclos", "blink_rate_per_min",
                "long_close_count", "yawn_count", "blink_state", "yaw", "pitch", "roll",
                "face_visible", "hr_bpm", "rr_per_min", "light_score", "motion_score",
                "quality_overall", "status"):
        assert col in row


def test_session_summary_aggregates(cfg):
    with fdb.FatigueDB(":memory:", cfg=cfg) as db:
        db.start_session(session_id="s2", started_at=0.0, age_band="adult", provenance="live")
        for i in range(5):
            db.add_frame("s2", mock_frame(i))
        db.finish_session("s2", ended_at=5.0)
        s = db.summary()["recent"][0]
    assert s["frames"] == 5
    assert s["perclos_mean"] is not None and s["quality_mean"] is not None


@pytest.fixture
def workdir():
    """一个**普通临时目录**（刻意不用 pytest 的 `tmp_path`）。

    原因（这是环境适配，不是风格偏好）：`tmp_path` 依赖 `tmpdir` 插件的
    "编号目录 + 符号链接锁"机制，在受限沙箱/受限 ACL 下会在 **setup** 阶段就
    `PermissionError: [WinError 5]`，而那不是代码问题。仓库 `AGENTS.md` §6.4/§6.1
    也记着同一类现象（"A 线段 pytest 在受限沙箱因 tempfile.mkstemp 卡顿"）。
    自己 `mkdir` 一个唯一目录 + 用完删掉，在受限与正常环境下都成立。
    """
    import shutil
    import tempfile
    import uuid

    d = Path(tempfile.gettempdir()) / f"vigilens_test_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_stub_sessions_stay_out_of_population_stats(cfg, workdir):
    """铁律：占位关键点的会话绝不能被当成人群数据。"""
    jsonl = workdir / "run.jsonl"
    jsonl.write_text("\n".join(json.dumps(mock_frame(i), ensure_ascii=False)
                              for i in range(3)), encoding="utf-8")
    summary = workdir / "summary.json"
    summary.write_text(json.dumps({"landmark_source": "stub", "logical_fps": 30,
                                   "frame_source": "synthetic"}), encoding="utf-8")
    with fdb.FatigueDB(workdir / "db.sqlite3", cfg=cfg) as db:
        res = fdb.import_session(db, jsonl, summary_path=summary, age_band="adult")
        assert res["provenance"] == "stub"       # 由 landmark_source 判定，不靠调用方自觉
        assert db.summary()["by_band"] == {}                     # 默认不计入
        assert db.summary(include_stub=True)["by_band"] == {"adult": 1}   # 显式要看才看


def test_live_import_marks_provenance(cfg, workdir):
    jsonl = workdir / "run.jsonl"
    jsonl.write_text("\n".join(json.dumps(mock_frame(i), ensure_ascii=False)
                              for i in range(4)), encoding="utf-8")
    summary = workdir / "summary.json"
    summary.write_text(json.dumps({"landmark_source": "mediapipe", "logical_fps": 30}),
                       encoding="utf-8")
    with fdb.FatigueDB(workdir / "db.sqlite3", cfg=cfg) as db:
        res = fdb.import_session(db, jsonl, summary_path=summary, age_band="adult",
                                 age_years=31, age_source="manual_input",
                                 confirmed_by="user_manual")
        assert res["provenance"] == "live" and res["frames"] == 4
        assert db.summary()["by_band"] == {"adult": 1}


def test_reference_norms_loaded_with_sources(cfg, norms):
    if norms.version == "missing":
        pytest.skip("参照基线文件不存在")
    with fdb.FatigueDB(":memory:", cfg=cfg) as db:
        n = db.load_reference_into_db(norms)
        assert n > 0
        rows = db.conn.execute("SELECT band, metric, confidence, source FROM reference_norms"
                               ).fetchall()
    # **每条基线都必须带核实状态**；这一条是"不许编数字"纪律的机械检查。
    assert all(r["confidence"] for r in rows)
    # 检查的是**解析后真正会被模型使用的**那条基线（它可能来自 global 兜底）——
    # 这才是耦合模型会打印到 reason 里的对象。注意 teen 组自己没填出处（文献缺），
    # 但它会兜底到 global 的 Doughty 基线，所以"解析后必须有出处"才是不变量。
    resolved = [b for b in (norms.baseline(r["metric"], r["band"]) for r in rows) if b is not None]
    assert resolved, "至少要有一条能用的数值基线（眨眼率 reading 场景）"
    assert all(b.source for b in resolved), "解析后的基线必须带出处（否则 reason 里无法交代）"
    assert all(b.usable for b in resolved)
    # 而"没填数字"的那些条目必须写明为什么（否则后人会以为只是漏了）
    for band, metrics in norms.bands.items():
        for metric, b in metrics.items():
            if not b.usable:
                assert b.notes, f"{band}.{metric} 没有可用数值，就必须写清楚缺口的说明"


def test_subject_signature_stored_as_bytes_not_image(cfg, face_image):
    sig = ai.FaceSignature.from_image(face_image, (200, 120, 180, 220), 16)
    with fdb.FatigueDB(":memory:", cfg=cfg) as db:
        db.upsert_subject({"subject": "sub-x", "signature": sig, "at": 1.0})
        row = db.conn.execute("SELECT signature, signature_bytes FROM subjects").fetchone()
    assert bytes(row["signature"]) == sig
    # 256 字节 ≠ 图像：一张 640x480x3 的原图是 921600 字节（契约 §0）
    assert row["signature_bytes"] == 256


def test_age_events_are_recorded(cfg):
    with fdb.FatigueDB(":memory:", cfg=cfg) as db:
        st = ai.AgeProfileStore(cfg, on_event=lambda ev: db.log_age_event(ev, session_id="s3"))
        st.set_manual(31, ts=1.0)
        st.reset(ts=2.0)
        kinds = [r["kind"] for r in db.conn.execute("SELECT kind FROM age_events")]
    assert "manual_confirmed" in kinds and "profile_reset" in kinds


def test_update_session_age_after_the_fact(cfg):
    with fdb.FatigueDB(":memory:", cfg=cfg) as db:
        db.start_session(session_id="s4", started_at=0.0)
        db.update_session_age("s4", age_years=31, age_band="adult",
                              age_source="manual_input", confirmed_by="user_manual")
        row = db.conn.execute("SELECT age_years, age_band, confirmed_by FROM sessions"
                              " WHERE session_id='s4'").fetchone()
    assert (row["age_years"], row["age_band"], row["confirmed_by"]) == (31, "adult", "user_manual")


# ---------------------------------------------------------------------------
# 6) B 线 /api/age
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(cfg):
    fastapi = pytest.importorskip("fastapi")
    from starlette.testclient import TestClient
    import api as api_mod
    api_mod._AGE_STORE = None       # 每个用例都用干净的档案（模块级单例必须重置）
    api_mod._latest_age = None
    app = api_mod.create_app(mock=False, mount_frontend=False)
    return TestClient(app)


def test_api_age_get_returns_valid_sidecar(api_client):
    body = api_client.get("/api/age").json()
    assert body["ok"] is True
    assert ac.validate_age_state(body["age"]) == []


def test_api_manual_confirm_locks_and_ignores_remote(api_client):
    r = api_client.post("/api/age", json={"action": "set_manual", "age_years": 31})
    assert r.status_code == 200
    age = r.json()["age"]
    assert age["profile"]["confirmed"] is True
    assert age["profile"]["locked"] is True
    assert age["profile"]["band"] == "adult"
    # 再推一份"远端观测"（年龄 70）→ 用户已确认的 31 岁**不许**被改掉
    remote = ac.new_age_state(ts=1.0, frame_id=1)
    remote["profile"].update({"age_years": 70, "band": "older_adult", "confirmed": True,
                              "confirmed_by": "user_manual", "confirmed_at": 1.0,
                              "locked": True, "source": "manual_input"})
    assert api_client.post("/api/age", json=remote).status_code == 200
    after = api_client.get("/api/age").json()["age"]
    assert after["profile"]["age_years"] == 31
    assert after["profile"]["band"] == "adult"


def test_api_rejects_unknown_action_and_bad_age(api_client):
    assert api_client.post("/api/age", json={"action": "sleep_now"}).status_code == 422
    assert api_client.post("/api/age", json={"action": "set_manual",
                                            "age_years": 500}).status_code == 422
    assert api_client.post("/api/age", json={"action": "accept_estimate"}).status_code == 422


def test_api_rejects_invalid_remote_sidecar(api_client):
    bad = ac.new_age_state(ts=1.0, frame_id=1)
    bad["profile"].update({"confirmed": True, "locked": True, "age_years": 30,
                           "source": "manual_input", "confirmed_by": "user_manual",
                           "confirmed_at": 1.0, "inference_calls_after_lock": 3})
    r = api_client.post("/api/age", json=bad)
    assert r.status_code == 422 and r.json()["ok"] is False


def test_api_age_events_and_db_summary(api_client):
    api_client.post("/api/age", json={"action": "set_manual", "age_years": 25})
    evs = api_client.get("/api/age/events").json()
    assert evs["ok"] and any(e["kind"] == "manual_confirmed" for e in evs["events"])
    db = api_client.get("/api/fatigue_db/summary").json()
    assert db["ok"] is True
    assert "db" in db and "exists" in db["db"]


def test_api_contract_frame_is_untouched_by_age_endpoints(api_client):
    """年龄旁路再怎么用，冻结契约帧必须仍然**恰好 9 个字段**。"""
    from contract import _TOP_KEYS, validate_frame
    api_client.post("/api/age", json={"action": "set_manual", "age_years": 31})
    frame = api_client.get("/api/status").json()["frame"]
    assert set(frame) == set(_TOP_KEYS)
    assert validate_frame(frame) == []
