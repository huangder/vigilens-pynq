"""Baseline invariance on synthetic signals; no human HR claims."""
import math
import numpy as np
import pytest
from backend.vital import GreenRppg


@pytest.mark.parametrize('timestamps', [False, True])
def test_centering_removes_only_measured_baseline_during_startup(timestamps):
    from board.autonomy.export_vital_config import build
    from pathlib import Path
    values=build(Path('config.yaml'),board_profile=True)
    signals=[]
    for offset in (-12000,0,12000):
        est=GreenRppg(fps=60,window_seconds=1.25,hr_band_hz=[.7,3.5],
            coeffs=values['coeffs'],shift=values['shift'],history_seconds=10,
            green_center_before_fir=True)
        for i in range(150):
            est.push(offset+round(1000*math.sin(2*math.pi*1.2*i/60)),i,1+i/60 if timestamps else None)
        n=est._analysis_count()
        wave=est._uniform_filtered(n) if timestamps else est._nominal_filtered(n)
        assert wave is not None
        signals.append(wave)
    np.testing.assert_array_equal(signals[0],signals[1])
    np.testing.assert_array_equal(signals[1],signals[2])


def test_centering_refuses_range_violation_instead_of_clipping():
    est=GreenRppg(fps=30,window_seconds=2.5,hr_band_hz=[.7,3.5],green_center_before_fir=True)
    assert est._filter_green(np.array([-32768]*74+[32767],dtype=np.int64)) is None
    assert est._signal_error=='green_signal_range'


def test_centering_flag_must_be_boolean():
    with pytest.raises(ValueError,match='must be boolean'):
        GreenRppg(fps=30,window_seconds=2.5,hr_band_hz=[.7,3.5],green_center_before_fir=1)
