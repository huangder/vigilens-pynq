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
    f = HrStabilizer(configured(strict=True))
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


def test_export_uses_same_config(tmp_path):
    from board.autonomy.export_vital_config import export
    report = export(ROOT / "config.yaml", tmp_path / "generated.h")
    assert report["hr_filter"] == configured().__dict__


def test_progressive_startup_and_confidence_weighted_ema():
    f=HrStabilizer(configured())
    assert f.update(72,.8,0)==(72,.8)
    assert f.update(74,.8,1)==(72.3,.8)
    assert f.update(None,None,1.1)==(None,None)
    assert f.update(80,.8,2)==(80,.8)


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


def test_c_python_parity(tmp_path):
    compiler = shutil.which("gcc")
    if not compiler: pytest.skip("C compiler unavailable")
    shim = tmp_path / "shim.c"
    shim.write_text('#include "hr_filter.h"\nstatic vg_hr_filter f;\n'
        'int start(int n,double a,double step,double gap,double spread,int startup){vg_hr_filter_config c={1,n,a,step,gap,spread,startup};return vg_hr_filter_init(&f,&c);}\n'
        'int update(double v,double c,double t){return vg_hr_filter_update(&f,v,c,t);}\n'
        'double value(void){return f.value;}\ndouble confidence(void){return f.conf;}\n'
        'const char *reason(void){return f.reason;}\n', encoding="utf-8")
    libpath = tmp_path / "filter.dll"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
        "-I", str(ROOT / "board/autonomy"), str(shim), "-o", str(libpath), "-lm"], check=True, capture_output=True)
    lib = ct.CDLL(str(libpath))
    lib.start.argtypes = [ct.c_int] + [ct.c_double] * 4 + [ct.c_int]
    lib.update.argtypes = [ct.c_double] * 3
    lib.value.restype = lib.confidence.restype = ct.c_double
    lib.reason.restype = ct.c_char_p
    cfg = configured(); f = HrStabilizer(cfg)
    assert lib.start(cfg.window,cfg.alpha,cfg.interval_s,cfg.max_gap_s,cfg.max_spread_bpm,cfg.startup_samples) == 0
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
