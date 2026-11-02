"""Local scalar-feature ablation; never substitutes for production output."""
from pathlib import Path
import sys
import json
import hashlib
import argparse
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.rppg_color import PosRppg, make_rppg, pos_waveform
from backend.vital import CENTER, SCALE

diagnostics = []
class FloatProbe(PosRppg):
    def _color_filtered(self, rgb, count):
        actual = super()._color_filtered(rgb, count)
        if actual is not None:
            wave = pos_waveform(rgb, self.color_window_samples)
            # Remove input rounding and output arithmetic shift, preserving
            # the same frozen FIR coefficients, color waveform and time grid.
            floating = np.convolve(wave * CENTER * SCALE, self.coeffs)[:len(wave)] / (1 << self.shift)
            floating = floating[-count:]
            row = {}
            for name, values in [('fixed', actual), ('float', floating)]:
                psd = np.abs(np.fft.rfft((values - values.mean()) * np.hanning(len(values)))) ** 2
                frequencies = np.fft.rfftfreq(len(values), 1 / self.fps)
                band = psd[(frequencies >= self.f_lo) & (frequencies <= self.f_hi)]
                k = int(np.argmax(band))
                row[name] = float(band[max(0,k-1):k+2].sum() / band.sum()) if band.sum() else 0.
            diagnostics.append(row)
        return actual

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--features', type=Path, required=True)
parser.add_argument('--config', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
path = args.features
est = make_rppg({**load_config(args.config), 'rppg_method': 'pos'})
est.__class__ = FloatProbe
mismatches = []
frames = 0
for line in path.read_text(encoding='utf-8').splitlines():
    frames += 1
    row = json.loads(line)
    ts = row['ts']
    if row.get('count', 0) <= 0:
        reason = row['pos']['reason']
        est.invalidate(reason, ts, allow_recovery=reason in ('quality_invalid', 'pose_invalid'))
    else:
        if est.last_sample_ts is not None and ts - est.last_sample_ts > est.max_gap_s:
            est.reset('source_gap')
        est.push_rgb([row[f'sum_{c}'] for c in 'rgb'], row['count'], row['frame_id'], ts)
    result = est.estimate()
    if result != row['pos']['result'] or est.last_reject != row['pos']['reason']:
        mismatches.append(row['frame_id'])
report = {'source_features_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
          'matched_production_frames': frames-len(mismatches), 'mismatches': mismatches,
          'spectral_windows': len(diagnostics), 'scope': 'scalar POS/fixed-versus-floating-FIR ablation, no HR reference',
          'max_fixed_peak_fraction': max((r['fixed'] for r in diagnostics), default=None),
          'max_float_peak_fraction': max((r['float'] for r in diagnostics), default=None),
          'float_majority_windows': sum(r['float'] > .5 for r in diagnostics)}
args.out.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
assert not mismatches
