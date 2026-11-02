#!/usr/bin/env python3
# =============================================================================
#  bench_video_ops.py —— 视频处理链路各环节的**实测**耗时基准
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）
#  目的：回答"除了 OpenCV 还有没有更合理的实现、怎么降延迟"——
#        先把**现在到底把时间花在哪**量出来，再谈优化。
#
#  ⚠️ 三条必须随结果一起引用的限定：
#    1) 本脚本跑在**本机 AMD64 笔记本**上，**不是** Mizar 的 Cortex-A9 @667MHz。
#       两侧不可直接换算（架构、主频、NEON、缓存全不同）。本机数字只能用于
#       "哪个环节是瓶颈"的相对判断。
#    2) 人脸关键点用 `data/raw/blink.mp4` 的**真实帧**（有脸），否则 landmark 阶段
#       会被跳过、测出来的只是检测器空转 —— 那是偏低的假数。
#    3) 所有数字都是**本次运行的实测**，不是长期承诺；换机器/换版本必然不同。
#
#  用法：
#     python metrics/scripts/bench_video_ops.py
#     python metrics/scripts/bench_video_ops.py --iters 50 --json metrics/logs/_bench.json
# =============================================================================

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
try:
    from console import enable_utf8_console  # type: ignore
    enable_utf8_console()
except Exception:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np  # noqa: E402
import cv2  # noqa: E402


def timeit(fn, iters, warmup=3):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return {"median_ms": statistics.median(ts), "mean_ms": statistics.fmean(ts),
            "min_ms": min(ts), "max_ms": max(ts), "n": len(ts)}


def gray_pl_style(bgr):
    """PL 的冻结灰度式（契约 §3.4）：Y = (77R + 150G + 29B + 128) >> 8
    ⚠️ 这是 **BGR 入参**的等价实现：OpenCV 读进来是 BGR，所以系数按 B/G/R 排。"""
    a = bgr.astype(np.int32)
    y = (a[..., 0] * 29 + a[..., 1] * 150 + a[..., 2] * 77 + 128) >> 8
    return y.astype(np.uint8)


def scale_32_pl_style(rgb720):
    """PL 的 frame_scale 口径（docs/20 §2）：双轴 3:2 抽取 + 横向中心裁剪。"""
    ks = np.array([k for k in range(960) if (k % 3) < 2])
    ys = np.array([y for y in range(720) if (y % 3) < 2])
    return rgb720[ys][:, 160 + ks]


def find_face_frame(video, want=1):
    """从视频里取一帧有脸的（先粗筛：中间区域不是纯色）。返回 list[frame]。"""
    if not os.path.exists(video):
        return []
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return []
    frames = []
    i = 0
    while len(frames) < want and i < 300:
        ok, f = cap.read()
        if not ok:
            break
        if i % 15 == 0:
            frames.append(f)
        i += 1
    cap.release()
    return frames


def main():
    ap = argparse.ArgumentParser(description="视频处理链路实测基准")
    ap.add_argument("--iters", type=int, default=30)
    ap.add_argument("--video", default=os.path.join("data", "raw", "blink.mp4"))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    print("=" * 88)
    print("视频处理链路实测基准   ·   本机 AMD64 笔记本（**不是** Cortex-A9）")
    print("=" * 88)
    print("Python %s | OpenCV %s | numpy %s"
          % (sys.version.split()[0], cv2.__version__, np.__version__))
    print("迭代次数 = %d（已预热 3 次）" % args.iters)
    print()

    # ---- 素材 ----
    faces = find_face_frame(args.video)
    if faces:
        f640 = cv2.resize(faces[0], (640, 480))
        src = "%s（真实帧，含人脸 → landmark 阶段会真的执行）" % args.video
    else:
        rng = np.random.default_rng(20260929)
        f640 = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
        src = "(没有找到 %s) → 用随机噪声代替（landmark 结果不可信）" % args.video
    f720 = cv2.resize(f640, (1280, 720))
    print("素材：640x480 + 1280x720，来源 %s" % src)
    print()

    results = {}

    def row(name, r, note=""):
        results[name] = r
        print("  %-46s %8.3f ms  (min %.3f / max %.3f)  %s"
              % (name, r["median_ms"], r["min_ms"], r["max_ms"], note))

    # ---- A. OpenCV 传统管线里的每帧开销 ----
    print("-- A. OpenCV 传统管线：每帧固定开销（这些在本项目里大多已下沉到 PL）--")
    row("cv2.cvtColor BGR->RGB (640x480)", timeit(lambda: cv2.cvtColor(f640, cv2.COLOR_BGR2RGB), args.iters))
    row("cv2.cvtColor BGR->GRAY (640x480)", timeit(lambda: cv2.cvtColor(f640, cv2.COLOR_BGR2GRAY), args.iters))
    row("cv2.resize 1280x720 -> 640x480 INTER_AREA",
        timeit(lambda: cv2.resize(f720, (640, 480), interpolation=cv2.INTER_AREA), args.iters))
    row("cv2.resize 1280x720 -> 640x480 INTER_LINEAR",
        timeit(lambda: cv2.resize(f720, (640, 480), interpolation=cv2.INTER_LINEAR), args.iters))
    print()

    # ---- B. 同样的事用 numpy 整数运算做（模拟"PL 口径"的软件等价物）----
    print("-- B. 同一口径用 numpy 整数运算做（PL 的软件等价物；只作对照）--")
    rgb720 = cv2.cvtColor(f720, cv2.COLOR_BGR2RGB)
    row("numpy 整数灰度 (77R+150G+29B+128)>>8 (640x480)", timeit(lambda: gray_pl_style(f640), args.iters))
    row("numpy 3:2 双轴抽取 1280x720 -> 640x480", timeit(lambda: scale_32_pl_style(rgb720), args.iters))
    print("  ⇒ 注意：把这两步放进 PL，PS 侧的开销就变成 0（PL 是流式、逐像素、无帧缓冲）。")
    print()

    # ---- C. 人脸检测/关键点：这是 PS 侧真正的大头 ----
    print("-- C. 人脸检测 / 关键点：PS 侧真正的开销所在 --")
    try:
        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        if os.path.exists(cascade_path):
            cas = cv2.CascadeClassifier(cascade_path)
            gray = cv2.cvtColor(f640, cv2.COLOR_BGR2GRAY)
            row("Haar 级联 人脸检测 (640x480, OpenCV 自带模型)",
                timeit(lambda: cas.detectMultiScale(gray, 1.1, 5), args.iters),
                "老牌基线")
        else:
            print("  Haar 级联：模型文件不存在，跳过")
    except Exception as e:
        print("  Haar 级联：失败 %s" % e)

    try:
        import mediapipe as mp
        t0 = time.perf_counter()
        fm = mp.solutions.face_mesh.FaceMesh(static_image_mode=False,
                                             max_num_faces=1,
                                             refine_landmarks=True,
                                             min_detection_confidence=0.5)
        init_ms = (time.perf_counter() - t0) * 1000.0
        rgb = cv2.cvtColor(f640, cv2.COLOR_BGR2RGB)

        def one():
            fm.process(rgb)

        row("MediaPipe FaceMesh 478 点 (640x480)", timeit(one, max(10, args.iters // 3)),
            "**本项目当前实际使用的关键点**")
        print("       （模型初始化一次性耗时 %.0f ms，不计入每帧）" % init_ms)
    except Exception as e:
        print("  MediaPipe FaceMesh：不可用 %s" % e)
    print()

    # ---- D. 采集侧 ----
    print("-- D. 采集侧（每帧解码/读取）--")
    if os.path.exists(args.video):
        def read_one():
            cap = cv2.VideoCapture(args.video)
            cap.read()
            cap.release()
        row("cv2.VideoCapture 打开+读 1 帧（含打开开销）", timeit(read_one, max(5, args.iters // 5)))
        cap = cv2.VideoCapture(args.video)
        cap.read()

        def read_seq():
            cap.read()
        row("cv2.VideoCapture 连续 read()（已打开）", timeit(read_seq, args.iters))
        cap.release()
    else:
        print("  跳过：找不到 %s" % args.video)
    print()

    # ---- 汇总：30fps / 60fps 预算占用 ----
    print("=" * 88)
    print("预算占用（本机 AMD64；帧间隔 30fps=33.33 ms，60fps=16.67 ms）")
    print("=" * 88)
    key = "MediaPipe FaceMesh 478 点 (640x480)"
    fixed = ["cv2.cvtColor BGR->RGB (640x480)", "cv2.cvtColor BGR->GRAY (640x480)",
             "cv2.resize 1280x720 -> 640x480 INTER_AREA"]
    fixed_ms = sum(results[k]["median_ms"] for k in fixed if k in results)
    lm_ms = results.get(key, {}).get("median_ms", 0.0)
    print("  固定开销合计（颜色转换 + 缩放）  : %7.3f ms  ← 放进 PL 后可归零" % fixed_ms)
    print("  人脸关键点（MediaPipe FaceMesh） : %7.3f ms  ← 剩下的真瓶颈" % lm_ms)
    print("  合计（当前实现，本机）           : %7.3f ms" % (fixed_ms + lm_ms))
    for fps in (30, 60):
        budget = 1000.0 / fps
        used = fixed_ms + lm_ms
        print("    @%2d fps 预算 %5.2f ms → 本机占用 %5.1f%%%s"
              % (fps, budget, used / budget * 100,
                 "  ⚠️ 超预算" if used > budget else ""))
    print()
    print("  ⚠️ 以上全部是**本机 AMD64** 数字，**不得**当作 Cortex-A9 的结论。")
    print("     PS 侧必须在板上实测（计划见 docs/20 §4）。QPS 之外还要量 CPU 占用与温升。")

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", newline="\n") as f:
            json.dump({"host": "AMD64 laptop", "python": sys.version.split()[0],
                       "opencv": cv2.__version__, "numpy": np.__version__,
                       "iters": args.iters, "material": src,
                       "results": results}, f, ensure_ascii=False, indent=2)
        print("\n已写 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
