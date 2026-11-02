"""Optional real-model CLI checks. Synthetic pixels are not accuracy evidence."""
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('ERT_PROBE') and os.environ.get('ERT_MODEL'),
                     'set ERT_PROBE and ERT_MODEL to local evaluation files')
class ErtProbeCLI(unittest.TestCase):
    def run_probe(self,payload,model=None):
        with tempfile.TemporaryDirectory(prefix='vg_ert_probe_') as directory:
            output=Path(directory)/'result.jsonl'
            result=subprocess.run([os.environ['ERT_PROBE'],model or os.environ['ERT_MODEL'],str(output)],
                                  input=payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            data=output.read_text() if output.exists() else ''
            return result,data

    def test_prediction_does_not_claim_face_detection(self):
        pixels=bytes((i*37+11)%256 for i in range(64*64*3))
        result,data=self.run_probe(b'1 0.033333333 64 64 16 16 32 32\n'+pixels)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        row=json.loads(data)
        self.assertEqual(row['frame_id'],1)
        self.assertEqual(row['bbox'],[16,16,32,32])
        self.assertEqual(len(row['points']),68)
        self.assertTrue(all(len(p)==2 and all(isinstance(v,int) for v in p) for p in row['points']))
        self.assertTrue(all(math.isfinite(row[k]) and row[k]>=0 for k in ('wall_ms','cpu_ms')))
        self.assertNotIn('face',row)
        self.assertIn(b'full_pipeline=0',result.stderr)

    def test_invalid_boxes_and_headers_reject_before_pixels(self):
        for header in (b'1 nan 64 64 16 16 32 32\n',b'1 1 64 64 -1 0 32 32\n',
                       b'1 1 64 64 40 0 32 32\n',b'1 1 64 64 0 0 0 32\n',
                       b'1 1 8192 8192 0 0 32 32\n',b'1 1 64 64 0 0 32 32 extra\n'):
            with self.subTest(header=header):
                result,data=self.run_probe(header)
                self.assertEqual(result.returncode,2,result.stderr.decode())
                self.assertEqual(data,'')

    def test_truncated_pixels_reject(self):
        result,data=self.run_probe(b'1 1 64 64 0 0 32 32\n'+b'\x00'*7)
        self.assertEqual(result.returncode,2)
        self.assertEqual(data,'')

    def test_missing_model_is_not_a_success(self):
        result,data=self.run_probe(b'',model='/nonexistent/vigilens-ert-test-model.dat')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(data,'')


if __name__=='__main__':unittest.main()
