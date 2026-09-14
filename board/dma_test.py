# -*- coding: utf-8 -*-
"""
dma_test.py —— C9 上板 DMA 测试：回环 + 缓存一致性 + 长跑不死锁

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）

对应验收（fpga/report/m3_system_budget_v1.md 第 5 节门限 / board/README C9）：
  门限 5  DMA 回环：短数组进出无随机错误（缓存一致性）
  门限 6  流深度不死锁：连续 ≥300 帧真实 DMA 节奏下不 stall
  门限 7  确定性：同一段 300 帧跑两次，寄存器结果逐位相同

用法（在 PYNQ-Z2 上）：
  python board/dma_test.py --bit system.bit                       # 回环 smoke
  python board/dma_test.py --bit system.bit --frames 300          # 回环 + 长跑
  python board/dma_test.py --bit system.bit --golden-gray fpga/sim/data_motion/gray.bin

退出码：0 = PASS；1 = FAIL。

⚠️ motion_quality 的"第 0 帧输出无效"（prev_buf 上电未定义，契约 §3.3），
   故确定性比对里 motion 相关寄存器从第 1 帧起比对，roi/rgb 从第 0 帧起比对。
"""

import argparse
import sys
import time

import regmap as R
from overlay.load_overlay import load, run_pixel_chain


def make_frame(seed: int = 0) -> bytes:
    """生成一帧确定性 RGB888 测试图（640x480，无 numpy 依赖，字节可复现）。"""
    out = bytearray()
    n = R.RGB_BYTES
    state = seed & 0xFFFFFFFF
    for i in range(n):
        # 简单 LCG，保证可复现；值域 0..255
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        out.append((state >> 24) & 0xFF)
    return bytes(out)


def _load_frame(path):
    if path is None:
        return make_frame(20260911)
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < R.RGB_BYTES:
        raise ValueError(f"帧文件不足一帧：{len(data)} < {R.RGB_BYTES}")
    return data[:R.RGB_BYTES]


# ---------------------------------------------------------------------------
# 回环 + 缓存一致性（门限 5）
# ---------------------------------------------------------------------------

def loopback_roundtrip(ol, handles, frame, *, golden_gray=None, n_runs=2):
    """同一帧跑 n_runs 次，S2MM 回读灰度逐字节一致 → 无随机错误（缓存一致性）。"""
    print("=" * 60)
    print("[门限 5] DMA 回环 / 缓存一致性自检")
    print("=" * 60)

    grays, rois = [], []
    for i in range(n_runs):
        res = run_pixel_chain(ol, handles, frame)
        grays.append(res["gray_out"])
        rois.append(tuple(res["roi"][k] for k in ("sum_r", "sum_g", "sum_b", "count")))
        print(f"  run {i}: roi(sum_r,g,b,count)={rois[-1]} gray_bytes={len(grays[-1])}")

    ok = True
    for i in range(1, n_runs):
        if grays[i] != grays[0]:
            print(f"  [FAIL] 回读灰度 run{i} != run0（疑似缓存一致性问题）")
            ok = False
        if rois[i] != rois[0]:
            print(f"  [FAIL] roi 寄存器 run{i} != run0")
            ok = False

    if ok:
        print(f"  [PASS] {n_runs} 次回读灰度与 roi 寄存器逐字节/逐位一致")

    if golden_gray is not None:
        with open(golden_gray, "rb") as f:
            gold = f.read(R.GRAY_PIXELS)
        if grays[0] == gold:
            print("  [PASS] 回读灰度与 C 线黄金 gray.bin 逐字节相等（容差 0）")
        else:
            print("  [FAIL] 回读灰度与 golden gray.bin 不一致（容差 0 判据）")
            ok = False

    return ok


# ---------------------------------------------------------------------------
# 长跑不死锁 + 确定性（门限 6 / 7）
# ---------------------------------------------------------------------------

def long_run(ol, handles, frame, *, n=300, runs=2):
    """连续 n 帧不 stall，且跑 runs 次结果逐位相同。"""
    print("=" * 60)
    print(f"[门限 6/7] 长跑 {n} 帧不死锁 + 确定性（{runs} 次）")
    print("=" * 60)

    def run_once():
        ids = []
        snapshots = []
        t0 = time.monotonic()
        for i in range(n):
            res = run_pixel_chain(ol, handles, frame)
            if not res["all_done"]:
                print(f"  [FAIL] 第 {i} 帧有 IP 未在超时内置 ap_done（疑似 stall）")
                return None
            ids.append(res["roi"]["frame_id"])
            # 只保留用于确定性比对的量（motion 第 0 帧无效，跳过）
            snap = {
                "gray": res["gray_out"],
                "roi": tuple(res["roi"][k] for k in ("sum_r", "sum_g", "sum_b", "count")),
            }
            if i >= 1:
                snap["mot"] = tuple(res["mot"][k] for k in ("diff_total", "motion_pixels",
                                                            "motion_ratio_q16", "count"))
            snapshots.append(snap)
        dt = time.monotonic() - t0
        return ids, snapshots, dt

    first = run_once()
    if first is None:
        return False
    ids1, snap1, dt1 = first

    ok = True
    # 门限 6：frame_id 必须单调 1..n，说明没丢帧没挂死
    expect = list(range(1, n + 1))
    if ids1 != expect:
        print(f"  [FAIL] frame_id 序列不连续：首尾 {ids1[0]}..{ids1[-1]}，应 1..{n}")
        ok = False
    else:
        print(f"  [PASS] 门限6：{n} 帧 frame_id 连续 1..{n}，无 stall（{dt1:.2f}s）")

    # 门限 7：再跑一次，逐位一致
    second = run_once()
    if second is None:
        return False
    ids2, snap2, dt2 = second
    same = (snap1 == snap2)
    if not same:
        print("  [FAIL] 门限7：两次长跑快照不一致（非确定性）")
        # 定位第一个差异帧
        for i, (a, b) in enumerate(zip(snap1, snap2)):
            if a != b:
                print(f"       首个差异在第 {i} 帧")
                break
        ok = False
    else:
        print(f"  [PASS] 门限7：两次长跑逐帧逐位一致（{dt2:.2f}s）")

    return ok


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="C9 上板 DMA 测试")
    ap.add_argument("--bit", default="system.bit")
    ap.add_argument("--frame", default=None, help="单帧 RGB888 文件（默认生成确定性图）")
    ap.add_argument("--golden-gray", default=None, help="C 线 golden gray.bin，做容差 0 比对")
    ap.add_argument("--frames", type=int, default=300, help="长跑帧数（门限 6，默认 300）")
    ap.add_argument("--runs", type=int, default=2, help="确定性重复次数（门限 7，默认 2）")
    ap.add_argument("--skip-long", action="store_true", help="只跑回环 smoke，不跑长跑")
    args = ap.parse_args(argv)

    ol, handles = load(args.bit)
    frame = _load_frame(args.frame)

    results = {}
    results["loopback"] = loopback_roundtrip(ol, handles, frame,
                                             golden_gray=args.golden_gray)
    if not args.skip_long:
        results["long_run"] = long_run(ol, handles, frame, n=args.frames, runs=args.runs)

    print("=" * 60)
    print("总结果：", {k: ("PASS" if v else "FAIL") for k, v in results.items()})
    all_ok = all(results.values())
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
