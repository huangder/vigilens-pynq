"""vital.py —— rPPG（心率/呼吸趋势）链路。

本文件按开发步骤 `backend/A_LINE_DEV_STEPS.md` 分两步落地：
  · **P1.4（本文当前内容）**：`roi_statistic` 的 ROI 累加均值 → **Q1.15 样本**的冻结量化口径；
  · P3：去直流/归一化 → 带通（复用 FIR 的 NumPy 参考实现）→ `rfft` 峰值 → BPM。

为什么量化口径要单独成一个函数：
    契约 `docs/interface.md` **4.6 节**把"ROI 均值怎么变成 int16 Q1.15 样本"冻结成了
    **唯一权威的整数式**，它是 A↔C 时间序列链路的接口。A 线若在这里自己发明一套
    （例如先浮点归一化、或先去均值再量化），软件与硬件的输入序列就会系统性错位，
    而且是"看起来都在工作"的那种错。**本函数是那一条口径在 A 线侧的实现。**

⚠️ 契约 4.6 的性质 P1：**合法 ROI 均值（0~255）永不触发裁剪** —— 若这里真的发生了
   clip，说明调用方给的 `sum_c` / `count` 不自洽，属于上游 bug，不应靠裁剪掩盖。
"""

from __future__ import annotations

from pathlib import Path

INT16_MIN = -32768
INT16_MAX = 32767

# 契约 4.6：中心 128（0~255 的中灰）映射到 0，使带通滤波器工作在零点附近
CENTER = 128
# 契约 4.6：32768 / 128 —— 把 [0,255] 的均值偏离映射到 Q1.15 量程
SCALE = 256


def rha_div(a: int, b: int) -> int:
    """有理数四舍五入（**远离零**）的整数实现，b > 0。

    契约 4.6 冻结式：a >= 0 时 `(2a + b) // (2b)`，否则 `-((-2a + b) // (2b))`。
    注意这是"远离零"而不是 Python 内置 `round()` 的"银行家舍入"，两者在 .5 处不同。
    """
    if b <= 0:
        raise ValueError("count 必须 > 0：空 ROI 的帧应被丢弃，不得填 0 或沿用上一帧（契约 4.6）")
    if a >= 0:
        return (2 * a + b) // (2 * b)
    return -((-2 * a + b) // (2 * b))


def q15_from_roi_sum(sum_c: int, count: int) -> int:
    """把 `roi_statistic` 的某个通道累加值量化成 int16 Q1.15 样本（契约 4.6，唯一权威口径）。

    参数
        sum_c: 该通道 ROI 内像素的整数累加值（`roi_statistic` 的 `sum_r/sum_g/sum_b`）
        count: ROI 内像素数（`roi_statistic` 的 `count`）

    返回
        量化后的 Q1.15 样本（-32768 ~ 32767）

    抛出
        ValueError：`count <= 0`。契约规定这类帧**必须整帧丢弃**，不得产生样本，
        也不得用 0 或上一帧的值顶替 —— 顶替会伪造出一段平坦的信号，
        在频谱上表现为虚假的低频成分。
    """
    if count <= 0:
        raise ValueError("count==0（空 ROI）的帧必须丢弃，不得产生 Q1.15 样本（契约 4.6）")
    num = SCALE * int(sum_c) - (CENTER * SCALE) * int(count)     # = 256*sum_c - 32768*count
    q = rha_div(num, int(count))
    # 合法输入永不裁剪（契约性质 P1）；这里保留 clip 只为防御上游不自洽的输入
    return INT16_MAX if q > INT16_MAX else (INT16_MIN if q < INT16_MIN else q)


def q15_of_mean(mean: float) -> int:
    """等价实数式：`round_half_away((mean - 128) * 256)`。

    **仅供对拍与可读性参考**，整数式 `q15_from_roi_sum` 才是权威实现
    （浮点在大样本上可能与整数式差 1 个量化台阶）。
    """
    v = (float(mean) - CENTER) * SCALE
    q = int(v + 0.5) if v >= 0 else -int(-v + 0.5)
    return INT16_MAX if q > INT16_MAX else (INT16_MIN if q < INT16_MIN else q)


# ===========================================================================
# FIR 带通（契约 3.5 / 4.5 节）
#
#   y[n] = sat16( ( Σ_{k=0}^{N-1} h[k] * x[n-k] ) >> 15 )
#   四条与 C 线逐字一致的运算约定（fir_filter.cpp 顶部列的那四条）：
#     1) 累加用精确整数，**不做任何中间舍入**；
#     2) 只在最后做**一次算术右移**（Python 的 >> 对负数向下取整，与 C++ 一致；
#        **不可**写成 /32768 —— 整数除法是向零取整，负样本会差 1）；
#     3) 移位结果**饱和**到 int16；
#     4) 饱和样本数要能被数出来（供 PS 做质量判据）。
#
#   ⚠️ 系数**只有一处来源**（契约 4.5 硬约束）：`fpga/src/fir_coeffs_q15.h`。
#      本模块运行时直接解析该头文件，而不是在 Python 里另存一份 —— 否则口径漂移
#      会以"A 线与硬件差一点点"的形式出现，且极难定位。
# ===========================================================================

REPO_ROOT_FOR_COEFFS = Path(__file__).resolve().parent.parent
FIR_HEADER_PATH = REPO_ROOT_FOR_COEFFS / "fpga" / "src" / "fir_coeffs_q15.h"


def load_fir_coeffs(path: str | Path | None = None) -> tuple[list[int], int, int]:
    """解析冻结系数头文件，返回 `(系数列表, 移位位数, 采样率 Hz)`。

    解析的对象是 C 线的**唯一来源** `fpga/src/fir_coeffs_q15.h`（由
    `fpga/sim/design_fir_coeffs.py` 生成）。C 线的 `gen_fir_vectors.py` 用同一种方式解析，
    因此两边拿到的必然是同一组数。
    """
    import re

    p = Path(path) if path else FIR_HEADER_PATH
    if not p.exists():
        raise FileNotFoundError(f"找不到冻结系数头文件：{p}（C 线产物，应由 fir_coeffs_q15.h 提供）")
    text = p.read_text(encoding="utf-8", errors="replace")

    m = re.search(r"FIR_COEFF_Q15\s*\[[^\]]*\]\s*=\s*\{(.*?)\}", text, re.S)
    if not m:
        raise ValueError(f"{p} 里没有找到 FIR_COEFF_Q15 表")
    coeffs = [int(v) for v in re.findall(r"-?\d+", m.group(1))]

    def _num(name: str, default: int) -> int:
        mm = re.search(rf"#define\s+{name}\s+(\d+)", text)
        return int(mm.group(1)) if mm else default

    taps = _num("FIR_NUM_TAPS", len(coeffs))
    if len(coeffs) != taps:
        raise ValueError(f"系数个数 {len(coeffs)} 与 FIR_NUM_TAPS={taps} 不一致")
    return coeffs, _num("FIR_COEFF_SHIFT", 15), _num("FIR_FS_HZ", 45)


def fir_process(samples, coeffs: list[int], shift: int = 15, hist=None):
    """对一段样本做带通滤波，返回 `(输出列表, 饱和样本数, 新的历史)`。

    语义与 `fpga/src/fir_filter.cpp` 一致：
      · 输出**含启动瞬态**，与输入一一对应、同序、等长（不丢弃前 N-1 个样本），
        这样"输入第 i 个样本 ↔ 输出第 i 个"的对应关系最简单，黄金参考可逐样本严格比对；
      · `hist` 是**段间保持**的延迟线（长度 N-1，最近的在最前）。`hist=None` 等价于
        `reset=1`（清零起点）；要承接上一段状态就把上一段返回的 `hist` 传进来。
      · ⚠️ 不能依赖"上电是 0"：HLS 把 `static` 延迟线实现为上电初始化、**复位不清零**
        （`fpga/src/fir_filter.cpp` 顶部注释与 skill 坑 #17 都记了这一条），
        所以确定性起点必须靠显式 `reset`/`hist=None` 建立。
    """
    import numpy as np

    h = np.asarray(coeffs, dtype=np.int64)
    n = len(h)
    x = np.asarray(list(samples), dtype=np.int64)
    if x.size == 0:
        return [], 0, (np.zeros(n - 1, dtype=np.int64) if hist is None else np.asarray(hist, dtype=np.int64))

    # 把历史拼在样本前面；np.convolve(ext, h)[k] 恰好等于 Σ h[j]*ext[k-j]
    if hist is None:
        ext = x
        off = 0
    else:
        hh = np.asarray(hist, dtype=np.int64)
        if hh.size != n - 1:
            raise ValueError(f"hist 长度应为 {n - 1}，收到 {hh.size}")
        ext = np.concatenate([hh, x])
        off = hh.size

    acc = np.convolve(ext, h)                       # 精确整数，无中间舍入
    seg = acc[off:off + x.size]
    y = seg >> shift                                 # 算术右移：对负数向下取整
    sat = int(np.count_nonzero((y > INT16_MAX) | (y < INT16_MIN)))
    y = np.clip(y, INT16_MIN, INT16_MAX)             # 最后一次性饱和到 int16

    new_hist = ext[-(n - 1):] if n > 1 else np.zeros(0, dtype=np.int64)
    return [int(v) for v in y], sat, new_hist
