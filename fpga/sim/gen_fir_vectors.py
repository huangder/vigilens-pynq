#!/usr/bin/env python3
# =============================================================================
#  gen_fir_vectors.py —— fir_filter 的测试向量与 Python 黄金参考（C5）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  契约：docs/interface.md 第 3.5 / 4.5 节
#
#  产出（默认写到本脚本同级的 data_fir/ 目录）：
#    data_fir/series.bin         输入样本（int16 小端，按段顺序拼接；**不入库，可由 seed 重建**）
#    data_fir/golden_fir.csv     每段一行的小结（case, seg_id, n_samples, reset, sat_count, out_count）
#    data_fir/golden_fir_out.csv **逐样本**期望输出（入库、可人工评审；3 列：seg_id, index, y）
#    data_fir/meta.txt           采样率/阶数/系数移位/带通口径/段数/字节数
#
#  【为什么值文件是 CSV 而不是 bin】
#    golden 结果按《02》A10 要求"入库且不得被后续改动破坏"，而 .bin 按仓库约定不入库。
#    所以期望输出用文本 CSV 入库（可 diff、可评审），输入样本用 .bin（可用 seed 精确重建）。
#
#  【系数只有一处来源】
#    本脚本**解析 fpga/src/fir_coeffs_q15.h**（由 design_fir_coeffs.py 生成）拿系数，
#    而不是另抄一份 —— 避免"C 与 Python 各有一份系数、改了一边"的经典事故。
#
#  【黄金参考的运算约定（必须与 fir_filter.cpp 逐字一致）】
#    acc = Σ h[k]*x[n-k]      （Python 大整数 / C 的 int32，均无中间舍入）
#    y   = acc >> 15          （算术右移 = 对负数向下取整；**不是** /32768 的向零取整）
#    y   = 饱和到 [-32768, 32767]，饱和样本计入 sat_count
#
#  设计原则：只用标准库（系统 Python 与项目 .venv 都能跑）；有 numpy 时自动独立对拍。
#
#  用法：
#    python fpga/sim/gen_fir_vectors.py
#    python fpga/sim/gen_fir_vectors.py --out-dir fpga/sim/data_fir_small --scale 0.25
# =============================================================================

import argparse
import math
import os
import re
import sys

# 允许复用仓库既有的 LCG（与 gen_frames.py 同一套伪随机，避免"两套随机数"）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_frames import lcg_bytes  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_HEADER = os.path.join(HERE, "..", "src", "fir_coeffs_q15.h")
DEF_OUT = os.path.join(HERE, "data_fir")
DEF_SEED = 20260910

INT16_MIN, INT16_MAX = -32768, 32767
DEF_FS = 30.0


# ---- 解析冻结系数表 ---------------------------------------------------------
def parse_coeff_header(path):
    """从 fir_coeffs_q15.h 读出 taps/shift/fs/band/系数。"""
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()

    def num(name):
        m = re.search(r"#define\s+%s\s+([-\d.]+)" % name, txt)
        if not m:
            raise ValueError("头文件里找不到 #define %s" % name)
        return float(m.group(1))

    m = re.search(r"FIR_COEFF_Q15\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", txt, re.S)
    if not m:
        raise ValueError("头文件里找不到 FIR_COEFF_Q15 数组")
    coeffs = [int(v) for v in re.findall(r"-?\d+", m.group(1))]

    taps = int(num("FIR_NUM_TAPS"))
    if len(coeffs) != taps:
        raise ValueError("系数个数 %d != FIR_NUM_TAPS %d" % (len(coeffs), taps))

    meta = dict(
        taps=taps,
        shift=int(num("FIR_COEFF_SHIFT")),
        fs=num("FIR_FS_HZ"),
        f1=num("FIR_F1_HZ"),
        f2=num("FIR_F2_HZ"),
        coeffs=coeffs,
    )
    # 对称性自检：I 型线性相位必须偶对称，否则"对称折叠"这个硬件优化就是错的
    if any(coeffs[k] != coeffs[taps - 1 - k] for k in range(taps)):
        raise ValueError("系数不是偶对称 —— 不能用对称折叠结构（硬件实现前提被破坏）")
    return meta


# ---- 冻结的黄金参考（精确整数）----------------------------------------------
def fir_segment(x, h, shift, hist):
    """对一段样本做滤波。

    hist：长度 (N-1) 的历史，hist[0] = 上一段的最后一个样本（x[n-1]），hist[-1] = x[n-(N-1)]。
          就地更新并返回（段间状态保持语义）。reset 由调用方通过传全零 hist 实现。
    返回 (out_list, sat_count)。
    """
    n = len(h)
    out = []
    sat = 0
    for v in x:
        curbuf = [v] + hist           # 长度 N：curbuf[k] = x[n-k]
        acc = 0
        for k in range(n):
            acc += h[k] * curbuf[k]
        y = acc >> shift              # Python 的 >> 对负数 = 向下取整，与 C 一致
        if y > INT16_MAX:
            y = INT16_MAX
            sat += 1
        elif y < INT16_MIN:
            y = INT16_MIN
            sat += 1
        out.append(y)
        hist[:] = curbuf[:n - 1]      # 更新历史（丢掉最老的）
    return out, sat


# ---- 测试序列构造（确定性）--------------------------------------------------
def sv(v):
    """夹到 int16。"""
    return max(INT16_MIN, min(INT16_MAX, int(v)))


def sine(n, fs, f, amp, phase=0.0):
    """整数化正弦（四舍五入远离零），确定性。"""
    out = []
    for i in range(n):
        v = amp * math.sin(2.0 * math.pi * f * i / fs + phase)
        out.append(sv(math.floor(v + 0.5) if v >= 0 else -math.floor(-v + 0.5)))
    return out


def rand_i16(n, seed):
    """复用仓库的 LCG（lcg_bytes），把字节流解释成 int16 满幅随机样本。"""
    b = lcg_bytes(2 * n, seed)
    return [sv(int.from_bytes(b[2 * i:2 * i + 2], "little", signed=True)) for i in range(n)]


def build_cases(scale):
    """返回 [(case, samples, reset), ...]，按"段顺序"排列。

    scale：小向量模式下的样本数缩放（cosim 用），保持段结构不变。
    """
    def nlen(v):
        return max(8, int(v * scale))

    cases = []
    # 1) 单位脉冲：输出即滤波器冲激响应（可检验系数顺序、群延迟 31、偶对称）
    imp = [0] * nlen(96)
    imp[0] = INT16_MAX
    cases.append(("impulse", imp, 1))

    # 2) 直流（正/负满幅）：带通应抑制 DC（Σh 极小），负满幅还压到饱和边界附近
    cases.append(("dc_pos", [INT16_MAX] * nlen(240), 1))
    cases.append(("dc_neg", [INT16_MIN] * nlen(240), 1))

    # 3) 奈奎斯特（逐样本交替满幅）：高阻带被抑制
    cases.append(("nyquist_alt", [INT16_MAX if i % 2 == 0 else INT16_MIN
                                  for i in range(nlen(240))], 1))

    # 4) 单频正弦：通带内（1.5 Hz）、下边缘（0.8 Hz）、上边缘（3.2 Hz）、
    #    呼吸/漂移带（0.2 Hz）、高阻带（8 Hz）
    cases.append(("sine_1p5hz", sine(nlen(300), DEF_FS, 1.5, 20000), 1))
    cases.append(("sine_0p8hz", sine(nlen(300), DEF_FS, 0.8, 20000), 1))
    cases.append(("sine_3p2hz", sine(nlen(300), DEF_FS, 3.2, 20000), 1))
    cases.append(("sine_0p2hz", sine(nlen(300), DEF_FS, 0.2, 20000), 1))
    cases.append(("sine_8hz", sine(nlen(300), DEF_FS, 8.0, 20000), 1))

    # 5) 满幅 1 Hz 方波：通带内的强信号 -> 必然触发饱和（检验饱和与计数）
    sq = [INT16_MAX if (i * 1 * 2 // int(DEF_FS)) % 2 == 0 else INT16_MIN
          for i in range(nlen(300))]
    cases.append(("square_1hz_full", sq, 1))

    # 6) 小幅度正弦（A=100）：不应饱和（sat_count 必须为 0）
    cases.append(("sine_small_amp", sine(nlen(300), DEF_FS, 1.5, 100), 1))

    # 7) 随机满幅：通用覆盖
    rnd = rand_i16(nlen(512), DEF_SEED)
    cases.append(("random_full", rnd, 1))

    # 8) 段间状态保持：同一段随机数据切成 3 段（第一段 reset=1，后两段 reset=0），
    #    三段连起来的结果必须与"一次调用"逐样本相同
    a, b, c = nlen(200), nlen(200), nlen(200)
    cases.append(("split_part1", rnd[:a], 1))
    cases.append(("split_part2", rnd[a:a + b], 0))
    cases.append(("split_part3", rnd[a + b:a + b + c], 0))

    return cases


# ---- numpy 独立对拍 ---------------------------------------------------------
def numpy_crosscheck(cases, outs, h, shift):
    """用完全独立的路径（np.convolve + 插入零历史）复算，并断言逐样本一致。"""
    try:
        import numpy as np
    except ImportError:
        return None

    n = len(h)
    # 构造"扩展输入"：每个 reset=1 的段前插 N-1 个零（等价于清空延迟线）。
    # 同时记下"真实样本"在扩展数组里的下标，比对时只取这些位置。
    ext = []
    out_mine = []
    real_idx = []
    for (case, x, rst), y in zip(cases, outs):
        if rst:
            ext.extend([0] * (n - 1))
        real_idx.extend(range(len(ext), len(ext) + len(x)))
        ext.extend(x)
        out_mine.extend(y)

    ext = np.array(ext, dtype=np.int64)
    hh = np.array(h, dtype=np.int64)
    conv = np.convolve(ext, hh)          # 全精度 int64 卷积
    y_all = conv[:len(ext)] >> shift     # 算术右移（numpy 对负数同样是向下取整）
    y_np = np.clip(y_all[np.array(real_idx, dtype=np.int64)], INT16_MIN, INT16_MAX)

    mine = np.array(out_mine, dtype=np.int64)
    if not np.array_equal(y_np, mine):
        bad = np.nonzero(y_np != mine)[0]
        print("!! numpy 对拍失败：不一致样本 %d 个，首个下标 %d（mine=%d numpy=%d）"
              % (len(bad), bad[0], mine[bad[0]], y_np[bad[0]]))
        return False
    return len(mine)


# ---- 幅度测量（用整数实现实测，不是只看浮点响应）----------------------------
def measured_gain_db(x, y, skip):
    """稳态段的 RMS 增益（dB）——skip 掉启动瞬态。"""
    xr = x[skip:]
    yr = y[skip:]
    if not xr:
        return None
    sx = math.sqrt(sum(float(v) * v for v in xr) / len(xr))
    sy = math.sqrt(sum(float(v) * v for v in yr) / len(yr))
    if sx < 1e-9:
        return None
    if sy < 1e-9:
        return -999.0
    return 20.0 * math.log10(sy / sx)


def main():
    ap = argparse.ArgumentParser(description="生成 fir_filter 的测试向量与 Python 黄金参考")
    ap.add_argument("--header", default=DEF_HEADER)
    ap.add_argument("--out-dir", default=DEF_OUT)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="样本数缩放（cosim 用小向量时用 0.25 之类；段结构不变）")
    args = ap.parse_args()

    meta = parse_coeff_header(os.path.abspath(args.header))
    h, shift, fs, taps = meta["coeffs"], meta["shift"], meta["fs"], meta["taps"]
    print("== gen_fir_vectors: taps=%d shift=%d fs=%.0f Hz band=%.2f~%.2f Hz (scale=%.2f) =="
          % (taps, shift, fs, meta["f1"], meta["f2"], args.scale))
    print("   系数来源：%s" % os.path.abspath(args.header))
    print("   系数：偶对称 ✅  Σh=%d  Σ|h|=%d  |acc|上界=%d < 2^31=%s"
          % (sum(h), sum(abs(v) for v in h), 32768 * sum(abs(v) for v in h),
             32768 * sum(abs(v) for v in h) < 2 ** 31))

    cases = build_cases(args.scale)
    os.makedirs(args.out_dir, exist_ok=True)

    hist = [0] * (taps - 1)
    outs = []
    rows = []
    for case, x, rst in cases:
        if rst:
            hist = [0] * (taps - 1)
        y, sat = fir_segment(x, h, shift, hist)
        outs.append(y)
        rows.append((case, len(x), rst, sat))

    # ---- series.bin（int16 小端）----
    p_series = os.path.join(args.out_dir, "series.bin")
    with open(p_series, "wb") as f:
        for _, x, _ in cases:
            f.write(b"".join(int(v).to_bytes(2, "little", signed=True) for v in x))
    print("已写 %s  (%d 段 / %d 样本 / %d B)"
          % (p_series, len(cases), sum(len(x) for _, x, _ in cases),
             os.path.getsize(p_series)))

    # ---- golden_fir.csv（每段小结）----
    p_sum = os.path.join(args.out_dir, "golden_fir.csv")
    with open(p_sum, "w", newline="\n", encoding="utf-8") as f:
        f.write("# fir_filter golden reference (per segment) -- generated by fpga/sim/gen_fir_vectors.py\n")
        f.write("# DO NOT EDIT BY HAND; regenerate instead.\n")
        f.write("# arithmetic: acc=sum(h[k]*x[n-k]); y=sat16(acc>>%d)\n" % shift)
        f.write("case,seg_id,n_samples,reset,sat_count,out_count\n")
        for i, (case, n, rst, sat) in enumerate(rows, start=1):
            f.write("%s,%d,%d,%d,%d,%d\n" % (case, i, n, rst, sat, n))
    print("已写 %s  (%d 段)" % (p_sum, len(rows)))

    # ---- golden_fir_out.csv（逐样本期望输出，入库可评审）----
    p_out = os.path.join(args.out_dir, "golden_fir_out.csv")
    with open(p_out, "w", newline="\n", encoding="utf-8") as f:
        f.write("# fir_filter golden reference (per sample) -- generated by fpga/sim/gen_fir_vectors.py\n")
        f.write("# DO NOT EDIT BY HAND; regenerate instead.\n")
        f.write("seg_id,index,y\n")
        for i, (case, x, rst) in enumerate(cases, start=1):
            for j, y in enumerate(outs[i - 1]):
                f.write("%d,%d,%d\n" % (i, j, y))
    print("已写 %s  (%d 行)" % (p_out, sum(len(x) for _, x, _ in cases)))

    # ---- meta.txt ----
    p_meta = os.path.join(args.out_dir, "meta.txt")
    with open(p_meta, "w", newline="\n", encoding="utf-8") as f:
        f.write("fs=%g\n" % fs)
        f.write("taps=%d\n" % taps)
        f.write("coeff_shift=%d\n" % shift)
        f.write("band_hz=%.2f,%.2f\n" % (meta["f1"], meta["f2"]))
        f.write("band_convention=-6dB\n")
        f.write("segments=%d\n" % len(cases))
        f.write("total_samples=%d\n" % sum(len(x) for _, x, _ in cases))
        f.write("series_bytes=%d\n" % os.path.getsize(p_series))
        f.write("scale=%g\n" % args.scale)
        f.write("seed=%d\n" % DEF_SEED)
        f.write("coeff_header=%s\n" % os.path.basename(args.header))
    print("已写 %s" % p_meta)

    # ---- 自检 1：冲激响应（结构性质，与黄金参考无关）----
    print("-- 冲激响应自检（与硬件/黄金参考无关的结构性质）--")
    imp_y = outs[0]
    argmax = max(range(len(imp_y)), key=lambda i: imp_y[i])
    sym_ok = all(imp_y[k] == imp_y[taps - 1 - k] for k in range(taps))
    print("   峰值下标 = %d（期望群延迟 %d）%s" % (argmax, (taps - 1) // 2,
                                          "OK" if argmax == (taps - 1) // 2 else "BAD"))
    print("   偶对称 out[k]==out[N-1-k]：%s" % ("OK" if sym_ok else "BAD"))
    if argmax != (taps - 1) // 2 or not sym_ok:
        print("!! 冲激响应结构自检失败")
        return 1

    # ---- 自检 2：用整数实现实测各段的幅度增益 ----
    print("-- 各段实测增益（整数实现、跳过前 %d 个瞬态样本）--" % (taps - 1))
    for idx, ((case, x, rst), y) in enumerate(zip(cases, outs)):
        g = measured_gain_db(x, y, taps - 1)
        sat = rows[idx][3]
        gs = ("%7.1f dB" % g) if g is not None else "    n/a  "
        print("   %-16s n=%-5d reset=%d 增益=%-10s 饱和样本=%d"
              % (case, len(x), rst, gs, sat))

    # ---- numpy 独立对拍 ----
    checked = numpy_crosscheck(cases, outs, h, shift)
    if checked is None:
        print("[numpy 对拍] SKIPPED —— 当前解释器没有 numpy")
    elif checked is False:
        print("[numpy 对拍] FAILED —— 请勿使用本次产物！")
        return 1
    else:
        print("[numpy 对拍] PASS —— %d 个样本与 numpy np.convolve 独立实现逐样本相等" % checked)

    print("== 完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
