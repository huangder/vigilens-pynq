#!/usr/bin/env python3
# =============================================================================
#  bench_filter_ps.py —— PS 侧（软件）FIR 参考实现基线（P0-4 / M4 对比用）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#  契约：docs/interface.md 第 3.5 / 4.6 节
#
#  作用：给 M4 的"软硬件对比"提供**同条件**的软件侧基线。
#    在 PYNQ-Z2 上，这段软件跑在 **ARM Cortex-A9 @650 MHz** 的 PS 上；
#    现在先在开发机上跑同一脚本，得到"同一份代码在不同平台"的数字。
#    ⚠️ **本机数字不能当成 PYNQ 的数字**：脚本会把平台信息一起打印并写进 JSON，
#        M4 时把同一脚本拷到板子上重跑即可（依赖只有 numpy，PYNQ 镜像自带）。
#
#  测什么（三种实现，同一份 Q15 系数、同一份输入序列）：
#    1) numpy_batch  ：np.convolve(int64) 一次算完整段 -> 离线/回放用
#    2) numpy_stream ：逐样本滑窗点积（带状态）-> **真实实时路径**（每秒 30 次调用）
#    3) scipy_lfilter：scipy 的浮点实现（若装了 scipy）-> 作为"另一种软件参照"
#    另有 python_ref（纯 Python 精确整数，只在小序列上跑，作为正确性锚点）
#
#  判读口径（写报告时照这个说）：
#    · 输出的 **µs/样本** 与 **"实时处理 30 样本/秒需要占 1 秒的比例"** 是两个关键数；
#    · 与 `fpga/report/c5_fir_filter_v1.md` 里 PL 的 **II=1（10 ns/样本）** 对比时，
#      **必须同条件**：同样 63 阶、同样 Q15、同样"每秒处理 30 个新样本"。
#
#  用法：
#    python metrics/scripts/bench_filter_ps.py
#    python metrics/scripts/bench_filter_ps.py --json fpga/report/logs/2026-09-11_ps_filter_baseline.json
#    python metrics/scripts/bench_filter_ps.py --repeats 5 --fs 30
# =============================================================================

import argparse
import json
import os
import platform
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEF_HEADER = os.path.join(REPO, "fpga", "src", "fir_coeffs_q15.h")

INT16_MIN, INT16_MAX = -32768, 32767


def parse_coeff_header(path):
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()

    def num(name):
        m = re.search(r"#define\s+%s\s+([-\d.]+)" % name, txt)
        if not m:
            raise ValueError("头文件里找不到 #define %s" % name)
        return float(m.group(1))

    m = re.search(r"FIR_COEFF_Q15\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", txt, re.S)
    coeffs = [int(v) for v in re.findall(r"-?\d+", m.group(1))]
    return dict(coeffs=coeffs, taps=int(num("FIR_NUM_TAPS")),
                shift=int(num("FIR_COEFF_SHIFT")), fs=num("FIR_FS_HZ"))


def make_series(n, seed=20260911):
    """确定性的 Q1.15 输入序列（复用仓库 LCG 的思路，纯标准库生成）。"""
    s = seed & 0xFFFFFFFF
    out = []
    for _ in range(n):
        s = (1103515245 * s + 12345) & 0x7FFFFFFF
        out.append((s >> 8) % 65536 - 32768)
    return out


# ---- 三种软件实现 -----------------------------------------------------------
def numpy_batch(x, h, shift):
    import numpy as np
    xa = np.array(x, dtype=np.int64)
    ha = np.array(h, dtype=np.int64)
    conv = np.convolve(xa, ha)[:len(xa)]
    y = conv >> shift
    return np.clip(y, INT16_MIN, INT16_MAX)


class NumpyStream:
    """逐样本滑窗 FIR，**状态跨调用保持**（等价于硬件"段间保持"语义）。

    ⚠️ 这里最容易写错的点：如果每个 chunk 都新建状态（hist 重新置零），
       结果会与一次性批量卷积**不一致** —— 本脚本内置了这条自检，
       第一次跑就是这么被抓出来的（见 README/skill 记录）。
    """

    def __init__(self, h, shift):
        import numpy as np
        self.ha = np.array(h, dtype=np.int64)
        self.n = len(h)
        self.shift = shift
        self.hist = [0] * (self.n - 1)
        self._np = np

    def process(self, chunk):
        ys = []
        for v in chunk:
            buf = [v] + self.hist
            acc = int(self._np.dot(self.ha, self._np.array(buf, dtype=self._np.int64)))
            acc >>= self.shift
            ys.append(INT16_MAX if acc > INT16_MAX else
                      (INT16_MIN if acc < INT16_MIN else acc))
            self.hist = buf[:self.n - 1]
        return ys


def scipy_lfilter(x, h):
    from scipy.signal import lfilter
    return lfilter([v / 32768.0 for v in h], [1.0], x)


def python_ref(x, h, shift):
    n = len(h)
    hist = [0] * (n - 1)
    ys = []
    for v in x:
        buf = [v] + hist
        acc = 0
        for k in range(n):
            acc += h[k] * buf[k]
        acc >>= shift
        ys.append(INT16_MAX if acc > INT16_MAX else
                  (INT16_MIN if acc < INT16_MIN else acc))
        hist = buf[:n - 1]
    return ys


def timeit(fn, repeats):
    best = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        r = fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best, r


def main():
    ap = argparse.ArgumentParser(description="PS 侧 FIR 参考实现基线")
    ap.add_argument("--header", default=DEF_HEADER)
    ap.add_argument("--fs", type=float, default=30.0)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--json", help="把结果写成 JSON（建议放 fpga/report/logs/）")
    args = ap.parse_args()

    meta = parse_coeff_header(os.path.abspath(args.header))
    h, shift, taps = meta["coeffs"], meta["shift"], meta["taps"]
    fs = args.fs
    chunk = int(fs)                       # 一秒的样本数 = 一帧

    have_np = have_sp = True
    try:
        import numpy  # noqa: F401
        np_ver = numpy.__version__
    except ImportError:
        have_np = False
        np_ver = None
    try:
        import scipy  # noqa: F401
        sp_ver = scipy.__version__
    except ImportError:
        have_sp = False
        sp_ver = None

    plat = dict(
        platform=platform.platform(),
        machine=platform.machine(),
        processor=platform.processor() or "(未报告)",
        python=sys.version.split()[0],
        numpy=np_ver,
        scipy=sp_ver,
    )
    print("== bench_filter_ps: PS 侧 FIR 基線（taps=%d, shift=%d, fs=%g Hz）==" % (taps, shift, fs))
    print("   平台：%s | %s | %s" % (plat["platform"], plat["machine"], plat["processor"]))
    print("   Python %s | numpy %s | scipy %s" % (plat["python"], np_ver, sp_ver))
    print("   ⚠️ 本机数字 ≠ PYNQ PS 数字；M4 需在板子上重跑本脚本。")
    if not have_np:
        print("!! 没有 numpy：只能跑 python_ref（很慢），结果不具代表性")
        return 2

    # 序列长度：10 s / 60 s / 300 s @fs
    lengths = [chunk * 10, chunk * 60, chunk * 300]
    results = {"platform": plat, "taps": taps, "shift": shift, "fs": fs,
               "chunk_samples": chunk, "cases": []}

    for n in lengths:
        x = make_series(n)
        chunks = [x[i:i + chunk] for i in range(0, n, chunk)]
        row = {"n_samples": n, "seconds": n / fs}

        # 1) numpy_batch（离线）
        dt, yb = timeit(lambda: numpy_batch(x, h, shift), args.repeats)
        row["numpy_batch_us_total"] = dt * 1e6
        row["numpy_batch_us_per_sample"] = dt * 1e6 / n

        # 2) numpy_stream（实时路径：每个 chunk = 一秒的样本，状态跨 chunk 保持）
        t0 = time.perf_counter()
        for _ in range(args.repeats):
            flt = NumpyStream(h, shift)
            out = []
            for c in chunks:
                out.extend(flt.process(c))
        dt_stream = (time.perf_counter() - t0) / args.repeats
        row["numpy_stream_us_total"] = dt_stream * 1e6
        row["numpy_stream_us_per_sample"] = dt_stream * 1e6 / n
        # 实时裕度：每秒处理 chunk 个样本需要多少时间，占 1 秒的比例
        per_chunk = dt_stream / len(chunks)
        row["numpy_stream_us_per_chunk"] = per_chunk * 1e6
        row["numpy_stream_realtime_load_pct"] = per_chunk * 100.0

        # 3) scipy（若有）
        if have_sp:
            dt_sp, _ = timeit(lambda: scipy_lfilter(x, h), args.repeats)
            row["scipy_lfilter_us_total"] = dt_sp * 1e6
            row["scipy_lfilter_us_per_sample"] = dt_sp * 1e6 / n

        # 4) 纯 Python 锚点（只在小序列上，且带正确性校验）
        if n == lengths[0]:
            dt_py, yp = timeit(lambda: python_ref(x, h, shift), 1)
            row["python_ref_us_total"] = dt_py * 1e6
            row["python_ref_us_per_sample"] = dt_py * 1e6 / n
            same_py = all(int(a) == int(b) for a, b in zip(yp, yb))
            same_st = all(int(a) == int(b) for a, b in zip(out, yb))
            row["python_ref_matches_numpy_batch"] = bool(same_py)
            row["numpy_stream_matches_numpy_batch"] = bool(same_st)
            if not (same_py and same_st):
                print("   !! 三种 numpy/纯 Python 实现结果不一致 —— 脚本有问题，请先查这里")
                print("      python_ref==batch: %s   numpy_stream==batch: %s" % (same_py, same_st))
                return 3

        results["cases"].append(row)
        print("   n=%-6d (%5.0f s @%gHz): batch %9.1f µs (%.3f µs/样本) | "
              "stream %9.1f µs (%.3f µs/样本, 实时占用 %.4f%%)%s"
              % (n, n / fs, fs,
                 row["numpy_batch_us_total"], row["numpy_batch_us_per_sample"],
                 row["numpy_stream_us_total"], row["numpy_stream_us_per_sample"],
                 row["numpy_stream_realtime_load_pct"],
                 (" | scipy %.3f µs/样本" % row["scipy_lfilter_us_per_sample"]) if have_sp else ""))

    # ---- 与 PL 的对比（同条件：每秒 30 个新样本）----
    pl_ns_per_sample = 10.0          # II=1 @100 MHz（见 c5 报告：Final II = 1）
    pl_us_per_chunk = chunk * pl_ns_per_sample / 1000.0
    print("")
    print("-- 同条件对比（每秒 30 个新样本）--")
    print("   PL  : II=1 @100 MHz -> %.3f µs/样本，%g 样本/秒 占 %.6f%% 的 1 秒"
          % (pl_ns_per_sample / 1000.0, fs, pl_us_per_chunk / 1e6 * 100))
    st = results["cases"][0]["numpy_stream_us_per_sample"]
    print("   PS  : numpy_stream %.3f µs/样本 -> 慢 PL 约 %.0f 倍（本机 %s）"
          % (st, st / (pl_ns_per_sample / 1000.0), plat["machine"]))
    print("   ⚠️ 这个倍数只在「同机测量」时成立；M4 要在 PYNQ PS 上重跑本脚本取数。")
    print("   注：PL 的价值不只是「快」，更是**抖动确定**（30 Hz 下 PS 其实也够用）——")
    print("       报告里要写成「延迟/抖动的确定性 + CPU 占用释放」，不要只写倍数。")

    results["pl_reference"] = {
        "ii": 1, "clock_mhz": 100.0, "ns_per_sample": pl_ns_per_sample,
        "us_per_chunk": pl_us_per_chunk, "source": "fpga/report/c5_fir_filter_v1.md",
    }

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", newline="\n", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("   已写 %s" % args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
