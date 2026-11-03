"""Compile and decode real JPEGs from synthetic RGB on the Linux host."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
@unittest.skipUnless(os.name=='posix' and shutil.which('gcc'),'Linux host gcc required')
class Preview(unittest.TestCase):
    def test_real_encoder_color_dimensions_atomicity_and_failures(self):
        with tempfile.TemporaryDirectory(prefix='vg_preview_') as temp:
            binary=Path(temp)/'preview_test'
            include=os.environ.get('VG_TEST_JPEG_INCLUDE')
            library=os.environ.get('VG_TEST_JPEG_LIBRARY','-ljpeg')
            extra=['-I',include] if include else []
            subprocess.run(['gcc','-O2','-std=c11','-Wall','-Wextra','-Werror','-pthread',
                *extra,
                '-I',str(ROOT/'board/autonomy'),str(ROOT/'board/autonomy/camera_preview.c'),
                str(ROOT/'board/autonomy/tests/camera_preview_test.c'),library,'-o',str(binary)],check=True)
            subprocess.run([str(binary),str(Path(temp)/'frame.jpg')],check=True,timeout=10)
if __name__=='__main__':unittest.main(verbosity=2)
