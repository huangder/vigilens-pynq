"""Real process lifecycle with explicit software fixtures; no camera/PL access.
Run with Linux's stdlib unittest, no package installation is required.
"""
import hashlib,json,os,shutil,signal,subprocess,sys,tempfile,time,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parents[1]
@unittest.skipUnless(os.name=='posix','POSIX lifecycle tests')
class Service(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='vg_cold_test_');self.root=Path(self.temp.name)
  self.app=self.root/'vigilens/app';self.app.mkdir(parents=True);self.run=self.root/'run';self.run.mkdir()
  self.bin=self.root/'bin';self.bin.mkdir();self.device=self.root/'camera';self.device.symlink_to('/dev/null')
  self.service=self.root/'vigilens-service';shutil.copyfile(HERE/'vigilens-service',self.service);self.service.chmod(0o755)
  (self.bin/'ifconfig').write_text('#!/bin/sh\nexit 0\n');(self.bin/'ifconfig').chmod(0o755)
  (self.root/'vigilens/READY').write_text('test-fixture\n');(self.root/'vigilens/config.yaml').write_text('software_fixture: true\n')
  camera='''import os,pathlib,signal,sys,time
r=pathlib.Path(os.environ['VIGI_CONTRACT_LATEST']).parent
with (r/'starts').open('a') as f:f.write(' '.join(sys.argv[1:])+'\\n')
signal.signal(signal.SIGTERM,lambda *a:sys.exit(0))
while True:
 if not pathlib.Path(sys.argv[1]).exists():sys.exit(7)
 if (r/'crash').exists():(r/'crash').unlink();sys.exit(8)
 if not (r/'freeze').exists():
  p=r/'fixture.tmp';p.write_text('{"vital":{"hr_bpm":null},"software_fixture":true}');p.replace(r/'latest.json')
 print('fixture diagnostic line',flush=True);time.sleep(.05)
'''
  http='''import signal,sys,time
signal.signal(signal.SIGTERM,lambda *a:sys.exit(0))
while True:time.sleep(.1)
'''
  for name,body in [('vigi_camera_autonomy',camera),('vg_metrics_http',http)]:
   p=self.app/name;p.write_text('#!'+sys.executable+'\n'+body);p.chmod(0o755)
  (self.app/'runtime.env').write_text('VG_LANDMARK_FILE=fixture\nVG_CAMERA_FORMAT=yuyv\nVG_CAPTURE_WIDTH=640\nVG_CAPTURE_HEIGHT=480\nVG_CAPTURE_FPS=30\nVG_MOTION_THRESHOLD=16\nVG_STALE_SECONDS=3\n')
  (self.app/'manifest.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in self.app.iterdir() if p.is_file()))
  envs={'VG_RUN_DIR':str(self.run),'VG_APP_DIR':str(self.app),'VG_BOOT_ROOT':str(self.root),'VG_RELEASE_ID':'test-fixture',
   'VG_CONFIG_SHA256':hashlib.sha256((self.root/'vigilens/config.yaml').read_bytes()).hexdigest(),
   'VG_STOP_GRACE_SECONDS':'1','VG_LOG_MAX_BYTES':'1024','VG_NETWORK_INTERFACE':'fixture','VG_BOARD_IP':'127.0.0.1','VG_NETMASK':'255.0.0.0',
   'VG_HTTP_BIND':'127.0.0.1','VG_HTTP_PORT':'8080','VG_CAMERA_DEVICE':str(self.device),'VG_RETRY_SECONDS':'1',
   'VG_STARTUP_GRACE_SECONDS':'3','VG_FRAME_STALE_SECONDS':'2','VG_HEALTH_POLL_SECONDS':'1'}
  self.config=self.root/'boot.env';self.config.write_text(''.join(k+"='"+v+"'\n" for k,v in envs.items()))
  self.env={**os.environ,'VIGILENS_BOOT_CONFIG':str(self.config),'PATH':str(self.bin)+':'+os.environ['PATH']}
 def command(self,action,check=True):
  return subprocess.run([str(self.service),action],env=self.env,text=True,capture_output=True,timeout=12,check=check)
 def wait_for(self,predicate,seconds=10):
  end=time.monotonic()+seconds
  while time.monotonic()<end:
   if predicate():return
   time.sleep(.05)
  self.fail('timeout; log='+self.log())
 def log(self):
  p=self.run/'supervisor.log';return p.read_text() if p.exists() else ''
 def starts(self):
  p=self.run/'starts';return p.read_text().splitlines() if p.exists() else []
 def start(self):
  self.command('start');self.wait_for(lambda:len(self.starts())>=1)
 def tearDown(self):
  try:self.command('stop',check=False)
  finally:self.temp.cleanup()
 def test_null_measurement_is_healthy_and_duplicate_start_is_single_owner(self):
  # Match the SD rootfs: a standalone stat command is unavailable.
  (self.bin/'stat').write_text('#!/bin/sh\nexit 127\n');(self.bin/'stat').chmod(0o755)
  self.start();self.command('start');time.sleep(3)
  self.assertEqual(len(self.starts()),1);self.assertIn(' 0 30 16',self.starts()[0])
  self.assertIsNone(json.loads((self.run/'latest.json').read_text())['vital']['hr_bpm'])
  self.assertIn('running',self.command('status').stdout)
  self.command('stop');self.assertFalse((self.run/'service.lock').exists())
 def test_process_failure_and_unplug_recover_without_another_owner(self):
  self.start();(self.run/'crash').touch();self.wait_for(lambda:len(self.starts())>=2)
  self.device.unlink();self.wait_for(lambda:'CAMERA_EXIT rc=7' in self.log())
  previous=len(self.starts());self.device.symlink_to('/dev/null');self.wait_for(lambda:len(self.starts())>previous)
 def test_frozen_frames_restart_and_logs_stay_bounded(self):
  self.start();(self.run/'freeze').touch();self.wait_for(lambda:'frame_stream_stale' in self.log())
  (self.run/'freeze').unlink();self.wait_for(lambda:len(self.starts())>=2)
  self.wait_for(lambda:(self.run/'camera.log.previous').exists())
  self.assertLess((self.run/'camera.log').stat().st_size,4096)
 def test_package_corruption_fails_before_executing_camera(self):
  (self.app/'runtime.env').write_text('tampered\n');self.command('start')
  self.wait_for(lambda:'package_check_failed' in self.log());self.assertEqual(self.starts(),[])
 def test_crlf_ready_marker_fails_before_executing_camera(self):
  (self.root/'vigilens/READY').write_bytes(b'test-fixture\r\n');self.command('start')
  self.wait_for(lambda:'package_check_failed' in self.log());self.assertEqual(self.starts(),[])
if __name__=='__main__':unittest.main(verbosity=2)
