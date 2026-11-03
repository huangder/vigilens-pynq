"""Synthetic timestamp resampling tests; no human measurement claims."""
import ctypes as ct
import math

import pytest

from backend.config import load_config
from backend.vital import GreenRppg, q15_from_roi_sum
from board.autonomy.export_vital_config import build as board_values
from board.autonomy.tests.test_vital_c import ROOT, Config, Result, build, rgb_stream


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    lib = ct.CDLL(str(build(tmp_path_factory.mktemp("source_timebase"))))
    lib.vg_vital_create.argtypes = [ct.POINTER(Config)]
    lib.vg_vital_create.restype = ct.c_void_p
    lib.vg_vital_destroy.argtypes = [ct.c_void_p]
    lib.vg_vital_push_rgb.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint32), ct.c_uint32, ct.c_uint64, ct.c_double]
    lib.vg_vital_estimate.argtypes = [ct.c_void_p, ct.POINTER(Result)]
    return lib


def compare(native, rate, *, density=True, gap=0, jitter=False, noise=False, bpm=72):
    values = board_values(ROOT / "config.yaml", board_profile=True)
    coeffs = (ct.c_short * len(values['coeffs']))(*values['coeffs'])
    minimum = values['source_min_hz'] if density else 0
    fields = {k: v for k, v in values.items() if k != 'coeffs'}
    fields.update(coeffs=coeffs, taps=len(coeffs), source_min_hz=minimum)
    config = Config(**fields)
    handle = native.vg_vital_create(ct.byref(config))
    assert handle
    ref = GreenRppg(fps=values['fps'], window_seconds=values['window_seconds'],
                    hr_band_hz=[values['f_lo'], values['f_hi']], coeffs=list(coeffs),
                    shift=values['shift'], fs_tol=values['fs_tol'],
                    max_gap_ms=values['max_gap_s']*1000,
                    jitter_ratio_max=values['jitter_ratio_max'],
                    history_seconds=values['history_seconds'], source_min_hz=minimum,
                    green_center_before_fir=bool(values['green_center_before_fir']))
    try:
        import numpy as np
        rng = np.random.default_rng(7)
        frames = math.ceil(12*rate)
        for i, (sums, count) in enumerate(rgb_stream(frames, rate, bpm)):
            ts = 1 + i/rate + (gap if i >= frames-10 else 0)
            if jitter:
                ts = 1 + (i//3)*.062 + (i % 3)*.001
            if noise:
                sums[1] = round(118000 + rng.normal(0, 5000))
            assert native.vg_vital_push_rgb(handle, (ct.c_uint32*3)(*sums), count, i, ts) == 0
            ref.push(q15_from_roi_sum(sums[1], count), i, ts)
        result = Result()
        assert native.vg_vital_estimate(handle, ct.byref(result)) == 0
        expected = ref.estimate()
        reject = result.reject.decode() if result.reject else None
        assert bool(result.present) == (expected['hr_bpm'] is not None)
        # Legacy Python and C deliberately name a low nominal rate differently.
        if not density and rate < values['fps']*(1-values['fs_tol']):
            assert reject == 'sampling_too_slow' and ref.last_reject == 'fs_mismatch'
        else:
            assert reject == ref.last_reject
        if result.present:
            assert result.hr_bpm == pytest.approx(expected['hr_bpm'], abs=1e-6)
            assert result.hr_conf == pytest.approx(expected['hr_conf'], abs=1e-6)
            assert ref.last_spectral_fs == values['fps']
        return result, reject, ref
    finally:
        native.vg_vital_destroy(handle)


@pytest.mark.parametrize('rate', [30.1, 40, 45.1, 48, 51.24, 60])
def test_variable_source_rates_preserve_physical_frequency(native, rate):
    result, reject, ref = compare(native, rate)
    assert result.present, reject
    # 72 is on the 10-second FFT grid. This detects the erroneous 60/rate
    # frequency scaling without asserting sub-bin precision for a 69 BPM input.
    assert result.hr_bpm == pytest.approx(72, abs=1.5)
    assert ref.last_fs_eff == pytest.approx(rate)


def test_legacy_mode_still_refuses_5124_for_60hz(native):
    result, reject, _ = compare(native, 51.24, density=False)
    assert not result.present and reject == 'sampling_too_slow'


def test_off_bin_signal_obeys_actual_frequency_grid_resolution(native):
    result, reject, ref = compare(native, 51.24, bpm=69)
    assert result.present, reject
    # The implementation selects a bin; interpolation of RGB does not add
    # spectral resolution. Verify the mathematical half-bin bound explicitly.
    grid_bpm = ref.last_spectral_fs / ref.last_uniform_samples * 60
    assert abs(result.hr_bpm - 69) <= grid_bpm / 2 + .1


@pytest.mark.parametrize('rate', [20, 29.9, 80])
def test_density_mode_still_refuses_unsupported_source_rates(native, rate):
    result, reject, _ = compare(native, rate)
    assert not result.present
    assert reject == ('fs_mismatch' if rate == 80 else 'sampling_too_slow')


@pytest.mark.parametrize('kwargs,reason', [({'gap': .3}, 'sampling_gap'),
                                         ({'jitter': True}, 'sampling_jitter')])
def test_density_mode_still_refuses_gaps_and_jitter(native, kwargs, reason):
    result, reject, _ = compare(native, 51.24, **kwargs)
    assert not result.present and reject == reason


def test_resampling_does_not_turn_noise_into_pulse(native):
    result, _, _ = compare(native, 51.24, noise=True)
    assert not result.present


@pytest.mark.parametrize('minimum', [-1, math.nan, math.inf, 7, 61])
def test_invalid_density_config_is_refused(minimum):
    with pytest.raises(ValueError, match='source sampling minimum'):
        GreenRppg(fps=60, window_seconds=1.25, hr_band_hz=[.7,3.5],
                  coeffs=[32767], shift=15, source_min_hz=minimum)


def test_pos_growing_window_waits_for_its_local_projection_context(native):
    from backend.rppg_color import PosRppg
    values = board_values(ROOT / 'config.yaml', board_profile=True)
    coeffs = (ct.c_short * len(values['coeffs']))(*values['coeffs'])
    fields = {k: v for k, v in values.items() if k != 'coeffs'}
    fields.update(coeffs=coeffs, taps=len(coeffs), method=0)
    handle = native.vg_vital_create(ct.byref(Config(**fields)))
    assert handle
    ref = PosRppg(fps=values['fps'], window_seconds=values['window_seconds'],
                   color_window_seconds=values['color_window_seconds'],
                   hr_band_hz=[values['f_lo'], values['f_hi']], coeffs=list(coeffs),
                   shift=values['shift'], history_seconds=values['history_seconds'],
                   source_min_hz=values['source_min_hz'],
                   jitter_ratio_max=values['jitter_ratio_max'])
    accepted = 0
    try:
        for i, (sums, count) in enumerate(rgb_stream(900, 60, 72)):
            ts = 1 + i/60
            assert native.vg_vital_push_rgb(handle, (ct.c_uint32*3)(*sums), count, i, ts) == 0
            ref.push_rgb(sums, count, i, ts)
            if i in (74, 90, 94, 100, 300, 899):
                result = Result()
                assert native.vg_vital_estimate(handle, ct.byref(result)) == 0
                expected = ref.estimate()
                assert bool(result.present) == (expected['hr_bpm'] is not None)
                assert (result.reject.decode() if result.reject else None) == ref.last_reject
                if i == 74:
                    assert ref.samples == 75
                    assert ref.last_reject == 'color_window_not_full'
                if result.present:
                    accepted += 1
                    assert result.hr_bpm == pytest.approx(expected['hr_bpm'], abs=1e-6)
        assert accepted > 0
    finally:
        native.vg_vital_destroy(handle)


def test_native_green_startup_is_invariant_to_rgb_dc_offset(native):
    values = board_values(ROOT / 'config.yaml', board_profile=True)
    assert values['green_center_before_fir'] == 1
    coeffs = (ct.c_short * len(values['coeffs']))(*values['coeffs'])
    fields = {k: v for k, v in values.items() if k != 'coeffs'}
    fields.update(coeffs=coeffs, taps=len(coeffs), method=1)
    observations=[]
    for baseline in (90000,128000,180000):
        handle = native.vg_vital_create(ct.byref(Config(**fields)))
        assert handle
        reports=[]
        try:
            for i in range(300):
                pulse=round(3500*math.sin(2*math.pi*1.2*i/60))
                sums=(ct.c_uint32*3)(120000,baseline+pulse,112000)
                assert native.vg_vital_push_rgb(handle,sums,1000,i,1+i/60) == 0
                if i in (74,149,299):
                    result=Result()
                    assert native.vg_vital_estimate(handle,ct.byref(result)) == 0
                    reports.append((bool(result.present),result.hr_bpm if result.present else None,
                                    result.hr_conf if result.present else None,
                                    result.reject.decode() if result.reject else None))
            observations.append(reports)
        finally:
            native.vg_vital_destroy(handle)
    assert observations[0] == observations[1] == observations[2]
    assert any(row[0] for row in observations[0])
