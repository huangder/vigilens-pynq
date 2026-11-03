"""Prepare a LOCAL board deployment package; never download models or flash disks."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from backend.config import load_config, rppg_window_seconds
from backend.hr_filter import HrFilterConfig
from board.autonomy.package_dashboard import package_dashboard


def package(build: Path, models: Path, landmark: Path, gomp: Path, output: Path, runtime: str='native'):
    if runtime not in ('native','ert_hybrid','ert_temporal'):raise ValueError('unsupported deployment runtime')
    landmark_name='shape_predictor_68_face_landmarks.dat' if runtime.startswith('ert_') else 'peppa_pruned.mnn'
    mapping = {"vigi_camera_autonomy":build/"vigi_camera_autonomy",
               "vg_vision_replay_arm":build/"vg_vision_replay_arm",
               "vg_metrics_http":build/"vg_metrics_http_arm",
               landmark_name:landmark,"libgomp.so.1":gomp,
               "display.html":ROOT/"board/autonomy/display.html",
               "install_native.sh":ROOT/"board/autonomy/install_native.sh",
               "run_native.sh":ROOT/"board/autonomy/run_native.sh"}
    for stage in (1,2,3):
        for suffix in ("param","bin"):
            name=f"det{stage}.{suffix}"
            mapping[name]=models/name
    for source in mapping.values():
        if not source.is_file(): raise FileNotFoundError(source)
    config=load_config()
    physiological=json.loads((build/'vg_vital_config.json').read_text(encoding='utf-8')).get('vital',{})
    vital_fps=float(physiological.get('fps',config['fps_nominal']))
    vital_seconds=float(physiological.get('window_seconds',rppg_window_seconds(config)))
    digest=hashlib.sha256((ROOT/"config.yaml").read_bytes()).hexdigest()
    if runtime!='native' or (build/'runtime_build.json').is_file():
        recorded=json.loads((build/'runtime_build.json').read_text(encoding='utf-8'))
        if recorded['runtime']!=runtime or recorded['mode']!='arm' or recorded['config_sha256']!=digest or recorded['camera_sha256']!=hashlib.sha256((build/'vigi_camera_autonomy').read_bytes()).hexdigest():
            raise ValueError('build runtime/config/binary mismatch; rebuild before packaging')
    for name,key in (("vg_config.json","source_sha256"),("vg_vital_config.json","config_sha256"),
                     ("vg_vision_config.json","config_sha256")):
        if json.loads((build/name).read_text(encoding="utf-8"))[key] != digest:
            raise ValueError("config.yaml changed since native build; rebuild before packaging")
    output.mkdir(parents=True,exist_ok=True)
    for name,source in mapping.items(): shutil.copyfile(source,output/name)
    display=(output/'display.html').read_text(encoding='utf-8')
    display=display.replace('/* HR_DISPLAY_BUNDLE */',(ROOT/'frontend/hr_display.js').read_text(encoding='utf-8'))
    display=display.replace('/* HR_DISPLAY_CONFIG */','const HR_DISPLAY_CONFIG='+json.dumps({k:config[k] for k in ('ws_disconnect_timeout_s','vital_require_quality','fps_nominal')})+';')
    (output/'display.html').write_text(display,encoding='utf-8',newline='\n')
    env=(f"# Generated from config.yaml; source sha256 {digest}\n"
         f"VG_CAPTURE_FPS={int(config['board_vision'].get('capture_fps',config['fps_nominal']))}\n"
         f"VG_DECODE_WORKERS={int(config['board_vision'].get('decode_workers',2))}\n"
         f"VG_CAPTURE_WIDTH={int(config['board_vision']['camera_width'])}\n"
         f"VG_CAPTURE_HEIGHT={int(config['board_vision']['camera_height'])}\n"
         f"VG_CAMERA_FORMAT={config['board_vision']['camera_format']}\n"
         f"VG_RUNTIME={runtime}\nVG_LANDMARK_FILE={landmark_name}\n"
         f"VG_MOTION_THRESHOLD={int(config['motion_thresh_gray'])}\n"
         f"VG_STALE_SECONDS={float(config['ws_disconnect_timeout_s'])}\n")
    (output/"runtime.env").write_text(env,encoding="utf-8",newline="\n")
    dashboard_files=package_dashboard(output,config)
    hashes={name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in (*mapping,"runtime.env",*dashboard_files)}
    (output/"manifest.sha256").write_text("".join(f"{digest}  {name}\n" for name,digest in hashes.items()),encoding="ascii",newline="\n")
    summary={"files":hashes,"config_sha256":digest,"runtime":runtime,
             "heart_rate_window_seconds":vital_seconds,
             "required_hr_samples":round(vital_seconds*vital_fps),
             "heart_rate_sampling_hz":vital_fps,
             "hr_history_seconds":max(rppg_window_seconds(config),float(config.get('rppg_history_seconds',0))),
             "hr_resample_jitter_ratio_max":float(config.get('rppg_resample_jitter_ratio_max',config['realtime_jitter_ratio_max'])),
             "blink_policy":{k:v for k,v in config['board_vision'].items() if k.startswith(('ear_','blink_'))},
             "preview_hz":config['board_vision']['preview_hz'],
             "dashboard_metrics_hz":config['board_vision']['dashboard_metrics_hz'],
             "hr_trend":{k:config['board_vision'][k] for k in ('hr_trend_bridge_seconds','hr_trend_hold_seconds')},
             "hr_display_animation":{k:config['board_vision'][k] for k in ('hr_display_transition_ms','dashboard_animation_hz')},
             "hr_filter":HrFilterConfig.from_config(config).__dict__,
             "capture":{"width":config['board_vision']['camera_width'],'height':config['board_vision']['camera_height'],'format':config['board_vision']['camera_format']},
             "full30_verified":False,
             "local_only":True,"hardware_boot_verified":False,"accuracy_verified":False}
    (output/"package.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8",newline="\n")
    return summary


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build",type=Path,default=ROOT/".tmp/autonomy/native_vision")
    p.add_argument("--models",type=Path,default=ROOT/".tmp/autonomy/mtcnn")
    p.add_argument("--landmark",type=Path,default=ROOT/".tmp/autonomy/deploy/peppa_pruned.mnn")
    p.add_argument("--libgomp",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument('--runtime',choices=('native','ert_hybrid','ert_temporal'),default='native')
    args=p.parse_args()
    print(json.dumps(package(args.build,args.models,args.landmark,args.libgomp,args.output,args.runtime),indent=2))
