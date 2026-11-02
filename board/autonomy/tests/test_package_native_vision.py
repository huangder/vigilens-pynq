"""Packaging format regression; dummy files are NOT executable/model fixtures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"board/autonomy"))
from package_native_vision import package


class Package(unittest.TestCase):
    def test_linux_manifest_and_environment_on_windows_too(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            build,models=root/"build",root/"models"
            build.mkdir();models.mkdir()
            for name in ("vigi_camera_autonomy","vg_vision_replay_arm","vg_metrics_http_arm"):
                (build/name).write_bytes(b"dummy packaging input, not an executable")
            digest=hashlib.sha256((ROOT/"config.yaml").read_bytes()).hexdigest()
            for name,key in (("vg_config.json","source_sha256"),("vg_vital_config.json","config_sha256"),
                             ("vg_vision_config.json","config_sha256")):
                (build/name).write_text(json.dumps({key:digest}),encoding="utf-8")
            for stage in (1,2,3):
                for suffix in ("param","bin"):(models/f"det{stage}.{suffix}").write_bytes(b"dummy model")
            landmark,gomp=root/"landmark",root/"gomp"
            landmark.write_bytes(b"dummy");gomp.write_bytes(b"dummy")
            output=root/"package"
            result=package(build,models,landmark,gomp,output)
            for name in ("manifest.sha256","runtime.env","run_native.sh"):
                self.assertNotIn(b"\r",(output/name).read_bytes(),name)
            for line in (output/"manifest.sha256").read_text().splitlines():
                digest,name=line.split("  ",1)
                self.assertEqual(digest,hashlib.sha256((output/name).read_bytes()).hexdigest())
            self.assertFalse(result["accuracy_verified"])
            self.assertEqual(result["heart_rate_window_seconds"], 5)
            self.assertEqual(result["required_hr_samples"], 150)
            display=(output/'display.html').read_text(encoding='utf-8')
            self.assertNotIn('/* HR_DISPLAY_BUNDLE */',display)
            self.assertIn('最近有效值',display)
            self.assertIn('const HR_DISPLAY_CONFIG=',display)
            record={'runtime':'ert_hybrid','mode':'arm','config_sha256':hashlib.sha256((ROOT/'config.yaml').read_bytes()).hexdigest(),
                    'camera_sha256':hashlib.sha256((build/'vigi_camera_autonomy').read_bytes()).hexdigest()}
            (build/'runtime_build.json').write_text(json.dumps(record),encoding='utf-8')
            hybrid=package(build,models,landmark,gomp,root/'hybrid','ert_hybrid')
            self.assertEqual(hybrid['runtime'],'ert_hybrid')
            self.assertIn('shape_predictor_68_face_landmarks.dat',hybrid['files'])
            self.assertFalse(hybrid['full30_verified'])
            with self.assertRaisesRegex(ValueError,'runtime/config/binary mismatch'):
                package(build,models,landmark,gomp,root/'wrong-runtime')
            record['runtime']='ert_temporal'
            (build/'runtime_build.json').write_text(json.dumps(record),encoding='utf-8')
            temporal=package(build,models,landmark,gomp,root/'temporal','ert_temporal')
            self.assertEqual(temporal['runtime'],'ert_temporal')
            self.assertIn('shape_predictor_68_face_landmarks.dat',temporal['files'])
            with self.assertRaisesRegex(ValueError,'runtime/config/binary mismatch'):
                package(build,models,landmark,gomp,root/'wrong-experiment','ert_hybrid')
            (build/'vigi_camera_autonomy').write_bytes(b'changed after build manifest')
            with self.assertRaisesRegex(ValueError,'runtime/config/binary mismatch'):
                package(build,models,landmark,gomp,root/'wrong-binary','ert_temporal')
            (build/'runtime_build.json').unlink()
            (build/"vg_config.json").write_text(json.dumps({"source_sha256":"different-config"}),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"changed since native build"):
                package(build,models,landmark,gomp,root/"mismatched")


if __name__=="__main__":unittest.main()
