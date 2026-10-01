# -*- coding: utf-8 -*-
"""
dma_test.py —— C9 上板 DMA 测试：回环 + 缓存一致性 + 长跑不死锁

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）

对应验收（fpga/report/m3_system_budget_v1.md 第 5 节门限 / board/README C9）：
  门限 5  DMA 回环：短数组进出无随机错误（缓存一致性）
  门限 6  流深度不死锁：连续 ≥300 帧真实 DMA 节奏下不 stall
  门限 7  确定性：同一段 300 帧跑两次，寄存器结果逐位相同

用法（在 **Mizar-Z7020** 上；🚧 v1.3 板卡变更待会签）：
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


def selftest(golden_gray=None):
    """离线自检（**不需要板卡**）：验"测试图生成 + 黄金参考档位"两件事。

    为什么这两件值得离线验：
      ① `make_frame()` 是**确定性**测试图（LCG，无 numpy），上板时"两次长跑逐位一致"
         （门限 7）就靠它可复现。若它的字节序/取值悄悄变了，门限 7 会变成
         "两次都一样地错"，**照样报 PASS** —— 所以必须钉住它的**具体字节**。
      ② `--golden-gray` 的比对读 `R.GRAY_PIXELS` 字节。若给的 gray.bin 是**别的档位**的
         （v1.5 后 720p/1080p 是 480×270，而 640×480 档是 384×288），
         在板子上会表现成"PL 与黄金参考不一致"，**实际是数据配错**。
         本项目已踩过三次同类（tb Layer1 写死 3/5、rgb2gray cosim 3/5 数据跑 3/8、
         fir_filter 45 Hz 数据跑 30 Hz 系数表），所以先在这里拦。
    """
    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    print("=" * 66)
    print("[离线自检] dma_test 的测试图与黄金参考（不需要板卡）")
    print("=" * 66)
    print(f"  档位 VIGILENS_TIER = {R.TIER}  (帧 {R.FRAME_W}x{R.FRAME_H}, 灰度 {R.GRAY_W}x{R.GRAY_H})")
    print("-" * 66)

    # ---- ① 测试图：长度 + 确定性（同 seed 同字节）+ LCG 具体值 ----
    f1 = make_frame(20260911)
    f2 = make_frame(20260911)
    chk("make_frame 长度 == RGB_BYTES", len(f1) == R.RGB_BYTES,
        f"{len(f1)} vs {R.RGB_BYTES}")
    chk("make_frame 同 seed 逐字节相同（门限 7 可复现的前提）", f1 == f2)
    chk("make_frame 不同 seed 必须不同（防 seed 被忽略）", make_frame(1) != make_frame(2))

    # LCG 具体值：`state = 1664525*state + 1013904223 (mod 2^32)`，取 (state>>24)&0xFF。
    # 这里按同一条式子独立重算前 4 个字节，钉住"实现没被悄悄改过"。
    st = 20260911 & 0xFFFFFFFF
    expect = []
    for _ in range(4):
        st = (1664525 * st + 1013904223) & 0xFFFFFFFF
        expect.append((st >> 24) & 0xFF)
    chk("make_frame 前 4 字节 == 独立重算的 LCG 序列",
        list(f1[:4]) == expect, f"got {list(f1[:4])} want {expect}")

    # 值域必须铺满 0..255（否则测试图太"平"，缓存一致性检查会失去分辨力）
    uniq = len(set(f1))
    chk("测试图字节值域足够宽（>=200 种取值）", uniq >= 200, f"distinct={uniq}")

    # ---- ② 黄金参考：必须与本档 GRAY_PIXELS 配套 ----
    if golden_gray is not None:
        import os
        if not os.path.exists(golden_gray):
            chk("golden_gray 文件存在", False, golden_gray)
        else:
            size = os.path.getsize(golden_gray)
            chk("golden_gray 至少 1 帧（GRAY_PIXELS）", size >= R.GRAY_PIXELS,
                f"{size} B vs need {R.GRAY_PIXELS} B")
            chk("golden_gray 能被本档 GRAY_PIXELS 整除（否则是别的档的数据）",
                size % R.GRAY_PIXELS == 0, f"{size} % {R.GRAY_PIXELS} = {size % R.GRAY_PIXELS}")
    else:
        print("  [info] 未给 --golden-gray：跳过黄金参考档位检查")
        print("         （上板做容差 0 比对时应给，且必须与本档配套）")

    bad = [n for n, ok, _ in checks if not ok]
    print("")
    for n, ok, detail in checks:
        line = f"  [{'PASS' if ok else 'FAIL'}] {n}"
        if detail:
            line += f"   ({detail})"
        print(line)
    print("-" * 66)
    print("RESULT:", "PASS" if not bad else "FAIL")
    print("⚠️ 本自检只证明'测试图可复现 + 黄金参考配套'；DMA 回环/长跑本身必须上板跑。")
    return 0 if not bad else 1


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
    ap.add_argument("--selftest", action="store_true",
                    help="离线自检：只验测试图与黄金参考配套，不需要板卡")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest(golden_gray=args.golden_gray)

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
