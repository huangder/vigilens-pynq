#!/usr/bin/env python3
# =============================================================================
#  link_budget.py —— 像素源链路预算判定器（分辨率 × 帧率 × 格式 能不能用）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#
#  这个脚本回答一个问题：**某个「分辨率 + 帧率 + 格式」组合到底能不能落地？**
#  它把两条独立的硬约束算出来并给出判定：
#    ① 链路：MIPI CSI-2 2-lane 的每 lane 净载荷 vs 可用 lane 速率
#    ② 片内：PL 里"存上一帧"的 BRAM 占用（HLS 按 2 的幂地址空间分配，台阶式）
#
#  ⚠️ 这是**算术判定，不是硬件实测**。它用到的输入全部标了出处：
#     · Mizar-Z7 手册：MIPI CSI 口实测 **672 Mbps/channel**；芯片规格上限 950 Mbps/lane
#     · Sony IMX219PQH5-C 规格书：Data rate **Max. 912 Mbps/lane (@2lane)**；
#       "Max. 30 frame/s in all-pixel scan mode"；"60 frame/s @1080p with V-crop"；
#       "180 frame/s @720p with 2x2 analog (special) binning"；Pixel rate 280 Mpixel/s
#     · 本项目 fpga/report/c4：片内数组按 2 的幂分配，2^17 像素 -> 64 个 BRAM18
#
#  ⚠️ 一个**刻意不装作知道**的量：CSI-2 协议开销 + 行/帧消隐占多少。
#     本脚本把它做成显式参数 `--efficiency`（默认 0.85 = 留 15% 余量），
#     并在输出里同时给出"乐观（无开销）"和"含余量"两列，让判定不依赖于这个假设。
#
#  用法：
#     python fpga/sim/link_budget.py
#     python fpga/sim/link_budget.py --json metrics/logs/link_budget.json
# =============================================================================

import argparse
import json
import os
import sys

# 控制台默认是 GBK，直接 print 中文/箭头会抛 UnicodeEncodeError —— 用仓库既有的助手
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
try:
    from console import enable_utf8_console  # type: ignore
    enable_utf8_console()
except Exception:  # 单独拷走本脚本时也不能崩
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---- 有出处的事实常量 -------------------------------------------------------
MIZAR_LANE_MBPS_TESTED = 672.0    # MicroPhase《Mizar-Z7 Reference Manual》MIPI CSI 节：实测
MIZAR_LANE_MBPS_CHIP = 950.0      # 同上：Zynq-7000 芯片规格上限
IMX219_LANE_MBPS_2LANE = 912.0    # Sony IMX219PQH5-C 规格书：Max. 912 Mbps/Lane (@2lane)
IMX219_PIXEL_RATE_MPPS = 280.0    # 同上：Pixel rate 280 Mpixel/s (All-pixels mode)

LANES = 2                          # 15-pin 树莓派相机接口 = 2 data lane + 1 clock lane

# ---- 片内 BRAM 规则（来源：fpga/report/c4_rgb2gray_motion_quality_v1.md 第 5 节）----
#   2^17 像素的 8bit 数组 -> 64 个 BRAM18 ⇒ 每个 BRAM18 承载 16384 bit
BRAM18_BITS = 16384.0
DEVICE_BRAM18 = 280.0              # xc7z020 共 280 个 BRAM18（fpga/report/c4 记载）


def next_pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def bram18_for(pixels, bits_per_pixel=8):
    """HLS 片内数组按 2 的幂地址空间分配：先取下一个 2 的幂，再算 BRAM18 个数。"""
    return int(-(-(next_pow2(pixels) * bits_per_pixel) // BRAM18_BITS))


# ---- 待判定的组合 -----------------------------------------------------------
# 格式只影响链路载荷；本项目的 PL 流水线口径是 RGB888，传感器口径是 RAW10。
def entry(name, w, h, pixbits, fps, note=""):
    payload_bits = w * h * pixbits * fps
    per_lane = payload_bits / LANES
    return {
        "name": name, "w": w, "h": h, "pixbits": pixbits, "fps": fps,
        "note": note,
        "payload_mbps": payload_bits / 1e6,
        "per_lane_mbps": per_lane / 1e6,
        "pixel_rate_mpps": (w * h * fps) / 1e6,
    }


def build_cases():
    c = []
    # 传感器侧：IMX219 出 RAW10（10 bit/像素）
    c.append(entry("1080p RAW10 @30", 1920, 1080, 10, 30, "all-pixel 之外的裁切/缩放读出"))
    c.append(entry("1080p RAW10 @45", 1920, 1080, 10, 45, "树莓派官方列出的 1080p45"))
    c.append(entry("1080p RAW10 @60", 1920, 1080, 10, 60, "规格书仅『60fps @1080p with V-crop』"))
    c.append(entry("720p RAW10 @60", 1280, 720, 10, 60, "规格书：720p 2x2 binning 可达 180fps"))
    c.append(entry("720p RAW10 @30", 1280, 720, 10, 30))
    c.append(entry("480p RAW10 @60", 640, 480, 10, 60))
    c.append(entry("480p RAW10 @100", 640, 480, 10, 100, "树莓派官方列出的 480p100"))
    # PL 流水线侧：契约是 RGB888（24 bit/像素）。
    # ⚠️ 这几行**不适用 MIPI lane 判定**：流水线的 RGB888 是从 PL 经 AXI DMA 写 DDR 的，
    #    不走 MIPI。列在这里只是为了看清"如果强行把 RGB888 塞进同一条 2-lane 链路会怎样"，
    #    以及 DMA/存储侧的相对量级。判定时请只看片内那一列。
    c.append(entry("640x480 RGB888 @30", 640, 480, 24, 30, "契约 §0 基准"))
    c.append(entry("640x480 RGB888 @45", 640, 480, 24, 45))
    c.append(entry("640x480 RGB888 @60", 640, 480, 24, 60))
    c.append(entry("1280x720 RGB888 @60", 1280, 720, 24, 60, "整条流水线抬到 720p 时的 DMA 侧"))
    c.append(entry("1920x1080 RGB888 @60", 1920, 1080, 24, 60, "整条流水线抬到 1080p"))
    return c


def main():
    ap = argparse.ArgumentParser(description="像素源链路预算判定器")
    ap.add_argument("--efficiency", type=float, default=0.85,
                    help="链路可用效率（1.0 = 完全无协议开销/消隐；默认 0.85 = 留 15% 余量）")
    ap.add_argument("--json", default=None, help="把结果写到该 JSON 文件")
    args = ap.parse_args()
    eff = args.efficiency

    print("=" * 100)
    print("像素源链路预算判定  ·  2 lane MIPI CSI-2  ·  链路效率假设 %.2f" % eff)
    print("=" * 100)
    print("可用 lane 速率（三档，取最保守的为准）：")
    print("  Mizar 实测          : %.0f Mbps/lane  <- **本方案按这一条判定**" % MIZAR_LANE_MBPS_TESTED)
    print("  Zynq-7000 芯片规格  : %.0f Mbps/lane" % MIZAR_LANE_MBPS_CHIP)
    print("  IMX219 规格书上限   : %.0f Mbps/lane (@2lane)" % IMX219_LANE_MBPS_2LANE)
    print("  IMX219 像素率上限   : %.0f Mpixel/s" % IMX219_PIXEL_RATE_MPPS)
    print()

    cases = build_cases()
    hdr = ("%-22s %10s %10s %10s %10s %8s %8s %8s"
           % ("组合", "载荷Mbps", "每lane", "乐观占比", "含余量", "像素率", "链路", "片内"))
    print(hdr)
    print("-" * len(hdr))

    results = []
    verdicts = {}
    for e in cases:
        raw_util = e["per_lane_mbps"] / MIZAR_LANE_MBPS_TESTED
        eff_util = e["per_lane_mbps"] / (MIZAR_LANE_MBPS_TESTED * eff)
        # 链路判定：含余量下的占比 <= 1.0 才算通过
        link_ok = eff_util <= 1.0
        # 传感器判定
        sensor_ok = e["pixel_rate_mpps"] <= IMX219_PIXEL_RATE_MPPS
        # 片内判定：本项目需要"存上一帧"的只有 motion_quality，其尺寸 = 输入灰度的 3/5 抽取
        gw = e["w"] // 5 * 3
        gh = e["h"] // 5 * 3
        gw = gw if gw > 0 else e["w"]
        gh = gh if gh > 0 else e["h"]
        bram = bram18_for(gw * gh, 8)
        mem_pct = bram / DEVICE_BRAM18 * 100.0
        mem_ok = mem_pct <= 50.0     # 只给 motion_quality 留一半器件（还要放 DMA/CSI-2/互连）

        mark = "OK" if (link_ok and sensor_ok) else "NG"
        verdicts[e["name"]] = {"link_ok": link_ok, "sensor_ok": sensor_ok,
                               "mem_ok": mem_ok, "eff_util": eff_util,
                               "bram18": bram}
        print("%-22s %10.1f %10.1f %9.0f%% %9.0f%% %7.1f  %6s %5d/%.0f%%"
              % (e["name"], e["payload_mbps"], e["per_lane_mbps"],
                 raw_util * 100, eff_util * 100, e["pixel_rate_mpps"],
                 mark, bram, mem_pct))
        results.append(e)

    print()
    print("=" * 100)
    print("判定说明")
    print("=" * 100)
    print("  · 乐观占比 = 每lane净载荷 / Mizar实测672（**不含**协议开销与消隐）")
    print("  · 含余量   = 每lane净载荷 / (672 x 效率)。>100% 即判定为**不可行**")
    print("  · 片内     = motion_quality 存上一帧所需的 BRAM18 个数及占器件(280)比例")
    print("               （尺寸按既有 rgb2gray 的 3/5 抽取推算；>50% 视为放不下其余逻辑）")
    print()

    def show(name, yes, no):
        v = verdicts[name]
        print("  %-22s %s" % (name, yes if (v["link_ok"] and v["sensor_ok"]) else no))

    show("1080p RAW10 @60", "通过", "**不通过**")
    show("720p RAW10 @60", "通过", "**不通过**")
    show("480p RAW10 @60", "通过", "**不通过**")
    print()
    v1080 = verdicts["1080p RAW10 @60"]
    v720 = verdicts["720p RAW10 @60"]
    print("  ⇒ 1080p@60 含余量占比 %.0f%%（阈值 100%%）；720p@60 为 %.0f%%"
          % (v1080["eff_util"] * 100, v720["eff_util"] * 100))
    print("  ⇒ 结论：**1080p + 60fps 不可用**；60fps 主档必须落在 720p（或 480p）")
    print("  ⇒ 整条流水线抬到 720p 时 motion_quality 需 %d 个 BRAM18（%.0f%%）→ 同样不可行"
          % (verdicts["1280x720 RGB888 @60"]["bram18"],
             verdicts["1280x720 RGB888 @60"]["bram18"] / DEVICE_BRAM18 * 100))
    print("     故正解是：**720p 采集 → PL 内缩放到 640×480 测量**（四个既有 IP 一行不改）")
    print()
    print("  ⚠️ 本脚本不含任何硬件实测：Mizar 从未上电，相机未购。结论是**规格+算术判定**。")

    if args.json:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", newline="\n") as f:
            json.dump({"efficiency": eff, "cases": results, "verdicts": verdicts},
                      f, ensure_ascii=False, indent=2)
        print("\n已写 %s" % args.json)

    # 退出码：1080p60 必须被判定为不可行，否则说明阈值被改坏了
    bad = verdicts["1080p RAW10 @60"]["link_ok"]
    if bad:
        print("\n!! 自检失败：1080p@60 在本阈值下被判为可行 —— 请核对输入常量")
        return 1
    print("\n[自检] 1080p@60 判定为不可行 ✅（若不成立，说明常量或效率假设被改动）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
