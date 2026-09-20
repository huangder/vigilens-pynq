#!/usr/bin/env python3
# =============================================================================
#  design_fir_coeffs.py —— fir_filter 的 Q15 系数设计器（C5）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  契约：docs/interface.md 第 3.5 节
#
#  作用：**一次性**设计出冻结的 Q15 整数系数表，写进
#        fpga/src/fir_coeffs_q15.h（唯一的系数来源，C 与 Python 都从它读）。
#
#  设计方法（全部只用标准库，任何人有 Python 就能复现）：
#    1) 理想带通冲激响应（线性相位，窗函数法）：
#         h_ideal[n] = (2*f2/fs)*sinc(2*f2/fs*(n-M)) - (2*f1/fs)*sinc(2*f1/fs*(n-M))
#         M = (N-1)/2，sinc(x) = sin(pi*x)/(pi*x)
#       （两项相减 = 低通(f2) - 低通(f1)，即带通）
#    2) Hamming 窗（旁瓣 -43 dB、过渡带 ≈ 3.3/N，是"窄过渡带 vs 阻带抑制"的折中；
#       Blackman 过渡带 5.5/N = 1.29 Hz，对 0.7~3.5 Hz 的通带来说太宽，故不选）
#    3) 归一化：把**通带内峰值增益**拉到 1.0（Q15 的 32768），使输出与输入同量纲
#    4) 量化到 int16 Q15（四舍五入远离零）
#
#  【-3 dB 口径】f1/f2 是**设计边缘**，窗函数法下实现的 -6 dB 点落在设计边缘上，
#    -3 dB 点会往里缩一个过渡带。本脚本用"外扩设计边缘 + 数值搜索"让**实现的
#    -3 dB 点正好落在 0.7 / 3.5 Hz**，并在报告里给出实测的 -3dB/-6dB/阻带数字。
#    这样"0.7~3.5 Hz"这句话在契约里指的是可实现、可复测的口径。
#
#  用法：
#    python fpga/sim/design_fir_coeffs.py                 # fs 取 config.yaml 的 fps_nominal（现 30）
#    python fpga/sim/design_fir_coeffs.py --fs 60         # 临时按 60 Hz 设计（会与 config 不一致并告警）
#    python fpga/sim/design_fir_coeffs.py --taps 47       # 换个阶数看看代价
#    python fpga/sim/design_fir_coeffs.py --band 0.1 0.5  # 呼吸带：看它为何不可行
#    python fpga/sim/design_fir_coeffs.py --no-write      # 只评估，不写头文件
#
#  【换采样率 = 换契约，必须成套做】（契约 §0/§3.5）：
#    1) 改仓库根 config.yaml 的 fps_nominal（**唯一来源**）
#    2) python fpga/sim/design_fir_coeffs.py --d 0.0      → 重生成 fpga/src/fir_coeffs_q15.h
#    3) python fpga/sim/gen_fir_vectors.py                → 重生成黄金参考（它从头部读 fs）
#    4) 跑 host_model_fir.cpp + metrics/scripts/check_fir_golden.py + pytest
#    5) 在**完整权限终端**重跑 csim / csynth / cosim（沙箱跑不了）
#    6) 走契约变更流程：docs/interface.md §0/§3.5 + §6 变更记录 → 群公告 → A/B 确认 → 会签升级版本号
# =============================================================================

import argparse
import math
import os
import re
import sys

DEF_TAPS = 63
DEF_F1 = 0.7
DEF_F2 = 3.5
# ⚠️ 采样率 DEF_FS **不在本文件写死**：它取仓库根 `config.yaml` 的 `fps_nominal`（契约 §0 的唯一来源）。
#    见下方 HERE 之后的 _config_fps()。这样"升级到 60 fps"= 改 config.yaml + 重跑两个脚本，
#    不依赖任何人记住数字，也不会出现"脚本里 30 / 契约里 45"这种双来源漂移。
Q15 = 32768
SHIFT = 15
COEFF_MIN, COEFF_MAX = -32768, 32767

HERE = os.path.dirname(os.path.abspath(__file__))


def _config_fps():
    """从仓库根 `config.yaml` 读 `fps_nominal`（契约 §0 的唯一来源）。

    读不到时回退 30.0 —— 只是为了"脚本能独立跑"，**不是**第二份事实来源。
    """
    p = os.path.join(HERE, "..", "..", "config.yaml")
    try:
        with open(p, "r", encoding="utf-8") as f:
            m = re.search(r"^fps_nominal:\s*([\d.]+)", f.read(), re.M)
        return float(m.group(1)) if m else 30.0
    except OSError:
        return 30.0


DEF_FS = _config_fps()

# Windows 控制台默认 GBK，中文会乱码；固定成 UTF-8（与仓库其它脚本一致的可读性要求）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---- 基础函数 ---------------------------------------------------------------
def sinc(x):
    if abs(x) < 1e-12:
        return 1.0
    return math.sin(math.pi * x) / (math.pi * x)


def q15_round(v):
    """四舍五入（远离零），与 C 侧 `round_half_away` 约定一致。"""
    return int(math.floor(v + 0.5)) if v >= 0 else -int(math.floor(-v + 0.5))


def clamp16(v):
    return max(COEFF_MIN, min(COEFF_MAX, v))


def ideal_bandpass(n_taps, fs, f1, f2):
    """理想线性相位带通（double）。M=(N-1)/2 为群延迟中心。"""
    m = (n_taps - 1) / 2.0
    h = []
    for n in range(n_taps):
        t = n - m
        lo = 2.0 * f1 / fs
        hi = 2.0 * f2 / fs
        h.append(hi * sinc(hi * t) - lo * sinc(lo * t))
    return h


def hamming(n_taps):
    return [0.54 - 0.46 * math.cos(2.0 * math.pi * n / (n_taps - 1))
            for n in range(n_taps)]


def mag_db_at(h_q15, fs, f):
    """定点系数的幅频响应（dB，已去掉线性相位项）。"""
    n_taps = len(h_q15)
    m = (n_taps - 1) / 2.0
    w = 2.0 * math.pi * f / fs
    re = 0.0
    im = 0.0
    for n, hv in enumerate(h_q15):
        a = w * (n - m)
        re += hv * math.cos(a)
        im -= hv * math.sin(a)
    mag = math.hypot(re, im) / Q15
    return 20.0 * math.log10(mag + 1e-15)


def design(n_taps, fs, f1_design, f2_design, normalize=True):
    """返回量化后的 Q15 系数（list[int]）。"""
    h = ideal_bandpass(n_taps, fs, f1_design, f2_design)
    w = hamming(n_taps)
    h = [a * b for a, b in zip(h, w)]
    if normalize:
        # 在设计通带内找峰值增益，把它归一到 1.0
        peak = 0.0
        steps = 200
        for i in range(steps + 1):
            f = f1_design + (f2_design - f1_design) * i / steps
            wq = 2.0 * math.pi * f / fs
            m = (n_taps - 1) / 2.0
            re = sum(a * math.cos(wq * (n - m)) for n, a in enumerate(h))
            im = -sum(a * math.sin(wq * (n - m)) for n, a in enumerate(h))
            peak = max(peak, math.hypot(re, im))
        if peak > 0:
            h = [a / peak for a in h]
    return [clamp16(q15_round(a * Q15)) for a in h]


def find_crossing(h_q15, fs, f_lo, f_hi, target_db, rising, step=0.002):
    """在 [f_lo, f_hi] 内找 dB 穿越 target_db 的频率（线性内插）。

    带通的 |H(f)| 是"低频衰减 -> 上升 -> 通带平台 -> 下降 -> 高频衰减"，
    所以扫描方向统一为**从 f_lo 升到 f_hi**：
      rising=True  -> 找"从下往上穿"的频率（下边缘）
      rising=False -> 找"从上往下穿"的频率（上边缘）
    """
    n = max(2, int((f_hi - f_lo) / step))
    prev_f = f_lo
    prev_db = mag_db_at(h_q15, fs, prev_f)
    for i in range(1, n + 1):
        f = f_lo + (f_hi - f_lo) * i / n
        db = mag_db_at(h_q15, fs, f)
        crossed = (prev_db <= target_db < db) if rising else (prev_db >= target_db > db)
        if crossed:
            # 线性内插
            t = (target_db - prev_db) / (db - prev_db)
            return prev_f + (f - prev_f) * t
        prev_f, prev_db = f, db
    return None


def band_extremes(h_q15, fs, f_lo, f_hi, step=0.01):
    lo_db, hi_db = 1e9, -1e9
    lo_f = hi_f = f_lo
    n = max(2, int((f_hi - f_lo) / step))
    for i in range(n + 1):
        f = f_lo + (f_hi - f_lo) * i / n
        db = mag_db_at(h_q15, fs, f)
        if db < lo_db:
            lo_db, lo_f = db, f
        if db > hi_db:
            hi_db, hi_f = db, f
    return lo_db, lo_f, hi_db, hi_f


def place_3db(n_taps, fs, f1, f2, d_max=0.75, d_step=0.02, verbose=True):
    """搜索设计边缘外扩量 d，使实现的 -3 dB 点最接近 (f1, f2)。"""
    best = None
    d = 0.0
    while d <= d_max + 1e-9:
        h = design(n_taps, fs, f1 - d, f2 + d)
        # 下边缘：从 f1+0.7 往下找 -3 dB；上边缘：从 f2-0.7 往上找
        e1 = find_crossing(h, fs, max(0.02, f1 - 0.7), f1 + 0.7, -3.0103, True, step=0.005)
        e2 = find_crossing(h, fs, f2 - 0.7, f2 + 0.7, -3.0103, False, step=0.005)
        if e1 is None or e2 is None:
            d += d_step
            continue
        err = abs(e1 - f1) + abs(e2 - f2)
        if best is None or err < best[0]:
            best = (err, d, e1, e2)
        d += d_step
    if best is None:
        raise RuntimeError("未能找到可行的设计边缘外扩量")
    err, d, e1, e2 = best
    if verbose:
        print("  -3dB 搜索：设计边缘外扩 d=%.2f Hz -> 设计边缘 %.3f / %.3f Hz"
              % (d, f1 - d, f2 + d))
        print("             实测 -3dB 点 = %.3f / %.3f Hz（目标 %.2f / %.2f，误差 %.3f Hz）"
              % (e1, e2, f1, f2, err))
    return d, e1, e2


def report(h_q15, fs, f1, f2, title):
    n_taps = len(h_q15)
    m = (n_taps - 1) / 2.0
    dc = sum(h_q15)
    abs_sum = sum(abs(v) for v in h_q15)
    peak_abs = max(abs(v) for v in h_q15)
    print("  阶数 N=%d（线性相位 I 型），群延迟 %.1f 样本 = %.1f ms"
          % (n_taps, m, m / fs * 1000.0))
    print("  Σh(Q15) = %d  (%.2f dB @DC)   Σ|h| = %d   峰值系数 = %d"
          % (dc, 20.0 * math.log10(abs(dc) / Q15 + 1e-15), abs_sum, peak_abs))
    # 溢出界：|acc| <= 32768 * Σ|h|
    bound = 32768 * abs_sum
    print("  累加器上界 |acc| <= 32768 * Σ|h| = %d  (int32 上限 %d)  -> %s"
          % (bound, 2 ** 31 - 1, "安全 ✅" if bound < 2 ** 31 else "溢出 ❌"))
    e1 = find_crossing(h_q15, fs, max(0.02, f1 - 0.9), f1 + 0.9, -3.0103, True)
    e2 = find_crossing(h_q15, fs, f2 - 0.9, f2 + 0.9, -3.0103, False)
    s1 = find_crossing(h_q15, fs, max(0.02, f1 - 0.9), f1 + 0.9, -6.0, True)
    s2 = find_crossing(h_q15, fs, f2 - 0.9, f2 + 0.9, -6.0, False)
    print("  实测 -3 dB 点：%s / %s Hz   -6 dB 点：%s / %s Hz"
          % ("%.3f" % e1 if e1 else "n/a", "%.3f" % e2 if e2 else "n/a",
             "%.3f" % s1 if s1 else "n/a", "%.3f" % s2 if s2 else "n/a"))
    lo_db, lo_f, hi_db, hi_f = band_extremes(h_q15, fs, f1, f2)
    print("  通带 [%.2f, %.2f] Hz 内：最低 %.2f dB @%.3f Hz，最高 %.2f dB @%.3f Hz"
          % (f1, f2, lo_db, lo_f, hi_db, hi_f))
    print("  通带（1.0~3.0 Hz 平台）起伏：", end="")
    lo2, lof2, hi2, hif2 = band_extremes(h_q15, fs, 1.0, 3.0)
    print("%.2f dB" % (hi2 - lo2))
    # 阻带
    for (a, b, name) in [(0.02, max(0.05, f1 - 0.35), "低阻带"),
                         (min(fs / 2 - 1e-9, f2 + 0.35), fs / 2, "高阻带")]:
        if b <= a:
            continue
        steps = 60
        worst = -1e9
        worst_f = a
        for i in range(steps + 1):
            f = a + (b - a) * i / steps
            db = mag_db_at(h_q15, fs, f)
            if db > worst:
                worst, worst_f = db, f
        print("  %s [%.2f, %.2f] Hz：最差 %.1f dB @%.3f Hz"
              % (name, a, b, worst, worst_f))
    print("  抽样点 (Hz -> dB)：", end="")
    for f in [0.1, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 8.0, 12.0]:
        print(" %.1f:%.1f" % (f, mag_db_at(h_q15, fs, f)), end="")
    print()
    return dict(taps=n_taps, dc=dc, abs_sum=abs_sum, bound=bound,
                e3=(e1, e2), e6=(s1, s2))


def scan_d(n_taps, fs, f1, f2, d_list):
    """打印不同"设计边缘外扩量 d"下的关键指标 —— 用于**有依据地**定 d。

    指标含义：
      -3dB 点  = 实现的半功率带宽（口径 A）
      -6dB 点  = 设计边缘（窗函数法的固有口径，口径 B）
      0.5 Hz   = 呼吸带顶端的抑制（rPPG 最大干扰源之一）
      0.35 Hz  = 低阻带抑制
      4.5 Hz   = 高阻带抑制（运动伪影/噪声）
      平台起伏 = 1.0~3.0 Hz 内的增益波动（实用心率带）
    """
    print("---- d 扫描（设计边缘 = %.2f-d / %.2f+d Hz）----" % (f1, f2))
    print("  %5s | %8s %8s | %8s %8s | %7s %7s | %7s | %7s %7s"
          % ("d", "-3dB低", "-3dB高", "-6dB低", "-6dB高",
             "0.5Hz", "0.35Hz", "4.5Hz", "平台起伏", "Σ|h|"))
    for d in d_list:
        h = design(n_taps, fs, f1 - d, f2 + d)
        e1 = find_crossing(h, fs, max(0.02, f1 - 0.9), f1 + 0.9, -3.0103, True, step=0.005)
        e2 = find_crossing(h, fs, f2 - 0.9, f2 + 0.9, -3.0103, False, step=0.005)
        s1 = find_crossing(h, fs, max(0.02, f1 - 0.9), f1 + 0.9, -6.0, True, step=0.005)
        s2 = find_crossing(h, fs, f2 - 0.9, f2 + 0.9, -6.0, False, step=0.005)
        lo2, _, hi2, _ = band_extremes(h, fs, 1.0, 3.0)
        print("  %5.2f | %8s %8s | %8s %8s | %7.1f %7.1f | %7.1f | %7.2f %7d"
              % (d,
                 "%.3f" % e1 if e1 else "n/a", "%.3f" % e2 if e2 else "n/a",
                 "%.3f" % s1 if s1 else "n/a", "%.3f" % s2 if s2 else "n/a",
                 mag_db_at(h, fs, 0.5), mag_db_at(h, fs, 0.35),
                 mag_db_at(h, fs, 4.5), hi2 - lo2, sum(abs(v) for v in h)))
    print("  ⚠️ d 越大 -> 低频/高频抑制越差（干扰泄漏越大），但 0.7 Hz 附近的通带越完整。")
    print("     最终取值必须由「哪个指标对 rPPG 更要紧」决定，并写进契约 3.5。")


def write_header(path, h_q15, fs, f1, f2, d):
    lines = []
    lines.append("// =============================================================================")
    lines.append("//  %s —— fir_filter 的冻结系数表（**自动生成，请勿手改**）" % os.path.basename(path))
    lines.append("// -----------------------------------------------------------------------------")
    lines.append("//  生成器：fpga/sim/design_fir_coeffs.py   （纯标准库，可用同一条命令复现）")
    lines.append("//  契约：docs/interface.md 第 3.5 节")
    lines.append("//")
    lines.append("//  设计：Hamming 窗理想带通，设计边缘 = %.3f / %.3f Hz（外扩 d=%.2f Hz 以让" % (f1 - d, f2 + d, d))
    lines.append("//        实现的 -3 dB 点落在 %.2f / %.2f Hz），通带峰值增益归一到 1.0（Q15 = 32768）" % (f1, f2))
    lines.append("//  采样率：%.0f Hz（rPPG 心率带 0.7~3.5 Hz）" % fs)
    lines.append("//  量化：int16 Q15，四舍五入远离零；运算约定见契约 3.5 节")
    lines.append("// =============================================================================")
    lines.append("")
    lines.append("#ifndef FIR_COEFFS_Q15_H")
    lines.append("#define FIR_COEFFS_Q15_H")
    lines.append("")
    lines.append("#define FIR_NUM_TAPS    %d" % len(h_q15))
    lines.append("#define FIR_FS_HZ       %d" % int(fs))
    lines.append("#define FIR_F1_HZ       %s" % ("%.2f" % f1))
    lines.append("#define FIR_F2_HZ       %s" % ("%.2f" % f2))
    lines.append("#define FIR_COEFF_SHIFT %d   // 累加后算术右移位数（Q15）" % SHIFT)
    lines.append("")
    lines.append("static const short FIR_COEFF_Q15[FIR_NUM_TAPS] = {")
    for i in range(0, len(h_q15), 8):
        chunk = ", ".join("%6d" % v for v in h_q15[i:i + 8])
        lines.append("    " + chunk + ("," if i + 8 < len(h_q15) else ""))
    lines.append("};")
    lines.append("")
    lines.append("#endif  // FIR_COEFFS_Q15_H")
    lines.append("")
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    ap = argparse.ArgumentParser(description="fir_filter 的 Q15 系数设计器")
    ap.add_argument("--taps", type=int, default=DEF_TAPS)
    ap.add_argument("--fs", type=float, default=DEF_FS)
    ap.add_argument("--band", type=float, nargs=2, default=[DEF_F1, DEF_F2],
                    metavar=("F1", "F2"))
    ap.add_argument("--out-header", default=os.path.join(HERE, "..", "src", "fir_coeffs_q15.h"))
    ap.add_argument("--search", action="store_true",
                    help="搜索让 -3dB 落在目标边缘的设计外扩量（默认开启；--no-... 见下）")
    ap.add_argument("--no-search", dest="search", action="store_false")
    ap.add_argument("--d", type=float, default=None,
                    help="直接指定设计边缘外扩量（Hz），跳过搜索")
    ap.add_argument("--scan", action="store_true",
                    help="打印 d 扫描表（用于有依据地定 d），并在选定的 d 上写头文件")
    ap.add_argument("--no-write", dest="write", action="store_false")
    ap.set_defaults(search=True, write=True)
    args = ap.parse_args()

    f1, f2 = args.band
    print("== design_fir_coeffs: N=%d, fs=%.0f Hz, 目标 -3dB 带 %.2f~%.2f Hz =="
          % (args.taps, args.fs, f1, f2))
    cfg_fs = _config_fps()
    if abs(args.fs - cfg_fs) > 1e-9:
        print("  ⚠️ --fs=%.0f 与 config.yaml 的 fps_nominal=%.0f **不一致**："
              % (args.fs, cfg_fs))
        print("     写出的系数表会与契约 §0 漂移（rPPG 频率轴错位）。"
              "这种表只应用于评估/对比，**不要直接提交**。")

    if args.scan:
        print("")
        scan_d(args.taps, args.fs, f1, f2, [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30])

    if args.d is not None:
        d = args.d
        print("  指定设计边缘外扩 d=%.2f Hz -> 设计边缘 %.3f / %.3f Hz"
              % (d, f1 - d, f2 + d))
    elif args.search:
        d, _, _ = place_3db(args.taps, args.fs, f1, f2)
    else:
        d = 0.0
        print("  未搜索：设计边缘 = 目标边缘（-6 dB 口径）")

    h = design(args.taps, args.fs, f1 - d, f2 + d)
    print("")
    print("---- 主设计评估 ----")
    report(h, args.fs, f1, f2, "main")

    if args.write:
        p = os.path.abspath(args.out_header)
        write_header(p, h, args.fs, f1, f2, d)
        print("")
        print("已写冻结系数表：%s  (%d 个 int16)" % (p, len(h)))

    # ---- 附：呼吸带（0.1~0.5 Hz）在同阶数下是否可行 —— 用于契约里的"已知限制" ----
    if abs(f1 - DEF_F1) < 1e-9 and abs(f2 - DEF_F2) < 1e-9:
        print("")
        print("---- 对照：把同一阶数 N=%d 用到呼吸带 0.1~0.5 Hz @%.0f Hz ----"
              % (args.taps, args.fs))
        # Hamming 窗的过渡带宽（Hz）：Δf ≈ 3.3/(2πN) * fs
        trans = 3.3 / (2.0 * math.pi * args.taps) * args.fs
        print("  Hamming 过渡带宽 Δf ≈ 3.3/(2πN)·fs = %.3f Hz；呼吸带宽仅 0.4 Hz"
              % trans)
        for need in (0.15, 0.10):
            n_need = 3.3 / (2.0 * math.pi * need) * args.fs
            print("    要把过渡带压到 %.2f Hz，需要 N ≳ %.0f 阶" % (need, n_need))
        try:
            d2, e1b, e2b = place_3db(args.taps, args.fs, 0.1, 0.5, d_max=0.3, verbose=False)
            hb = design(args.taps, args.fs, 0.1 - d2, 0.5 + d2)
            report(hb, args.fs, 0.1, 0.5, "resp")
        except RuntimeError:
            hb = design(args.taps, args.fs, 0.1, 0.5)
            print("  ❌ 在 ±0.3 Hz 范围内找不到 -3 dB 边沿（说明通带已被过渡带吃掉）")
            print("     直接按设计边缘 0.1/0.5 Hz 出系数时的实测响应：")
            for f in [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]:
                print("       %.2f Hz -> %6.1f dB" % (f, mag_db_at(hb, args.fs, f)))
        print("  ⚠️ 结论：%d 阶在 %.0f Hz 采样下**无法**实现 0.1~0.5 Hz 的呼吸带通 ——"
              % (args.taps, args.fs))
        print("     过渡带 %.2f Hz 已与整个通带（0.4 Hz）同量级。呼吸带必须靠"
              % trans)
        print("     PS 侧降采样（如降到 2 Hz 采样 -> 过渡带 %.2f Hz）后再用同一 IP，"
              % (3.3 / (2.0 * math.pi * args.taps) * 2.0))
        print("     或对呼吸单独增加阶数/改用 IIR。这是一条**契约级已知限制**。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
