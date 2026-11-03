"""Compare actual JPEG output pixels for one-row and recommended group reads."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]


@unittest.skipUnless(os.name=='posix' and shutil.which('gcc'),'Linux host gcc required')
class JpegRows(unittest.TestCase):
    def test_recommended_groups_match_single_rows_exactly(self):
        with tempfile.TemporaryDirectory(prefix='vg_jpeg_rows_') as temp:
            binary=Path(temp)/'jpeg_rows_test'
            include=os.environ.get('VG_TEST_JPEG_INCLUDE')
            library=os.environ.get('VG_TEST_JPEG_LIBRARY','-ljpeg')
            extra=['-I',include] if include else []
            subprocess.run(['gcc','-O2','-std=c11','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined',*extra,
                str(ROOT/'board/w7_buildroot/tests/jpeg_rows_test.c'),library,'-lm','-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True,timeout=20)


if __name__=='__main__':unittest.main(verbosity=2)
