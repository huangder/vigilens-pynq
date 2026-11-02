"""Byte-exact resize and model-input parity with the frozen old implementation.

No inference performance/accuracy claim. Real local RGB fixtures stay local.
Run: python3 board/autonomy/tests/test_landmark_preprocess.py
"""
import ctypes as ct
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]


class LandmarkPreprocess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "posix" or not shutil.which("g++"):
            raise unittest.SkipTest("Linux host g++ required")
        cls.temp=tempfile.TemporaryDirectory(prefix="vg_preprocess_")
        library=Path(cls.temp.name)/"preprocess.so"
        subprocess.run(["g++","-std=c++17","-O2","-Wall","-Wextra","-Werror","-shared","-fPIC",
            "-I",str(ROOT/"board/autonomy"),str(ROOT/"board/autonomy/tests/landmark_preprocess_reference.cpp"),
            "-o",str(library)],check=True)
        cls.library=ct.CDLL(str(library))
        cls.library.vg_test_preprocess_random.restype=ct.c_int
        cls.library.vg_test_preprocess_compare.argtypes=[ct.c_void_p]+[ct.c_int]*6
        cls.library.vg_test_preprocess_compare.restype=ct.c_int

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_2048_random_rois_and_edges(self):
        self.assertEqual(self.library.vg_test_preprocess_random(),2048)

    def test_real_clip_model_input_exact_equality(self):
        directory=Path(os.environ.get("VG_PREPROCESS_FIXTURE",ROOT/".tmp/autonomy/native_validation_final"))
        source=directory/"frames.rgbstream"
        contracts=directory/"contract.jsonl"
        if not source.exists() or not contracts.exists():
            self.skipTest("local real-clip RGB stream and contract boxes absent")
        rows={int(r["frame_id"]):r for r in map(json.loads,contracts.read_text(encoding="utf-8").splitlines())
              if r["face"]["visible"]>0}
        matched=0
        with source.open("rb") as stream:
            while True:
                header=stream.readline()
                if not header:break
                fields=header.split()
                self.assertEqual(len(fields),7)
                frame_id,width,height=int(fields[0]),int(fields[2]),int(fields[3])
                pixels=stream.read(width*height*3)
                self.assertEqual(len(pixels),width*height*3)
                if frame_id not in rows:continue
                x,y,w,h=rows[frame_id]["face"]["bbox"]
                # Freeze the existing runner's crop rule, including integer truncation.
                cx,cy=x+w//2,y+h//2
                half_w,half_h=int(w*1.4)//2,int(h*1.4)//2
                x0,y0=max(0,cx-half_w),max(0,cy-half_h)
                x1,y1=min(width,cx+half_w),min(height,cy+half_h)
                self.assertEqual(self.library.vg_test_preprocess_compare(pixels,width,height,x0,y0,x1,y1),0,
                                 f"model input mismatch frame_id={frame_id}")
                matched+=1
        self.assertGreater(matched,0)
        print(f"REAL_CLIP_INPUT_EXACT frames={matched} rgb_bytes_per_frame={160*160*3} nchw_floats_per_frame={160*160*3}")


if __name__=="__main__":
    unittest.main(verbosity=2)
