"""Local guarded wrapper for official NCNN 20240410 quantization tools.

No images are copied/uploaded. Quantization is not human accuracy evidence.
Run on the same host as --tools (e.g. WSL for Linux executables).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess

QUANTIZABLE = {"Convolution","ConvolutionDepthWise","InnerProduct"}
# ncnn2table uses %19[^,] per channel. This short spelling rounds to the same
# IEEE754 float32 as 1/127 while allowing all three channels to be parsed.
NORM = "0.007874016"
PREPROCESS = ["pixel=BGR","mean=[127,127,127]",f"norm=[{NORM},{NORM},{NORM}]",
              "shape=[160,160,3]","thread=2"]


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda:file.read(1024*1024),b""):digest.update(chunk)
    return digest.hexdigest()


def parse_param(path):
    lines=[line.strip() for line in Path(path).read_text().splitlines() if line.strip()]
    if len(lines)<2 or lines[0]!="7767517":raise ValueError("invalid NCNN param header")
    counts=lines[1].split()
    if len(counts)!=2:raise ValueError("invalid NCNN graph counts")
    layer_count,blob_count=map(int,counts)
    layers={}
    for line in lines[2:]:
        fields=line.split()
        if len(fields)<4:raise ValueError("incomplete NCNN layer")
        kind,name=fields[:2]
        bottom,top=map(int,fields[2:4])
        if bottom<0 or top<0 or len(fields)<4+bottom+top or name in layers:
            raise ValueError("invalid/duplicate NCNN layer")
        attributes={}
        for token in fields[4+bottom+top:]:
            if "=" not in token:raise ValueError("invalid NCNN layer attribute")
            key,value=token.split("=",1)
            if key in attributes:raise ValueError("duplicate NCNN attribute")
            attributes[key]=value
        scale_term=int(attributes.get("8","0")) if kind in QUANTIZABLE else 0
        layers[name]={"type":kind,"int8_scale_term":scale_term}
    if layer_count!=len(layers) or layer_count<=0 or blob_count<=0:
        raise ValueError("truncated/inconsistent NCNN graph")
    return layers


def contained_file(root, relative):
    if not isinstance(relative,str) or not relative or "\\" in relative or any(c.isspace() for c in relative):
        raise ValueError("invalid calibration relative path")
    candidate=Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or ":" in relative:
        raise ValueError("calibration path must be relative and contained")
    path=(root/candidate).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError("missing/escaped calibration file")
    return path


def validate_calibration(directory):
    root=Path(directory).resolve()
    manifest_path=root/"manifest.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("kind")!="peppa_local_calibration_preparation":raise ValueError("wrong calibration manifest kind")
    split=manifest.get("split_frame")
    if isinstance(split,bool) or not isinstance(split,int) or split<0:raise ValueError("invalid split frame")
    samples=manifest.get("samples")
    if not isinstance(samples,list) or not samples:raise ValueError("no manifest samples")
    calibration=[]
    seen_ids,seen_paths=set(),set()
    for sample in samples:
        frame=sample.get("frame_id")
        if isinstance(frame,bool) or not isinstance(frame,int) or frame<0 or frame in seen_ids:
            raise ValueError("invalid/duplicate frame_id")
        seen_ids.add(frame)
        expected="calibration" if frame<split else "holdout"
        if sample.get("split")!=expected:raise ValueError("manifest violates source-frame split")
        relative=sample.get("png")
        path=contained_file(root,relative)
        if Path(relative).parts[0]!=expected or path.relative_to(root).parts[0]!=expected:
            raise ValueError("PNG path crosses calibration/holdout partition")
        if path in seen_paths:raise ValueError("duplicate manifest PNG")
        seen_paths.add(path)
        if sha256(path)!=sample.get("png_sha256"):raise ValueError("PNG SHA256 mismatch")
        if expected=="calibration":calibration.append(relative)
    list_path=root/"calibration.relative.list"
    listing=list_path.read_text(encoding="utf-8").splitlines()
    if not calibration or len(listing)!=len(set(listing)) or set(listing)!=set(calibration):
        raise ValueError("list must contain all and only unique calibration samples; no holdout")
    for item in listing:contained_file(root,item)
    settings=manifest.get("ncnn2table_preprocessing_required",{})
    if settings.get("pixel")!="BGR" or settings.get("mean")!=[127,127,127] or settings.get("shape_whc")!=[160,160,3]:
        raise ValueError("manifest preprocessing differs from author ONNX path")
    norms=settings.get("norm",[])
    if len(norms)!=3 or any(struct.pack("<f",v)!=struct.pack("<f",1/127) for v in norms):
        raise ValueError("manifest norm differs from float32 1/127")
    return manifest,{"manifest_sha256":sha256(manifest_path),"list_sha256":sha256(list_path),
        "calibration_samples":len(calibration),"holdout_samples":len(samples)-len(calibration)}


def filter_table(source,destination,original,keep_float):
    lines=Path(source).read_text().splitlines()
    parsed={}
    weight_nonfinite=0
    for line in lines:
        fields=line.split()
        if not fields:continue
        name=fields[0]
        if name.startswith("#") or name in parsed or len(fields)<2:raise ValueError("invalid/duplicate/commented table line")
        values=[float(value) for value in fields[1:]]
        weight=name.endswith("_param_0")
        if weight:
            weight_nonfinite+=sum(not math.isfinite(value) for value in values)
        else:
            for value in values:
                try: native=struct.unpack("<f",struct.pack("<f",value))[0]
                except OverflowError: raise ValueError("activation scale exceeds native float32") from None
                if not math.isfinite(native) or native<=0:
                    raise ValueError("activation scales must be finite and positive in native float32")
        parsed[name]=(line,values)
    expected={name for name,layer in original.items() if layer["type"] in QUANTIZABLE}
    for name in expected:
        if name not in parsed or name+"_param_0" not in parsed:
            raise ValueError("missing activation/weight scale pair for "+name)
    excluded=keep_float|{name+"_param_0" for name in keep_float}
    # Remove BOTH exact rows. A comment is not a supported skip instruction.
    filtered=[line for name,(line,_) in parsed.items() if name not in excluded]
    Path(destination).write_text("\n".join(filtered)+"\n",encoding="utf-8")
    return {"weight_nonfinite_values":weight_nonfinite,
            "weight_nonfinite_policy":"preserved upstream values unchanged; all-zero-channel infinities not treated as accuracy evidence",
            "activation_scale_rows":sum(not name.endswith("_param_0") for name in parsed),
            "removed_exact_rows":sorted(excluded)}


def run_tool(command,cwd,output,label):
    result=subprocess.run([str(arg) for arg in command],cwd=cwd,capture_output=True,text=True,check=False)
    (output/(label+".stdout.log")).write_text(result.stdout,encoding="utf-8")
    (output/(label+".stderr.log")).write_text(result.stderr,encoding="utf-8")
    lower=result.stderr.lower()
    if result.returncode or "need to be quantized" in lower or "failed" in lower:
        raise RuntimeError(label+" rejected: exit code/stderr; inspect saved logs")
    return [str(arg) for arg in command]


def quantize(tools,float_param,float_weights,calibration_dir,output_dir,*,method="kl",keep_float=()):
    if method not in ("kl","aciq"):raise ValueError("method must be kl or aciq")
    if len(NORM.encode("ascii"))>19 or struct.pack("<f",float(NORM))!=struct.pack("<f",1/127):
        raise RuntimeError("unsafe ncnn2table norm spelling")
    tools,param,weights,calib,output=map(lambda p:Path(p).resolve(),(tools,float_param,float_weights,calibration_dir,output_dir))
    if output.exists() and (not output.is_dir() or any(output.iterdir())):raise FileExistsError("output must be absent or empty")
    executables=[tools/"ncnn2table",tools/"ncnn2int8"]
    if not all(path.is_file() for path in executables+[param,weights]):raise FileNotFoundError("missing tool/model file")
    original=parse_param(param)
    candidates={name for name,layer in original.items() if layer["type"] in QUANTIZABLE}
    if not candidates or any(layer["int8_scale_term"] for layer in original.values()):raise ValueError("input must be a float graph with quantizable layers")
    keep=set(keep_float)
    if not keep<=candidates:raise ValueError("keep-float contains unknown/nonquantizable layers")
    manifest,validation=validate_calibration(calib)
    output.mkdir(parents=True,exist_ok=True)
    raw_table,table=output/"peppa.raw.table",output/"peppa.table"
    quant_param,quant_weights=output/"peppa.param",output/"peppa.bin"
    commands=[]
    commands.append(run_tool([executables[0],param,weights,"calibration.relative.list",raw_table,*PREPROCESS,"method="+method],calib,output,"ncnn2table"))
    table_audit=filter_table(raw_table,table,original,keep)
    commands.append(run_tool([executables[1],param,weights,quant_param,quant_weights,table],calib,output,"ncnn2int8"))
    actual_graph=parse_param(quant_param)
    if {name:layer["type"] for name,layer in actual_graph.items()}!={name:layer["type"] for name,layer in original.items()}:
        raise ValueError("quantizer changed/missed/added graph layers")
    actual={name for name,layer in actual_graph.items() if layer["type"] in QUANTIZABLE and layer["int8_scale_term"]>0}
    if actual!=candidates-keep:raise ValueError("actual INT8 layer set does not match requested float exclusions")
    if not quant_weights.is_file() or not quant_weights.stat().st_size:raise ValueError("missing/empty quantized weights")
    summary={"kind":"peppa_ncnn_quantization_workflow","ncnn_tools_target_version":"20240410","tool_version_verified_from_binary":False,
        "accuracy_verified":False,"full_pipeline_realtime_verified":False,"method":method,"keep_float":sorted(keep),
        "actual_int8_layers":sorted(actual),"actual_int8_scale_terms":{name:actual_graph[name]["int8_scale_term"] for name in sorted(actual)},
        "source_video_sha256":manifest.get("source_sha256"),"split_independence":manifest.get("split_independence"),
        "calibration_validation":validation,**table_audit,"commands":commands,"cwd":str(calib),
        "sha256":{str(path):sha256(path) for path in executables+[param,weights,raw_table,table,quant_param,quant_weights]+
                  [output/(stage+suffix) for stage in ("ncnn2table","ncnn2int8") for suffix in (".stdout.log",".stderr.log")]},
        "limitations":["Same-video temporal split is not independent population accuracy evidence.",
                        "Quantized layer-set validation is not tensor accuracy or full-pipeline validation.",
                        "Upstream weight-scale infinities are recorded, never silently replaced."]}
    (output/"summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return summary


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools",type=Path,required=True)
    parser.add_argument("--float-param",type=Path,required=True)
    parser.add_argument("--float-weights",type=Path,required=True)
    parser.add_argument("--calibration-dir",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--method",choices=("kl","aciq"),default="kl")
    parser.add_argument("--keep-float",action="append",default=[])
    args=parser.parse_args()
    report=quantize(args.tools,args.float_param,args.float_weights,args.calibration_dir,args.output_dir,method=args.method,keep_float=args.keep_float)
    print(json.dumps({"output":str(args.output_dir),"actual_int8_layers":report["actual_int8_layers"],"accuracy_verified":False}))
