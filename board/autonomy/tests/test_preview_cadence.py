"""Synthetic camera leases with a fake JPEG sink; no image/accuracy claims."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]


@unittest.skipUnless(os.name=='posix' and shutil.which('gcc'),'Linux pthread compiler required')
class PreviewCadence(unittest.TestCase):
    def test_rgb_aggregation_uses_real_inputs_and_resets_across_losses(self):
        with tempfile.TemporaryDirectory(prefix='vg_sampler_') as temp:
            executable=Path(temp)/'sampler_test'
            subprocess.run(['gcc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-I',str(ROOT/'board/autonomy'),
                str(ROOT/'board/autonomy/tests/rgb_sampler_test.c'),'-lm','-o',str(executable)],check=True)
            subprocess.run([str(executable)],check=True,timeout=15)

    def test_four_slot_queue_preserves_fifo_and_lease_ownership(self):
        with tempfile.TemporaryDirectory(prefix='vg_buffered_') as temp:
            executable=Path(temp)/'buffered_test'
            subprocess.run(['gcc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-pthread',
                '-fsanitize=address,undefined','-DVG_ANALYSIS_SLOTS=4','-I',str(ROOT/'board/autonomy'),
                str(ROOT/'board/autonomy/autonomy_camera.c'),
                str(ROOT/'board/autonomy/tests/autonomy_buffered_test.c'),'-o',str(executable)],check=True)
            subprocess.run([str(executable)],check=True,timeout=15)

    def test_encoding_is_throttled_without_skipping_measurement_or_breaking_leases(self):
        with tempfile.TemporaryDirectory(prefix='vg_preview_cadence_') as temp:
            folder=Path(temp)
            (folder/'vg_vision_config.h').write_text(
                '#define VG_PREVIEW_JPEG_QUALITY 80\n#define VG_PREVIEW_INTERVAL_S 0.2\n')
            executable=folder/'camera_test'
            subprocess.run(['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-pthread',
                '-DVIGI_CAMERA_PREVIEW','-I',str(folder),'-I',str(ROOT/'board/autonomy'),
                str(ROOT/'board/autonomy/autonomy_camera.c'),
                str(ROOT/'board/autonomy/tests/autonomy_camera_test.c'),'-o',str(executable)],check=True)
            subprocess.run([str(executable)],check=True,timeout=15)
