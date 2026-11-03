"""Filter behavior and cross-language parity on controlled estimates, not accuracy."""
import ctypes as ct
import math
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.config import load_config
from backend.hr_filter import HrFilterConfig, HrStabilizer

ROOT = Path(__file__).resolve().parents[2]


def configured(strict=False):
    cfg=load_config()
    if strict: cfg={**cfg,"hr_stabilizer_startup_samples":3}
    return HrFilterConfig.from_config(cfg)


def test_three_estimates_deduplicate_spikes_and_follow_real_step():
    f = HrStabilizer(HrFilterConfig.from_config({**load_config(),"hr_stabilizer_alpha":.35,"hr_stabilizer_startup_samples":3}))
    assert f.update(72, .8, 0) == (None, None)
    for t in (.01, .2, .5):
        assert f.update(72, .8, t) == (None, None)
    assert len(f.values) == 1
    assert f.update(74, .9, 1) == (None, None)
    assert f.update(73, .7, 2) == (73, .7)
    assert f.update(160, .95, 3) == (None, None)
    assert f.reason == "unstable"
    assert f.update(90, .8, 4) == (None, None)
    assert f.update(90, .8, 5) == (None, None)
    assert f.update(90, .8, 6) == (90, .8)
    # EMA reduces small steps but eventually follows the changed current data.
    outputs = [f.update(94, .8, t)[0] for t in range(7, 17)]
    assert outputs[1] > 90 and outputs[-1] > 93.5


def test_rejection_gap_and_time_rollback_never_reuse_old_value():
    f = HrStabilizer(configured(strict=True))
    for t in (0, 1, 2): f.update(72, .8, t)
    assert f.value == 72
    assert f.update(None, None, 2.1) == (None, None)
    assert f.update(72, .8, 3) == (None, None)
    assert f.update(72, .8, 4) == (None, None)
    assert f.update(72, .8, 5) == (72, .8)
    assert f.update(72, .8, 7) == (None, None)
    assert len(f.values) == 1
    assert f.update(72, .8, 6) == (None, None)
    assert f.reason == "invalid_time"


@pytest.mark.parametrize("key,value", [("window", 2), ("window", 11), ("window", 3.5),
    ("alpha", 0), ("alpha", math.nan), ("interval_s", 0), ("max_gap_s", .2), ("max_spread_bpm", -1)])
def test_invalid_config_rejected(key, value):
    cfg = {**load_config(), "hr_stabilizer_" + key: value}
    with pytest.raises(ValueError): HrFilterConfig.from_config(cfg)


def test_disabled_legacy_config_preserves_raw():
    f = HrStabilizer(HrFilterConfig.from_config({}))
    assert f.update(73, .7, 1) == (73, .7)


@pytest.mark.parametrize('enabled', [False, True])
def test_report_floor_discards_low_candidate_without_choosing_another_peak(enabled):
    f = HrStabilizer(HrFilterConfig.from_config({**load_config(), 'hr_stabilizer_enabled': enabled}))
    assert f.update(59.9, .99, 1) == (None, None)
    assert f.reason == 'below_report_min' and f.ema is None
    value, conf = f.update(60, .8, 2)
    assert value == 60 and conf == .8


@pytest.mark.parametrize('minimum', [-1, math.nan, math.inf])
def test_invalid_report_floor_is_refused(minimum):
    with pytest.raises(ValueError, match='report minimum'):
        HrFilterConfig.from_config({**load_config(), 'hr_report_min_bpm': minimum})


def test_export_uses_same_config(tmp_path):
    from board.autonomy.export_vital_config import export
    report = export(ROOT / "config.yaml", tmp_path / "generated.h")
    assert report["hr_filter"] == configured().__dict__


def test_progressive_startup_and_confidence_weighted_ema():
    f=HrStabilizer(HrFilterConfig.from_config({**load_config(),"hr_stabilizer_alpha":.35}))
    assert f.update(72,.8,0)==(72,.8)
    assert f.update(74,.8,1)==(72.3,.8)
    assert f.update(None,None,1.1)==(None,None)
    assert f.update(80,.8,2)==(80,.8)


def test_production_smoothing_uses_lower_weight_without_synthesizing_missing_values():
    f=HrStabilizer(configured())
    assert f.cfg.alpha == .20
    assert f.update(72,.8,0)==(72,.8)
    assert f.update(74,.8,1)==(72.2,.8)
    assert f.update(None,None,1.1)==(None,None)


def test_pipeline_loss_clears_filter_before_reacquiring():
    import numpy as np
    from backend.run_pipeline import update_rppg
    from backend.decision import DecisionEngine
    from backend.tests.test_decision_quality_gate import observation, invalid_observation, GOOD_Q
    class Raw:
        fps=30;last_sample_ts=None;max_gap_s=.1
        good=True
        def push(self,*args,**kwargs): self.good=True
        def invalidate(self,*args,**kwargs): self.good=False
        def estimate(self):return {"hr_bpm":72 if self.good else None,"hr_conf":.8 if self.good else None,"rr_per_min":None,"rr_conf":None}
    raw=Raw();f=HrStabilizer(configured(strict=True));engine=DecisionEngine(load_config())
    image=np.full((480,640,3),128,dtype=np.uint8)
    for fid in range(3):
        result=update_rppg(raw,engine,obs=observation(),quality=GOOD_Q,image=image,frame_id=fid,ts=float(fid),fallback=(640,480),stabilizer=f)
    assert result["hr_bpm"]==72
    result=update_rppg(raw,engine,obs=invalid_observation("absent"),quality=GOOD_Q,image=image,frame_id=3,ts=3.,fallback=(640,480),stabilizer=f)
    assert result["hr_bpm"] is None and len(f.values)==0
    result=update_rppg(raw,engine,obs=observation(),quality=GOOD_Q,image=image,frame_id=4,ts=4.,fallback=(640,480),stabilizer=f)
    assert result["hr_bpm"] is None


def test_soft_pause_preserves_evidence_but_requires_fresh_admitted_estimate():
    f = HrStabilizer(configured(strict=True))
    for ts, bpm in ((0, 72), (1, 74), (2, 73)):
        f.update(bpm, .8, ts)
    evidence = list(f.values)
    previous_ema = f.ema
    assert f.pause(2.1)
    assert f.value is None and f.conf is None
    assert list(f.values) == evidence and f.ema == previous_ema
    # Rapid calls after recovery supply no new independent smoothing vote.
    assert f.update(79, .8, 2.2) == (None, None)
    assert f.reason == "paused"
    value, conf = f.update(79, .8, 3)
    assert value == pytest.approx(73.2) and conf == .8
    assert 73 < value < 79 and len(f.values) == f.cfg.window


def test_repeated_pauses_expire_from_last_admitted_estimate():
    f = HrStabilizer(configured(strict=True))
    for ts in (0, 1, 2):
        f.update(72, .8, ts)
    for ts in (2.1, 2.5, 3, 3.5):
        assert f.pause(ts)
        assert f.last_ts == 2 and f.value is None
    assert not f.pause(3.51)
    assert len(f.values) == 0 and f.ema is None
    assert f.update(80, .8, 4) == (None, None)
    assert not f.pause(3.9)  # time rollback also discards retained evidence
    assert len(f.values) == 0


def test_pipeline_spectral_rejection_retains_filter_but_hard_loss_resets():
    import numpy as np
    from backend.run_pipeline import update_rppg
    from backend.decision import DecisionEngine
    from backend.tests.test_decision_quality_gate import observation, invalid_observation, GOOD_Q

    class Raw:
        fps = 30
        max_gap_s = recovery_s = .2
        last_sample_ts = None
        samples = 75
        last_reject = None
        def push(self, value, frame_id, ts):
            self.last_sample_ts = ts
        def invalidate(self, reason, ts, *, allow_recovery=False):
            self.samples = 75 if allow_recovery else 0
            self.last_reject = reason
        def estimate(self):
            valid = self.last_reject is None
            return dict(hr_bpm=79 if valid else None, hr_conf=.8 if valid else None,
                        rr_per_min=None, rr_conf=None)

    raw = Raw()
    f = HrStabilizer(configured(strict=True))
    for ts, bpm in ((0, 72), (1, 74), (2, 73)):
        f.update(bpm, .8, ts)
    engine = DecisionEngine(load_config())
    image = np.full((64, 64, 3), 128, dtype=np.uint8)
    def process(ts, obs=None, quality=GOOD_Q):
        return update_rppg(raw, engine, obs=obs or observation(), quality=quality,
                           image=image, frame_id=round(ts * 30), ts=ts,
                           fallback=(64, 64), stabilizer=f)
    raw.last_reject = "no_dominant_peak"
    assert process(2.1)["hr_bpm"] is None
    assert len(f.values) == 3 and f.value is None
    raw.last_reject = None
    assert process(2.2)["hr_bpm"] is None
    raw.last_sample_ts = None  # isolate spectral/filter recovery from source-gap reset
    assert process(3)["hr_bpm"] == pytest.approx(73.2)
    assert process(3.1, quality={**GOOD_Q, "overall": 0})["hr_bpm"] is None
    assert len(f.values) == 3
    assert process(3.2, obs=invalid_observation("absent"))["hr_bpm"] is None
    assert len(f.values) == 0 and f.ema is None


def test_c_python_parity(tmp_path):
    compiler = shutil.which("gcc")
    if not compiler: pytest.skip("C compiler unavailable")
    shim = tmp_path / "shim.c"
    shim.write_text('#include "hr_filter.h"\nstatic vg_hr_filter f;\n'
        'int start(int n,double a,double step,double gap,double spread,int startup,double minimum){vg_hr_filter_config c={1,n,a,step,gap,spread,startup,minimum};return vg_hr_filter_init(&f,&c);}\n'
        'int update(double v,double c,double t){return vg_hr_filter_update(&f,v,c,t);}\n'
        'int pause_at(double t){return vg_hr_filter_pause(&f,t);}\n'
        'int present(void){return f.present;}\n'
        'double value(void){return f.value;}\ndouble confidence(void){return f.conf;}\n'
        'const char *reason(void){return f.reason;}\n', encoding="utf-8")
    libpath = tmp_path / "filter.dll"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
        "-I", str(ROOT / "board/autonomy"), str(shim), "-o", str(libpath), "-lm"], check=True, capture_output=True)
    lib = ct.CDLL(str(libpath))
    lib.start.argtypes = [ct.c_int] + [ct.c_double] * 4 + [ct.c_int, ct.c_double]
    lib.update.argtypes = [ct.c_double] * 3
    lib.pause_at.argtypes = [ct.c_double]
    lib.value.restype = lib.confidence.restype = ct.c_double
    lib.reason.restype = ct.c_char_p
    cfg = configured(); f = HrStabilizer(cfg)
    assert lib.start(cfg.window,cfg.alpha,cfg.interval_s,cfg.max_gap_s,cfg.max_spread_bpm,cfg.startup_samples,cfg.min_bpm) == 0
    values = [(72,.9,0),(74,.8,1),(73,.7,2),(74,.9,2),(75,.9,2.1),
        (76,.8,3),(150,.9,4),(72,.8,5),(72,.8,6),(72,.8,7),
        (math.nan,math.nan,7.1),(82,.8,8),(83,.8,9),(81,.8,10),(80,.8,12),(80,.8,11)]
    for bpm, conf, ts in values:
        expected = f.update(bpm, conf, ts)
        present = lib.update(bpm, conf, ts)
        assert bool(present) == (expected[0] is not None)
        assert lib.reason().decode() == f.reason
        if present:
            assert lib.value() == pytest.approx(expected[0], abs=1e-9)
            assert lib.confidence() == pytest.approx(expected[1], abs=1e-9)
    for strict in (False, True):
        cfg = configured(strict=strict)
        f = HrStabilizer(cfg)
        assert lib.start(cfg.window,cfg.alpha,cfg.interval_s,cfg.max_gap_s,cfg.max_spread_bpm,cfg.startup_samples,cfg.min_bpm) == 0
        operations = [(0,72,.8),(1,74,.8),(2,73,.8),(2.1,None,None),
                      (2.2,79,.8),(3,79,.8),(3.1,None,None),(4,None,None),
                      (4.51,None,None),(5,80,.8),(4.9,None,None),
                      (6,80,.8),(6.1,59.9,.8),(7,60,.8),(math.nan,None,None)]
        for ts, bpm, conf in operations:
            if bpm is None:
                assert bool(lib.pause_at(ts)) == f.pause(ts)
                assert not lib.present() and f.value is None
            else:
                expected = f.update(bpm, conf, ts)
                assert bool(lib.update(bpm, conf, ts)) == (expected[0] is not None)
                if expected[0] is not None:
                    assert lib.value() == pytest.approx(expected[0], abs=1e-9)
                    assert lib.confidence() == pytest.approx(expected[1], abs=1e-9)
            assert lib.reason().decode() == f.reason
