"""Compare a Peppa candidate with FP32 on local development tensors; no accuracy claim.

Runs a supplied native NCNN probe on the exact same files, never through a
shell. Face images stay in the local calibration folder. The report contains
aggregate point displacement, not predicted points or face images.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def distribution(values):
    values=sorted(values)
    if not values: raise ValueError("empty distribution")
    def quantile(q):
        position=(len(values)-1)*q
        lower=math.floor(position)
        return values[lower]+(values[math.ceil(position)]-values[lower])*(position-lower)
    return {"n":len(values),"mean":math.fsum(values)/len(values),
            "p50":quantile(.5),"p95":quantile(.95),"max":values[-1]}


def read_output(path):
    raw=Path(path).read_bytes()
    if len(raw)!=143*4: raise ValueError("expected 143 float32 output scalars")
    values=struct.unpack("<143f",raw)
    if any(not math.isfinite(v) for v in values): raise ValueError("nonfinite output")
    return values


def point_differences(reference,quantized,crop):
    if len(reference)!=143 or len(quantized)!=143: raise ValueError("143 outputs required")
    width,height=crop[2]-crop[0],crop[3]-crop[1]
    if width<=0 or height<=0: raise ValueError("invalid crop")
    delta=[abs(float(a)-float(b)) for a,b in zip(reference,quantized)]
    if any(not math.isfinite(v) for v in delta): raise ValueError("nonfinite difference")
    points=[math.hypot(delta[2*k]*width,delta[2*k+1]*height) for k in range(68)]
    return delta[:136],points


def selected_holdout(manifest):
    samples=manifest["samples"]
    if len({sample["frame_id"] for sample in samples})!=len(samples): raise ValueError("duplicate frame ids")
    split=manifest["split_frame"]
    for sample in samples:
        expected="calibration" if sample["frame_id"]<split else "holdout"
        if sample["split"]!=expected: raise ValueError("temporal split mismatch")
    result=[sample for sample in samples if sample["split"]=="holdout"]
    if not result: raise ValueError("no held-out samples")
    return result


def compare(manifest_path,probe,float_param,float_weights,int8_param,int8_weights,work,*,candidate_kind="quantized"):
    if candidate_kind not in ("quantized","fp32_zero_channel_pruning"):
        raise ValueError("unsupported candidate kind")
    manifest_path=Path(manifest_path).resolve()
    folder=manifest_path.parent
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    samples=selected_holdout(manifest)
    work=Path(work).resolve()
    if work.exists() and any(work.iterdir()): raise FileExistsError("comparison work directory must be empty")
    work.mkdir(parents=True,exist_ok=True)
    coordinates,points,frame_rms=[],[],[]
    for sample in samples:
        input_file=(folder/sample["f32"]).resolve()
        if not input_file.is_relative_to(folder): raise ValueError("input path escapes manifest directory")
        if sha256(input_file)!=sample["f32_sha256"]: raise ValueError("input tensor hash mismatch")
        if input_file.stat().st_size!=3*160*160*4: raise ValueError("input tensor size mismatch")
        outputs=[]
        for name,param,weights in (("fp32",float_param,float_weights),("candidate",int8_param,int8_weights)):
            prefix=work/f"frame_{sample['frame_id']:06d}_{name}"
            result=subprocess.run([str(probe),str(param),str(weights),"160","160","input1","output1",
                                   str(prefix),"1","2","143",str(input_file)],capture_output=True,text=True,timeout=60)
            if result.returncode or "NCNN_MODEL_PROBE_PASS" not in result.stdout:
                raise RuntimeError("NCNN local comparison failed: "+result.stderr)
            outputs.append(read_output(str(prefix)+".output0.f32"))
        delta,distances=point_differences(*outputs,sample["crop_xyxy_half_open"])
        coordinates.extend(delta)
        points.extend(distances)
        frame_rms.append(math.sqrt(math.fsum(d*d for d in distances)/68))
    paths={"probe":probe,"float_param":float_param,"float_weights":float_weights,
           "candidate_param":int8_param,"candidate_weights":int8_weights,"manifest":manifest_path}
    return {"kind":"peppa_local_fp32_candidate_development_comparison","candidate_kind":candidate_kind,"frames":len(samples),
            "frame_ids":[s["frame_id"] for s in samples],"split_frame":manifest["split_frame"],
            "source_sha256":manifest["source_sha256"],"sha256":{k:sha256(v) for k,v in paths.items()},
            "first_136_coordinate_abs_difference":distribution(coordinates),
            "point_displacement_original_crop_pixels":distribution(points),
            "frame_point_rms_pixels":distribution(frame_rms),"compared_points_per_frame":68,
            "auxiliary_outputs_ignored":7,"accuracy_verified":False,"threshold_calibration_verified":False,
            "full_pipeline_realtime_verified":False,
            "limitations":["Same video, disjoint frame indices; not independent subjects or scenes.",
                           "FP32 predictions are a numerical reference, not human ground truth.",
                           "No EAR/MAR/pose mapping or acceptance tolerance is frozen here.",
                           "Candidate kind is caller supplied, not a graph or precision attestation.",
                           "No host timing is reported as board performance."]}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("manifest","probe","float-param","float-weights","int8-param","int8-weights","work","output"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--candidate-kind",choices=("quantized","fp32_zero_channel_pruning"),default="quantized")
    args=parser.parse_args()
    report=compare(args.manifest,args.probe,args.float_param,args.float_weights,args.int8_param,args.int8_weights,args.work,candidate_kind=args.candidate_kind)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("frames","point_displacement_original_crop_pixels","accuracy_verified")}))
