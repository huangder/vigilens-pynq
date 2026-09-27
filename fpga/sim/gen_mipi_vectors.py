#!/usr/bin/env python3
# =============================================================================
#  gen_mipi_vectors.py —— raw10_unpack / bayer_demosaic 的测试向量与 Python 黄金参考
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  契约：**尚未进入 docs/interface.md** —— 口径提案见 docs/19 §6（待 A/B 会签）
#
#  产出（默认写到本脚本同级的 data_mipi/ 目录）：
#    data_mipi/raw10.bin          RAW10 打包输入（每 5 字节一组 = 4 像素）
#    data_mipi/golden_bayer16.bin 解包黄金参考（每像素 2 字节小端，值域 0..1023）
#                                 = raw10_unpack 的期望输出 = bayer_demosaic 的输入
#    data_mipi/golden_rgb.bin     去马赛克黄金参考（RGB888，每帧 W*H*3 字节）
#    data_mipi/golden_mipi.csv    **入库的黄金参考**：逐帧抽查点（含四角/边界/两种 G 相位）
#    data_mipi/meta.txt           尺寸、帧数、字节数（入库）
#
#  【三条冻结口径，见 fpga/src/raw10_unpack.cpp 与 bayer_demosaic.cpp 头注释】
#    1) RAW10 打包：P0=(b0<<2)|(b4&3) … 第 5 字节装 4 个低 2 位（P0 在最低位）
#    2) Bayer 相位 RGGB：(x%2,y%2)=(0,0)R (1,0)G (0,1)G (1,1)B
#    3) 双线性 + 四舍五入（+2>>2 / +1>>1），边界坐标钳位，末尾 v8=sat8((v10+2)>>2)
#
#  设计原则（沿用 gen_motion_vectors.py）：只用标准库；有 numpy 时用**独立实现**自动对拍。
#
#  用法： python fpga/sim/gen_mipi_vectors.py
# =============================================================================

import argparse
import os
import sys

# 复用同一个 LCG，避免仓库里出现两套伪随机实现（同 seed 必得同产物）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_frames import lcg_bytes  # noqa: E402

DEF_W = 640
DEF_H = 480
DEF_SEED = 20260928

# ---- 冻结口径常量 -----------------------------------------------------------
BAYER_PATTERN = "RGGB"     # 首像素 (0,0) = R


def to8(v10):
    """10 bit -> 8 bit：四舍五入右移 2 位并饱和（仅 1022/1023 触发饱和）。"""
    t = (v10 + 2) >> 2
    return 255 if t > 255 else t


def rand_bayer(w, h, seed):
    """确定性 10 bit Bayer 底图（RGGB 相位本身在下游决定，这里只是 10 bit 值序列）。"""
    raw = lcg_bytes(w * h * 2, seed)
    out = [0] * (w * h)
    for i in range(w * h):
        out[i] = ((raw[2 * i] | (raw[2 * i + 1] << 8)) & 0x3FF)
    return out


def build_bayer_frames(w, h, seed):
    """返回 [(名字, [10bit 值 ...]), ...]；覆盖边界、两种 G 相位与满量程。"""
    n = w * h
    frames = []

    # 帧 0：随机 —— 通用覆盖（也是唯一"每个像素都可能不同"的帧）
    frames.append(("random", rand_bayer(w, h, seed)))

    # 帧 1：全零 —— 输出必须全 0（最容易被"忘了初始化"暴露）
    frames.append(("all_zero", [0] * n))

    # 帧 2：满量程 1023 —— 触发 to8 的饱和路径（(1023+2)>>2 = 256 -> 255）
    frames.append(("all_max", [1023] * n))

    # 帧 3：逐列条纹 —— 让两种 G 相位（奇 x 偶 y / 偶 x 奇 y）都拿到不同的上下左右邻域，
    #        否则"G 位置的 R/B 取错方向"这类 bug 在平坦图上根本显不出来
    stripes = [(1023 if (x & 1) else 0) for y in range(h) for x in range(w)]
    frames.append(("col_stripes", stripes))

    # 帧 4：单点冲激 + 单点冷点 —— 检验插值是否真的把邻居算进去（而不是复制中心值）
    imp = [0] * n
    imp[(h // 2) * w + (w // 2)] = 1023          # 正中心爆点
    imp[(h // 3) * w + (w // 3)] = 0             # 另一个位置保持全 0 邻域
    if (w // 2) + 2 < w:
        imp[(h // 2) * w + (w // 2) + 2] = 1     # 低 2 位非零 -> 同时检验打包的 b4 高/低位
    frames.append(("impulse", imp))

    return frames


def pack_raw10(bayer, w, h):
    """标准库实现：按 MIPI CSI-2 RAW10 打包，行内独立，每行 W/4 个 5 字节组。"""
    if w % 4:
        raise ValueError("width 必须是 4 的整数倍（当前 %d）" % w)
    out = bytearray()
    for y in range(h):
        base = y * w
        for g in range(w // 4):
            p = bayer[base + 4 * g: base + 4 * g + 4]
            out.append((p[0] >> 2) & 0xFF)
            out.append((p[1] >> 2) & 0xFF)
            out.append((p[2] >> 2) & 0xFF)
            out.append((p[3] >> 2) & 0xFF)
            out.append((p[0] & 0x3) | ((p[1] & 0x3) << 2) |
                       ((p[2] & 0x3) << 4) | ((p[3] & 0x3) << 6))
    return bytes(out)


def unpack_raw10(raw, w, h):
    """标准库实现：解包（与 C 侧同一式）。"""
    out = [0] * (w * h)
    k = 0
    groups_per_line = w // 4
    for y in range(h):
        for g in range(groups_per_line):
            o = 5 * (y * groups_per_line + g)
            b0, b1, b2, b3, b4 = raw[o], raw[o + 1], raw[o + 2], raw[o + 3], raw[o + 4]
            out[k + 0] = (b0 << 2) | ((b4 >> 0) & 0x3)
            out[k + 1] = (b1 << 2) | ((b4 >> 2) & 0x3)
            out[k + 2] = (b2 << 2) | ((b4 >> 4) & 0x3)
            out[k + 3] = (b3 << 2) | ((b4 >> 6) & 0x3)
            k += 4
    return out


def demosaic(bayer, w, h):
    """标准库实现：整数双线性 + 坐标钳位 + 四舍五入。返回 bytes（RGB888）。"""
    xm_list = [x - 1 if x > 0 else 0 for x in range(w)]
    xp_list = [x + 1 if x < w - 1 else w - 1 for x in range(w)]
    ym_list = [y - 1 if y > 0 else 0 for y in range(h)]
    yp_list = [y + 1 if y < h - 1 else h - 1 for y in range(h)]

    out = bytearray(w * h * 3)
    for y in range(h):
        row = y * w
        rup, rdn = ym_list[y] * w, yp_list[y] * w
        odd_y = y & 1
        for x in range(w):
            xm, xp = xm_list[x], xp_list[x]
            c = bayer[row + x]
            up = bayer[rup + x]
            dn = bayer[rdn + x]
            lf = bayer[row + xm]
            rt = bayer[row + xp]
            ul = bayer[rup + xm]
            ur = bayer[rup + xp]
            dl = bayer[rdn + xm]
            dr = bayer[rdn + xp]

            if not (x & 1) and not odd_y:            # R 位置
                r, g, b = c, (up + dn + lf + rt + 2) >> 2, (ul + ur + dl + dr + 2) >> 2
            elif (x & 1) and odd_y:                  # B 位置
                r, g, b = (ul + ur + dl + dr + 2) >> 2, (up + dn + lf + rt + 2) >> 2, c
            elif (x & 1) and not odd_y:              # G 位置（Gr）
                r, g, b = (lf + rt + 1) >> 1, c, (up + dn + 1) >> 1
            else:                                    # G 位置（Gb）
                r, g, b = (up + dn + 1) >> 1, c, (lf + rt + 1) >> 1

            o = 3 * (row + x)
            out[o + 0] = to8(r)
            out[o + 1] = to8(g)
            out[o + 2] = to8(b)
    return bytes(out)


def numpy_crosscheck(bayer_frames, gold_bayer, gold_rgb, w, h):
    """有 numpy 时用完全独立的向量化实现复算，并断言与标准库实现一致。"""
    try:
        import numpy as np
    except ImportError:
        return None

    ow = w
    oh = h

    # ---- 1) 打包/解包往返：numpy 独立实现打包，再与标准库解包结果比对 ----
    for i, (_, bayer) in enumerate(bayer_frames):
        a = np.array(bayer, dtype=np.uint16).reshape(h, w)
        g = a.reshape(h, w // 4, 4)
        b0 = (g[..., 0] >> 2).astype(np.uint8)
        b1 = (g[..., 1] >> 2).astype(np.uint8)
        b2 = (g[..., 2] >> 2).astype(np.uint8)
        b3 = (g[..., 3] >> 2).astype(np.uint8)
        b4 = ((g[..., 0] & 3) | ((g[..., 1] & 3) << 2) |
              ((g[..., 2] & 3) << 4) | ((g[..., 3] & 3) << 6)).astype(np.uint8)
        packed = np.stack([b0, b1, b2, b3, b4], axis=-1).astype(np.uint8).tobytes()
        ref = pack_raw10(bayer, w, h)
        if packed != ref:
            print("!! numpy 打包对拍失败 frame=%d" % i)
            return False

    # ---- 2) 解包：numpy 独立实现 ----
    arr = np.frombuffer(b"".join(gold_bayer), dtype="<u2").reshape(len(gold_bayer), h, w)
    for i, (_, bayer) in enumerate(bayer_frames):
        if not np.array_equal(arr[i].astype(np.int32), np.array(bayer).reshape(h, w)):
            print("!! numpy 解包对拍失败 frame=%d" % i)
            return False

    # ---- 3) 去马赛克：numpy 用 edge 填充 + 四类相位掩码，独立复算 ----
    rgb = np.frombuffer(b"".join(gold_rgb), dtype=np.uint8).reshape(len(gold_rgb), h, w, 3)
    a = np.frombuffer(b"".join(gold_bayer), dtype="<u2").reshape(len(gold_bayer), h, w).astype(np.int32)
    pad = np.pad(a, ((0, 0), (1, 1), (1, 1)), mode="edge")
    up = pad[:, 0:h, 1:w + 1]
    dn = pad[:, 2:h + 2, 1:w + 1]
    lf = pad[:, 1:h + 1, 0:w]
    rt = pad[:, 1:h + 1, 2:w + 2]
    ul = pad[:, 0:h, 0:w]
    ur = pad[:, 0:h, 2:w + 2]
    dl = pad[:, 2:h + 2, 0:w]
    dr = pad[:, 2:h + 2, 2:w + 2]

    g4 = (up + dn + lf + rt + 2) >> 2
    d4 = (ul + ur + dl + dr + 2) >> 2

    xs = np.arange(w)
    ys = np.arange(h)
    odd_x = (xs & 1).astype(bool)[None, None, :]
    odd_y = (ys & 1).astype(bool)[None, :, None]

    R = np.where(~odd_x & ~odd_y, a,
        np.where(odd_x & odd_y, d4,
        np.where(odd_x & ~odd_y, (lf + rt + 1) >> 1, (up + dn + 1) >> 1)))
    G = np.where(~odd_x & ~odd_y, g4,
        np.where(odd_x & odd_y, g4,
        np.where(odd_x & ~odd_y, a, a)))
    # B 与 R 的取法在四类相位上正好互换
    B = np.where(~odd_x & ~odd_y, d4,
        np.where(odd_x & odd_y, a,
        np.where(odd_x & ~odd_y, (up + dn + 1) >> 1, (lf + rt + 1) >> 1)))

    def to8_np(v):
        return np.minimum(255, (v + 2) >> 2).astype(np.uint8)

    np_rgb = np.stack([to8_np(R), to8_np(G), to8_np(B)], axis=-1)
    if not np.array_equal(np_rgb, rgb):
        bad = int((np_rgb != rgb).sum())
        print("!! numpy 去马赛克对拍失败，不一致字节 %d" % bad)
        return False

    return len(gold_rgb)


def spot_points(w, h):
    """入库 CSV 的抽查点：四角 + 边界 + 两种 G 相位 + 中心。"""
    return [
        (0, 0), (1, 0), (0, 1), (1, 1),
        (2, 2), (3, 2), (2, 3), (3, 3),
        (w // 2, h // 2), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
    ]


def main():
    ap = argparse.ArgumentParser(description="生成 raw10_unpack / bayer_demosaic 的向量与黄金参考")
    ap.add_argument("--width", type=int, default=DEF_W)
    ap.add_argument("--height", type=int, default=DEF_H)
    ap.add_argument("--seed", type=int, default=DEF_SEED)
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "data_mipi"))
    args = ap.parse_args()

    w, h = args.width, args.height
    if w % 4:
        print("!! width 必须是 4 的整数倍（打包口径要求），当前 %d" % w)
        return 1
    if h < 2:
        print("!! height 必须 >= 2（去马赛克需要上/下邻域可钳位），当前 %d" % h)
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    print("== gen_mipi_vectors: %dx%d, seed=%d, bayer=%s ==" % (w, h, args.seed, BAYER_PATTERN))

    frames = build_bayer_frames(w, h, args.seed)

    raws, gold_bayer, gold_rgb = [], [], []
    for name, bayer in frames:
        raw = pack_raw10(bayer, w, h)
        rb = unpack_raw10(raw, w, h)
        if rb != bayer:
            print("!! 打包/解包往返失败 frame=%s（说明两侧口径不一致）" % name)
            return 1
        raws.append(raw)
        gold_bayer.append(b"".join(v.to_bytes(2, "little") for v in rb))
        gold_rgb.append(demosaic(rb, w, h))
        print("   frame %-12s raw10=%d B  bayer16=%d B  rgb=%d B"
              % (name, len(raw), len(gold_bayer[-1]), len(gold_rgb[-1])))

    # ---- 写三个 .bin（体积大 -> .gitignore 不入库，可由本脚本确定性重建）----
    p = os.path.join(args.out_dir, "raw10.bin")
    with open(p, "wb") as f:
        for b in raws:
            f.write(b)
    print("已写 %s  (%d 帧 x %d B = %d B)" % (p, len(raws), w * h * 5 // 4, os.path.getsize(p)))

    p = os.path.join(args.out_dir, "golden_bayer16.bin")
    with open(p, "wb") as f:
        for b in gold_bayer:
            f.write(b)
    print("已写 %s  (%d 帧 x %d B = %d B)" % (p, len(gold_bayer), w * h * 2, os.path.getsize(p)))

    p = os.path.join(args.out_dir, "golden_rgb.bin")
    with open(p, "wb") as f:
        for b in gold_rgb:
            f.write(b)
    print("已写 %s  (%d 帧 x %d B = %d B)" % (p, len(gold_rgb), w * h * 3, os.path.getsize(p)))

    # ---- 写入库的黄金参考 CSV（逐帧抽查点，人工可评审）----
    pts = spot_points(w, h)
    p = os.path.join(args.out_dir, "golden_mipi.csv")
    rows = 0
    with open(p, "w", newline="\n") as f:
        f.write("# raw10_unpack + bayer_demosaic golden reference\n")
        f.write("# generated by fpga/sim/gen_mipi_vectors.py -- DO NOT EDIT BY HAND; regenerate instead.\n")
        f.write("# raw10 packing: P0=(b0<<2)|(b4&3) ... b4 carries four 2-bit LSBs (P0 lowest)\n")
        f.write("# bayer phase   : %s ((0,0)=R (1,0)=G (0,1)=G (1,1)=B)\n" % BAYER_PATTERN)
        f.write("# demosaic      : integer bilinear, round-half-up ((+2)>>2 / (+1)>>1), index CLAMP at borders\n")
        f.write("# to8           : v8 = min(255, (v10+2)>>2)\n")
        f.write("# columns: frame,name,x,y,bayer10,r8,g8,b8   (RGB order, NOT BGR)\n")
        f.write("frame,name,x,y,bayer10,r8,g8,b8\n")
        for i, (name, bayer) in enumerate(frames):
            rgb = gold_rgb[i]
            for (x, y) in pts:
                o = 3 * (y * w + x)
                f.write("%d,%s,%d,%d,%d,%d,%d,%d\n"
                        % (i, name, x, y, bayer[y * w + x], rgb[o], rgb[o + 1], rgb[o + 2]))
                rows += 1
    print("已写 %s  (%d 行抽查点, 每帧 %d 点)" % (p, rows, len(pts)))

    # ---- meta.txt（入库）----
    p = os.path.join(args.out_dir, "meta.txt")
    with open(p, "w", newline="\n") as f:
        f.write("in_width=%d\n" % w)
        f.write("in_height=%d\n" % h)
        f.write("out_width=%d\n" % w)
        f.write("out_height=%d\n" % h)
        f.write("frames=%d\n" % len(frames))
        f.write("raw10_bytes=%d\n" % (w * h * 5 // 4))
        f.write("bayer16_bytes=%d\n" % (w * h * 2))
        f.write("rgb_bytes=%d\n" % (w * h * 3))
        f.write("bayer_pattern=%s\n" % BAYER_PATTERN)
        f.write("seed=%d\n" % args.seed)
        f.write("frame_names=%s\n" % ",".join(n for n, _ in frames))
    print("已写 %s" % p)

    # ---- 人工可核对的小样本自检（不依赖任何外部库）----
    print("-- 口径自检（人工可核对）--")
    ok = True
    checks = [
        # (10bit 输入, 期望 8bit)  -> 四舍五入右移 2 位，1022/1023 饱和
        (0, 0), (1, 0), (2, 1), (3, 1), (4, 1), (6, 2), (1020, 255), (1021, 255),
        (1022, 255), (1023, 255),
    ]
    for v10, exp in checks:
        got = to8(v10)
        flag = "OK " if got == exp else "BAD"
        if got != exp:
            ok = False
        print("   %s to8(%-5d) = %3d (期望 %3d)" % (flag, v10, got, exp))

    # 打包口径：4 个像素 (0,1,2,1023) 应打成 00 00 00 FF E4
    probe = [0, 1, 2, 1023]
    packed = pack_raw10(probe, 4, 1)
    exp_packed = bytes([0x00, 0x00, 0x00, 0xFF,
                        (0 & 3) | ((1 & 3) << 2) | ((2 & 3) << 4) | ((3 & 3) << 6)])
    print("   %s RAW10 打包 (0,1,2,1023) -> %s (期望 %s)"
          % ("OK " if packed == exp_packed else "BAD ",
             packed.hex(" "), exp_packed.hex(" ")))
    if packed != exp_packed:
        ok = False

    # 去马赛克口径：2x2 单色场 R=G=B 常数 -> 输出应恒等于 to8(常数)
    for v in (0, 511, 1023):
        rgb = demosaic([v] * 4, 2, 2)
        exp = to8(v)
        good = all(rgb[k] == exp for k in range(12))
        print("   %s 平坦场 %4d -> 输出恒为 %3d" % ("OK " if good else "BAD", v, exp))
        if not good:
            ok = False

    if not ok:
        print("!! 口径自检失败")
        return 1

    # ---- NumPy 独立对拍 ----
    checked = numpy_crosscheck(frames, gold_bayer, gold_rgb, w, h)
    if checked is None:
        print("[numpy 对拍] SKIPPED —— 当前解释器没有 numpy")
    elif checked is False:
        print("[numpy 对拍] FAILED —— 请勿使用本次产物！")
        return 1
    else:
        print("[numpy 对拍] PASS —— 打包/解包/去马赛克三项均与 numpy 独立实现一致（%d 帧）" % checked)

    print("== 完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
