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
#   时间戳估计另有一个处理分支：原始有效 Q1.15 → 等间隔化 → 同一冻结 FIR → FFT。
#   对拍该分支时必须提供同一份派生输入，不能把它与非等间隔原序列逐点混比。
# ===========================================================================


def forehead_roi(bbox, width: int, height: int) -> tuple[int, int, int, int]:
    """从人脸框里取**额头 ROI**（半开区间），返回 `(x0, y0, x1, y1)`。

    为什么取额头：皮肤暴露、运动伪影比脸颊小、避开眼睛与嘴（眨眼/说话是强干扰源）。

    **前提：`bbox` 是整张脸的外接框**（额头到下巴，见 `face_landmark.bbox_from_points`
    与台账 BUG-010/011）。所以这里取框高的 **8%~30%**、框宽中间 **50%**：
    · 上界不用 0（框顶就是发际线，贴着边容易吃头发）；
    · 下界留到 30% —— 眼睛大约在整脸框的 40%~50% 高处，30% 以内不会碰到眼睛；
    · 左右各留 25% —— 避开脸缘的高光/阴影与耳朵。

    人脸框无效（宽或高 <= 0）时返回空 ROI（四项相等，累加结果为 0）。
    """
    x, y, w, h = (int(v) for v in bbox)
    if w <= 0 or h <= 0:
        return 0, 0, 0, 0
    x0 = x + int(w * 0.25)
    x1 = x + int(w * 0.75)
    y0 = y + int(h * 0.08)
    y1 = y + int(h * 0.30)
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
      · **滑窗填满才出数**：窗口长度取 `config.yaml` 的 `rppg_window_seconds`，缺省沿用 `window_seconds`，
        没填满一律返回 null —— 没测够就是没测够，不拿半个窗去猜；
      · **带通复用冻结 FIR**：有真实时间戳时先将原始有效样本等间隔化，再用同一组
        Q15 系数与整数算术；未传时间戳的离线兼容路径保留流式 FIR；
      · **峰不过半就不报数**：判定"找到真峰"的条件是**峰值 ±1 个频点内的能量占带内总能量过半**。
        这是**结构性判据**（"这个峰是否占主导"），不是可调阈值，所以没有引入新的魔法数字；
      · **丢弃的帧不补成采集样本**：空 ROI/丢脸清窗，短时质量/姿态暂停期间拒报。
        插值仅形成处理网格，窗口就绪仍以真实有效样本数计算，长缺口继续拒绝；
      · **倍频歧义拒报**：主峰半频处有足够强的独立峰时，不把最高峰直接称作心率。
    """

    def __init__(self, *, fps: float, window_seconds: float, hr_band_hz,
                 coeffs: list[int] | None = None, shift: int | None = None,
                 fs_tol: float = 0.10, jitter_ratio_max: float | None = None,
                 max_gap_ms: float | None = None, recovery_ms: float = 0,
                 recovery_samples: int | None = None, harmonic_ratio_min: float | None = None):
        design_fps = None
        if coeffs is None or shift is None:
            coeffs, shift, _fs = load_fir_coeffs()
            design_fps = float(_fs)
        self.coeffs = coeffs
        self.shift = shift
        self.fps = float(fps)
        self.fir_design_fps = design_fps
        # 真实采样率与标称值的相对偏差上限（`config.yaml: rppg_fs_tol`，默认 0.10）。
        # 为什么必须有这条护栏：FIR 系数是按 `self.fps` 设计的，真实采样率偏 k 倍时
        # 频率轴整体缩放 k 倍 ⇒ 报出来的是"看起来正常但错误的数"（docs/16 BUG-029：
        # 13 fps 实测把 72 bpm 报成 166 bpm、conf 0.9998）。宁可 null，不报错数。
        self.fs_tol = float(fs_tol)
        try:
            from .config import load_config
        except ImportError:
            from config import load_config
        defaults = load_config()
        if jitter_ratio_max is None:
            jitter_ratio_max = defaults["realtime_jitter_ratio_max"]
        self.jitter_ratio_max = float(jitter_ratio_max)
        self.max_gap_s = float(defaults["rppg_max_gap_ms"] if max_gap_ms is None else max_gap_ms) / 1000
        # Direct callers retain immediate-reset semantics unless explicitly
        # enabled. The production from_config path enables bounded recovery.
        self.recovery_s = float(recovery_ms) / 1000
        self.recovery_samples = int(defaults["rppg_recovery_samples"] if recovery_samples is None else recovery_samples)
        self.harmonic_ratio_min = float(defaults["rppg_harmonic_ratio_min"] if harmonic_ratio_min is None else harmonic_ratio_min)
        self.f_lo, self.f_hi = float(hr_band_hz[0]), float(hr_band_hz[1])
        self.need = max(2, int(round(float(window_seconds) * self.fps)))
        if (not 0 < self.f_lo < self.f_hi < self.fps / 2 or self.max_gap_s <= 0
                or not 0 <= self.recovery_s <= self.max_gap_s or self.recovery_samples < 1
                or not 0 < self.harmonic_ratio_min < 1):
            raise ValueError("invalid rPPG frequency/gap/recovery/harmonic configuration")
        from collections import Counter
        self._reject_counts = Counter()
        self._reset_counts = Counter()
        self._invalid_counts = Counter()
        self.total_samples = self.max_segment_samples = self.max_window_samples = 0
        self.resample_calls = self.soft_pause_frames = self.resumed_segments = 0
        self.last_reset_reason = None
        self.reset()

    @classmethod
    def from_config(cls, cfg, fps: float | None = None) -> "GreenRppg":
        """按 `config.yaml` 建实例（采样率取 `fps_nominal`，与契约 §0 一致）。"""
        try:
            from .config import rppg_window_seconds
        except ImportError:
            from config import rppg_window_seconds
        return cls(fps=float(fps if fps is not None else cfg["fps_nominal"]),
                   window_seconds=rppg_window_seconds(cfg),
                   hr_band_hz=cfg["hr_band_hz"],
                   fs_tol=float(cfg.get("rppg_fs_tol", 0.10)),
                   jitter_ratio_max=float(cfg["realtime_jitter_ratio_max"]),
                   max_gap_ms=float(cfg["rppg_max_gap_ms"]), recovery_ms=float(cfg["rppg_recovery_ms"]),
                   recovery_samples=int(cfg["rppg_recovery_samples"]),
                   harmonic_ratio_min=float(cfg["rppg_harmonic_ratio_min"]))

    def reset(self, reason: str = "manual") -> None:
        from collections import deque

        if getattr(self, "_valid", 0):
            self._reset_counts[reason] += 1
            self.last_reset_reason = reason
        self._win = deque(maxlen=self.need)      # 带通后的样本
        self._fid = deque(maxlen=self.need)      # 对应的 frame_id（无时间戳时反推采样率用）
        self._ts = deque(maxlen=self.need)       # 对应的**采集侧真实时间戳**（活帧源才有；离线为 None）
        self._hist = None                        # FIR 段间状态（连续流语义）
        # Extra raw history supplies the fixed FIR's warmup before each window.
        # Derived uniform-grid points are never counted as captured samples.
        self._raw = deque()
        # Memory is bounded even for malformed high-rate inputs. Normal
        # retention is by elapsed warmup time, not an assumed local rate.
        self._raw_limit = 2 * self.need + self.warmup_samples + 1
        self._history_limited = False
        self._blocked_reason = None
        self._recovery_left = 0
        self._valid = 0                          # 累计有效样本数
        self.last_fs_eff: float | None = None    # 最近一次 estimate() 用的有效采样率（诊断用）
        self.last_time_base: str | None = None   # "source_ts" / "fid_nominal"（诊断用）
        self.last_reject: str | None = None      # 最近一次"不报数"的原因（诊断用）
        self.last_conf: float | None = None      # 最近一次"峰邻域能量占比"（诊断用）
        self.last_interval_p95_s: float | None = None
        self.last_uniform_samples = 0
        self.last_harmonic_ratio = None
        self.last_spectral_fs = None

    def invalidate(self, reason: str, ts: float | None, *, allow_recovery: bool = False) -> None:
        """Never reuse an estimate while invalid; retain only bounded soft gaps."""
        import math
        self._invalid_counts[reason] += 1
        previous_ts = self._ts[-1] if self._ts else None
        short = (allow_recovery and self.recovery_s > 0 and self.samples > 0
                 and ts is not None and previous_ts is not None
                 and math.isfinite(ts) and math.isfinite(previous_ts)
                 and 0 < ts - previous_ts <= self.recovery_s)
        if short:
            self.soft_pause_frames += 1
            self._recovery_left = self.recovery_samples
        else:
            self.reset(reason)
        self._blocked_reason = reason

    def stats(self) -> dict:
        return {"estimate_calls": sum(self._reject_counts.values()),
                "reject_counts": dict(self._reject_counts), "reset_counts": dict(self._reset_counts),
                "invalid_counts": dict(self._invalid_counts), "raw_samples_total": self.total_samples,
                "max_segment_samples": self.max_segment_samples, "max_window_samples": self.max_window_samples,
                "soft_pause_frames": self.soft_pause_frames, "resumed_segments": self.resumed_segments,
                "resample_calls": self.resample_calls, "last_uniform_samples": self.last_uniform_samples,
                "last_spectral_fs": self.last_spectral_fs,
                "raw_history_samples": len(self._raw),
                "last_harmonic_ratio": self.last_harmonic_ratio, "last_reset_reason": self.last_reset_reason,
                "recovery_samples_remaining": self._recovery_left}

    @property
    def samples(self) -> int:
        """当前窗口内的有效样本数。"""
        return len(self._win)

    @property
    def ready(self) -> bool:
        """窗口是否已填满（填满才可能出数）。"""
        return len(self._win) >= self.need

    @property
    def last_sample_ts(self) -> float | None:
        return self._ts[-1] if self._ts else None

    @property
    def warmup_samples(self) -> int:
        return len(self.coeffs) - 1

    @property
    def nominal_history_samples(self) -> int:
        return self.need

    def push(self, q15: int | None, frame_id: int, ts: float | None = None) -> None:
        """喂一个样本。`q15=None` 表示这一帧按契约被丢弃（不喂、不补值）。

        `ts` 是**采集侧的真实到达时间**（`capture.Frame.ts`）。活帧源必须传：
        只有这样 `estimate()` 才能按真实采样率算频率轴（docs/16 BUG-029 / docs/37 R14）。
        离线回放（视频文件）传 `None`：那些源的 wall clock 反映的是解码速度，不是采样间隔，
        用 `frame_id / fps_nominal` 反而可复现。
        """
        if q15 is None:
            return
        if self._blocked_reason is not None and self._recovery_left:
            # A late recovery cannot reuse a history that was merely paused.
            last = self._ts[-1]
            if ts is None or last is None or not 0 < ts - last <= self.recovery_s:
                self.reset("recovery_timeout")
            else:
                self.resumed_segments += 1
        self._blocked_reason = None
        if self._recovery_left:
            self._recovery_left -= 1
        ys, _sat, self._hist = fir_process([int(q15)], self.coeffs, self.shift, self._hist)
        self._win.append(ys[0])
        self._fid.append(int(frame_id))
        self._ts.append(None if ts is None else float(ts))
        self._raw.append((None if ts is None else float(ts), int(q15)))
        import math
        first_ts = self._ts[0]
        if first_ts is not None and math.isfinite(first_ts):
            cutoff = first_ts - self.warmup_samples / self.fps
            while (len(self._raw) > self.need and self._raw[1][0] is not None
                   and math.isfinite(self._raw[1][0]) and self._raw[1][0] <= cutoff):
                self._raw.popleft()
        elif ts is None:
            while len(self._raw) > self.nominal_history_samples:
                self._raw.popleft()
        while len(self._raw) > self._raw_limit:
            self._raw.popleft()
            self._history_limited = True
        self._valid += 1
        self.total_samples += 1
        self.max_segment_samples = max(self.max_segment_samples, self._valid)
        self.max_window_samples = max(self.max_window_samples, self.samples)

    def estimate(self) -> dict:
        result = self._estimate()
        self._reject_counts[self.last_reject or "accepted"] += 1
        return result

    def _uniform_context(self, n: int):
        """Return captured context and a uniform grid without extrapolation."""
        import numpy as np
        raw = list(self._raw)
        # Only extend the window's warmup into a valid monotonic prefix.
        first = len(raw) - n
        while first > 0:
            previous, current = raw[first - 1][0], raw[first][0]
            if (previous is None or not np.isfinite(previous) or previous >= current
                    or current - previous > self.max_gap_s):
                break
            first -= 1
        context = raw[first:]
        times = np.asarray([p[0] for p in context], dtype=np.float64)
        end, start = times[-1], float(self._ts[0])
        if self._history_limited and times[0] > start - self.warmup_samples / self.fps:
            return None
        # Endpoint-aligned grids need no extrapolation. A sub-ULP short window
        # may have one fewer derived point; captured sample count is unchanged.
        full_count = int(np.floor((end - times[0]) * self.fps)) + 1
        count = int(np.floor((end - start) * self.fps)) + 1
        grid = end - np.arange(full_count - 1, -1, -1) / self.fps
        return first, times, grid, count

    def _uniform_filtered(self, n: int):
        """Re-grid raw valid samples at the frozen rate BEFORE FIR, no extrapolation."""
        import numpy as np
        context = self._uniform_context(n)
        if context is None:
            return None
        first, times, grid, count = context
        values = np.asarray([p[1] for p in list(self._raw)[first:]], dtype=np.float64)
        sampled = np.interp(grid, times, values)
        # Same half-away rounding as the frozen Q1.15 interface. These are
        # derived processing samples, not fabricated camera observations.
        quantized = np.where(sampled >= 0, np.floor(sampled + .5), np.ceil(sampled - .5)).astype(np.int64)
        filtered, _, _ = fir_process(quantized, self.coeffs, self.shift)
        self.resample_calls += 1
        self.last_uniform_samples = count
        return np.asarray(filtered[-count:], dtype=np.float64)

    def _nominal_filtered(self, n: int):
        import numpy as np
        return np.asarray(self._win, dtype=np.float64)

    def _estimate(self) -> dict:
        """返回契约要求的四个键（`hr_bpm` / `hr_conf` / `rr_per_min` / `rr_conf`）。

        出不了可信结果时**四项全为 None** —— 这是产品承诺（"先判断能不能测"），不是缺陷。
        `rr_*` 恒为 None：契约 §3.5 已冻结"63 阶在 45 fps 下做不了呼吸带"，
        呼吸率必须由 PS 侧先降采样再用**另一组系数**，而那组系数尚未冻结（见 §3.5 已知限制）。
        """
        none4 = {"hr_bpm": None, "hr_conf": None, "rr_per_min": None, "rr_conf": None}
        self.last_reject = None      # 每次重新判，避免上一次的原因粘住
        self.last_conf = None        # 同上：避免上一次的置信度粘住
        self.last_interval_p95_s = None
        self.last_harmonic_ratio = None
        self.last_uniform_samples = 0
        self.last_spectral_fs = None
        self._signal_error = None
        if self._blocked_reason is not None:
            self.last_reject = self._blocked_reason
            return dict(none4)
        if self._recovery_left:
            self.last_reject = "recovering"
            return dict(none4)

        if self.fir_design_fps is not None and self.fps != self.fir_design_fps:
            # A CLI rate override cannot silently turn the frozen 30 Hz FIR
            # into a filter designed for 60 Hz (or another sampling rate).
            self.last_reject = "fir_rate_mismatch"
            return dict(none4)

        import numpy as np

        y = np.asarray(self._win, dtype=np.float64)
        fids = np.asarray(self._fid, dtype=np.float64)
        n = y.size

        # ---- 时间基准（docs/16 BUG-029）------------------------------------
        # 首选采集侧真实时间戳：漏帧/降速都会被如实反映。
        # 没有时间戳（离线回放）才退回 frame_id/fps —— 那条路径**恒等于标称 fps**，
        # 因此它只能证明"帧号连续"，不能证明"采样率正确"，诊断里如实标注。
        #
        # ⚠️ 这一段放在"窗口是否填满"之前：真机上窗口要 30 s 才填满（实测链路只有 8~17 fps），
        # 如果只在填满后才算，运维要等半分钟才知道"采样率根本对不上"。
        fs_eff = None
        if n >= 2:
            ts_list = list(self._ts)
            has_any_ts = any(t is not None for t in ts_list)
            has_ts = (len(ts_list) == n and all(t is not None for t in ts_list))
            time_base = "fid_nominal"
            span_s = (fids[-1] - fids[0]) / self.fps
            if has_any_ts:
                ts_arr = np.asarray(ts_list, dtype=np.float64)
                if not has_ts or not np.all(np.isfinite(ts_arr)) or not np.all(np.diff(ts_arr) > 0):
                    self.last_time_base = "invalid_source_ts"
                    self.last_fs_eff = None
                    self.last_reject = "invalid_timestamps"
                    return dict(none4)
                time_base = "source_ts"
                span_s = float(ts_arr[-1] - ts_arr[0])
                self.last_interval_p95_s = float(np.percentile(np.diff(ts_arr), 95))
            self.last_time_base = time_base
            if span_s <= 0:
                self.last_fs_eff = None
                self.last_reject = "no_time_span"
                return dict(none4)
            fs_eff = (n - 1) / span_s
            self.last_fs_eff = float(fs_eff)
            if time_base == "source_ts":
                # 真实采样率偏离标称值过多：频率轴会整体缩放，报出来的 bpm 不可辩护 → 不报数。
                if abs(fs_eff - self.fps) > self.fs_tol * self.fps:
                    self.last_reject = "fs_mismatch"
                    return dict(none4)
                if self.last_interval_p95_s * self.fps > self.jitter_ratio_max:
                    self.last_reject = "sampling_jitter"
                    return dict(none4)
                if float(np.diff(ts_arr).max()) > self.max_gap_s:
                    self.last_reject = "sampling_gap"
                    return dict(none4)
            elif not (0 < fs_eff <= self.fps * 1.001):
                self.last_reject = "fs_eff_implausible"
                return dict(none4)

        if not self.ready or fs_eff is None:
            self.last_reject = self.last_reject or "window_not_full"
            return dict(none4)
        self.last_reject = None
        if self.last_time_base == "source_ts":
            y = self._uniform_filtered(n)
            fs_eff = self.fps
        else:
            y = self._nominal_filtered(n)
        if y is None:
            self.last_reject = self._signal_error or "warmup_history_unavailable"
            return dict(none4)
        n = y.size
        self.last_spectral_fs = float(fs_eff)

        # 去直流 + Hann 窗（抑制谱泄漏）→ 实 FFT
        yw = (y - y.mean()) * np.hanning(n)
        power = np.abs(np.fft.rfft(yw)) ** 2
        freqs = np.fft.rfftfreq(n, d=1.0 / fs_eff)

        band = (freqs >= self.f_lo) & (freqs <= self.f_hi)
        if int(band.sum()) < 3:
            # 诊断口径：**每一条 return 都必须写清原因**，否则运维只能看到 null，
            # 分不清"没测够 / 没峰 / 采样率不对"（docs/16 BUG-041）。
            self.last_reject = "band_too_narrow"
            return dict(none4)
        p_band = power[band]
        f_band = freqs[band]
        total = float(p_band.sum())
        if total <= 0:
            # 带内一点能量都没有：典型是恒定输入（去直流后全 0）或整窗全被丢帧。
            self.last_reject = "no_band_energy"
            return dict(none4)

        k = int(np.argmax(p_band))
        lo_i, hi_i = max(0, k - 1), min(p_band.size, k + 2)
        conf = float(p_band[lo_i:hi_i].sum()) / total

        self.last_conf = float(conf)

        # 结构性判据：峰邻域的能量必须占带内**过半**，否则视为"没有真峰"。
        # ⚠️ 这一条是 BUG-041 的现场：以前它**静默返回**，`last_reject` 还是 None 或
        # 上一轮的旧值，于是"窗口已满 900/900 却全 null"无法从 summary 里看出原因。
        if conf <= 0.5:
            self.last_reject = "no_dominant_peak"
            return dict(none4)

        half = float(f_band[k]) / 2
        if self.f_lo <= half <= self.f_hi:
            j = int(np.argmin(np.abs(f_band - half)))
            # Require an actual local peak, not the dominant peak's Hann skirt
            # leaking into the half-frequency neighborhood of a short window.
            candidates = range(max(0, j - 1), min(p_band.size, j + 2))
            j = max(candidates, key=lambda index: p_band[index])
            local_peak = ((j == 0 or p_band[j] > p_band[j - 1])
                          and (j + 1 == p_band.size or p_band[j] > p_band[j + 1]))
            half_energy = float(p_band[max(0, j - 1):min(p_band.size, j + 2)].sum()) if local_peak else 0.0
            dominant_energy = float(p_band[lo_i:hi_i].sum())
            self.last_harmonic_ratio = half_energy / dominant_energy
            if local_peak and self.last_harmonic_ratio >= self.harmonic_ratio_min:
                # A strong half-frequency component makes the pulse rate
                # ambiguous. Refuse rather than confidently double/halve it.
                self.last_reject = "harmonic_ambiguity"
                return dict(none4)

        return {
            "hr_bpm": round(float(f_band[k]) * 60.0, 1),
            "hr_conf": round(conf, 4),
            "rr_per_min": None,
            "rr_conf": None,
        }
