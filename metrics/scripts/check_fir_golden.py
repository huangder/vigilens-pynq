"""check_fir_golden.py —— A↔C `fir_filter` 逐样本黄金参考对拍（契约 `docs/interface.md` 4.5 节）。

为什么需要它：
    `fir_filter` 是 A 线 rPPG 链路里的带通级，也是唯一一条**时间序列**链路。
    契约 4.5 把它的输入输出冻结成逐样本可比的黄金参考，容差 **0**（v0.94 起从 ±1 LSB 收紧）。
    只要 A 线的实现与它逐样本相同，就说明"两边算的是同一个东西"。

    被测对象是 `backend/vital.py::fir_process` —— 它**从 C 线的冻结头文件读系数**
    （契约 4.5 的硬约束：系数只有一处来源），不在 Python 侧另存一份。

对拍的三条硬口径（与 `fir_filter.cpp` 顶部四条一致）：
    · 累加精确整数，无中间舍入；
    · 只在最后做**一次算术右移** `>> 15`（对负数向下取整，**不是** /32768）；
    · 结果饱和到 int16，并统计饱和样本数。
    段语义：`reset=1` 的段从零起点开始；`reset=0` 的段承接上一段的状态；
    输出含启动瞬态，逐样本一一对应。

前置：
    python fpga/sim/gen_fir_vectors.py

用法（仓库根）：
    python metrics/scripts/check_fir_golden.py
退出码：0 = 4036 个样本全等且段统计一致；1 = 有差异或前置缺失。

⚠️ 本脚本只做**比对**，不修改任何 C 线产物。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

DATA_FIR = REPO_ROOT / "fpga" / "sim" / "data_fir"
SERIES = DATA_FIR / "series.bin"
SEG_CSV = DATA_FIR / "golden_fir.csv"
OUT_CSV = DATA_FIR / "golden_fir_out.csv"
COEFF_HEADER = REPO_ROOT / "fpga" / "src" / "fir_coeffs_q15.h"


def load_segments() -> list[dict[str, str]]:
    with SEG_CSV.open(encoding="utf-8", newline="") as fh:
        rows = [ln for ln in fh if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(rows))


def load_expected() -> list[tuple[int, int, int]]:
    with OUT_CSV.open(encoding="utf-8", newline="") as fh:
        rows = [ln for ln in fh if not ln.lstrip().startswith("#")]
    return [(int(r["seg_id"]), int(r["index"]), int(r["y"])) for r in csv.DictReader(rows)]


def main() -> int:
    try:
        import numpy as np
    except ImportError:
        print("[FAIL] 需要 numpy：pip install -r requirements.txt（或用项目 .venv）", file=sys.stderr)
        return 1

    for p in (SERIES, SEG_CSV, OUT_CSV, COEFF_HEADER):
        if not p.exists():
            print(f"[FAIL] 缺少文件：{p}")
            if p == SERIES:
                print("       先生成：python fpga/sim/gen_fir_vectors.py")
            return 1

    from vital import fir_process, load_fir_coeffs

    coeffs, shift, fs = load_fir_coeffs()
    segments = load_segments()
    expected = load_expected()

    print("=== A↔C fir_filter 逐样本黄金参考对拍（契约 4.5 节）===")
    print(f"系数 : {COEFF_HEADER.relative_to(REPO_ROOT)}  N={len(coeffs)}，shift={shift}，fs={fs} Hz")
    print(f"输入 : {SERIES.relative_to(REPO_ROOT)}  ({SERIES.stat().st_size} B = "
          f"{SERIES.stat().st_size // 2} 个 int16 样本)")
    print(f"黄金 : {OUT_CSV.relative_to(REPO_ROOT)}  ({len(expected)} 个逐样本期望值)")
    print("口径 : acc 无中间舍入 → 单次算术右移 → sat16；输出含启动瞬态；容差 0")
    print()

    x = np.fromfile(SERIES, dtype="<i2")
    total = sum(int(s["n_samples"]) for s in segments)
    if total != x.size:
        print(f"[FAIL] 段样本合计 {total} ≠ series.bin 实际 {x.size}")
        return 1

    # 期望值按 seg_id 分组，便于逐样本核对
    exp_by_seg: dict[int, list[int]] = {}
    for seg_id, _idx, y in expected:
        exp_by_seg.setdefault(seg_id, []).append(y)

    pos = 0
    hist = None
    bad: list[str] = []
    for seg in segments:
        seg_id = int(seg["seg_id"])
        n = int(seg["n_samples"])
        do_reset = int(seg["reset"]) == 1
        chunk = x[pos:pos + n]
        pos += n

        ys, sat, hist = fir_process(chunk, coeffs, shift, None if do_reset else hist)
        want_y = exp_by_seg.get(seg_id, [])
        want_sat = int(seg["sat_count"])

        diff = [i for i, (a, b) in enumerate(zip(ys, want_y)) if a != b] if len(ys) == len(want_y) else [-1]
        if len(ys) != len(want_y):
            bad.append(f"seg {seg_id} ({seg['case']}): 样本数 {len(ys)} ≠ 期望 {len(want_y)}")
        elif diff:
            bad.append(f"seg {seg_id} ({seg['case']}): {len(diff)}/{n} 个样本不一致，"
                       f"首个在 index {diff[0]}：本机 {ys[diff[0]]} 期望 {want_y[diff[0]]}")
        elif sat != want_sat:
            bad.append(f"seg {seg_id} ({seg['case']}): 饱和样本数 {sat} ≠ 期望 {want_sat}")
        else:
            note = f"，饱和 {sat}" if sat else ""
            print(f"  seg {seg_id:2d} {seg['case']:<16} reset={int(seg['reset'])} "
                  f"n={n:3d}  ✅ 全等{note}")

    print()
    if bad:
        for line in bad:
            print(f"  [FAIL] {line}")
        print(f"\n[FAIL] {len(bad)} 段不一致（容差 0）")
        return 1

    print("（实现位于 backend/vital.py::fir_process；P3 的心率带通将直接复用它）")
    print(f"检测通过：契约 §5.2 第 12 项的时间序列部分 —— {len(segments)} 段 / {total} 个样本"
          f"逐样本全等，段统计一致（容差 0）✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
