"""Local-only Peppa ONNX preprocessing fixtures; no calibration/accuracy claim.

Writes face crops ONLY below .tmp/autonomy/calibration/peppa. Nothing is uploaded.
Author preprocessing reference: .tmp/autonomy/candidates/peppa/onnx_detector.py.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_OUTPUT = ROOT/".tmp/autonomy/calibration/peppa"
AUTHOR_SOURCE = ROOT/".tmp/autonomy/candidates/peppa/onnx_detector.py"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024*1024), b""): digest.update(chunk)
    return digest.hexdigest()


def crop_bounds(bbox_xyxy, image_width, image_height):
    """Match author's 1.4 expansion, integer center, float //2, then clamp.

    Keep floating-point 1.4 arithmetic, including its floor behavior; do not
    replace it with a 1.5 expansion or rational rounding shortcut.
    """
    if image_width<=0 or image_height<=0 or len(bbox_xyxy)!=4:
        raise ValueError("invalid image dimensions/bbox length")
    values = []
    for value in bbox_xyxy:
        if isinstance(value,bool) or not math.isfinite(value) or int(value)!=value:
            raise ValueError("bbox must contain finite integer pixel coordinates")
        values.append(int(value))
    x0,y0,x1,y1 = values
    if x1<=x0 or y1<=y0: raise ValueError("degenerate source bbox")
    cx,cy = (x0+x1)//2,(y0+y1)//2
    half_w = ((1+2*.2)*(x1-x0))//2
    half_h = ((1+2*.2)*(y1-y0))//2
    bounds = (int(max(0,cx-half_w)),int(max(0,cy-half_h)),
              int(min(image_width,cx+half_w)),int(min(image_height,cy+half_h)))
    if bounds[2]<=bounds[0] or bounds[3]<=bounds[1]: raise ValueError("empty clamped crop")
    return bounds


def preprocess(image, bbox_xyxy):
    import cv2
    import numpy as np
    if not isinstance(image,np.ndarray) or image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3:
        raise ValueError("expected uint8 HWC BGR frame")
    bounds = crop_bounds(bbox_xyxy,image.shape[1],image.shape[0])
    x0,y0,x1,y1 = bounds
    resized = cv2.resize(image[y0:y1,x0:x1,:],(160,160),interpolation=cv2.INTER_LINEAR)
    # Original ONNX code computes in float64 then casts after transpose/batch.
    normalized = (resized-127.0)/127.0
    tensor = np.array([np.transpose(normalized,(2,0,1))]).astype("<f4")
    return resized,tensor,bounds


def sample_split(frame_index, split_frame):
    if frame_index<0 or split_frame<0: raise ValueError("negative split/frame index")
    return "calibration" if frame_index<split_frame else "holdout"


def wsl_path(path):
    text = str(Path(path).resolve()).replace("\\","/")
    if re.match(r"^[A-Za-z]:/",text): return "/mnt/"+text[0].lower()+text[2:]
    return text


def validate_paths(source, output, frames, stride, split_frame):
    if not isinstance(frames,int) or isinstance(frames,bool) or not 1<=frames<=100000:
        raise ValueError("frames must bound decoded source frames to 1..100000")
    if not isinstance(stride,int) or isinstance(stride,bool) or stride<1: raise ValueError("stride must be positive")
    if not isinstance(split_frame,int) or isinstance(split_frame,bool) or split_frame<0: raise ValueError("split-frame must be nonnegative")
    source,output = Path(source).resolve(),Path(output).resolve()
    allowed = Path(ALLOWED_OUTPUT).absolute() # Do not resolve away a symlink escape.
    if not output.is_relative_to(allowed): raise ValueError("output must stay below .tmp/autonomy/calibration/peppa")
    if not source.is_file(): raise FileNotFoundError(source)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("output must be absent or empty; old samples may not be mixed")
    return source,output


def prepare(source, output=ALLOWED_OUTPUT, *, frames=600, stride=3, split_frame=300):
    source,output = validate_paths(source,output,frames,stride,split_frame)
    author_hash = sha256(AUTHOR_SOURCE) # Fail before writing crops if provenance is missing.
    import cv2
    import numpy as np
    import mediapipe as mp
    import sys
    if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
    from backend.face_landmark import MediaPipeLandmarker
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened(): raise RuntimeError("cannot open local source video")
    fps = capture.get(cv2.CAP_PROP_FPS)
    meta = {"fps":fps,"width":capture.get(cv2.CAP_PROP_FRAME_WIDTH),"height":capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
            "frame_count":capture.get(cv2.CAP_PROP_FRAME_COUNT),"orientation_meta_deg":capture.get(cv2.CAP_PROP_ORIENTATION_META),
            "orientation_auto":capture.get(cv2.CAP_PROP_ORIENTATION_AUTO),"explicit_rotation_deg":0}
    if not math.isfinite(fps) or fps<=0:
        capture.release(); raise ValueError("source must expose finite positive FPS")
    detector = None
    samples,skipped = [],[]
    counts = Counter({key:0 for key in ("decoded_frames","selected_frames","calibration_samples","holdout_samples",
                                        "skipped_face_not_visible","skipped_invalid_bbox")})
    output.mkdir(parents=True,exist_ok=True)
    try:
        for frame_index in range(frames):
            ok,image = capture.read()
            if not ok: break
            counts["decoded_frames"] += 1
            if frame_index%stride: continue
            counts["selected_frames"] += 1
            if detector is None:
                detector = MediaPipeLandmarker(image.shape[1],image.shape[0],refine_landmarks=True)
            observation = detector.detect(image,frame_index)
            if observation.source!="mediapipe": raise RuntimeError("refusing non-MediaPipe bbox")
            split = sample_split(frame_index,split_frame)
            if not math.isfinite(observation.visible) or observation.visible<=0:
                skipped.append({"frame_id":frame_index,"split":split,"reason":"face_not_visible"})
                counts["skipped_face_not_visible"] += 1
                continue
            x,y,w,h = observation.bbox
            bbox = [x,y,x+w,y+h]
            try: png,tensor,crop = preprocess(image,bbox)
            except ValueError:
                skipped.append({"frame_id":frame_index,"split":split,"reason":"invalid_or_empty_bbox"})
                counts["skipped_invalid_bbox"] += 1
                continue
            folder = output/split
            folder.mkdir(exist_ok=True)
            png_path = folder/f"frame_{frame_index:06d}.png"
            tensor_path = folder/f"frame_{frame_index:06d}.f32"
            if not cv2.imwrite(str(png_path),png): raise RuntimeError("PNG write failed")
            tensor.tofile(tensor_path)
            samples.append({"frame_id":frame_index,"ts":frame_index/fps,"split":split,
                "source_bbox_xywh":list(observation.bbox),"crop_xyxy_half_open":list(crop),"visible":observation.visible,
                "source_frame_shape_hwc":list(image.shape),"png":png_path.relative_to(output).as_posix(),
                "png_sha256":sha256(png_path),"f32":tensor_path.relative_to(output).as_posix(),
                "f32_sha256":sha256(tensor_path),"f32_shape_nchw":[1,3,160,160],"f32_bytes":tensor_path.stat().st_size})
            counts[split+"_samples"] += 1
    finally:
        capture.release()
        if detector is not None: detector._mesh.close()
    calibration = [sample for sample in samples if sample["split"]=="calibration"]
    (output/"calibration.list").write_text("".join(wsl_path(output/sample["png"])+"\n" for sample in calibration),encoding="utf-8")
    (output/"calibration.relative.list").write_text("".join(sample["png"]+"\n" for sample in calibration),encoding="utf-8")
    manifest = {"kind":"peppa_local_calibration_preparation","accuracy_verified":False,"quantization_verified":False,
        "source":str(source),"source_sha256":sha256(source),"video_metadata":meta,
        "bbox_source":"backend.face_landmark.MediaPipeLandmarker(refine_landmarks=True); xywh->xyxy; visible<=0 skipped",
        "author_source":str(AUTHOR_SOURCE),"author_source_sha256":author_hash,
        "preprocessing":"ONNX 1.4 expansion; integer-floor center; float //2; clamp; resize160 INTER_LINEAR; BGR; (pixel-127)/127; NCHW little-endian float32",
        "ncnn2table_preprocessing_required":{"pixel":"BGR","mean":[127,127,127],"norm":[1/127]*3,"shape_whc":[160,160,3]},
        "requested_source_frame_limit":frames,"stride":stride,"split_frame":split_frame,
        "split_rule":"source frame_id < split_frame is calibration; >= is holdout",
        "split_independence":"same-video temporal split; not independent subjects/scenes and not accuracy validation",
        "tracking_policy":"one real MediaPipe tracker advanced on selected frames only; no synthetic bbox fallback",
        "dependencies":{"opencv":cv2.__version__,"numpy":np.__version__,"mediapipe":mp.__version__},
        "counts":dict(counts),"samples":samples,"skipped":skipped,
        "calibration_list":"calibration.list (absolute WSL paths); calibration.relative.list requires cwd=output directory",
        "privacy":"local face crops; never upload or copy into deploy/evidence; artifacts stay under .tmp/autonomy/calibration/peppa"}
    (output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return manifest


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,default=ALLOWED_OUTPUT)
    parser.add_argument("--frames",type=int,default=600)
    parser.add_argument("--stride",type=int,default=3)
    parser.add_argument("--split-frame",type=int,default=300)
    args=parser.parse_args()
    result=prepare(args.source,args.output,frames=args.frames,stride=args.stride,split_frame=args.split_frame)
    print(json.dumps({"output":str(args.output),"counts":result["counts"],"accuracy_verified":False}))
