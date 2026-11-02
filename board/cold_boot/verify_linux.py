"""Inspect actual generated DT/initramfs/kernel dependencies, no hardware."""
import gzip,hashlib,json,struct,subprocess,sys
from pathlib import Path
work,br=map(Path,sys.argv[1:]);dt=work/'boot/fat/system.dtb'
fdtget=str(br/'output/host/bin/fdtget')
def prop(node,name,kind='s'):
    return subprocess.check_output([fdtget,'-t',kind,str(dt),node,name],text=True).strip()
actual={
 'sd_status':prop('/axi/mmc@e0100000','status'),
 'sd_bus_width':prop('/axi/mmc@e0100000','bus-width','u'),
 'sd_max_frequency':prop('/axi/mmc@e0100000','max-frequency','u'),
 'sd_broken_cd':prop('/axi/mmc@e0100000','broken-cd'),
 'sd_disable_wp':prop('/axi/mmc@e0100000','disable-wp'),
 'usb_status':prop('/axi/usb@e0002000','status'),
 'usb_role':prop('/axi/usb@e0002000','dr_mode'),
 'gem_status':prop('/axi/ethernet@e000b000','status'),
 'phy_max_speed':prop('/axi/ethernet@e000b000/ethernet-phy@1','max-speed','u'),
 'dram':prop('/memory@00100000','reg','x'),
 'dma_reservation':prop('/reserved-memory/vigilens-dma@12000000','reg','x'),
 'pl_profile':prop('/chosen','vigilens,pl-profile')}
expected={'sd_status':'okay','sd_bus_width':'4','sd_max_frequency':'25000000','sd_broken_cd':'','sd_disable_wp':'',
          'usb_status':'okay','usb_role':'host','gem_status':'okay','phy_max_speed':'100',
          'dram':'100000 1ff00000','dma_reservation':'12000000 1000000','pl_profile':'720p-3of8'}
assert actual==expected,(actual,expected)
kernel=(br/'output/build/linux-6.6.158/.config').read_text()
required=['CONFIG_MMC','CONFIG_MMC_BLOCK','CONFIG_MMC_SDHCI','CONFIG_MMC_SDHCI_OF_ARASAN',
          'CONFIG_FAT_FS','CONFIG_VFAT_FS','CONFIG_BLK_DEV_INITRD','CONFIG_DEVTMPFS']
for option in required:assert option+'=y\n' in kernel,option
data=gzip.decompress((work/'rootfs.cpio.gz').read_bytes())
def cpio_files(data):
    pos=0;result={}
    while True:
        assert data[pos:pos+6]==b'070701','invalid newc header'
        fields=[int(data[pos+i:pos+i+8],16) for i in range(6,110,8)]
        size,namesize=fields[6],fields[11]
        name=data[pos+110:pos+110+namesize]
        assert name.endswith(b'\0')
        name=name[:-1].decode('utf-8');start=(pos+110+namesize+3)&~3
        assert start+size<=len(data)
        if name=='TRAILER!!!':return result
        result[name.removeprefix('./')]=data[start:start+size]
        pos=(start+size+3)&~3
files=cpio_files(data)
if 'CONFIG_USB_VIDEO_CLASS=m\n' in kernel:
    modules=[name for name in files if '/uvcvideo.ko' in name]
    assert modules,'modular UVC driver missing from initrd'
    assert any(name.endswith('/modules.dep') and b'uvcvideo.ko' in body for name,body in files.items()),'UVC module dependencies missing'
    uvc={'state':'module','files':modules}
else:
    assert 'CONFIG_USB_VIDEO_CLASS=y\n' in kernel
    uvc={'state':'builtin'}
for text in [b'etc/init.d/S99vigilens',b'usr/sbin/vigilens-service',b'etc/vigilens/boot.env',b'etc/init.d/S98usb_host']:
    assert text in data,text
assert b'etc/init.d/S99vigi\0' not in data
assert (work/'boot/fat/uInitrd').stat().st_size<0x04000000,'initrd overlaps DTB load address'
assert (work/'boot/fat/zImage').stat().st_size<0x01000000,'zImage overlaps active boot script'
report={'passed':True,'device_tree':actual,'kernel_builtins':required,'uvc_driver':uvc,'rootfs_uncompressed_bytes':len(data),
        'rootfs_sha256':hashlib.sha256((work/'rootfs.cpio.gz').read_bytes()).hexdigest(),
        'contains_autostart_service':True,'contains_model_weights':b'shape_predictor_68_face_landmarks.dat\0' in data,
        'hardware_sd_boot_verified':False}
assert not report['contains_model_weights'],'models must stay on SD, not consume initrd RAM'
(work/'dt_verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('COLD_BOOT_DT_KERNEL_INITRD_PASS')
