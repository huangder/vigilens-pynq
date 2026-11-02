"""Validate each actual U-Boot FIT payload and the selected Mizar configuration."""
import hashlib,json,struct,subprocess,sys,zlib
from pathlib import Path
work,br=map(Path,sys.argv[1:]);path=work/'boot/fat/u-boot.img';data=path.read_bytes()
fdtget=str(br/'output/host/bin/fdtget')
def get(node,name,kind='s'):
    return subprocess.check_output([fdtget,'-t',kind,str(path),node,name],text=True).strip()
def nodes(node):return subprocess.check_output([fdtget,'-l',str(path),node],text=True).split()
assert data[:4]==bytes.fromhex('d00dfeed')
base=(struct.unpack_from('>I',data,4)[0]+3)&~3
images={}
for name in nodes('/images'):
    node='/images/'+name
    start=base+int(get(node,'data-offset','u'));size=int(get(node,'data-size','u'))
    assert 0<size and start>=base and start+size<=len(data)
    payload=data[start:start+size]
    algo=get(node+'/hash','algo');stored=bytes.fromhex(''.join(f'{int(b):02x}' for b in get(node+'/hash','value','bu').split()))
    if algo=='crc32':expected=struct.pack('>I',zlib.crc32(payload))
    elif algo=='sha256':expected=hashlib.sha256(payload).digest()
    else:raise ValueError('unsupported FIT hash '+algo)
    assert stored==expected,('FIT hash',name)
    assert get(node,'arch')=='arm'
    images[name]={'type':get(node,'type'),'description':get(node,'description'),'bytes':size,'hash_algorithm':algo,'hash_verified':True}
configurations=nodes('/configurations');assert len(configurations)==1,configurations
default=get('/configurations','default');assert default==configurations[0]
node='/configurations/'+default
firmware=get(node,'firmware');fdt=get(node,'fdt')
assert images[firmware]['type']=='firmware'
assert images[fdt]['type']=='flat_dt' and images[fdt]['description']=='zynq-mizar-candidate',images
assert int(get('/images/'+firmware,'load','u'))==0x04000000
report={'passed':True,'sha256':hashlib.sha256(data).hexdigest(),'images':images,'default_configuration':default,
        'firmware':firmware,'fdt':fdt,'sole_board':'zynq-mizar-candidate','hardware_boot_verified':False}
(work/'fit_verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('MIZAR_FIT_PAYLOADS_AND_DEFAULT_CONFIG_PASS')
