"""check_q15_golden.py —— A↔C `fir_filter` 输入序列 Q1.15 量化口径对拍（契约 4.6 节）。

为什么需要它：
    契约 §5.2 第 11 项要求 A 线表态"Q1.15 量化口径 + `count==0` 的帧一律丢弃"是否认可，
    并明确建议**用代码确认而不是口头认可**：跑 `fpga/sim/q15_ref.py --selftest`，
    再用 `--sums-csv` 与 A 线自己的实现对拍。本脚本就是后者。

    被测对象是 A 线自己的实现 `backend/vital.py::q15_from_roi_sum` —— 它属于 rPPG 链路的
    输入级（P3 会在这个模块上继续长出滤波与峰值检测）。

三层比对：
    ① C 线参考脚本的自检（`--selftest`）；
    ② 用**真实 ROI 数据**走 C 线的 `--sums-csv` CLI，与 A 线实现逐样本比；
    ③ 全量扫描：count 取多个值、均值覆盖 0~255 全区间，与 C 线参考实现逐点比。

前置：
    python fpga/sim/gen_frames.py

用法（仓库根）：
    python metrics/scripts/check_q15_golden.py
退出码：0 = 三层全部通过；1 = 有差异或前置缺失。

⚠️ 本脚本只做**比对**，不修改任何 C 线产物。
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
SIM = REPO_ROOT / "fpga" / "sim"
sys.path.insert(0, str(BACKEND))

FRAMES_PATH = SIM / "data" / "frames.bin"
GOLDEN_ROI = SIM / "data" / "golden_roi.csv"
Q15_REF = SIM / "q15_ref.py"
SCRATCH = REPO_ROOT / "metrics" / "logs" / "_q15"

W, H = 640, 480


def roi_cases() -> list[tuple[str, int, int, int, int]]:
    """契约 §4.2 的 9 个 ROI 用例（与 gen_frames.build_cases 同源）。"""
    return [
        ("full_frame",       0, 0, W, H),
        ("center_roi",       W // 4, H // 4, W * 3 // 4, H * 3 // 4),
        ("top_left_1x1",     0, 0, 1, 1),
        ("bottom_right_1x1", W - 1, H - 1, W, H),
        ("single_pixel",     W // 2, H // 2, W // 2 + 1, H // 2 + 1),
        ("empty_roi",        100, 100, 100, 200),          # count == 0，契约要求丢弃
        ("inverted_roi",     400, 300, 200, 100),          # count == 0，契约要求丢弃
        ("odd_offset",       3, 5, W - 3, H - 3),
        ("oversized_roi",    W - 40, H - 20, W + 60, H + 20),
    ]


def clamp_roi(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    return (max(0, min(x0, W)), max(0, min(y0, H)),
            max(0, min(x1, W)), max(0, min(y1, H)))


def main() -> int:
    try:
        import numpy as np
    except ImportError:
        print("[FAIL] 需要 numpy：pip install -r requirements.txt（或用项目 .venv）", file=sys.stderr)
        return 1

    if not FRAMES_PATH.exists():
        print(f"[FAIL] 缺少测试向量：{FRAMES_PATH}")
        print("       先生成：python fpga/sim/gen_frames.py")
        return 1

    from vital import q15_from_roi_sum

    print("=== A↔C Q1.15 量化口径对拍（契约 4.6 节）===")
    print("口径 : q = round_half_away((mean-128)*256)，整数权威式 "
          "q = rha_div(256*sum_c - 32768*count, count)")
    print()

    # ---- ① C 线参考脚本自检 ---------------------------------------------------
    r = subprocess.run([sys.executable, str(Q15_REF), "--selftest"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  [FAIL] q15_ref.py --selftest 退出码 {r.returncode}")
        print(r.stdout[-800:] or r.stderr[-800:])
        return 1
    print("① C 线参考脚本自检：PASS")

    # ---- ② 真实 ROI 数据走 CLI ------------------------------------------------
    frames = np.fromfile(FRAMES_PATH, dtype=np.uint8).reshape(-1, H, W, 3)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    sums_csv = SCRATCH / "roi_sums.csv"

    rows_in: list[tuple[int, int, int, int, int]] = []
    with sums_csv.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("frame,count,sum_r,sum_g,sum_b\n")
        for fi in range(frames.shape[0]):
            for _case, x0, y0, x1, y1 in roi_cases():
                cx0, cy0, cx1, cy1 = clamp_roi(x0, y0, x1, y1)
                if cx1 <= cx0 or cy1 <= cy0:
                    count, sr, sg, sb = 0, 0, 0, 0
                else:
                    roi = frames[fi][cy0:cy1, cx0:cx1]
                    count = (cx1 - cx0) * (cy1 - cy0)
                    sr = int(roi[..., 0].sum(dtype=np.int64))
                    sg = int(roi[..., 1].sum(dtype=np.int64))
                    sb = int(roi[..., 2].sum(dtype=np.int64))
                rows_in.append((fi, count, sr, sg, sb))
                fh.write(f"{fi},{count},{sr},{sg},{sb}\n")

    out_csv = SCRATCH / "q15_ref_out.csv"
    r = subprocess.run([sys.executable, str(Q15_REF), "--sums-csv", str(sums_csv),
                        "--channel", "g", "--out-csv", str(out_csv)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  [FAIL] q15_ref.py --sums-csv 退出码 {r.returncode}")
        print(r.stdout[-800:] or r.stderr[-800:])
        return 1

    n_empty = sum(1 for row in rows_in if row[1] == 0)
    with out_csv.open(encoding="utf-8", newline="") as fh:
        out_rows = list(csv.DictReader(ln for ln in fh if not ln.lstrip().startswith("#")))

    print(f"② 真实 ROI 数据（{len(rows_in)} 行，其中空 ROI {n_empty} 行应被丢弃）")
    print(f"   C 线参考产出 {len(out_rows)} 个有效样本 "
          f"（{len(rows_in)} - {n_empty} = {len(rows_in) - n_empty}）")
    if len(out_rows) != len(rows_in) - n_empty:
        print(f"   [FAIL] 丢弃规则不符预期")
        return 1

    bad = []
    for row in out_rows:
        cnt, s = int(row["count"]), int(row["sum_used"])
        want = int(row["q15"])
        mine = q15_from_roi_sum(s, cnt)
        if mine != want:
            bad.append((row["frame"], cnt, s, mine, want))
    if bad:
        print(f"   [FAIL] {len(bad)}/{len(out_rows)} 个样本不一致")
        for f, c, s, m, w in bad[:5]:
            print(f"      frame {f} count {c} sum {s}: 本机 {m}  参考 {w}")
        return 1
    print(f"   ✅ {len(out_rows)}/{len(out_rows)} 个样本与 C 线参考逐样本相同")
    print()

    # ---- ③ 全量扫描：count × 均值 0~255 --------------------------------------
    sys.path.insert(0, str(SIM))
    import q15_ref as ref                                    # C 线参考实现

    print("③ 全量扫描（与 C 线参考实现逐点比）")
    counts = [1, 2, 3, 7, 1000, 110592]                      # 含不整除与真实 ROI 尺寸
    checked = 0
    bad3 = []
    for count in counts:
        for mean_x256 in range(0, 256 * 256 + 1, 1):         # 均值以 1/256 灰阶为步长扫满区间
            # 构造一个"均值恰为 mean_x256/256"的累加值（可能不整除，正是要测的边界）
            s = (mean_x256 * count) // 256
            mine = q15_from_roi_sum(s, count)
            want = ref.q15_of_sum(s, count)
            checked += 1
            if mine != want:
                bad3.append((count, s, mine, want))
                if len(bad3) >= 5:
                    break
        if bad3:
            break
    if bad3:
        print(f"   [FAIL] {len(bad3)} 处不一致")
        for c, s, m, w in bad3[:5]:
            print(f"      count {c} sum {s}: 本机 {m}  参考 {w}")
        return 1
    print(f"   ✅ {checked} 个 (count, sum) 组合全等（均值覆盖 0~255，含不整除边界）")

    # ---- 契约要求的丢弃行为 ----------------------------------------------------
    try:
        q15_from_roi_sum(0, 0)
        print("   [FAIL] count==0 未报错 —— 契约要求丢弃该帧，不得产生样本")
        return 1
    except ValueError:
        print("   ✅ count==0 被拒绝（契约要求：该帧整帧丢弃，不得填 0 或沿用上一帧）")

    print()
    print("（Q1.15 实现位于 backend/vital.py::q15_from_roi_sum，P3 将在其上继续搭 rPPG 链路）")
    print("检测通过：契约 §5.2 第 11 项 —— A 线可表态认可 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
