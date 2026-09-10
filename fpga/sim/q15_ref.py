#!/usr/bin/env python3
# =============================================================================
#  q15_ref.py —— fir_filter 输入序列的 Q1.15 量化参考实现（P0-1，契约 4.6 节）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  契约：docs/interface.md 第 4.6 节（**本节的口径由本文件实现，两者必须一致**）
#
#  为什么需要它：
#    fir_filter 的输入是 int16 Q1.15 样本，但"roi_statistic 的 ROI 累加均值"
#    怎么变成 Q1.15 之前**没有定义**——A 线做不出（也无法与硬件对齐）黄金参考。
#    本文件把这一步冻结成**整数、逐位可复现**的一步，并自带自检。
#
#  冻结口径（契约 4.6 节，整数版为唯一权威）：
#    num[n] = 256 * sum_c[n] - 32768 * count[n]        （int64，c 为选定通道）
#    q[n]   = clip( rha_div(num[n], count[n]), -32768, 32767 )
#    rha_div(a, b) = a >= 0 ?  (2a + b) // (2b)        （四舍五入远离零）
#                           : -((-2a + b) // (2b))      b > 0
#    等价实数式：q[n] = round_half_away( (mean[n] - 128) * 256 ),  mean = sum_c / count
#
#  性质（本文件会逐条自检，全部通过才退出 0）：
#    P1 合法 ROI 均值（0 ~ 255）**永不触发裁剪**：(mean-128)*256 ∈ [-32768, 32512]
#    P2 mean = 128 -> q = 0（把中灰映射到 0，带通因此工作在零点附近）
#    P3 量化台阶 = 1/256 gray level；往返误差 ≤ 1/512 gray
#    P4 count == 0（空 ROI）→ **不产生样本**（契约规定丢弃该帧，不得填 0 或上一帧值）
#    P5 整数版与浮点版在大样本随机数据上一致（不一致会明确报出来，整数版为准）
#
#  用法：
#    python fpga/sim/q15_ref.py --selftest
#    python fpga/sim/q15_ref.py --sums-csv in.csv --channel g --out-csv q15.csv
#      in.csv 列：frame,count,sum_r,sum_g,sum_b（表头任意，按列名找；无表头则按此顺序）
# =============================================================================

import argparse
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

INT16_MIN, INT16_MAX = -32768, 32767
CENTER = 128          # ROI 均值中心（0~255 的中灰）
SCALE = 256           # 32768 / 128：把 [-128, 127] 映射到 Q1.15 满量程
CHANNEL_SLOT = {"r": 2, "g": 3, "b": 4}   # sums-csv 的列位置（1 基，含 frame,count）


def rha_div(a, b):
    """有理数四舍五入（远离零）的整数实现。b > 0。"""
    if b <= 0:
        raise ValueError("count 必须 > 0（空 ROI 的帧应被丢弃，见契约 4.6）")
    if a >= 0:
        return (2 * a + b) // (2 * b)
    return -((-2 * a + b) // (2 * b))


def q15_of_sum(sum_c, count):
    """冻结口径（整数版，唯一权威）。输入为该通道的 ROI 累加值与像素数。"""
    num = SCALE * int(sum_c) - (CENTER * SCALE) * int(count)   # = 256*sum - 32768*count
    q = rha_div(num, int(count))
    if q > INT16_MAX:
        return INT16_MAX
    if q < INT16_MIN:
        return INT16_MIN
    return q


def q15_of_mean(mean):
    """等价实数式（浮点，仅供交叉核对用；整数版为准）。"""
    v = (float(mean) - CENTER) * SCALE
    q = int(math.floor(v + 0.5)) if v >= 0 else -int(math.floor(-v + 0.5))
    return max(INT16_MIN, min(INT16_MAX, q))


def dequant_mean(q):
    """反量化：q -> 均值（gray level）。用于 A 线核对幅度。"""
    return q / float(SCALE) + CENTER


# ---- 自检 -------------------------------------------------------------------
def selftest():
    fails = []

    # P2/P3：手工可核对的关键点
    checks = [
        ((0, 1), -32768),        # 全黑
        ((128, 1), 0),           # 中灰 -> 0
        ((255, 1), 32512),       # 全白 -> +127*256
        ((1, 1), -32512),        # 1 灰阶
        ((257, 2), 128),         # mean=128.5 -> 0.5*256
        ((255, 2), -128),        # mean=127.5 -> -0.5*256
    ]
    for (s, c), exp in checks:
        got = q15_of_sum(s, c)
        if got != exp:
            fails.append("手工点 (%d,%d): got %d exp %d" % (s, c, got, exp))

    # P1：合法 ROI 均值范围内不裁剪，且单调
    prev = None
    for mean_x2 in range(0, 511):       # mean = mean_x2/2，覆盖 0.0~255.0（半灰阶步进）
        s = mean_x2 * 1000
        c = 2000                     # sum/count = mean_x2/2
        q = q15_of_sum(s, c)
        if q < INT16_MIN or q > INT16_MAX:
            fails.append("越界 q=%d @mean=%.1f" % (q, mean_x2 / 2.0))
        if prev is not None and q < prev:
            fails.append("非单调 @mean=%.1f" % (mean_x2 / 2.0))
        prev = q
    q_min = q15_of_sum(0, 1)
    q_max = q15_of_sum(255, 1)
    if (q_min, q_max) != (-32768, 32512):
        fails.append("端点 (%d,%d) != (-32768,32512)" % (q_min, q_max))

    # P3：往返误差
    worst = 0.0
    for mean_x2 in range(0, 511):
        q = q15_of_sum(mean_x2 * 1000, 2000)
        err = abs(dequant_mean(q) - mean_x2 / 2.0)
        worst = max(worst, err)
    if worst > 1.0 / 512 + 1e-12:
        fails.append("往返误差 %.6f > 1/512" % worst)

    # P4：count==0 必须报错（调用方应丢弃该帧）
    try:
        q15_of_sum(0, 0)
        fails.append("count==0 未报错（应拒绝，契约 4.6 要求丢弃该帧）")
    except ValueError:
        pass

    # P5：整数版 vs 浮点版（随机大样本）
    import random
    rng = random.Random(20260911)
    diff = 0
    for _ in range(200000):
        c = rng.randint(1, 307200)
        s = rng.randint(0, 255 * c)
        if q15_of_sum(s, c) != q15_of_mean(s / c):
            diff += 1
    if diff:
        fails.append("整数版与浮点版有 %d 处不一致（整数版为准）" % diff)

    print("== q15_ref 自检 ==")
    print("  P1 不裁剪/单调  : 0.0~255.0 半灰阶步进全覆盖，端点 %d ~ %d" % (q_min, q_max))
    print("  P2 中灰映射     : mean=128 -> 0（带通工作在零点附近）")
    print("  P3 往返误差     : 最大 %.6f gray（<= 1/512 = 0.001953）" % worst)
    print("  P4 空 ROI       : count==0 被拒绝（契约要求丢弃该帧）")
    print("  P5 整数 vs 浮点 : 200000 组随机数，不一致 %d 处" % diff)
    if fails:
        print("== FAIL ==")
        for f in fails:
            print("  - " + f)
        return 1
    print("== PASS ==")
    return 0


# ---- CSV 批量转换（给 A 线用）------------------------------------------------
def convert_csv(in_path, out_path, channel):
    slot = CHANNEL_SLOT[channel]
    rows = []
    with open(in_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if parts[0].lower() in ("frame", "case", "n", "index"):
                continue
            try:
                vals = [int(p) for p in parts]
            except ValueError:
                continue
            if len(vals) < 5:
                continue
            frame, count, sums = vals[0], vals[1], vals[1:5]
            if count <= 0:
                continue                      # 契约：丢弃空 ROI 帧
            s = sums[slot - 1]
            rows.append((frame, count, s, q15_of_sum(s, count)))

    with open(out_path, "w", newline="\n", encoding="utf-8") as f:
        f.write("# q15 series -- generated by fpga/sim/q15_ref.py (contract 4.6, channel=%s)\n" % channel)
        f.write("# q = round_half_away((mean-%d)*%d); count==0 frames are DROPPED\n" % (CENTER, SCALE))
        f.write("frame,count,sum_used,q15\n")
        for frame, count, s, q in rows:
            f.write("%d,%d,%d,%d\n" % (frame, count, s, q))
    print("已写 %s（%d 个有效样本；空 ROI 帧已丢弃）" % (out_path, len(rows)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="fir_filter 输入序列的 Q1.15 量化参考实现")
    ap.add_argument("--selftest", action="store_true", help="跑自检（默认动作）")
    ap.add_argument("--sums-csv", help="输入：每帧 count + sum_r/sum_g/sum_b 的 CSV")
    ap.add_argument("--channel", default="g", choices=list(CHANNEL_SLOT.keys()))
    ap.add_argument("--out-csv", help="输出：frame,count,sum_used,q15")
    args = ap.parse_args()

    if args.sums_csv:
        if not args.out_csv:
            print("ERROR: --sums-csv 需要同时给 --out-csv")
            return 2
        return convert_csv(args.sums_csv, args.out_csv, args.channel)
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
