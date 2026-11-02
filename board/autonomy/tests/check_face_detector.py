"""Local-only MTCNN smoke/repeatability test. No raw face image leaves this PC.

Windows prepare (OpenCV), WSL run (stdlib ctypes); references are development
MediaPipe boxes from an existing manifest, never human ground truth.
All operating parameters are required from JSON, exported from config.yaml.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import time


class Params(C.Structure):
    _fields_ = [(x, C.c_float) for x in (
        "min_face", "scale_factor", "score_p", "score_r", "score_o",
        "nms_p_scale", "nms_p", "nms_r", "nms_o")] + [("max_candidates", C.c_int)]


class Box(C.Structure):
    _fields_ = [(x, C.c_int) for x in ("x1", "y1", "x2", "y2")] + [("score", C.c_float)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["prepare", "run"])
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--video", type=Path)
    p.add_argument("--reference", type=Path)
    p.add_argument("--library", type=Path)
    p.add_argument("--models", type=Path)
    p.add_argument("--params", type=Path)
    p.add_argument("--frames", default="0,30,60,150,300,450,570")
    p.add_argument("--check-refine", action="store_true")
    p.add_argument("--packing", type=int, choices=[0,1], default=0)
    p.add_argument("--result-name", default="results.json")
    a = p.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    if a.mode == "prepare":
        import cv2
        references = json.loads(a.reference.read_text(encoding="utf-8"))
        refs = {s["frame_id"]: s["source_bbox_xywh"] for s in references["samples"]}
        cap = cv2.VideoCapture(str(a.video))
        samples = []
        for fid in map(int, a.frames.split(",")):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fid)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"decode failed: {fid}")
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            name = f"{fid}.rgb"
            (a.work / name).write_bytes(rgb.tobytes())
            samples.append(dict(frame_id=fid, width=rgb.shape[1], height=rgb.shape[0],
                                rgb=name, mediapipe_xywh=refs.get(fid)))
        cap.release()
        manifest = dict(video_sha256=hashlib.sha256(a.video.read_bytes()).hexdigest(), samples=samples)
        (a.work / "inputs.json").write_text(json.dumps(manifest), encoding="utf-8")
        print("prepared", len(samples))
        return
    config = json.loads(a.params.read_text())
    params = config.get("board_vision", config)
    runtime = Params(**{k: params[k] for k, _ in Params._fields_})
    lib = C.CDLL(str(a.library.resolve()))
    lib.vg_fd_create.argtypes = [C.c_char_p, C.c_int]
    lib.vg_fd_create.restype = C.c_void_p
    lib.vg_fd_create_ex.argtypes = [C.c_char_p, C.c_int, C.c_int]
    lib.vg_fd_create_ex.restype = C.c_void_p
    lib.vg_fd_detect.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int,
                                 C.POINTER(Params), C.POINTER(Box), C.c_int]
    lib.vg_fd_destroy.argtypes = [C.c_void_p]
    lib.vg_fd_refine.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int,
                                 C.POINTER(Params), C.POINTER(Box), C.POINTER(Box), C.c_int]
    threads = int(params.get("threads", 1))
    assert not lib.vg_fd_create_ex(str(a.models).encode(), threads, -1)
    assert not lib.vg_fd_create_ex(str(a.models).encode(), threads, 2)
    ctx = lib.vg_fd_create_ex(str(a.models).encode(), threads, a.packing)
    assert ctx, "model load failed"
    results = []
    manifest = json.loads((a.work / "inputs.json").read_text())
    previous = None
    previous_id = None
    refine_checks = []
    try:
        for sample in manifest["samples"]:
            data = (a.work / sample["rgb"]).read_bytes()
            w, h = sample["width"], sample["height"]
            buffer = C.create_string_buffer(data)
            output = (Box * runtime.max_candidates)()
            start = time.perf_counter()
            n = lib.vg_fd_detect(ctx, buffer, w, h, w*3, C.byref(runtime), output, len(output))
            elapsed = (time.perf_counter()-start)*1000
            assert n >= 0, n
            boxes = [[b.x1, b.y1, b.x2, b.y2, b.score] for b in output[:n]]
            for x1, y1, x2, y2, _ in boxes:
                assert 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h
            n2 = lib.vg_fd_detect(ctx, buffer, w, h, w*3, C.byref(runtime), output, len(output))
            assert boxes == [[b.x1,b.y1,b.x2,b.y2,b.score] for b in output[:n2]], "nondeterministic"
            padded = b"".join(data[y*w*3:(y+1)*w*3] + bytes(13) for y in range(h))
            padded_buffer = C.create_string_buffer(padded)
            n3 = lib.vg_fd_detect(ctx,padded_buffer,w,h,w*3+13,C.byref(runtime),output,len(output))
            assert boxes == [[b.x1,b.y1,b.x2,b.y2,b.score] for b in output[:n3]], "stride mismatch"
            results.append(dict(frame_id=sample["frame_id"],boxes=boxes,ms=elapsed,
                                mediapipe_xywh=sample["mediapipe_xywh"]))
            if a.check_refine and previous is not None and sample["frame_id"] == previous_id+1:
                start = time.perf_counter()
                nr = lib.vg_fd_refine(ctx,buffer,w,h,w*3,C.byref(runtime),C.byref(previous),output,len(output))
                refine_ms = (time.perf_counter()-start)*1000
                assert nr in (0,1), nr
                rb = [[b.x1,b.y1,b.x2,b.y2,b.score] for b in output[:nr]]
                # Deterministic, and exactly the same for padded input.
                nr2 = lib.vg_fd_refine(ctx,padded_buffer,w,h,w*3+13,C.byref(runtime),C.byref(previous),output,len(output))
                assert rb == [[b.x1,b.y1,b.x2,b.y2,b.score] for b in output[:nr2]]
                # Input/output alias is supported for an in-place caller.
                alias = Box.from_buffer_copy(previous)
                nr3 = lib.vg_fd_refine(ctx,buffer,w,h,w*3,C.byref(runtime),C.byref(alias),C.byref(alias),1)
                assert nr3 == nr
                if nr:
                    assert rb[0] == [alias.x1,alias.y1,alias.x2,alias.y2,alias.score]
                iou = None
                if rb and boxes:
                    x1,y1,x2,y2,_ = rb[0]
                    a1,b1,a2,b2,_ = boxes[0]
                    intersection = max(0,min(x2,a2)-max(x1,a1))*max(0,min(y2,b2)-max(y1,b1))
                    union = (x2-x1)*(y2-y1)+(a2-a1)*(b2-b1)-intersection
                    iou = intersection/union if union else 0
                refine_checks.append(dict(frame_id=sample["frame_id"],boxes=rb,ms=refine_ms,
                                          full_box_iou_development_only=iou))
                previous = Box(*rb[0]) if rb else None
            else:
                previous = Box(*boxes[0]) if boxes else None
            previous_id = sample["frame_id"]
        # A no-face image must not reuse the previous result.
        blank = C.create_string_buffer(bytes(w*h*3))
        assert lib.vg_fd_detect(ctx,blank,w,h,w*3,C.byref(runtime),output,len(output)) == 0
        if a.check_refine:
            assert refine_checks, "prepare adjacent frames to check refine"
            assert previous is not None, "last real frame needs a candidate for black-frame check"
            assert lib.vg_fd_refine(ctx,blank,w,h,w*3,C.byref(runtime),C.byref(previous),output,len(output)) == 0
            invalid_box = Box(-1,0,w,h,1)
            assert lib.vg_fd_refine(ctx,blank,w,h,w*3,C.byref(runtime),C.byref(invalid_box),output,len(output)) == -1
        invalid = Params.from_buffer_copy(runtime)
        invalid.scale_factor = 1
        assert lib.vg_fd_detect(ctx,blank,w,h,w*3,C.byref(invalid),output,len(output)) == -1
    finally:
        lib.vg_fd_destroy(ctx)
    report = dict(kind="local_development_comparison_not_accuracy",parameters=params,
                  packing=a.packing,
                  video_sha256=manifest["video_sha256"],results=results,
                  repeatability_verified=True,padded_stride_verified=True,
                  refine_checks=refine_checks,refine_black_frame_verified=a.check_refine,
                  black_frame_no_face=True,invalid_parameter_rejected=True,
                  model_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(a.models.glob("det*.*"))},
                  library_sha256=hashlib.sha256(a.library.read_bytes()).hexdigest())
    (a.work / a.result_name).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
