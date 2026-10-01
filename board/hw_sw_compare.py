# -*- coding: utf-8 -*-
"""
hw_sw_compare.py —— C10 软硬件一致性比对（PL vs 黄金参考，容差 0）

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）

对应验收（fpga/report/m3_system_budget_v1.md 门限 8 / board/README C10）：
  同一帧下 PL 的 sum_r/g/b、灰度、diff_total 与 A 线软件结果**容差 0**；
  FIR 输出**逐样本相等**。
  对比表模板见 fpga/report/m4_baseline_v1.md 第 5 节，本脚本产出其中的
  "硬件 vs 黄金参考"那一列，供人工回填到报告。

黄金参考来源（C 线已入库，容差 0）：
  fpga/sim/data/golden_roi.csv            roi_statistic（40 行 = 8 用例 × 5 帧）
  fpga/sim/data_motion/gray.bin           rgb2gray 输出灰度（10 帧 × 384×288）
  fpga/sim/data_motion/golden_motion.csv  motion_quality（帧 1..9）
  fpga/sim/data_fir/series.bin            fir_filter 输入（int16 LE）
  fpga/sim/data_fir/golden_fir.csv        每段小结（15 段）
  fpga/sim/data_fir/golden_fir_out.csv    逐样本期望输出（3940 行）

用法（在 **Mizar-Z7020** 上，先把 fpga/sim/data* 拷到板上；🚧 v1.3 板卡变更待会签）：
  python board/hw_sw_compare.py --bit system.bit --data-root fpga/sim

退出码：0 = 全部容差 0 PASS；1 = 有 FAIL。
"""

import argparse
import csv
import os
import sys

import regmap as R
from overlay.load_overlay import load, run_pixel_chain, run_fir_segment


def _s16(u):
    """把从 FIFO 读到的 16 bit 无符号还原为有符号 int16。"""
    u &= 0xFFFF
    return u if u < 0x8000 else u - 0x10000


def read_frames(bin_path, frame_bytes, n_frames):
    with open(bin_path, "rb") as f:
        data = f.read()
    need = frame_bytes * n_frames
    if len(data) < need:
        raise ValueError(f"{bin_path} 数据不足：{len(data)} < {need}")
    return [data[i * frame_bytes:(i + 1) * frame_bytes] for i in range(n_frames)]


def selftest(roi_dir=None, motion_dir=None, fir_dir=None):
    """离线自检（**不需要板卡**）：验"黄金参考数据与当前档位口径是否配套"。

    为什么值得有：本脚本是**上板**才跑的，而它最大的风险不是寄存器读写，而是
    **拿错档位的黄金参考** —— 那会在板子上表现为"PL 算错了"，实际是数据配错。
    这个坑本项目已经踩过三次（`tb_rgb2gray.cpp` Layer 1 写死 3/5、
    `rgb2gray` cosim 拿 3/5 数据跑 3/8、`fir_filter` 拿 45 Hz 数据跑 30 Hz 系数表），
    症状与"IP 算错"完全一样。所以上板**之前**先把这套一致性验掉。
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roi_dir = roi_dir or os.path.join(root, "fpga", "sim", "data")
    motion_dir = motion_dir or os.path.join(root, "fpga", "sim", "data_motion")
    fir_dir = fir_dir or os.path.join(root, "fpga", "sim", "data_fir")

    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    print("=" * 72)
    print("[离线自检] hw_sw_compare 的数据/口径一致性（不需要板卡）")
    print("=" * 72)
    print(f"  档位 VIGILENS_TIER = {R.TIER}  (帧 {R.FRAME_W}x{R.FRAME_H}, 灰度 {R.GRAY_W}x{R.GRAY_H})")
    print(f"  roi_dir    = {roi_dir}")
    print(f"  motion_dir = {motion_dir}")
    print(f"  fir_dir    = {fir_dir}")
    print("-" * 72)

    # ---- roi_statistic：frames.bin 要够 5 帧、且每帧正好是本档的 RGB_BYTES ----
    fb = os.path.join(roi_dir, "frames.bin")
    gc = os.path.join(roi_dir, "golden_roi.csv")
    if os.path.exists(fb) and os.path.exists(gc):
        size = os.path.getsize(fb)
        chk("roi: frames.bin 至少 5 帧（每帧 = FRAME_W*FRAME_H*3）",
            size >= R.RGB_BYTES * 5, f"{size} B vs need {R.RGB_BYTES * 5} B")
        # 关键：文件大小必须能被本档单帧整除，否则就是**别的档**的数据
        chk("roi: frames.bin 能被本档单帧整除（否则是别的档的数据）",
            size % R.RGB_BYTES == 0, f"{size} % {R.RGB_BYTES} = {size % R.RGB_BYTES}")
        header = _rows(gc)[0]
        chk("roi: golden_roi.csv 表头符合契约字段",
            header == ["case", "frame", "x0", "y0", "x1", "y1",
                       "sum_r", "sum_g", "sum_b", "count"], str(header))
        chk("roi: golden_roi.csv 有数据行", len(_rows(gc)) > 1, f"{len(_rows(gc)) - 1} 行")
    else:
        chk("roi: 数据文件存在（frames.bin + golden_roi.csv）", False,
            "缺文件 —— 先跑 python fpga/sim/gen_frames.py")

    # ---- rgb2gray / motion_quality ----
    gb = os.path.join(motion_dir, "gray.bin")
    gm = os.path.join(motion_dir, "golden_motion.csv")
    rgbf = os.path.join(motion_dir, "rgb_frames.bin")
    if os.path.exists(gb) and os.path.exists(gm):
        gsize = os.path.getsize(gb)
        chk("motion: gray.bin 能被本档 GRAY_PIXELS 整除（否则是别的抽取比的数据）",
            gsize % R.GRAY_PIXELS == 0, f"{gsize} % {R.GRAY_PIXELS} = {gsize % R.GRAY_PIXELS}")
        chk("motion: gray.bin 至少 10 帧", gsize >= R.GRAY_PIXELS * 10, f"{gsize} B")
        if os.path.exists(rgbf):
            rsize = os.path.getsize(rgbf)
            chk("motion: rgb_frames.bin 能被本档单帧整除",
                rsize % R.RGB_BYTES == 0, f"{rsize} % {R.RGB_BYTES} = {rsize % R.RGB_BYTES}")
            # 灰度帧数应与 RGB 帧数一致（同一段序列）
            chk("motion: 灰度帧数 == RGB 帧数（同一段序列）",
                gsize // R.GRAY_PIXELS == rsize // R.RGB_BYTES,
                f"gray {gsize // R.GRAY_PIXELS} 帧 vs rgb {rsize // R.RGB_BYTES} 帧")
        chk("motion: golden_motion.csv 有数据行", len(_rows(gm)) > 1, f"{len(_rows(gm)) - 1} 行")
    else:
        chk("motion: 数据文件存在（gray.bin + golden_motion.csv）", False,
            "缺文件 —— 先跑 python fpga/sim/gen_motion_vectors.py（并确认 --decim 与本档一致）")

    # ---- fir_filter ----
    fc = os.path.join(fir_dir, "golden_fir.csv")
    fo = os.path.join(fir_dir, "golden_fir_out.csv")
    if os.path.exists(fc) and os.path.exists(fo):
        seg_rows = _rows(fc)
        chk("fir: golden_fir.csv 有数据行", len(seg_rows) > 1, f"{len(seg_rows) - 1} 行")
        chk("fir: golden_fir_out.csv 有数据行", len(_rows(fo)) > 0, f"{len(_rows(fo))} 行")
        # 段级 csv 通常带 fs= 说明；有就核对它是否与档位一致（30/45/60）
        meta = os.path.join(fir_dir, "meta.txt")
        if os.path.exists(meta):
            fs_line = ""
            with open(meta, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    if ln.startswith("fs="):
                        fs_line = ln.strip()
            chk("fir: meta.txt 声明了 fs=", bool(fs_line), fs_line or "未找到 fs=")
    else:
        chk("fir: 数据文件存在（golden_fir.csv + golden_fir_out.csv）", False,
            "缺文件 —— 先跑 python fpga/sim/gen_fir_vectors.py")

    bad = [n for n, ok, _ in checks if not ok]
    print("")
    for n, ok, detail in checks:
        line = f"  [{'PASS' if ok else 'FAIL'}] {n}"
        if detail:
            line += f"   ({detail})"
        print(line)
    print("-" * 72)
    print("RESULT:", "PASS" if not bad else "FAIL")
    print("⚠️ 本自检只证明'数据与档位配套'；PL 侧数值一致性仍必须上板跑本脚本正文。")
    return 0 if not bad else 1


def _rows(csv_path):
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        for line in reader:
            if not line or line[0].lstrip().startswith("#"):
                continue
            rows.append(line)
    return rows


# ---------------------------------------------------------------------------
# roi_statistic
# ---------------------------------------------------------------------------

def compare_roi(ol, handles, frames_bin, golden_csv):
    print("=" * 60)
    print("[roi_statistic] 逐行比对 golden_roi.csv（容差 0）")
    print("=" * 60)
    header = _rows(golden_csv)[0]
    assert header == ["case", "frame", "x0", "y0", "x1", "y1",
                      "sum_r", "sum_g", "sum_b", "count"], f"golden_roi 表头不符: {header}"
    rows = _rows(golden_csv)[1:]
    frames = read_frames(frames_bin, R.RGB_BYTES, 5)

    ok = 0
    bad = 0
    for r in rows:
        case, frame, x0, y0, x1, y1 = r[0], int(r[1]), *map(int, r[2:6])
        exp = {k: int(v) for k, v in zip(("sum_r", "sum_g", "sum_b", "count"), r[6:10])}
        res = run_pixel_chain(ol, handles, frames[frame], roi=(x0, y0, x1, y1))
        got = {k: res["roi"][k] for k in exp}
        if got == exp:
            ok += 1
        else:
            bad += 1
            print(f"  [FAIL] {case} frame={frame} roi=({x0},{y0},{x1},{y1})")
            print(f"         got  = {got}")
            print(f"         want = {exp}")
    print(f"  一致 {ok} / 不一致 {bad}")
    return bad == 0


# ---------------------------------------------------------------------------
# rgb2gray（灰度逐像素）+ motion_quality
# ---------------------------------------------------------------------------

def compare_gray_and_motion(ol, handles, rgb_frames_bin, gray_bin, golden_motion_csv):
    print("=" * 60)
    print("[rgb2gray + motion_quality] 逐像素/逐帧比对（容差 0）")
    print("=" * 60)
    frames = read_frames(rgb_frames_bin, R.RGB_BYTES, 10)
    with open(gray_bin, "rb") as f:
        gray_all = f.read()
    n_gray_frames = len(gray_all) // R.GRAY_PIXELS

    # 逐帧跑，先拿灰度比对 + motion 寄存器
    gray_bad_px = 0
    mot_rows = _rows(golden_motion_csv)
    assert mot_rows[0] == ["frame", "diff_total", "motion_pixels", "motion_ratio_q16", "count"]
    mot_exp = {int(r[0]): [int(v) for v in r[1:]] for r in mot_rows[1:]}

    motion_results = {}
    for i in range(min(10, n_gray_frames)):
        res = run_pixel_chain(ol, handles, frames[i])
        # 灰度比对
        want_gray = gray_all[i * R.GRAY_PIXELS:(i + 1) * R.GRAY_PIXELS]
        if res["gray_out"] != want_gray:
            # 统计差异像素（不求快，求清楚）
            bad = sum(1 for a, b in zip(res["gray_out"], want_gray) if a != b)
            gray_bad_px += bad
            print(f"  [FAIL] 帧 {i} 灰度不一致像素 {bad} / {R.GRAY_PIXELS}")
        motion_results[i] = {k: res["mot"][k] for k in
                             ("diff_total", "motion_pixels", "motion_ratio_q16", "count")}

    mot_ok = mot_bad = 0
    for frame_i, exp in mot_exp.items():   # 帧 1..9（帧 0 无意义，契约 §3.3）
        got = motion_results[frame_i]
        if got == {"diff_total": exp[0], "motion_pixels": exp[1],
                   "motion_ratio_q16": exp[2], "count": exp[3]}:
            mot_ok += 1
        else:
            mot_bad += 1
            print(f"  [FAIL] motion 帧 {frame_i}: got={got} want={exp}")

    print(f"  灰度：累计不一致像素 {gray_bad_px}")
    print(f"  motion：一致 {mot_ok} 帧 / 不一致 {mot_bad} 帧")
    return gray_bad_px == 0 and mot_bad == 0


# ---------------------------------------------------------------------------
# fir_filter（逐样本）
# ---------------------------------------------------------------------------

def compare_fir(ol, handles, series_bin, golden_fir_csv, golden_fir_out_csv):
    print("=" * 60)
    print("[fir_filter] 逐样本比对 golden_fir_out.csv（容差 0）")
    print("=" * 60)
    seg_rows = _rows(golden_fir_csv)
    assert seg_rows[0] == ["case", "seg_id", "n_samples", "reset", "sat_count", "out_count"]
    seg_rows = seg_rows[1:]

    with open(series_bin, "rb") as f:
        series = f.read()
    # int16 小端解码
    import array
    samples = array.array("h")
    samples.frombytes(series)
    if sys.byteorder != "little":
        samples.byteswap()

    out_rows = _rows(golden_fir_out_csv)
    assert out_rows[0] == ["seg_id", "index", "y"]
    out_exp = {}
    for r in out_rows[1:]:
        seg, idx, y = int(r[0]), int(r[1]), int(r[2])
        out_exp[(seg, idx)] = y

    pos = 0
    bad = 0
    total = 0
    for r in seg_rows:
        case, seg, n, reset, sat, out_cnt = r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4]), int(r[5])
        seg_samples = list(samples[pos:pos + n])
        pos += n
        res = run_fir_segment(ol, handles, seg_samples, reset=bool(reset))
        got_signed = [_s16(v) for v in res["out"]]
        for idx in range(n):
            total += 1
            want = out_exp[(seg, idx)]
            if got_signed[idx] != want:
                bad += 1
                if bad <= 5:
                    print(f"  [FAIL] seg={seg}({case}) idx={idx}: got={got_signed[idx]} want={want}")
        if res["saturation_count"] != sat:
            print(f"  [FAIL] seg={seg}({case}) sat_count got={res['saturation_count']} want={sat}")
            bad += 1
    print(f"  样本一致 {total - bad} / 不一致 {bad}（总 {total}）")
    return bad == 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="C10 软硬件一致性比对")
    ap.add_argument("--bit", default="system.bit")
    ap.add_argument("--data-root", default="fpga/sim", help="黄金参考根目录")
    ap.add_argument("--skip-fir", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="离线自检：只验黄金参考数据与本档口径是否配套，不需要板卡")
    args = ap.parse_args(argv)

    d = args.data_root
    # ⚠️ 相对路径锚到**仓库根**，而不是当前工作目录。
    #    原来直接用 os.path.join(d, ...)：从 board/ 目录调用 `python hw_sw_compare.py`
    #    就会去找 board/fpga/sim/...（不存在），于是自检报"缺文件" —— 那是**路径假故障**，
    #    与"数据真缺"长得一模一样（本自检本身就是为了消灭这类假故障）。
    if not os.path.isabs(d):
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(_root, d)
    roi_dir = os.path.join(d, "data")
    motion_dir = os.path.join(d, "data_motion")
    fir_dir = os.path.join(d, "data_fir")

    if args.selftest:
        return selftest(roi_dir=roi_dir, motion_dir=motion_dir, fir_dir=fir_dir)

    ol, handles = load(args.bit)

    results = {}
    results["roi_statistic"] = compare_roi(
        ol, handles, os.path.join(roi_dir, "frames.bin"), os.path.join(roi_dir, "golden_roi.csv"))
    results["rgb2gray+motion"] = compare_gray_and_motion(
        ol, handles, os.path.join(motion_dir, "rgb_frames.bin"),
        os.path.join(motion_dir, "gray.bin"), os.path.join(motion_dir, "golden_motion.csv"))
    if not args.skip_fir:
        results["fir_filter"] = compare_fir(
            ol, handles, os.path.join(fir_dir, "series.bin"),
            os.path.join(fir_dir, "golden_fir.csv"), os.path.join(fir_dir, "golden_fir_out.csv"))

    print("=" * 60)
    print("软硬件一致性汇总（容差一律 0）：")
    for k, v in results.items():
        print(f"  {k:16s} : {'PASS' if v else 'FAIL'}")
    all_ok = all(results.values())
    print("RESULT:", "PASS" if all_ok else "FAIL")
    print(">>> 请把本表结果回填到 fpga/report/m4_baseline_v1.md 第 5 节对比表 <<<")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
