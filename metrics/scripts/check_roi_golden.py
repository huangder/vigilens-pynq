"""check_roi_golden.py —— A↔C `roi_statistic` 黄金参考对拍（契约 `docs/interface.md` 4.2 节）。

为什么需要它：
    C 线的 `roi_statistic` 逐点严格相等（容差 0）的前提是**两边对 ROI 的理解完全一致**：
      · 半开区间 `[x0,x1) × [y0,y1)`（`x0,y0` 含、`x1,y1` 不含），等价 `img[y0:y1, x0:x1]`；
      · 越界**裁剪**到图像内（`clamp_roi`）；
      · 裁剪后 `cx1<=cx0` 或 `cy1<=cy0` → 判空，四项全 0；
      · R/G/B 分通道整数累加，`count = (cx1-cx0)*(cy1-cy0)`。
    本脚本用 A 线自己的 NumPy 代码复算这 9 个用例 × 5 帧，与 `golden_roi.csv` 逐行比对。
    这直接对应契约 §5.2 第 1 项（RGB 通道顺序 + 半开区间 ROI）与第 2 项（时间序列口径）。

前置（向量由固定 seed 生成，可随时重建）：
    python fpga/sim/gen_frames.py

用法（仓库根）：
    python metrics/scripts/check_roi_golden.py
退出码：0 = 45 行全部全等；1 = 有不一致或前置缺失。

⚠️ 本脚本只做**比对**，不修改任何 C 线产物。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAMES_PATH = REPO_ROOT / "fpga" / "sim" / "data" / "frames.bin"
GOLDEN_PATH = REPO_ROOT / "fpga" / "sim" / "data" / "golden_roi.csv"

W, H = 640, 480


def clamp_roi(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    """与 C 线 `gen_frames.py::clamp_roi` 同义：越界裁剪到图像内。"""
    return (max(0, min(x0, W)), max(0, min(y0, H)),
            max(0, min(x1, W)), max(0, min(y1, H)))


def roi_sum(img, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    """半开区间 [x0,x1) × [y0,y1) 的 R/G/B 整数累加。

    等价于对裁剪后的切片 `img[cy0:cy1, cx0:cx1]` 求和 —— 无需 ±1 心算。
    """
    import numpy as np

    cx0, cy0, cx1, cy1 = clamp_roi(x0, y0, x1, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return 0, 0, 0, 0
    roi = img[cy0:cy1, cx0:cx1]
    return (int(roi[..., 0].sum(dtype=np.int64)),
            int(roi[..., 1].sum(dtype=np.int64)),
            int(roi[..., 2].sum(dtype=np.int64)),
            (cx1 - cx0) * (cy1 - cy0))


def load_golden() -> list[dict[str, str]]:
    with GOLDEN_PATH.open(encoding="utf-8", newline="") as fh:
        rows = [ln for ln in fh if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(rows))


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
    if not GOLDEN_PATH.exists():
        print(f"[FAIL] 缺少黄金参考：{GOLDEN_PATH}")
        return 1

    frames = np.fromfile(FRAMES_PATH, dtype=np.uint8).reshape(-1, H, W, 3)
    golden = load_golden()

    print("=== A↔C roi_statistic 黄金参考对拍（契约 4.2 节）===")
    print(f"输入 : {FRAMES_PATH.relative_to(REPO_ROOT)}  ({frames.shape[0]} 帧 × {H}×{W}×3，通道序 R,G,B)")
    print(f"黄金 : {GOLDEN_PATH.relative_to(REPO_ROOT)}  ({len(golden)} 行 = "
          f"{len({r['case'] for r in golden})} 用例 × {frames.shape[0]} 帧)")
    print("口径 : 半开区间 [x0,x1)×[y0,y1)，越界裁剪，空 ROI 四项全 0，容差 0")
    print()

    bad: list[str] = []
    per_case: dict[str, int] = {}
    for row in golden:
        case = row["case"]
        fi = int(row["frame"])
        x0, y0, x1, y1 = (int(row[k]) for k in ("x0", "y0", "x1", "y1"))
        mine = roi_sum(frames[fi], x0, y0, x1, y1)
        want = (int(row["sum_r"]), int(row["sum_g"]), int(row["sum_b"]), int(row["count"]))
        if mine == want:
            per_case[case] = per_case.get(case, 0) + 1
        else:
            bad.append(case)
            print(f"  [FAIL] {case:<18} frame {fi}  ROI=[{x0},{y0},{x1},{y1})")
            print(f"         本机 {mine}")
            print(f"         黄金 {want}")

    for case, ok_n in per_case.items():
        print(f"  {case:<18} ✅ {ok_n}/{frames.shape[0]} 帧全等")

    print()
    if bad:
        print(f"[FAIL] {len(bad)} 行不一致（容差 0）—— 契约 §5.2 第 1/2 项不能表态")
        return 1
    print(f"检测通过：{len(golden)} 行逐行全等（容差 0）—— "
          f"契约 §5.2 第 1/2 项：A 线可表态认可 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
