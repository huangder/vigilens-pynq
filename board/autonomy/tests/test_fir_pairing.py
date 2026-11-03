"""Exact scalar equivalence of the native FIR optimization; synthetic only."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_paired_fir_matches_scalar_with_startup_and_saturation(tmp_path):
    compiler = shutil.which('gcc') or shutil.which('cc')
    if not compiler:
        pytest.skip('C compiler unavailable')
    source = Path(__file__).resolve().parents[1]
    harness = tmp_path / 'fir_pairing.c'
    harness.write_text(r'''
#include "vital.c"
int main(void) {
    int16_t coefficients[6];
    int64_t samples[97];
    double paired[97], scalar[97];
    vg_vital v = {0};
    v.cfg.coeffs = coefficients;
    v.cfg.method = 1;
    for (int taps=1;taps<=6;++taps) {
        v.cfg.taps=taps;
        for(int k=0;k<taps;++k) coefficients[k]=(int16_t)(1+(k<taps-1-k?k:taps-1-k));
        for(int trial=0;trial<40;++trial) {
            for(int i=0;i<97;++i) samples[i]=((i*7919+trial*3571)%65536)-32768;
            v.cfg.shift=trial%2?8:0;
            v.symmetric_fir=0;
            const char *a=fir_window(&v,samples,97,scalar);
            v.symmetric_fir=1;
            const char *b=fir_window(&v,samples,97,paired);
            if ((a==NULL)!=(b==NULL)) return 1;
            if(a) { if(strcmp(a,b)) return 2; }
            else for(int i=0;i<97;++i) if(scalar[i]!=paired[i]) return 3;
        }
    }
    return 0;
}
''', encoding='utf-8')
    executable = tmp_path / 'fir_pairing.exe'
    subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                    '-I', str(source), str(harness), '-lm', '-o', str(executable)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, timeout=30)
