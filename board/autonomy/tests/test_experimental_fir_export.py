"""Explicit experimental FIR selection cannot silently reuse a different rate."""
import hashlib
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('experimental_fir_export',ROOT/'board/autonomy/export_vital_config.py')
exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)


class ExperimentalFirExport(unittest.TestCase):
    def test_board_profile_is_full60_and_keeps_75_real_sample_start(self):
        values=exporter.build(ROOT/'config.yaml',board_profile=True)
        self.assertEqual(values['fps'],60)
        self.assertEqual(values['window_seconds']*values['fps'],75)
        self.assertEqual(len(values['coeffs']),125)
        self.assertEqual(exporter.build(ROOT/'config.yaml')['fps'],30)

    def test_default_rejects_60hz_and_explicit_header_is_recorded(self):
        with tempfile.TemporaryDirectory(prefix='vg_fir_export_') as temp:
            folder=Path(temp);config=folder/'config.yaml';header=folder/'test60.h';output=folder/'vital.h'
            text=(ROOT/'config.yaml').read_text(encoding='utf-8')
            config.write_text(re.sub(r'^fps_nominal:.*$','fps_nominal: 60',text,count=1,flags=re.M),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'design rate disagree'):exporter.export(config,output)
            # Relabel coefficients only to exercise selection/metadata validation.
            # This fixture is NOT a designed filter or signal-quality test.
            source=(ROOT/'fpga/src/fir_coeffs_q15.h').read_text(encoding='utf-8')
            header.write_text(re.sub(r'(#define FIR_FS_HZ\s+)30',r'\g<1>60',source),encoding='utf-8')
            manifest=exporter.export(config,output,header)
            self.assertEqual(manifest['vital']['fps'],60)
            self.assertEqual(manifest['fir_sha256'],hashlib.sha256(header.read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError,'design rate disagree'):
                exporter.export(ROOT/'config.yaml',output,header)
            for rate in ('30.5','30.9'):
                config.write_text(re.sub(r'^fps_nominal:.*$',f'fps_nominal: {rate}',text,count=1,flags=re.M),encoding='utf-8')
                with self.subTest(rate=rate),self.assertRaisesRegex(ValueError,'design rate disagree'):
                    exporter.export(config,output)


if __name__=='__main__':unittest.main()
