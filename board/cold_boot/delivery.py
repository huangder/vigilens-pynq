"""Assemble ordinary, copyable SD files. Never enumerate/open disks or flash.

The boot binaries, Linux/initrd and current measurement package are sealed
together. Offline checks do not constitute a physical SD power-on acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import shlex
import shutil
import struct
import sys
import zipfile
import zlib

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from backend.config import load_config
from board.autonomy.package_native_vision import package
from board.w7_buildroot.boot_delivery import validate_zynq_header

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def environment(cfg):
    c=cfg['cold_boot'];values={}
    for key in ('board_ip','netmask','http_bind'):ipaddress.IPv4Address(c[key])
    for key in ('network_interface','camera_device'):
        if not isinstance(c[key],str) or not c[key] or '\n' in c[key]:raise ValueError(key)
    limits={'http_port':(1,65535),'mount_wait_seconds':(1,3600),'retry_seconds':(1,3600),
            'health_poll_seconds':(1,60),'startup_grace_seconds':(1,3600),
            'frame_stale_seconds':(1,3600),'stop_grace_seconds':(1,60),'log_max_bytes':(1024,67108864)}
    for key,(lo,hi) in limits.items():
        value=c[key]
        if type(value) is not int or not lo<=value<=hi:raise ValueError(key)
    if type(c['enabled']) is not bool:raise ValueError('enabled')
    for key in ('board_ip','netmask','network_interface','camera_device','http_bind',*limits):values['VG_'+key.upper()]=str(c[key])
    values.update(VG_BOOT_ENABLED=str(int(c['enabled'])),VG_BOOT_DEVICE='/dev/mmcblk0p1',
                  VG_BOOT_ROOT='/mnt/vigilens-boot',VG_APP_DIR='/mnt/vigilens-boot/vigilens/app',VG_RUN_DIR='/run/vigilens',
                  VG_CONFIG_SHA256=digest(ROOT/'config.yaml'))
    values['VG_RELEASE_ID']='VigiLens-SD-'+values['VG_CONFIG_SHA256'][:16]
    return values

def prepare(work,build,models):
    if (work/'application').exists():raise ValueError('application output already exists')
    cfg=load_config();values=environment(cfg)
    meta=package(build,models,models/'shape_predictor_68_face_landmarks.dat',models/'libgomp.so.1',work/'application','ert_temporal')
    # This marker distinguishes the new continuous-capable capture binary.
    if b'CAPTURE_CONTINUOUS' not in (work/'application/vigi_camera_autonomy').read_bytes():raise ValueError('continuous capture binary required')
    (work/'boot.env').write_text(''.join(k+'='+shlex.quote(v)+'\n' for k,v in values.items()),encoding='utf-8',newline='\n')
    sources=[ROOT/'config.yaml',ROOT/'board/w7_buildroot/uvc_probe.c',ROOT/'board/w7_buildroot/capture_budget.h',
             *[p for p in HERE.iterdir() if p.is_file()]]
    report={'kind':'cold_boot_build_inputs','config_sha256':values['VG_CONFIG_SHA256'],'release_id':values['VG_RELEASE_ID'],
            'inputs':{str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in sources},'application':meta,
            'hardware_sd_boot_verified':False}
    (work/'preparation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('COLD_BOOT_APPLICATION_AND_ENV_READY')

def legacy_image(path,expected_type=None):
    data=Path(path).read_bytes()
    if len(data)<64:raise ValueError(f'short uImage: {path}')
    magic,hcrc,stamp,size,load,entry,dcrc,os_id,arch,kind,compression,name=struct.unpack('>7I4B32s',data[:64])
    header=bytearray(data[:64]);header[4:8]=b'\0'*4
    if magic!=0x27051956 or zlib.crc32(header)!=hcrc or size!=len(data)-64 or zlib.crc32(data[64:])!=dcrc:
        raise ValueError(f'uImage CRC/length: {path}')
    if arch!=2 or (expected_type is not None and kind!=expected_type):raise ValueError(f'uImage architecture/type: {path}')
    return {'bytes':len(data),'payload_bytes':size,'type':kind,'compression':compression,'name':name.rstrip(b'\0').decode('ascii')}

def assemble(work,out):
    if out.exists():raise ValueError('refusing to overwrite a delivery')
    prep=json.loads((work/'preparation.json').read_text(encoding='utf-8'))
    for rel,expected in prep['inputs'].items():
        if digest(ROOT/rel)!=expected:raise ValueError('source changed: '+rel)
    boot=work/'boot/fat'
    uimages={name:legacy_image(boot/name,kind) for name,kind in [('boot.scr',6),('uInitrd',3)]}
    fit=json.loads((work/'fit_verification.json').read_text(encoding='utf-8'))
    if not fit['passed'] or fit['sha256']!=digest(boot/'u-boot.img'):raise ValueError('FIT image not verified')
    raw=(boot/'BOOT.BIN').read_bytes()
    validate_zynq_header(raw)
    if b'U-Boot SPL' not in raw:raise ValueError('SPL BootROM identity')
    bootgen=json.loads((work/'boot/bootgen_record.json').read_text(encoding='utf-8'))
    payload=(work/'boot/spl_payload.bin').read_bytes()
    offset=struct.unpack_from('<I',raw,0x30)[0]
    if bootgen['image_sha256']!=digest(boot/'BOOT.BIN') or bootgen['payload_sha256']!=hashlib.sha256(payload).hexdigest() or raw[offset:offset+len(payload)]!=payload:
        raise ValueError('AMD bootgen/SPL payload mismatch')
    if (boot/'zImage').read_bytes()[0x24:0x28]!=bytes.fromhex('18286f01'):raise ValueError('zImage header')
    dt=json.loads((work/'dt_verification.json').read_text(encoding='utf-8'))
    if not dt['passed']:raise ValueError('device tree verification failed')
    root=out/'copy_to_sd';root.mkdir(parents=True)
    for name in ('BOOT.BIN','u-boot.img','boot.scr','boot.cmd','system.bit','system.dtb','zImage','uInitrd'):shutil.copyfile(boot/name,root/name)
    shutil.copytree(work/'application',root/'vigilens/app')
    shutil.copyfile(ROOT/'config.yaml',root/'vigilens/config.yaml')
    shutil.copyfile(work/'boot.env',root/'vigilens/boot.env')
    (root/'vigilens/READY').write_text(prep['release_id']+'\n',encoding='ascii')
    files={p.relative_to(root).as_posix():{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file()}
    manifest={'kind':'copyable_SD_appliance_candidate','release_id':prep['release_id'],'config_sha256':prep['config_sha256'],
              'files':files,'bytes':sum(x['bytes'] for x in files.values()),'legacy_images':uimages,'uboot_fit':fit,'device_tree':dt,
              'hardware_sd_boot_verified':False,'latest_application_continuous_mode_hardware_verified':False,
              'prior_filtered_HR_measurement_exists':True,'accuracy_verified':False,
              'limitations':['Physical independent SD power-on must be checked after the human copies these files.',
                             'Continuous mode and recovery tests are recorded separately; not an accuracy certification.',
                             'RR is not implemented; age sidecar and PC recording tools are not native board features.']}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    (out/'SHA256SUMS').write_text(''.join(v['sha256']+'  copy_to_sd/'+k+'\n' for k,v in files.items()),encoding='ascii')
    shutil.copyfile(HERE/'Verify-Copy.ps1',out/'Verify-Copy.ps1')
    shutil.copyfile(HERE/'README.md',out/'先读_拷贝与上电说明.md')
    archive=out.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(out).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip():raise ValueError('delivery archive CRC')
    result={'directory':str(out),'archive':str(archive),'archive_sha256':digest(archive),
            'archive_bytes':archive.stat().st_size,'file_count':len(files),'copy_bytes':manifest['bytes'],'hardware_sd_boot_verified':False}
    (work/'delivery_result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','assemble'))
    p.add_argument('--work',type=Path,required=True);p.add_argument('--build',type=Path);p.add_argument('--models',type=Path);p.add_argument('--out',type=Path)
    a=p.parse_args()
    if a.action=='prepare':
        if not a.build or not a.models:p.error('prepare requires --build and --models')
        prepare(a.work.resolve(),a.build.resolve(),a.models.resolve())
    else:
        if not a.out:p.error('assemble requires --out')
        assemble(a.work.resolve(),a.out.resolve())
