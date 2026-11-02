"""Deployment validation on controlled file/config fixtures, not board results."""
import copy,struct,sys,tempfile,unittest,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from backend.config import load_config
from board.cold_boot.delivery import environment,legacy_image
class Delivery(unittest.TestCase):
 def test_environment_uses_actual_configuration_and_rejects_invalid_operations(self):
  cfg=load_config();env=environment(cfg)
  self.assertEqual(env['VG_HTTP_PORT'],str(cfg['cold_boot']['http_port']))
  self.assertEqual(env['VG_BOOT_DEVICE'],'/dev/mmcblk0p1')
  for key,value in [('http_port',True),('log_max_bytes',0),('retry_seconds',0),('board_ip','not-an-ip')]:
   bad=copy.deepcopy(cfg);bad['cold_boot'][key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):environment(bad)
 def test_real_legacy_image_crc_detects_corruption_and_wrong_architecture(self):
  payload=b'explicit software artifact fixture'
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'image'
   def write(arch=2):
    header=bytearray(struct.pack('>7I4B32s',0x27051956,0,0,len(payload),0,0,zlib.crc32(payload),5,arch,6,0,b'fixture'))
    header[4:8]=struct.pack('>I',zlib.crc32(header));target.write_bytes(header+payload)
   write();self.assertEqual(legacy_image(target,6)['payload_bytes'],len(payload))
   target.write_bytes(target.read_bytes()[:-1]+b'X')
   with self.assertRaises(ValueError):legacy_image(target)
   write(arch=3)
   with self.assertRaises(ValueError):legacy_image(target)
if __name__=='__main__':unittest.main(verbosity=2)
