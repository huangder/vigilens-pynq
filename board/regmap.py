# -*- coding: utf-8 -*-
"""
regmap.py —— 四个 HLS IP 的 AXI-Lite 寄存器偏移（PS 侧单一来源）

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
权威来源：`docs/interface.md` §3（契约 v1.0 已冻结；偏移均已与实综合逐行核对）。

⚠️ 本文件是 PS 侧的"偏移量镜像"。契约 §5.3 冻结纪律：改任何偏移必须先改
   docs/interface.md 并走会签流程，再同步本文件 —— 不允许"代码悄悄改"。

离线自检（无需板卡、无第三方依赖）：
    python board/regmap.py --selftest
"""

# ---------------------------------------------------------------------------
# 全局冻结口径（docs/interface.md §0）
# ---------------------------------------------------------------------------
FRAME_W = 640            # 输入图像宽（RGB888）
FRAME_H = 480            # 输入图像高
RGB_BYTES = FRAME_W * FRAME_H * 3     # 单帧字节数 = 921600

# ---- 测量口径档位（与 config.yaml 的 tier_* 键、run_hls.tcl 的 VIGILENS_TIER 同一组）----
#  2026-09-30：v1.5 草案把测量口径档位化，**灰度工作尺寸不再恒为 3/5 的 384×288**：
#      · 640×480 档（既有）      ： 3/5 -> 384×288（110592 像素）
#      · 720p60（主档）          ： 3/8 -> 480×270（129600 像素）
#      · 1080p45（备档）         ： 1/4 -> 480×270（129600 像素）
#  后两档的像素数**都在 2^17 以内**，所以 motion_quality 的片内帧缓存仍是 64 个 BRAM18。
#  ⚠️ 原来这里把 GRAY_W/GRAY_H 写死 384×288 并且自检也断言它 —— 档位化之后，
#     那份自检会在 720p/1080p 档**悄悄断言错的东西**（与 tb_rgb2gray.cpp Layer 1 同一类缺陷）。
#     现在改成**由档位推导 + 自检跟着推导值走**。
import os as _os

_TIERS = {
    "640x480": {"w": 640, "h": 480, "num": 3, "den": 5},
    "720p60": {"w": 1280, "h": 720, "num": 3, "den": 8},
    "1080p45": {"w": 1920, "h": 1080, "num": 1, "den": 4},
}
# 档位来源优先级：环境变量（跑测试时用）> 缺省既有档。
# ⚠️ 刻意**不**在 import 时去读 config.yaml：本文件要求"无第三方依赖也能跑"
#    （见文件头"离线自检"），而 PyYAML 在本机可能缺失；且它是 PS 侧镜像，
#    拿到板卡上跑时未必有 config.yaml 在旁边。口径仍以 docs/interface.md 为准。
TIER = _os.environ.get("VIGILENS_TIER", "640x480")
if TIER not in _TIERS:
    raise ValueError("未知档位 VIGILENS_TIER=%r，可选：%s" % (TIER, sorted(_TIERS)))
_T = _TIERS[TIER]
FRAME_W, FRAME_H = _T["w"], _T["h"]
DECIM_NUM, DECIM_DEN = _T["num"], _T["den"]
RGB_BYTES = FRAME_W * FRAME_H * 3

GRAY_W = FRAME_W // DECIM_DEN * DECIM_NUM   # rgb2gray 输出 / motion_quality 工作宽
GRAY_H = FRAME_H // DECIM_DEN * DECIM_NUM   # 输出高
GRAY_PIXELS = GRAY_W * GRAY_H

# 图像帧率 = 时间序列采样率。**45/30/60 三个数在仓库里同时存在**，故必须写明属于哪一份口径：
#   · docs/interface.md **v1.1（生效）** 冻结 45 fps
#   · v1.2 草案（2026-09-20，待会签）改成 30 Hz；config.yaml 的 fps_nominal 现为 30
#   · 用户 2026-09-30 选定两条链路：720p60 = 60 fps、1080p45 = 45 fps
# ⚠️ 所以 FPS **不能**再当成一个全局常量硬编码（那正是 BUG-004：本文件 45、config 30，
#    上板做时间换算时整体差 1.5 倍）。这里跟着档位走。
_FPS_BY_TIER = {"640x480": 30, "720p60": 60, "1080p45": 45}
FPS = _FPS_BY_TIER[TIER]

FIR_TAPS = 63            # fir_filter 阶数（I 型线性相位）
FIR_COEFF_SHIFT = 15     # Q1.15 移位
FIR_GROUP_DELAY = 31     # (N-1)/2，群延迟（样本）

# ---------------------------------------------------------------------------
# AXI-Lite 通用控制寄存器（所有 IP 一致，docs/interface.md §3.1）
# ---------------------------------------------------------------------------
CTRL = 0x00            # RW：bit0 AP_START / bit1 AP_DONE / bit2 AP_IDLE / bit3 AP_READY
GIER = 0x04            # 全局中断使能
IP_IER = 0x08            # IP 中断使能
IP_ISR = 0x0C            # IP 中断状态

# CTRL 寄存器 bit 位（HLS 标准 s_axilite 约定）
AP_START = 0
AP_DONE = 1
AP_IDLE = 2
AP_READY = 3
AUTO_RESTART = 7
INTERRUPT = 9

# ---------------------------------------------------------------------------
# 每个输出数据寄存器后面都跟着一个 *_ctrl（ap_vld）寄存器（偏移 = 数据 + 4）。
# 契约 §3.2 明确：判断"统计值是否已刷新"要查 *_ctrl 的 bit0，配合 frame_id/seg_id。
# ---------------------------------------------------------------------------

class RoiStatistic:
    """roi_statistic v1（docs/interface.md §3.2）"""
    roi_x0 = 0x10   # W  ROI 左边界（含，11 bit）
    roi_y0 = 0x18   # W  ROI 上边界（含）
    roi_x1 = 0x20   # W  ROI 右边界（不含）
    roi_y1 = 0x28   # W  ROI 下边界（不含）
    width = 0x30   # W  图像宽（640）
    height = 0x38   # W  图像高（480）
    sum_r = 0x40   # R  （+0x44 sum_r_ctrl）
    sum_g = 0x48   # R  （+0x4c sum_g_ctrl）
    sum_b = 0x50   # R  （+0x54 sum_b_ctrl）
    count = 0x58   # R  （+0x5c count_ctrl）
    frame_id = 0x60   # R  （+0x64 frame_id_ctrl）


class Rgb2Gray:
    """rgb2gray v2（docs/interface.md §3.4）：640×480 RGB → 384×288 灰度"""
    width = 0x10   # W  输入宽 640（须为 5 的整数倍）
    height = 0x18   # W  输入高 480
    out_width = 0x20   # R  输出宽 384（+0x24 _ctrl）
    out_height = 0x28   # R  输出高 288（+0x2c _ctrl）
    pixel_count = 0x30   # R  输出像素数 110592（+0x34 _ctrl）
    sum_gray = 0x38   # R  灰度累加和（+0x3c _ctrl）
    frame_id = 0x40   # R  （+0x44 _ctrl）


class MotionQuality:
    """motion_quality v2（docs/interface.md §3.3）：工作尺寸 384×288 灰度"""
    width = 0x10   # W  工作灰度宽 = 384
    height = 0x18   # W  工作灰度高 = 288
    motion_thresh = 0x20   # W  运动判定阈值（u8，暂定 16）
    diff_total = 0x28   # R  帧差总量 Σ|cur-prev|（+0x2c _ctrl）
    motion_pixels = 0x30   # R  运动像素个数（+0x34 _ctrl）
    motion_ratio_q16 = 0x38   # R  运动比例 Q16（+0x3c _ctrl）
    count = 0x40   # R  本帧像素数 110592（+0x44 _ctrl）
    frame_id = 0x48   # R  （+0x4c _ctrl）


class FirFilter:
    """fir_filter v1（docs/interface.md §3.5）：时间序列，TDATA=16（Q1.15）"""
    n_samples = 0x10   # W  本段样本数（1..65535）
    reset = 0x18   # W  1 = 读取样本前清空延迟线与饱和计数
    out_count = 0x20   # R  本段输出样本数（== n_samples）（+0x24 _ctrl）
    saturation_count = 0x28   # R  本段饱和样本数（+0x2c _ctrl）
    seg_id = 0x30   # R  自 IP 上电以来的调用序号，从 1 开始（+0x34 _ctrl）


# ---------------------------------------------------------------------------
# 自检：把本文件的偏移量与契约 §3 的写死值核对，防止手滑改错。
# ---------------------------------------------------------------------------

def _next_pow2(n):
    """下一个 2 的幂（HLS 片内数组按 2 的幂地址空间分配，见 fpga/report/c4 §5）。"""
    p = 1
    while p < n:
        p <<= 1
    return p


def _motion_capacity_pixels():
    """读 `fpga/src/motion_quality_cap.h` 里 motion_quality 片内"上一帧"缓存的容量。

    ⚠️ **为什么不在这里写死 129600**：那正是 BUG-004 / BUG-033 的老路 ——
    "常量对常量"的自检在任何档位下都自洽，**档位一改就自洽地错**。
    容量只有一个来源（C 头文件），这里只是把它读出来用。

    读不到/解析不出时返回 None —— 自检会因此 **FAIL**，不会静默通过。
    """
    cap_h = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                          "..", "fpga", "src", "motion_quality_cap.h")
    try:
        vals = {}
        with open(cap_h, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if (len(parts) == 3 and parts[0] == "#define"
                        and parts[1] in ("MOTION_MAX_W", "MOTION_MAX_H")):
                    vals[parts[1]] = int(parts[2])
        if len(vals) != 2:
            return None
        return vals["MOTION_MAX_W"] * vals["MOTION_MAX_H"]
    except (OSError, ValueError):
        return None


def selftest():
    checks = [
        ("CTRL/GIER/IP_IER/IP_ISR", [CTRL, GIER, IP_IER, IP_ISR], [0x00, 0x04, 0x08, 0x0C]),
        ("CTRL bit", [AP_START, AP_DONE, AP_IDLE, AP_READY, AUTO_RESTART, INTERRUPT],
         [0, 1, 2, 3, 7, 9]),
        #   左值 = 本文件声明的常量，右值 = **由档位维度推导**出来的值。
        #   写成 `[FRAME_W, ...]` 对 `[FRAME_W, ...]` 是恒真的废检查（我第一版就写错了），
        #   所以右值必须是**能独立算出来的具体数**：
        ("global size（右值由尺寸独立算出，不是自我比较）",
         [FRAME_W, FRAME_H, RGB_BYTES, GRAY_W, GRAY_H, GRAY_PIXELS],
         [640 if TIER == "640x480" else (1280 if TIER == "720p60" else 1920),
          480 if TIER == "640x480" else (720 if TIER == "720p60" else 1080),
          (640 * 480 * 3) if TIER == "640x480" else ((1280 * 720 * 3) if TIER == "720p60" else (1920 * 1080 * 3)),
          {640: 384, 1280: 480, 1920: 480}[FRAME_W],
          {480: 288, 720: 270, 1080: 270}[FRAME_H],
          {640: 110592, 1280: 129600, 1920: 129600}[FRAME_W]]),
        #   v1.5 的唯一技术要点：**两档的灰度像素数都必须 ≤ 2^17**，否则 BRAM 台阶跨过去，
        #   motion_quality 的片内帧缓存就从 64 个 BRAM18 跳到 128/256（器件只有 280）。
        ("档位自洽：灰度像素数 ≤ 2^17（BRAM 台阶不跨）",
         [_next_pow2(GRAY_PIXELS) <= 131072], [True]),
        #   motion_quality 的片内"上一帧"缓存是**编译期定长**的，而工作尺寸由 PS 在运行时给：
        #   某档的灰度像素数一旦超过容量，csim 越界（实测表现是"编译完成后挂死"，极易
        #   误判成算力问题）、RTL 越界（地址越出 ram，读回 X）。台账 **BUG-033**。
        #   ⚠️ 容量**只从 C 头文件读**，这里不写死第二份。
        ("档位自洽：灰度像素数 ≤ motion_quality 片内容量（读 fpga/src/motion_quality_cap.h）",
         [GRAY_PIXELS <= (_motion_capacity_pixels() or 0)], [True]),
        #   相位完备：输入宽高必须是抽取分母的整数倍，否则最后一组不满、相位错位。
        ("档位自洽：输入宽高是抽取分母的整数倍（相位完备）",
         [FRAME_W % DECIM_DEN, FRAME_H % DECIM_DEN], [0, 0]),
        ("fir", [FIR_TAPS, FIR_COEFF_SHIFT, FIR_GROUP_DELAY], [63, 15, 31]),
        ("roi_statistic", [RoiStatistic.roi_x0, RoiStatistic.roi_y0, RoiStatistic.roi_x1,
                           RoiStatistic.roi_y1, RoiStatistic.width, RoiStatistic.height,
                           RoiStatistic.sum_r, RoiStatistic.sum_g, RoiStatistic.sum_b,
                           RoiStatistic.count, RoiStatistic.frame_id],
         [0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48, 0x50, 0x58, 0x60]),
        ("rgb2gray", [Rgb2Gray.width, Rgb2Gray.height, Rgb2Gray.out_width, Rgb2Gray.out_height,
                      Rgb2Gray.pixel_count, Rgb2Gray.sum_gray, Rgb2Gray.frame_id],
         [0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40]),
        ("motion_quality", [MotionQuality.width, MotionQuality.height, MotionQuality.motion_thresh,
                            MotionQuality.diff_total, MotionQuality.motion_pixels,
                            MotionQuality.motion_ratio_q16, MotionQuality.count,
                            MotionQuality.frame_id],
         [0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40, 0x48]),
        ("fir_filter", [FirFilter.n_samples, FirFilter.reset, FirFilter.out_count,
                        FirFilter.saturation_count, FirFilter.seg_id],
         [0x10, 0x18, 0x20, 0x28, 0x30]),
    ]
    failed = 0
    for name, got, want in checks:
        ok = (got == want)
        if not ok:
            failed += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"       got  = {got}")
            print(f"       want = {want}")
    print("-" * 40)
    print("RESULT:", "PASS" if failed == 0 else f"{failed} FAILED")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if selftest() else 1)
