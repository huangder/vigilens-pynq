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
    return coeffs, _num("FIR_COEFF_SHIFT", 15), _num("FIR_FS_HZ", 30)


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
    #
    # ⚠️ 即使 hist=None 也必须**补足 n-1 个零**再拼，不能直接把 x 当 ext：
    #    否则当 x 短于 n-1 时（例如逐样本喂，x 只有 1 个），返回的历史长度就不对，
    #    下一帧的 `hist` 校验会报 "hist 长度应为 62"。这条是逐样本流式路径专有的坑。
    if hist is None:
        hh = np.zeros(n - 1, dtype=np.int64)
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


# ===========================================================================
# rPPG：ROI → G 通道 Q1.15 序列 → 带通 → 频谱峰值 → BPM（契约 3.6 的时间序列链路）
#
#   硬件链路（契约 3.6）：roi_statistic → Q1.15 量化 → fir_filter
#   本模块在软件侧走**同一条链**，只是把最后一段从"输出滤波序列"延长到"估出 BPM"：
#       ROI 绿通道累加 ──q15_from_roi_sum──▶ Q1.15 序列 ──fir_process──▶ 带通序列
#                                     └──────────────▶ 频谱峰值 ──▶ BPM + 置信度
#   这样 PL 与 PS 看到的是**同一串输入样本**，M4 的软硬件对比才有意义。
# ===========================================================================


def forehead_roi(bbox, width: int, height: int) -> tuple[int, int, int, int]:
    """从人脸框里取**额头 ROI**（半开区间），返回 `(x0, y0, x1, y1)`。

    为什么取额头：皮肤暴露、运动伪影比脸颊小、避开眼睛与嘴（眨眼/说话是强干扰源）。
    取人脸框**上部**的中间区域 —— 上 35% 高、中间 60% 宽，再裁剪到画面内。
    人脸框无效（宽或高 <= 0）时返回空 ROI（四项相等，累加结果为 0）。
    """
    x, y, w, h = (int(v) for v in bbox)
    if w <= 0 or h <= 0:
        return 0, 0, 0, 0
    x0 = x + int(w * 0.20)
    x1 = x + int(w * 0.80)
    y0 = y
    y1 = y + int(h * 0.35)
    # 裁剪到画面内（与 roi_statistic 的 clamp 同义）
    return (max(0, min(x0, width)), max(0, min(y0, height)),
            max(0, min(x1, width)), max(0, min(y1, height)))


def roi_channel_sum(image, roi: tuple[int, int, int, int], channel: int = 1) -> tuple[int, int]:
    """对 ROI 内的某个通道做整数累加，返回 `(sum_c, count)`；与 `roi_statistic` 同口径。

    · 半开区间 `[x0,x1) × [y0,y1)`，越界裁剪，空 ROI 返回 `(0, 0)`；
    · `channel` 默认 1 —— 在 **BGR 与 RGB 里 1 都是绿通道**，这样即使上游忘了做通道序转换，
      rPPG 用的也仍然是绿通道（契约 §0.1 点名的 R/B 互换陷阱在这里被结构性规避）。
    """
    import numpy as np

    x0, y0, x1, y1 = roi
    if x1 <= x0 or y1 <= y0:
        return 0, 0
    arr = image
    if not isinstance(arr, np.ndarray):
        return 0, 0                      # 合成帧源不是图像：当作"没有 ROI"
    h, w = arr.shape[0], arr.shape[1]
    cx0, cy0 = max(0, min(x0, w)), max(0, min(y0, h))
    cx1, cy1 = max(0, min(x1, w)), max(0, min(y1, h))
    if cx1 <= cx0 or cy1 <= cy0:
        return 0, 0
    sub = arr[cy0:cy1, cx0:cx1, channel] if arr.ndim == 3 else arr[cy0:cy1, cx0:cx1]
    return int(sub.sum(dtype=np.int64)), (cx1 - cx0) * (cy1 - cy0)


class GreenRppg:
    """流式 rPPG 估计器：逐帧喂 ROI 的绿通道 Q1.15 样本，定期给出心率与置信度。

    设计要点（都是为了让"报出来的数字"可辩护）：
      · **滑窗填满才出数**：窗口长度取 `config.yaml` 的 `window_seconds`（契约 §1 的局部窗口），
        没填满一律返回 null —— 没测够就是没测够，不拿半个窗去猜；
      · **带通复用冻结 FIR**：直接调 `fir_process`（同一组 Q15 系数、同一算术），
        所以软件与 PL 看到的是同一串滤波后序列；
      · **峰不过半就不报数**：判定"找到真峰"的条件是**峰值 ±1 个频点内的能量占带内总能量过半**。
        这是**结构性判据**（"这个峰是否占主导"），不是可调阈值，所以没有引入新的魔法数字；
      · **丢弃的帧不补值**：契约 §4.6 规定 `count==0` 的帧必须丢弃，
        所以人脸丢失/空 ROI 的那一帧不喂样本。窗口因此可能跨越多于 `window_seconds` 的真实时间，
        `estimate()` 用窗口首尾帧号反推**有效采样率**再做 FFT —— 否则漏帧会把频率系统性抬高。
    """

    def __init__(self, *, fps: float, window_seconds: float, hr_band_hz,
                 coeffs: list[int] | None = None, shift: int | None = None):
        if coeffs is None or shift is None:
            coeffs, shift, _fs = load_fir_coeffs()
        self.coeffs = coeffs
        self.shift = shift
        self.fps = float(fps)
        self.f_lo, self.f_hi = float(hr_band_hz[0]), float(hr_band_hz[1])
        self.need = max(2, int(round(float(window_seconds) * self.fps)))
        self.reset()

    @classmethod
    def from_config(cls, cfg, fps: float | None = None) -> "GreenRppg":
        """按 `config.yaml` 建实例（采样率取 `fps_nominal`，与契约 §0 一致）。"""
        return cls(fps=float(fps if fps is not None else cfg["fps_nominal"]),
                   window_seconds=float(cfg["window_seconds"]),
                   hr_band_hz=cfg["hr_band_hz"])

    def reset(self) -> None:
        from collections import deque

        self._win = deque(maxlen=self.need)      # 带通后的样本
        self._fid = deque(maxlen=self.need)      # 对应的 frame_id（算有效采样率用）
        self._hist = None                        # FIR 段间状态（连续流语义）
        self._valid = 0                          # 累计有效样本数

    @property
    def samples(self) -> int:
        """当前窗口内的有效样本数。"""
        return len(self._win)

    @property
    def ready(self) -> bool:
        """窗口是否已填满（填满才可能出数）。"""
        return len(self._win) >= self.need

    def push(self, q15: int | None, frame_id: int) -> None:
        """喂一个样本。`q15=None` 表示这一帧按契约被丢弃（不喂、不补值）。"""
        if q15 is None:
            return
        ys, _sat, self._hist = fir_process([int(q15)], self.coeffs, self.shift, self._hist)
        self._win.append(ys[0])
        self._fid.append(int(frame_id))
        self._valid += 1

    def estimate(self) -> dict:
        """返回契约要求的四个键（`hr_bpm` / `hr_conf` / `rr_per_min` / `rr_conf`）。

        出不了可信结果时**四项全为 None** —— 这是产品承诺（"先判断能不能测"），不是缺陷。
        `rr_*` 恒为 None：契约 §3.5 已冻结"63 阶在 45 fps 下做不了呼吸带"，
        呼吸率必须由 PS 侧先降采样再用**另一组系数**，而那组系数尚未冻结（见 §3.5 已知限制）。
        """
        none4 = {"hr_bpm": None, "hr_conf": None, "rr_per_min": None, "rr_conf": None}
        if not self.ready:
            return dict(none4)

        import numpy as np

        y = np.asarray(self._win, dtype=np.float64)
        fids = np.asarray(self._fid, dtype=np.float64)
        n = y.size

        # 有效采样率：窗口首尾帧号跨越的真实时间（漏帧时 < fps）
        span_s = (fids[-1] - fids[0]) / self.fps
        if span_s <= 0:
            return dict(none4)
        fs_eff = (n - 1) / span_s
        if not (0 < fs_eff <= self.fps * 1.001):
            return dict(none4)

        # 去直流 + Hann 窗（抑制谱泄漏）→ 实 FFT
        yw = (y - y.mean()) * np.hanning(n)
        power = np.abs(np.fft.rfft(yw)) ** 2
        freqs = np.fft.rfftfreq(n, d=1.0 / fs_eff)

        band = (freqs >= self.f_lo) & (freqs <= self.f_hi)
        if int(band.sum()) < 3:
            return dict(none4)
        p_band = power[band]
        f_band = freqs[band]
        total = float(p_band.sum())
        if total <= 0:
            return dict(none4)

        k = int(np.argmax(p_band))
        lo_i, hi_i = max(0, k - 1), min(p_band.size, k + 2)
        conf = float(p_band[lo_i:hi_i].sum()) / total

        # 结构性判据：峰邻域的能量必须占带内**过半**，否则视为"没有真峰"
        if conf <= 0.5:
            return dict(none4)

        return {
            "hr_bpm": round(float(f_band[k]) * 60.0, 1),
            "hr_conf": round(conf, 4),
            "rr_per_min": None,
            "rr_conf": None,
        }
