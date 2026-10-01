# -*- coding: utf-8 -*-
"""
bringup_check.py —— C8 上板自检：AXI-Lite 寄存器读写 + 计数器复位语义

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）

对应验收（fpga/report/m3_system_budget_v1.md 第 5 节门限）：
  门限 3  寄存器读写：按契约偏移逐个读写自检，含每个输出的 *_ctrl(ap_vld)
  门限 4  复位语义（P0-2）：显式 PL 复位后 frame_id / seg_id 从 1 开始

用法（在 **Mizar-Z7020** 上，bitstream 已下载到板；🚧 v1.3 板卡变更待会签）：
  python board/bringup_check.py --bit system.bit            # 只跑寄存器自检（不需 DMA）
  python board/bringup_check.py --bit system.bit --frame fpga/sim/data/frames.bin \
      --frames 2 --with-dma                                # 再加复位语义自检（需 DMA 已接好）

退出码：0 = 全 PASS；1 = 有 FAIL。
"""

import argparse
import sys

import regmap as R

# 本文件可在 PC 上 py_compile（pynq 只在 load() 内导入）
from overlay.load_overlay import (load, read_reg, write_reg, ap_idle, ap_done,
                                  ap_start, ap_wait_done, run_pixel_chain,
                                  run_fir_segment, pulse_pl_reset)


# ---------------------------------------------------------------------------
# Stage 1：寄存器映射 smoke 自检（门限 3 的"地址能访问"部分）
# ---------------------------------------------------------------------------

def stage1_register_smoke(handles):
    """每个 IP 读一次 CTRL，确认 mmio 可达；报告 ap_idle / ap_ready。"""
    print("=" * 60)
    print("[Stage 1] 寄存器映射 smoke 自检（读 CTRL + 报告控制位）")
    print("=" * 60)
    ok = True
    for key, ip in handles.items():
        if key in ("dma", "fifo_tx", "fifo_rx"):
            continue
        try:
            ctrl = read_reg(ip, R.CTRL)
            idle = bool((ctrl >> R.AP_IDLE) & 1)
            ready = bool((ctrl >> R.AP_READY) & 1)
            print(f"  [{key}] CTRL=0x{ctrl:08X}  ap_idle={int(idle)} ap_ready={int(ready)}")
            # 未启动时 ap_idle 应为 1（HLS 上电后即 idle）
            if not idle:
                print(f"  [FAIL] {key} 上电后 ap_idle 应为 1，实测 {int(idle)}")
                ok = False
        except Exception as e:  # noqa: BLE001 —— 自检脚本要能报告失败而非崩
            print(f"  [FAIL] {key} 读 CTRL 异常：{e}")
            ok = False
    return ok


# ---------------------------------------------------------------------------
# Stage 2：写回自检（门限 3 的"值能写进去"部分）
# ---------------------------------------------------------------------------

def stage2_write_back(handles):
    """写若干 W 寄存器再读回对比。

    注意：HLS s_axilite 的输入寄存器实现为可读回；但个别 IP/工具版本可能读回 0，
    故此阶段结论标注为"信息性"，读回不一致时提示人工核对而非直接判 FAIL。
    """
    print("=" * 60)
    print("[Stage 2] W 寄存器写回自检")
    print("=" * 60)
    roi, rgb, mot, fir = handles["roi"], handles["rgb"], handles["mot"], handles["fir"]

    # ⚠️ 期望值一律取自 regmap（= 契约 §0 的单一来源），**不写字面量**。
    #    原来这里写死 640/480/384/288：档位化之后（v1.5：720p/1080p 两档的灰度是 480×270），
    #    这些字面量会与真正下载的 bitstream 口径不一致，而且**看着仍然"自洽"**——
    #    与 `tb_rgb2gray.cpp` Layer 1、`regmap.py` 旧自检是同一类缺陷。
    #    现在改成推导值：档位由 `VIGILENS_TIER` 选（缺省 640×480，行为不变）。
    cases = [
        ("roi.width", roi, R.RoiStatistic.width, R.FRAME_W),
        ("roi.height", roi, R.RoiStatistic.height, R.FRAME_H),
        ("roi.roi_x0", roi, R.RoiStatistic.roi_x0, 0),
        ("roi.roi_x1", roi, R.RoiStatistic.roi_x1, R.FRAME_W),
        ("rgb.width", rgb, R.Rgb2Gray.width, R.FRAME_W),
        ("rgb.height", rgb, R.Rgb2Gray.height, R.FRAME_H),
        ("mot.width", mot, R.MotionQuality.width, R.GRAY_W),
        ("mot.height", mot, R.MotionQuality.height, R.GRAY_H),
        ("mot.motion_thresh", mot, R.MotionQuality.motion_thresh, 16),
        ("fir.n_samples", fir, R.FirFilter.n_samples, 8),
        ("fir.reset", fir, R.FirFilter.reset, 1),
    ]
    all_match = True
    for name, ip, off, val in cases:
        write_reg(ip, off, val)
        got = read_reg(ip, off)
        match = (got == val)
        all_match &= match
        mark = "PASS" if match else "info"
        print(f"  [{mark}] {name}: 写 {val} 读回 {got}" + ("" if match else "  (读回不一致，人工核对)"))
    print(f"  写回一致：{all_match}")
    return all_match


# ---------------------------------------------------------------------------
# Stage 3：计数器复位语义自检（门限 4）
# ---------------------------------------------------------------------------

def stage3_reset_semantics(ol, handles, frame_rgb, *, n_pre=2):
    """PL 复位前跑 n_pre 帧（frame_id 递增），复位后跑 1 帧应回到 1。"""
    print("=" * 60)
    print("[Stage 3] 计数器复位语义自检（frame_id 从 1 开始）")
    print("=" * 60)

    ids = []
    for i in range(n_pre):
        res = run_pixel_chain(ol, handles, frame_rgb)
        ids.append(res["roi"]["frame_id"])
        print(f"  复位前第 {i + 1} 帧 roi.frame_id = {res['roi']['frame_id']}")
    expect_inc = (ids == list(range(1, n_pre + 1)))
    if not expect_inc:
        print(f"  [FAIL] 复位前 frame_id 应为 1..{n_pre}，实测 {ids}")
        return False

    print("  -> 触发 PL 复位 ...")
    pulse_pl_reset(ol)

    res = run_pixel_chain(ol, handles, frame_rgb)
    after = res["roi"]["frame_id"]
    print(f"  复位后第 1 帧 roi.frame_id = {after}")
    if after != 1:
        print(f"  [FAIL] 复位后首次调用 frame_id 应为 1，实测 {after}")
        return False

    # FIR 侧同理（seg_id）
    fir_out = run_fir_segment(ol, handles, [0] * 8, reset=True)
    print(f"  复位后首段 fir.seg_id = {fir_out['seg_id']}")
    if fir_out["seg_id"] != 1:
        print(f"  [FAIL] 复位后首次 FIR 调用 seg_id 应为 1，实测 {fir_out['seg_id']}")
        return False

    print("  [PASS] 复位语义正确（frame_id / seg_id 复位后从 1 开始）")
    return True


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _load_frame(path):
    if path is None:
        # 默认：全零帧（足够验证寄存器/复位语义，不用于算法比对）
        return bytes(R.RGB_BYTES)
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < R.RGB_BYTES:
        raise ValueError(f"帧文件 {path} 不足一帧（{len(data)} < {R.RGB_BYTES}）")
    return data[:R.RGB_BYTES]


def selftest():
    """离线自检（**不需要板卡/bitstream**）：把本脚本将用到的口径打印出来并断言其自洽。

    为什么值得有：上板脚本以前只能 `py_compile`，口径对不对**要等到板子到手那天才知道**。
    而它用的期望值全部来自 `regmap`（档位化之后会随 `VIGILENS_TIER` 变），
    所以最该先验的就是"**口径自洽**"：帧尺寸/灰度尺寸/单帧字节数三者必须一致，
    且 Stage 2 真的用的是这些值而不是字面量。
    """
    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    print("=" * 60)
    print("[离线自检] bringup_check 的口径（不需要板卡）")
    print("=" * 60)
    print(f"  档位 VIGILENS_TIER = {R.TIER}")
    print(f"  帧尺寸   FRAME_W x FRAME_H = {R.FRAME_W} x {R.FRAME_H}  (RGB_BYTES={R.RGB_BYTES})")
    print(f"  灰度尺寸 GRAY_W  x GRAY_H  = {R.GRAY_W} x {R.GRAY_H}  (GRAY_PIXELS={R.GRAY_PIXELS})")
    print(f"  抽取比   {R.DECIM_NUM}/{R.DECIM_DEN}   帧率 FPS={R.FPS}")
    print("-" * 60)

    chk("RGB_BYTES == FRAME_W*FRAME_H*3", R.RGB_BYTES == R.FRAME_W * R.FRAME_H * 3)
    chk("GRAY_PIXELS == GRAY_W*GRAY_H", R.GRAY_PIXELS == R.GRAY_W * R.GRAY_H)
    chk("灰度尺寸 == 帧尺寸按抽取比推导",
        (R.GRAY_W, R.GRAY_H) == (R.FRAME_W // R.DECIM_DEN * R.DECIM_NUM,
                                 R.FRAME_H // R.DECIM_DEN * R.DECIM_NUM))
    # 相位完备：输入宽高必须是抽取分母的整数倍，否则最后一组不满、相位错位
    chk("输入宽高是抽取分母的整数倍（相位完备）",
        R.FRAME_W % R.DECIM_DEN == 0 and R.FRAME_H % R.DECIM_DEN == 0,
        f"{R.FRAME_W}%{R.DECIM_DEN}={R.FRAME_W % R.DECIM_DEN}, "
        f"{R.FRAME_H}%{R.DECIM_DEN}={R.FRAME_H % R.DECIM_DEN}")
    # v1.5 的关键约束：灰度像素数必须 ≤ 2^17，否则 motion_quality 的片内帧缓存跨 BRAM 台阶
    p = 1
    while p < R.GRAY_PIXELS:
        p <<= 1
    chk("灰度像素数 <= 2^17（BRAM 台阶不跨）", p <= 131072, f"next_pow2={p}")
    # regmap 自身的一致性（偏移表）也要过
    chk("regmap.selftest() 通过", R.selftest())

    bad = [n for n, ok, _ in checks if not ok]
    print("")
    for n, ok, detail in checks:
        line = f"  [{'PASS' if ok else 'FAIL'}] {n}"
        if detail:
            line += f"   ({detail})"
        print(line)
    print("-" * 60)
    print("RESULT:", "PASS" if not bad else "FAIL")
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="C8 上板自检")
    ap.add_argument("--bit", default="system.bit", help="bitstream 路径（默认 system.bit）")
    ap.add_argument("--frame", default=None, help="单帧 RGB888 文件（默认全零帧）")
    ap.add_argument("--with-dma", action="store_true", help="追加 Stage 3 复位语义自检（需 DMA 已接好）")
    ap.add_argument("--motion-thresh", type=int, default=16)
    ap.add_argument("--selftest", action="store_true",
                    help="离线自检：只验口径自洽，不需要板卡/bitstream")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    ol, handles = load(args.bit)
    results = {}

    results["stage1"] = stage1_register_smoke(handles)
    results["stage2"] = stage2_write_back(handles)

    if args.with_dma:
        frame = _load_frame(args.frame)
        results["stage3"] = stage3_reset_semantics(ol, handles, frame)

    print("=" * 60)
    print("总结果：", {k: ("PASS" if v else "FAIL") for k, v in results.items()})
    all_ok = all(results.values())
    print("=" * 60)
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
