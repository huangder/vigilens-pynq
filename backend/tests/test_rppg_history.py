"""Synthetic recovery/history regressions; no human accuracy is asserted."""
import math

import numpy as np
import pytest

from backend.config import load_config
from backend.vital import GreenRppg
from backend.rppg_color import make_rppg


def feed(est, i, ts=None):
    ts=1+i/30 if ts is None else ts
    value=round(3000*math.sin(2*math.pi*1.2*ts))
    if getattr(est,'needs_rgb',False):
        est.push_rgb([120000,128000+round(value*1000/256),112000],1000,i,ts)
    else:
        est.push(value,i,ts)


@pytest.mark.parametrize('method',['green','pos'])
def test_minimum_is_real_samples_and_overlap_grows_but_expires(method):
    est=make_rppg({**load_config(),'rppg_method':method})
    for i in range(74):
        feed(est,i*2 if i==73 else i)
    assert est.samples==74 and not est.ready
    assert est.estimate()['hr_bpm'] is None
    est.reset()
    for i in range(900):
        # A repeated 3-frame drop is within the configured 200ms boundary.
        if i%120 in (60,61,62):
            continue
        feed(est,i)
    assert est.need==75 and est.samples==75
    assert 75<len(est._win)<=300
    assert est._ts[-1]-est._ts[0]<=est.history_seconds
    assert est.last_sample_ts==pytest.approx(1+899/30)
    assert est.estimate()['hr_bpm'] is not None,est.last_reject
    assert est.stats()['raw_history_samples']<=est._raw_limit


@pytest.mark.parametrize('method',['green','pos'])
def test_short_pause_keeps_history_then_requires_new_observations(method):
    est=make_rppg({**load_config(),'rppg_method':method})
    for i in range(600):feed(est,i)
    count=len(est._win)
    est.invalidate('quality_or_pose',1+600/30,allow_recovery=True)
    assert len(est._win)==count
    assert est.estimate()['hr_bpm'] is None
    for i in (603,604,605):
        feed(est,i)
        result=est.estimate()
        if i<605:
            assert result['hr_bpm'] is None and est.last_reject=='recovering'
    assert len(est._win)>75
    assert result['hr_bpm'] is not None,est.last_reject
    est.invalidate('face_missing',1+606/30,allow_recovery=False)
    assert est.samples==0 and len(est._raw)==0
    for i in range(606,680):feed(est,i)
    assert not est.ready and est.estimate()['hr_bpm'] is None


def test_long_pause_and_repeated_pauses_do_not_keep_aging_history():
    est=GreenRppg.from_config(load_config())
    for i in range(300):feed(est,i)
    end=est.last_sample_ts
    est.invalidate('quality_or_pose',end+.05,allow_recovery=True)
    assert est.samples==75
    est.invalidate('quality_or_pose',end+.25,allow_recovery=True)
    assert est.samples==0
    assert est.estimate()['hr_bpm'] is None


def test_extension_never_reaches_back_across_a_long_gap():
    est=GreenRppg.from_config(load_config())
    times=np.r_[np.arange(3*est.need)/10000,1+np.arange(1,est.need+1)/30]
    for i,ts in enumerate(times):feed(est,i,float(ts))
    assert len(est._win)>75
    assert est._analysis_count()==75
    for i in range(75):feed(est,len(times)+i,float(times[-1]+(i+1)/30))
    assert est._analysis_count()==150
    assert est.estimate()['hr_bpm']==pytest.approx(72,abs=2)


def test_long_history_still_rejects_white_noise_and_bad_source_rate():
    for rate in (30,20):
        est=GreenRppg.from_config(load_config())
        rng=np.random.default_rng(7)
        for i in range(900):est.push(round(float(rng.normal(0,3000))),i,1+i/rate)
        assert est.estimate()['hr_bpm'] is None
        if rate==20:assert est.last_reject=='fs_mismatch'


@pytest.mark.parametrize('history',[-1,math.nan,math.inf])
def test_invalid_history_is_refused(history):
    with pytest.raises(ValueError,match='history'):
        GreenRppg.from_config({**load_config(),'rppg_history_seconds':history})


@pytest.mark.parametrize('method', ['green', 'pos'])
def test_full_ring_replaces_oldest_one_at_a_time_and_keeps_raw_on_rejection(method):
    est = make_rppg({**load_config(), 'rppg_method': method})
    for i in range(600):
        feed(est, i)
    before = list(est._fid)
    assert len(before) == est.analysis_capacity
    for i in range(600, 610):
        feed(est, i)
        after = list(est._fid)
        assert after[:-1] == before[1:] and after[-1] == i
        before = after
    # Rejecting a spectrum never consumes or clears the acquired observations.
    for i in range(610, 1210):
        if getattr(est, 'needs_rgb', False):
            est.push_rgb([120000, 128000, 112000], 1000, i, 1 + i / 30)
        else:
            est.push(0, i, 1 + i / 30)
    before = list(est._fid)
    assert est.estimate()['hr_bpm'] is None
    assert list(est._fid) == before and est.ready
    feed(est, 1210)
    assert list(est._fid)[:-1] == before[1:]


@pytest.mark.parametrize('method', ['green', 'pos'])
def test_aged_samples_cannot_fill_startup_or_survive_a_paused_window(method):
    est = make_rppg({**load_config(), 'rppg_method': method})
    # Sparse timestamps deliberately fail the timing checks. Old observations
    # must also leave the window before 75 nominal slots have accumulated.
    for i in range(est.need):
        feed(est, i, ts=1 + i)
    assert est.samples < est.need and not est.ready
    assert est._ts[-1] - est._ts[0] <= est.history_seconds
    before = len(est._win)
    est.invalidate('quality_or_pose', est.last_sample_ts + est.recovery_s / 2,
                   allow_recovery=True)
    assert len(est._win) == before - 1
    assert est.estimate()['hr_bpm'] is None
    for i in range(est.need, 2 * est.need):
        feed(est, i, ts=75 + est.recovery_s / 2 + (i - est.need + 1) / 30)
    assert len(est._win) <= est.analysis_capacity
    if method == 'pos':
        assert len(est._rgb) == len(est._raw)


@pytest.mark.parametrize('method', ['green', 'pos'])
def test_rolling_window_eventually_follows_new_signal_instead_of_old_hr(method):
    est = make_rppg({**load_config(), 'rppg_method': method})
    for i in range(600):
        feed(est, i)
    assert est.estimate()['hr_bpm'] == pytest.approx(72, abs=2)
    # Phase-continuous synthetic step from 1.2 Hz to 1.5 Hz. No resets or
    # historical BPM inputs: subsequent windows must be made from new samples.
    for i in range(600, 1200):
        ts = 1 + i / 30
        phase = 2 * math.pi * (1.2 * 21 + 1.5 * (i - 600) / 30)
        value = round(3000 * math.sin(phase))
        if getattr(est, 'needs_rgb', False):
            est.push_rgb([120000, 128000 + round(value * 1000 / 256), 112000], 1000, i, ts)
        else:
            est.push(value, i, ts)
    assert min(est._fid) >= 600
    assert est.estimate()['hr_bpm'] == pytest.approx(90, abs=2)
