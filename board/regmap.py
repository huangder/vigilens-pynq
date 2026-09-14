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
GRAY_W = 384             # rgb2gray 输出 / motion_quality 工作宽
GRAY_H = 288             # 输出高
GRAY_PIXELS = GRAY_W * GRAY_H         # 110592
FPS = 45                 # 图像帧率 = 时间序列采样率（45 Hz）

FIR_TAPS = 63            # fir_filter 阶数（I 型线性相位）
FIR_COEFF_SHIFT = 15     # Q1.15 移位
FIR_GROUP_DELAY = 31     # (N-1)/2，群延迟（样本）

# ---------------------------------------------------------------------------
# AXI-Lite 通用控制寄存器（所有 IP 一致，docs/interface.md §3.1）
# ---------------------------------------------------------------------------
CTRL   = 0x00            # RW：bit0 AP_START / bit1 AP_DONE / bit2 AP_IDLE / bit3 AP_READY
GIER   = 0x04            # 全局中断使能
IP_IER = 0x08            # IP 中断使能
IP_ISR = 0x0C            # IP 中断状态

# CTRL 寄存器 bit 位（HLS 标准 s_axilite 约定）
AP_START      = 0
AP_DONE       = 1
AP_IDLE       = 2
AP_READY      = 3
AUTO_RESTART  = 7
INTERRUPT     = 9

# ---------------------------------------------------------------------------
# 每个输出数据寄存器后面都跟着一个 *_ctrl（ap_vld）寄存器（偏移 = 数据 + 4）。
# 契约 §3.2 明确：判断"统计值是否已刷新"要查 *_ctrl 的 bit0，配合 frame_id/seg_id。
# ---------------------------------------------------------------------------

class RoiStatistic:
    """roi_statistic v1（docs/interface.md §3.2）"""
    roi_x0   = 0x10   # W  ROI 左边界（含，11 bit）
    roi_y0   = 0x18   # W  ROI 上边界（含）
    roi_x1   = 0x20   # W  ROI 右边界（不含）
    roi_y1   = 0x28   # W  ROI 下边界（不含）
    width    = 0x30   # W  图像宽（640）
    height   = 0x38   # W  图像高（480）
    sum_r    = 0x40   # R  （+0x44 sum_r_ctrl）
    sum_g    = 0x48   # R  （+0x4c sum_g_ctrl）
    sum_b    = 0x50   # R  （+0x54 sum_b_ctrl）
    count    = 0x58   # R  （+0x5c count_ctrl）
    frame_id = 0x60   # R  （+0x64 frame_id_ctrl）


class Rgb2Gray:
    """rgb2gray v2（docs/interface.md §3.4）：640×480 RGB → 384×288 灰度"""
    width       = 0x10   # W  输入宽 640（须为 5 的整数倍）
    height      = 0x18   # W  输入高 480
    out_width   = 0x20   # R  输出宽 384（+0x24 _ctrl）
    out_height  = 0x28   # R  输出高 288（+0x2c _ctrl）
    pixel_count = 0x30   # R  输出像素数 110592（+0x34 _ctrl）
    sum_gray    = 0x38   # R  灰度累加和（+0x3c _ctrl）
    frame_id    = 0x40   # R  （+0x44 _ctrl）


class MotionQuality:
    """motion_quality v2（docs/interface.md §3.3）：工作尺寸 384×288 灰度"""
    width            = 0x10   # W  工作灰度宽 = 384
    height           = 0x18   # W  工作灰度高 = 288
    motion_thresh    = 0x20   # W  运动判定阈值（u8，暂定 16）
    diff_total       = 0x28   # R  帧差总量 Σ|cur-prev|（+0x2c _ctrl）
    motion_pixels    = 0x30   # R  运动像素个数（+0x34 _ctrl）
    motion_ratio_q16 = 0x38   # R  运动比例 Q16（+0x3c _ctrl）
    count            = 0x40   # R  本帧像素数 110592（+0x44 _ctrl）
    frame_id         = 0x48   # R  （+0x4c _ctrl）


class FirFilter:
    """fir_filter v1（docs/interface.md §3.5）：时间序列，TDATA=16（Q1.15）"""
    n_samples        = 0x10   # W  本段样本数（1..65535）
    reset            = 0x18   # W  1 = 读取样本前清空延迟线与饱和计数
    out_count        = 0x20   # R  本段输出样本数（== n_samples）（+0x24 _ctrl）
    saturation_count = 0x28   # R  本段饱和样本数（+0x2c _ctrl）
    seg_id           = 0x30   # R  自 IP 上电以来的调用序号，从 1 开始（+0x34 _ctrl）


# ---------------------------------------------------------------------------
# 自检：把本文件的偏移量与契约 §3 的写死值核对，防止手滑改错。
# ---------------------------------------------------------------------------

def selftest():
    checks = [
        ("CTRL/GIER/IP_IER/IP_ISR", [CTRL, GIER, IP_IER, IP_ISR], [0x00, 0x04, 0x08, 0x0C]),
        ("CTRL bit", [AP_START, AP_DONE, AP_IDLE, AP_READY, AUTO_RESTART, INTERRUPT],
         [0, 1, 2, 3, 7, 9]),
        ("global size", [FRAME_W, FRAME_H, RGB_BYTES, GRAY_W, GRAY_H, GRAY_PIXELS],
         [640, 480, 921600, 384, 288, 110592]),
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
