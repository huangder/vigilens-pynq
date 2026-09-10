#!/usr/bin/env python3
# =============================================================================
#  gen_frames.py —— 生成 roi_statistic 的测试向量与 Python 黄金参考
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  契约：docs/interface.md 第 4.1 / 4.2 节（文件格式与比对口径已冻结）
#
#  产出（默认写到本脚本同级的 data/ 目录）：
#    data/frames.bin      uint8 裸二进制，5 帧 × 640×480×3，通道顺序 R,G,B
#    data/golden_roi.csv  黄金参考：9 个 ROI 用例 × 5 帧 = 45 行
#
#  设计原则：
#    1) **只用标准库** —— 保证在系统 Python 或项目 .venv 里都能跑，不因缺 numpy 而阻塞；
#    2) 若环境里有 numpy，额外用 NumPy 独立复算一遍并断言与标准库结果完全一致
#       （两套独立实现对拍，等于自证黄金参考可信）；
#    3) 完全确定性：同 seed 必得同 frames.bin / golden_roi.csv（赛制"可复现"要求）。
#
#  用法：
#    python fpga/sim/gen_frames.py
#    python fpga/sim/gen_frames.py --width 640 --height 480 --seed 20260910
# =============================================================================

import argparse
import os
import sys

# ---- 冻结的默认参数（勿随意改；改则须同步 docs/interface.md 并升版本号）--------
DEF_W = 640
DEF_H = 480
DEF_SEED = 20260910
DEF_SEED2 = 20260911          # 第 5 帧（第二段随机）用的另一个种子

# 线性同余发生器（ANSI C 经典参数，31 bit 模）。
# 选它而不是 random 模块，是因为它**跨 Python 版本逐字节稳定**，且易于在 C 里复现。
LCG_MUL = 1103515245
LCG_ADD = 12345
LCG_MASK = 0x7FFFFFFF


def lcg_bytes(n, seed):
    """产生 n 个确定性伪随机字节。"""
    s = seed & LCG_MASK
    out = bytearray(n)
    for i in range(n):
        s = (LCG_MUL * s + LCG_ADD) & LCG_MASK
        out[i] = (s >> 16) & 0xFF
    return bytes(out)


def build_frames(w, h, seed, seed2):
    """构造 5 帧，覆盖《skill》要求的四类边界：随机 / 全零 / 全满 / 单点变化。

    返回 [(名字, bytes), ...]
    """
    n = w * h * 3
    frames = []

    # 帧 0：随机输入
    frames.append(("random_a", lcg_bytes(n, seed)))

    # 帧 1：全零
    frames.append(("all_zero", bytes(n)))

    # 帧 2：全满（255）
    frames.append(("all_full", b"\xff" * n))

    # 帧 3：单点变化 —— 只有画面正中央那个像素为白，其余全黑
    single = bytearray(n)
    cx, cy = w // 2, h // 2
    off = 3 * (cy * w + cx)
    single[off + 0] = 255
    single[off + 1] = 255
    single[off + 2] = 255
    frames.append(("single_change", bytes(single)))

    # 帧 4：随机输入（另一 seed）
    frames.append(("random_b", lcg_bytes(n, seed2)))

    return frames


def clamp_roi(x0, y0, x1, y1, w, h):
    """把 ROI 裁剪到图像范围内。

    硬件不做 clamp，只是"越界范围没有像素能匹配"，等价于把 ROI 裁到图像内。
    黄金参考必须按同样口径裁剪，否则对不上。
    """
    cx0 = max(0, min(x0, w))
    cx1 = max(0, min(x1, w))
    cy0 = max(0, min(y0, h))
    cy1 = max(0, min(y1, h))
    return cx0, cy0, cx1, cy1


def roi_sum(buf, w, x0, y0, x1, y1):
    """标准库实现：对 ROI 内像素做 R/G/B 整数累加。

    半开区间 [x0,x1) × [y0,y1)，与硬件 `x0 <= x < x1 && y0 <= y < y1` 一致。
    逐行用切片 + sum()，避免逐字节 Python 循环（快一个量级）。
    """
    cx0, cy0, cx1, cy1 = clamp_roi(x0, y0, x1, y1, w, len(buf) // (w * 3))
    if cx1 <= cx0 or cy1 <= cy0:
        return 0, 0, 0, 0

    sr = sg = sb = 0
    for y in range(cy0, cy1):
        base = 3 * (y * w + cx0)
        end = 3 * (y * w + cx1)
        sr += sum(buf[base + 0:end:3])
        sg += sum(buf[base + 1:end:3])
        sb += sum(buf[base + 2:end:3])

    count = (cx1 - cx0) * (cy1 - cy0)
    return sr, sg, sb, count


def build_cases(w, h):
    """9 个 ROI 用例（前 8 个为契约冻结用例，第 9 个测越界裁剪）。"""
    return [
        ("full_frame",      0,   0,   w,   h),    # 全图基准
        ("center_roi",      w // 4, h // 4, w * 3 // 4, h * 3 // 4),
        ("top_left_1x1",    0,   0,   1,   1),    # 下标 0 的 off-by-one
        ("bottom_right_1x1", w - 1, h - 1, w, h), # 末元素的 off-by-one
        ("single_pixel",    w // 2, h // 2, w // 2 + 1, h // 2 + 1),
        ("empty_roi",       100, 100, 100, 200),  # x0 == x1 -> 空
        ("inverted_roi",    400, 300, 200, 100),  # x0 > x1 -> 空（健壮性）
        ("odd_offset",      3,   5,   w - 3, h - 3),  # 奇数偏移
        ("oversized_roi",   w - 40, h - 20, w + 60, h + 20),  # 越界 -> 裁剪
    ]


def numpy_crosscheck(frames, w, h, cases):
    """若有 numpy，用完全独立的 NumPy 路径复算一遍并与标准库结果对拍。"""
    try:
        import numpy as np
    except ImportError:
        return None

    arr = np.frombuffer(b"".join(f[1] for f in frames), dtype=np.uint8)
    arr = arr.reshape(len(frames), h, w, 3)

    checked = 0
    for case, x0, y0, x1, y1 in cases:
        cx0, cy0, cx1, cy1 = clamp_roi(x0, y0, x1, y1, w, h)
        for fi, (fname, buf) in enumerate(frames):
            ref = roi_sum(buf, w, x0, y0, x1, y1)
            if cx1 <= cx0 or cy1 <= cy0:
                np_ref = (0, 0, 0, 0)
            else:
                sub = arr[fi, cy0:cy1, cx0:cx1, :]
                s = sub.sum(axis=(0, 1))
                np_ref = (int(s[0]), int(s[1]), int(s[2]),
                          (cx1 - cx0) * (cy1 - cy0))
            if tuple(ref) != tuple(np_ref):
                print("!! numpy 对拍失败: case=%s frame=%d  标准库=%s  numpy=%s"
                      % (case, fi, ref, np_ref))
                return False
            checked += 1
    return checked


def main():
    ap = argparse.ArgumentParser(
        description="生成 roi_statistic 测试向量与 Python 黄金参考（契约见 docs/interface.md 第 4 节）")
    ap.add_argument("--width", type=int, default=DEF_W)
    ap.add_argument("--height", type=int, default=DEF_H)
    ap.add_argument("--seed", type=int, default=DEF_SEED)
    ap.add_argument("--seed2", type=int, default=DEF_SEED2)
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "data"))
    args = ap.parse_args()

    w, h = args.width, args.height
    os.makedirs(args.out_dir, exist_ok=True)

    print("== gen_frames: %dx%d, seed=%d/%d ==" % (w, h, args.seed, args.seed2))

    frames = build_frames(w, h, args.seed, args.seed2)
    cases = build_cases(w, h)

    # ---- 写 frames.bin -------------------------------------------------------
    bin_path = os.path.join(args.out_dir, "frames.bin")
    with open(bin_path, "wb") as f:
        for name, buf in frames:
            f.write(buf)
    frame_bytes = w * h * 3
    print("已写 %s  (%d 帧 x %d B = %d B)"
          % (bin_path, len(frames), frame_bytes,
             os.path.getsize(bin_path)))

    # ---- 写 golden_roi.csv ---------------------------------------------------
    csv_path = os.path.join(args.out_dir, "golden_roi.csv")
    rows = 0
    with open(csv_path, "w", newline="\n") as f:
        f.write("# roi_statistic golden reference -- generated by fpga/sim/gen_frames.py\n")
        f.write("# DO NOT EDIT BY HAND; regenerate instead.\n")
        f.write("# meta width=%d height=%d frames=%d seed=%d bytes=%d rgb888_interleaved_rgb\n"
                % (w, h, len(frames), args.seed, frame_bytes))
        f.write("case,frame,x0,y0,x1,y1,sum_r,sum_g,sum_b,count\n")
        for case, x0, y0, x1, y1 in cases:
            for fi, (fname, buf) in enumerate(frames):
                sr, sg, sb, cnt = roi_sum(buf, w, x0, y0, x1, y1)
                f.write("%s,%d,%d,%d,%d,%d,%d,%d,%d,%d\n"
                        % (case, fi, x0, y0, x1, y1, sr, sg, sb, cnt))
                rows += 1
    print("已写 %s  (%d 行 = %d 用例 x %d 帧)"
          % (csv_path, rows, len(cases), len(frames)))

    # ---- NumPy 独立对拍（可选）-----------------------------------------------
    checked = numpy_crosscheck(frames, w, h, cases)
    if checked is None:
        print("[numpy 对拍] SKIPPED —— 当前解释器没有 numpy（不影响黄金参考有效性）")
    elif checked is False:
        print("[numpy 对拍] FAILED —— 标准库与 numpy 结果不一致，请勿使用本次产物！")
        return 1
    else:
        print("[numpy 对拍] PASS —— 标准库与 numpy 两套独立实现完全一致（%d 项）"
              % checked)

    print("== 完成。接下来：vitis-run --mode hls --tcl run_hls.tcl ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
