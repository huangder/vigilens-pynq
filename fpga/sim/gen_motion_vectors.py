#!/usr/bin/env python3
# =============================================================================
#  gen_motion_vectors.py —— rgb2gray / motion_quality 的测试向量与 Python 黄金参考
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  契约：docs/interface.md 第 3.3（motion_quality）/ 3.4（rgb2gray）/ 4 节
#
#  产出（默认写到本脚本同级的 data_motion/ 目录）：
#    data_motion/rgb_frames.bin   RGB888 输入（10 帧 640x480），喂 rgb2gray
#    data_motion/gray.bin         缩小灰度黄金参考（10 帧 384x288）
#                                 = rgb2gray 的期望输出 = motion_quality 的输入
#    data_motion/golden_motion.csv  motion_quality 黄金参考（帧 1..9，共 9 行）
#    data_motion/meta.txt         输入/输出尺寸、帧数、字节数、阈值
#
#  【两个冻结口径，A 线必须照做】
#   1) 灰度：  Y = (77*R + 150*G + 29*B + 128) >> 8
#   2) 缩放：  640x480 -> 384x288，即 3/5 相位抽取：保留 (x%5<3) 且 (y%5<3) 的像素
#              每 5 列取 3 列 -> 640/5*3 = 384；每 5 行取 3 行 -> 480/5*3 = 288
#              实现上是"先按行抽、再按列抽"的点采样（非均值），故保留原始灰度值。
#
#  与 gen_frames.py 的分工：
#    - sim/data/        roi_statistic 的黄金参考（**已冻结，勿动**：docs/02 A10 要求黄金结果不可被后续改动破坏）
#    - sim/data_motion/ 本文件产出，rgb2gray 与 motion_quality 共用
#
#  设计原则：只用标准库（系统 Python 与项目 .venv 都能跑）；有 numpy 时自动独立对拍。
#
#  用法： python fpga/sim/gen_motion_vectors.py
# =============================================================================

import argparse
import os
import sys

# 允许 `import gen_frames` 复用同一个 LCG（避免两套伪随机实现）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_frames import lcg_bytes  # noqa: E402

DEF_W = 640
DEF_H = 480
DEF_SEED = 20260910
DEF_SEED2 = 20260911
DEF_MOTION_THRESH = 16   # 灰度差 > 此值才算"运动像素"；**待 A 线按 OpenCV 口径确认**

# ---- 冻结的灰度公式（docs/interface.md 第 3.4 节）----------------------------
#   Y = (77*R + 150*G + 29*B + 128) >> 8
#   77+150+29 = 256（BT.601 的 0.299/0.587/0.114 按 x256 四舍五入）
#   -> 纯白 255、纯黑 0，无需裁剪、不会溢出（16 bit 内最大 65408）
GRAY_R = 77
GRAY_G = 150
GRAY_B = 29
GRAY_ROUND = 128
GRAY_SHIFT = 8

# ---- 冻结的缩放口径（docs/interface.md 第 3.4 节）----------------------------
#   3/5 相位抽取：每 DEN 个像素保留前 NUM 个
DECIM_NUM = 3
DECIM_DEN = 5


def gray_of(r, g, b):
    return (GRAY_R * r + GRAY_G * g + GRAY_B * b + GRAY_ROUND) >> GRAY_SHIFT


def kept_indices(n):
    """相位抽取保留的下标：x % DECIM_DEN < DECIM_NUM。"""
    return [i for i in range(n) if (i % DECIM_DEN) < DECIM_NUM]


def out_dims(w, h):
    """输出尺寸。要求 w、h 是 DECIM_DEN 的整数倍（640、480 满足）。"""
    if w % DECIM_DEN or h % DECIM_DEN:
        raise ValueError("width/height 必须是 %d 的整数倍（当前 %dx%d）" % (DECIM_DEN, w, h))
    return w // DECIM_DEN * DECIM_NUM, h // DECIM_DEN * DECIM_NUM


def build_rgb_frames(w, h, seed, seed2):
    """10 帧，兼顾 rgb2gray 系数检验与 motion_quality 的帧对覆盖。

    返回 [(名字, bytes), ...]，每帧 W*H*3 字节，通道顺序 R,G,B。
    """
    n = w * h * 3
    frames = []

    def solid(r, g, b):
        return bytes((r, g, b)) * (w * h)

    # 帧 0：随机 —— rgb2gray 通用覆盖；也是 motion 的"底帧"（其输出不参与比对）
    frames.append(("random_a", lcg_bytes(n, seed)))

    # 帧 1/2：全零（互为相同帧 -> 运动量应为 0）
    frames.append(("all_zero", solid(0, 0, 0)))
    frames.append(("all_zero_2", solid(0, 0, 0)))

    # 帧 3/4：全满（互为相同帧 -> 运动量应为 0）
    frames.append(("all_full", solid(255, 255, 255)))
    frames.append(("all_full_2", solid(255, 255, 255)))

    # 帧 5：单点变化（只有正中那个像素为白）
    single = bytearray(n)
    off = 3 * ((h // 2) * w + (w // 2))
    single[off:off + 3] = b"\xff\xff\xff"
    frames.append(("single_center", bytes(single)))

    # 帧 6：全零（相对帧 5 是"单点变化" -> 若该点被保留，diff_total 应为 255）
    frames.append(("all_zero_3", solid(0, 0, 0)))

    # 帧 7/8/9：纯 R / 纯 G / 纯 B —— 直接检验三个系数
    #   期望灰度：纯红 77、纯绿 149、纯蓝 29
    frames.append(("pure_red", solid(255, 0, 0)))
    frames.append(("pure_green", solid(0, 255, 0)))
    frames.append(("pure_blue", solid(0, 0, 255)))

    return frames


def rgb_to_gray_decimated(rgb, w, h):
    """标准库实现：灰度化 + 3/5 相位抽取。返回 (bytes, out_w, out_h)。

    抽取顺序与硬件一致：先按行挑（y%5<3），再在保留的行里按列挑（x%5<3）。
    """
    xs = kept_indices(w)
    ys = kept_indices(h)
    ow, oh = len(xs), len(ys)
    out = bytearray(ow * oh)
    k = 0
    for y in ys:
        row = 3 * y * w
        for x in xs:
            o = row + 3 * x
            out[k] = gray_of(rgb[o], rgb[o + 1], rgb[o + 2])
            k += 1
    return bytes(out), ow, oh


def motion_of(cur, prev, n, thresh):
    """标准库实现：逐像素绝对差。d > thresh 才算运动像素（**严格大于**，等于不算）。"""
    diff_total = 0
    motion_pixels = 0
    for i in range(n):
        d = cur[i] - prev[i]
        if d < 0:
            d = -d
        diff_total += d
        if d > thresh:
            motion_pixels += 1
    ratio = (motion_pixels << 16) // n if n > 0 else 0   # 整除向下取整
    return diff_total, motion_pixels, ratio, n


def numpy_crosscheck(rgb_frames, grays, w, h, ow, oh, thresh):
    """有 numpy 时用完全独立的路径复算灰度与运动量，并断言一致。"""
    try:
        import numpy as np
    except ImportError:
        return None

    arr = np.frombuffer(b"".join(rgb_frames), dtype=np.uint8).reshape(len(rgb_frames), h, w, 3)
    g = np.frombuffer(b"".join(grays), dtype=np.uint8).reshape(len(grays), oh, ow)

    # 1) 灰度 + 缩放对拍（用 int32 防溢出；np.ix_ 镜像"先挑行再挑列"）
    a = arr.astype(np.int32)
    full = ((a[..., 0] * GRAY_R + a[..., 1] * GRAY_G + a[..., 2] * GRAY_B + GRAY_ROUND)
            >> GRAY_SHIFT).astype(np.uint8)
    ys = np.array(kept_indices(h))
    xs = np.array(kept_indices(w))
    np_gray = full[:, ys][:, :, xs]
    if not np.array_equal(np_gray, g):
        bad = int((np_gray != g).sum())
        print("!! numpy 灰度/缩放对拍失败，不一致像素 %d" % bad)
        return False

    # 2) 运动量对拍
    checked = 0
    for i in range(1, len(grays)):
        cur, prev = g[i], g[i - 1]
        d = np.abs(cur.astype(np.int32) - prev.astype(np.int32))
        np_diff = int(d.sum())
        np_motion = int((d > thresh).sum())
        np_ratio = (np_motion << 16) // (ow * oh) if ow * oh > 0 else 0
        ref = motion_of(bytes(cur), bytes(prev), ow * oh, thresh)
        if ref != (np_diff, np_motion, np_ratio, ow * oh):
            print("!! numpy 运动量对拍失败 frame=%d 标准库=%s numpy=%s"
                  % (i, ref, (np_diff, np_motion, np_ratio, ow * oh)))
            return False
        checked += 1

    return checked


def main():
    ap = argparse.ArgumentParser(
        description="生成 rgb2gray / motion_quality 测试向量与黄金参考")
    ap.add_argument("--width", type=int, default=DEF_W)
    ap.add_argument("--height", type=int, default=DEF_H)
    ap.add_argument("--seed", type=int, default=DEF_SEED)
    ap.add_argument("--seed2", type=int, default=DEF_SEED2)
    ap.add_argument("--motion-thresh", type=int, default=DEF_MOTION_THRESH)
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "data_motion"))
    args = ap.parse_args()

    w, h, th = args.width, args.height, args.motion_thresh
    ow, oh = out_dims(w, h)
    os.makedirs(args.out_dir, exist_ok=True)
    print("== gen_motion_vectors: in %dx%d -> out %dx%d (%d/%d 抽取), motion_thresh=%d =="
          % (w, h, ow, oh, DECIM_NUM, DECIM_DEN, th))

    rgb_frames = build_rgb_frames(w, h, args.seed, args.seed2)
    grays = [rgb_to_gray_decimated(rgb, w, h)[0] for _, rgb in rgb_frames]

    # ---- 写 rgb_frames.bin ----
    p = os.path.join(args.out_dir, "rgb_frames.bin")
    with open(p, "wb") as f:
        for _, b in rgb_frames:
            f.write(b)
    rgb_bytes = w * h * 3
    print("已写 %s  (%d 帧 x %d B = %d B)" % (p, len(rgb_frames), rgb_bytes, os.path.getsize(p)))

    # ---- 写 gray.bin ----
    p = os.path.join(args.out_dir, "gray.bin")
    with open(p, "wb") as f:
        for b in grays:
            f.write(b)
    gray_bytes = ow * oh
    print("已写 %s  (%d 帧 x %d B = %d B)" % (p, len(grays), gray_bytes, os.path.getsize(p)))

    # ---- 写 golden_motion.csv（帧 0 的输出无意义：其 prev 缓冲未初始化，故从帧 1 起）----
    p = os.path.join(args.out_dir, "golden_motion.csv")
    rows = 0
    with open(p, "w", newline="\n") as f:
        f.write("# motion_quality golden reference -- generated by fpga/sim/gen_motion_vectors.py\n")
        f.write("# DO NOT EDIT BY HAND; regenerate instead.\n")
        f.write("# NOTE: frame 0 is EXCLUDED -- prev-frame buffer is undefined at power-on,\n")
        f.write("#       so a valid result starts from frame 1 (see docs/interface.md 3.3).\n")
        f.write("frame,diff_total,motion_pixels,motion_ratio_q16,count\n")
        for i in range(1, len(grays)):
            dt, mp, rat, cnt = motion_of(grays[i], grays[i - 1], gray_bytes, th)
            f.write("%d,%d,%d,%d,%d\n" % (i, dt, mp, rat, cnt))
            rows += 1
    print("已写 %s  (%d 行, 帧 1..%d)" % (p, rows, len(grays) - 1))

    # ---- 写 meta.txt ----
    p = os.path.join(args.out_dir, "meta.txt")
    with open(p, "w", newline="\n") as f:
        f.write("in_width=%d\n" % w)
        f.write("in_height=%d\n" % h)
        f.write("out_width=%d\n" % ow)
        f.write("out_height=%d\n" % oh)
        f.write("frames=%d\n" % len(rgb_frames))
        f.write("rgb_bytes=%d\n" % rgb_bytes)
        f.write("gray_bytes=%d\n" % gray_bytes)
        f.write("motion_thresh=%d\n" % th)
        f.write("seed=%d\n" % args.seed)
        f.write("gray_formula=(77R+150G+29B+128)>>8\n")
        f.write("decim=%d/%d keep_x%%%d<%d keep_y%%%d<%d\n"
                % (DECIM_NUM, DECIM_DEN, DECIM_DEN, DECIM_NUM, DECIM_DEN, DECIM_NUM))
    print("已写 %s" % p)

    # ---- 系数自检（人工可核对的小样本）----
    print("-- 灰度公式自检（人工可核对）--")
    checks = [
        ((255, 0, 0), 77), ((0, 255, 0), 149), ((0, 0, 255), 29),
        ((255, 255, 255), 255), ((0, 0, 0), 0),
    ]
    ok = True
    for (rgb, exp) in checks:
        got = gray_of(*rgb)
        flag = "OK " if got == exp else "BAD"
        if got != exp:
            ok = False
        print("   %s gray%-16s = %3d (期望 %3d)" % (flag, str(rgb), got, exp))
    if not ok:
        print("!! 灰度公式自检失败")
        return 1

    # ---- 抽取口径自检 ----
    print("-- 缩放口径自检 --")
    print("   %dx%d -> %dx%d   （列保留 %d/%d，行保留 %d/%d）"
          % (w, h, ow, oh, len(kept_indices(w)), w, len(kept_indices(h)), h))

    # ---- 运动量抽查 ----
    print("-- 运动量抽查（帧对）--")
    for i in range(1, len(grays)):
        dt, mp, rat, cnt = motion_of(grays[i], grays[i - 1], gray_bytes, th)
        print("   frame %d vs %d: %-14s diff_total=%-10d motion_pixels=%-7d ratio_q16=%d"
              % (i, i - 1, rgb_frames[i][0], dt, mp, rat))

    # ---- NumPy 独立对拍 ----
    checked = numpy_crosscheck([r for _, r in rgb_frames], grays, w, h, ow, oh, th)
    if checked is None:
        print("[numpy 对拍] SKIPPED —— 当前解释器没有 numpy")
    elif checked is False:
        print("[numpy 对拍] FAILED —— 请勿使用本次产物！")
        return 1
    else:
        print("[numpy 对拍] PASS —— 灰度/缩放与运动量均与 numpy 独立实现一致（%d 帧对）" % checked)

    print("== 完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
