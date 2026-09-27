#!/usr/bin/env python3
# =============================================================================
#  gen_scale_vectors.py —— frame_scale 的测试向量与 Python 黄金参考
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  契约：**尚未进入 docs/interface.md** —— 提案见 docs/20 §5（待 A/B 会签）
#
#  产出（默认写到本脚本同级的 data_scale/ 目录）：
#    data_scale/rgb_in.bin        输入 RGB888 1280x720（5 帧）
#    data_scale/golden_scale.bin  缩放黄金参考 RGB888 640x480（5 帧）
#    data_scale/golden_scale.csv  **入库**：逐帧抽查点（含"被丢掉的列/行"必须不出现）
#    data_scale/meta.txt          参数与尺寸（入库）
#
#  【冻结口径：双轴 3:2 抽取 + 横向中心裁剪】（与 frame_scale.cpp 头注释同一式）
#    横向：只取 x ∈ [crop_x0, crop_x0+crop_w)，且 (x-crop_x0) % 3 < 2
#    纵向：只取 y，且 y % 3 < 2
#    1280x720, crop_x0=160, crop_w=960  ->  640x480（两个方向都是 3:2 ⇒ 不失真）
#    正向映射：输入 (x,y) -> 输出 ( (x-crop_x0) 的组内序号 , y 的组内序号 )
#    反向映射（本脚本与主机模型都用它做独立复算）：
#        输出 (ox,oy)  <=  输入 ( crop_x0 + ox + ox//2 , oy + oy//2 )
#
#  设计原则：只用标准库；有 numpy 时用**独立实现**（花式索引）自动对拍。
#
#  用法： python fpga/sim/gen_scale_vectors.py
# =============================================================================

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
try:
    from console import enable_utf8_console  # type: ignore
    enable_utf8_console()
except Exception:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_frames import lcg_bytes  # noqa: E402

DEF_W = 1280          # 传感器 720p
DEF_H = 720
DEF_CROP_X0 = 160     # (1280-960)/2 —— 中心裁出 4:3
DEF_CROP_W = 960      # 4:3 窗口；960/3*2 = 640
DEF_SEED = 20260929

NUM = 2               # 每 DEN 个采样保留前 NUM 个
DEN = 3


def out_dims(crop_w, h):
    if crop_w % DEN or h % DEN:
        raise ValueError("crop_w 与 height 都必须是 %d 的整数倍（当前 %d, %d）" % (DEN, crop_w, h))
    return crop_w // DEN * NUM, h // DEN * NUM


def kept_k(crop_w):
    """裁剪窗口内的组内序号 k：保留 k % 3 < 2 —— 640 个。"""
    return [k for k in range(crop_w) if (k % DEN) < NUM]


def kept_y(h):
    """保留的行号 y：y % 3 < 2 —— 480 个。"""
    return [y for y in range(h) if (y % DEN) < NUM]


def build_frames(w, h):
    """返回 [(名字, bytes), ...]；每帧 w*h*3 字节，通道顺序 R,G,B。"""
    n = w * h * 3
    frames = []

    # 帧 0：随机 —— 通用覆盖
    frames.append(("random", lcg_bytes(n, DEF_SEED)))

    # 帧 1/2：全零 / 全满 —— 最容易被"忘了初始化"或"裁剪越界"暴露
    frames.append(("all_zero", bytes(n)))
    frames.append(("all_full", b"\xff" * n))

    # 帧 3：斜坡 —— **相位探针**。R 随列变、G 随行变、B 随行列和变：
    #        任何"抽错了第几列/第几行"或 off-by-one 都会立刻显形。
    ramp = bytearray(n)
    for y in range(h):
        base = y * w * 3
        g = y & 0xFF
        for x in range(w):
            o = base + 3 * x
            ramp[o] = x & 0xFF
            ramp[o + 1] = g
            ramp[o + 2] = (x * 3 + y * 5) & 0xFF
    frames.append(("ramp", bytes(ramp)))

    # 帧 4：双冲激 —— 一个落在**保留**位置、一个落在**被丢弃**位置。
    #        黄金输出里必须出现前者、且**任何位置都不得出现**后者。
    imp = bytearray(n)
    keep_x = DEF_CROP_X0 + 0     # k=0 -> 保留
    drop_x = DEF_CROP_X0 + 2     # k=2 -> 丢弃
    for (x, y) in ((keep_x, 0), (drop_x, 2)):
        o = 3 * (y * w + x)
        imp[o:o + 3] = b"\xff\xff\xff"
    frames.append(("impulse_keep_and_drop", bytes(imp)))

    return frames


def scale_ref(rgb, w, h, crop_x0, crop_w):
    """标准库实现：用**反向映射**逐输出像素取源像素（与正向扫描是两条不同路径）。"""
    ow, oh = out_dims(crop_w, h)
    out = bytearray(ow * oh * 3)
    k = 0
    for oy in range(oh):
        sy = oy + oy // 2                      # 反向映射：oy -> 源行
        sbase = sy * w * 3
        for ox in range(ow):
            sx = crop_x0 + ox + ox // 2        # 反向映射：ox -> 源列
            o = sbase + 3 * sx
            out[k + 0] = rgb[o + 0]
            out[k + 1] = rgb[o + 1]
            out[k + 2] = rgb[o + 2]
            k += 3
    return bytes(out), ow, oh


def numpy_crosscheck(frames, gold, w, h, crop_x0, crop_w):
    """有 numpy 时用完全独立的向量化实现（花式索引）复算，并断言一致。"""
    try:
        import numpy as np
    except ImportError:
        return None

    ks = np.array(kept_k(crop_w))
    ys = np.array(kept_y(h))
    sx = crop_x0 + ks
    arr_all = np.frombuffer(b"".join(frames), dtype=np.uint8).reshape(len(frames), h, w, 3)
    ref = arr_all[:, ys][:, :, sx]                 # (N, oh, ow, 3) —— 独立路径
    got = np.frombuffer(b"".join(gold), dtype=np.uint8).reshape(len(gold), -1, 3).reshape(ref.shape)
    if not np.array_equal(ref, got):
        bad = int((ref != got).sum())
        print("!! numpy 缩放对拍失败，不一致字节 %d" % bad)
        return False
    return len(gold)


def main():
    ap = argparse.ArgumentParser(description="生成 frame_scale 的向量与黄金参考")
    ap.add_argument("--width", type=int, default=DEF_W)
    ap.add_argument("--height", type=int, default=DEF_H)
    ap.add_argument("--crop-x0", type=int, default=DEF_CROP_X0)
    ap.add_argument("--crop-w", type=int, default=DEF_CROP_W)
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "data_scale"))
    args = ap.parse_args()

    w, h, x0, cw = args.width, args.height, args.crop_x0, args.crop_w
    if cw % DEN or h % DEN:
        print("!! crop_w 与 height 必须是 %d 的整数倍" % DEN)
        return 1
    if x0 < 0 or x0 + cw > w:
        print("!! 裁剪窗口越界：crop_x0=%d + crop_w=%d > width=%d" % (x0, cw, w))
        return 1
    ow, oh = out_dims(cw, h)
    if ow * 3 != cw * 2 or oh * 3 != h * 2:
        print("!! 非 3:2 关系，几何会失真")
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    print("== gen_scale_vectors: in %dx%d, crop_x0=%d crop_w=%d -> out %dx%d (双轴 %d/%d) =="
          % (w, h, x0, cw, ow, oh, NUM, DEN))
    print("   横向覆盖传感器 %.0f%% 视场，纵向 100%%；两轴比例相同 ⇒ 无几何畸变"
          % (cw / w * 100.0))

    frames = build_frames(w, h)
    gold = []
    for name, rgb in frames:
        g, gw, gh = scale_ref(rgb, w, h, x0, cw)
        if (gw, gh) != (ow, oh):
            print("!! 输出尺寸不一致")
            return 1
        gold.append(g)
        print("   frame %-24s in=%d B  out=%d B" % (name, len(rgb), len(g)))

    p = os.path.join(args.out_dir, "rgb_in.bin")
    with open(p, "wb") as f:
        for b in frames:
            f.write(b[1])
    print("已写 %s  (%d 帧 x %d B = %d B)" % (p, len(frames), w * h * 3, os.path.getsize(p)))

    p = os.path.join(args.out_dir, "golden_scale.bin")
    with open(p, "wb") as f:
        for b in gold:
            f.write(b)
    print("已写 %s  (%d 帧 x %d B = %d B)" % (p, len(gold), ow * oh * 3, os.path.getsize(p)))

    # ---- 入库 CSV：抽查点（含反向映射的四角与"被丢弃位置"的邻居）----
    pts = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 2), (3, 2), (2, 3),
           (ow // 2, oh // 2), (ow - 1, 0), (0, oh - 1), (ow - 1, oh - 1)]
    p = os.path.join(args.out_dir, "golden_scale.csv")
    rows = 0
    with open(p, "w", newline="\n") as f:
        f.write("# frame_scale golden reference\n")
        f.write("# generated by fpga/sim/gen_scale_vectors.py -- DO NOT EDIT BY HAND\n")
        f.write("# rule : keep x in [crop_x0, crop_x0+crop_w) with (x-crop_x0)%%3<2 ; keep y with y%%3<2\n")
        f.write("# inverse map: out(ox,oy) <- in(crop_x0 + ox + ox//2, oy + oy//2)\n")
        f.write("# columns: frame,name,ox,oy,sx,sy,r8,g8,b8   (RGB order, NOT BGR)\n")
        f.write("frame,name,ox,oy,sx,sy,r8,g8,b8\n")
        for i, (name, _) in enumerate(frames):
            g = gold[i]
            for (ox, oy) in pts:
                o = 3 * (oy * ow + ox)
                # 源坐标：注意 CSV 里同时给出，便于人工核对反向映射
                sx = x0 + ox + ox // 2
                sy = oy + oy // 2
                f.write("%d,%s,%d,%d,%d,%d,%d,%d,%d\n"
                        % (i, name, ox, oy, sx, sy, g[o], g[o + 1], g[o + 2]))
                rows += 1
    print("已写 %s  (%d 行抽查点)" % (p, rows))

    p = os.path.join(args.out_dir, "meta.txt")
    with open(p, "w", newline="\n") as f:
        f.write("in_width=%d\n" % w)
        f.write("in_height=%d\n" % h)
        f.write("crop_x0=%d\n" % x0)
        f.write("crop_w=%d\n" % cw)
        f.write("out_width=%d\n" % ow)
        f.write("out_height=%d\n" % oh)
        f.write("frames=%d\n" % len(frames))
        f.write("rgb_in_bytes=%d\n" % (w * h * 3))
        f.write("rgb_out_bytes=%d\n" % (ow * oh * 3))
        f.write("decim=%d/%d\n" % (NUM, DEN))
        f.write("frame_names=%s\n" % ",".join(n for n, _ in frames))
    print("已写 %s" % p)

    # ---- 人工可核对的自检 ----
    print("-- 口径自检（人工可核对）--")
    ok = True
    # (a) 反向映射的前几个：ox -> sx 应为 0,1,3,4,6,7（加上 crop_x0）
    seq = [x0 + ox + ox // 2 for ox in range(8)]
    exp = [x0 + v for v in (0, 1, 3, 4, 6, 7, 9, 10)]
    print("   %s 反向映射前 8 列 -> %s (期望 %s)"
          % ("OK " if seq == exp else "BAD", seq, exp))
    if seq != exp:
        ok = False
    # (b) 被丢弃的位置绝不能出现在输出里（用冲激帧）
    imp = frames[-1][1]
    g = gold[-1]
    drop_px = (x0 + 2, 2)
    present = any(g[o:o + 3] == b"\xff\xff\xff" for o in range(0, len(g), 3))
    # 该帧只有两个白点；若"被丢弃位置"也漏进输出，就会有 2 个白点
    whites = sum(1 for o in range(0, len(g), 3) if g[o:o + 3] == b"\xff\xff\xff")
    print("   %s 冲激帧：输入 2 个白点（1 保留 + 1 丢弃）-> 输出白点数 = %d（期望 1）"
          % ("OK " if whites == 1 else "BAD", whites))
    if whites != 1:
        ok = False
    # (c) 输出尺寸
    print("   %s 输出尺寸 %dx%d = %d 像素（期望 640x480）"
          % ("OK " if (ow, oh) == (640, 480) else "注意", ow, oh, ow * oh))
    if not ok:
        print("!! 口径自检失败")
        return 1

    checked = numpy_crosscheck([b for _, b in frames], gold, w, h, x0, cw)
    if checked is None:
        print("[numpy 对拍] SKIPPED —— 当前解释器没有 numpy")
    elif checked is False:
        print("[numpy 对拍] FAILED —— 请勿使用本次产物！")
        return 1
    else:
        print("[numpy 对拍] PASS —— 缩放与 numpy 花式索引独立实现一致（%d 帧）" % checked)

    print("== 完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
