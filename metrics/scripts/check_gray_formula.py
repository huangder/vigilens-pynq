"""check_gray_formula.py —— A↔C 灰度 + 缩放口径对拍（契约 `docs/interface.md` 4.4 节）。

为什么需要它：
    `rgb2gray` 的灰度式与缩放规则都是**自定义冻结口径**，不是 OpenCV 的默认行为：
      · 灰度  Y = (77R + 150G + 29B + 128) >> 8   —— 与"浮点系数四舍五入"差 1 LSB
        （例：纯红，浮点式 0.299×255 = 76.245 → 76，本设计给 77）；
      · 缩放  3/5 相位点采样（保留 x%5<3 且 y%5<3）—— 不是插值，`cv2.resize` 必然对不上。
    所以 A 线**不能**想当然地用 `cv2.cvtColor` + `cv2.resize` 当黄金参考。
    本脚本用 A 线自己的 NumPy 代码复算冻结口径，与 C 线产出的 `gray.bin` 逐像素比对，
    把"两边算的是同一个东西"变成**可自动检查**的判据（对应契约 §5.2 第 4 项）。

前置（向量由固定 seed 生成，可随时重建）：
    python fpga/sim/gen_motion_vectors.py

用法（仓库根）：
    python metrics/scripts/check_gray_formula.py
退出码：0 = 逐像素全等；1 = 存在不一致或前置缺失。

⚠️ 本脚本只做**比对**，不修改任何 C 线产物。
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 控制台默认 GBK 代码页，打印 ✅/❌ 会 UnicodeEncodeError 直接崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 —— 老解释器没有 reconfigure，忽略
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
RGB_PATH = REPO_ROOT / "fpga" / "sim" / "data_motion" / "rgb_frames.bin"
GRAY_PATH = REPO_ROOT / "fpga" / "sim" / "data_motion" / "gray.bin"

# 契约 §0：输入 640×480 RGB888
W, H = 640, 480
# 契约 §3.4 冻结口径二：3/5 相位点采样，先挑行再挑列
YS = [y for y in range(H) if y % 5 < 3]          # 288 个
XS = [x for x in range(W) if x % 5 < 3]          # 384 个


def frozen_gray_and_decimate(rgb_frame):
    """调用 **A 线运行时的那份实现**（`backend/quality.py::to_gray`），不在这里另抄一份。

    为什么这么写：口径只允许有**一处**实现。本脚本一开始自带了一份副本，但那样
    "运行时改了、检查脚本没改"就没人发现 —— 现在两边永远同一个实现。

    入参是 **RGB** 帧（来自 `rgb_frames.bin`）；而 `capture.py` 给 `to_gray` 的是 BGR，
    所以这里先翻转通道再喂，与真实链路一致。
    """
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from quality import to_gray      # A 线运行时的灰度路径（契约 §3.4 冻结口径）

    return to_gray(rgb_frame[..., ::-1].copy())


def main() -> int:
    try:
        import numpy as np
    except ImportError:
        print("[FAIL] 需要 numpy：pip install -r requirements.txt（或用项目 .venv）", file=sys.stderr)
        return 1

    missing = [p for p in (RGB_PATH, GRAY_PATH) if not p.exists()]
    if missing:
        print("[FAIL] 缺少测试向量：")
        for p in missing:
            print(f"       {p}")
        print("       先生成：python fpga/sim/gen_motion_vectors.py")
        return 1

    rgb_all = np.fromfile(RGB_PATH, dtype=np.uint8).reshape(-1, H, W, 3)
    gold_all = np.fromfile(GRAY_PATH, dtype=np.uint8).reshape(-1, len(YS), len(XS))
    n = rgb_all.shape[0]

    print("=== A↔C 灰度 + 缩放口径对拍（契约 4.4 节）===")
    print(f"输入 : {RGB_PATH.relative_to(REPO_ROOT)}  ({n} 帧 × {H}×{W}×3)")
    print(f"黄金 : {GRAY_PATH.relative_to(REPO_ROOT)}  ({gold_all.shape[0]} 帧 × {len(YS)}×{len(XS)})")
    print(f"口径 : Y=(77R+150G+29B+128)>>8 ，3/5 相位抽取（先挑行再挑列）")
    print()

    if n != gold_all.shape[0]:
        print(f"[FAIL] 帧数不一致：输入 {n} 帧 vs 黄金 {gold_all.shape[0]} 帧")
        return 1

    bad_frames: list[int] = []
    for i in range(n):
        mine = frozen_gray_and_decimate(rgb_all[i])
        gold = gold_all[i]
        if mine.shape != gold.shape:
            print(f"  frame {i}: [FAIL] 尺寸不一致 {mine.shape} vs {gold.shape}")
            bad_frames.append(i)
            continue
        n_diff = int((mine != gold).sum())
        if n_diff == 0:
            print(f"  frame {i}: ✅ 逐像素全等")
        else:
            print(f"  frame {i}: ❌ 不一致像素 {n_diff} / {mine.size}")
            bad_frames.append(i)

    # ---- 可选：OpenCV 对照（**仅供了解**，不要据此改硬件口径）-----------------
    print()
    try:
        import cv2

        img0 = rgb_all[0]
        cv_gray = cv2.cvtColor(img0, cv2.COLOR_RGB2GRAY)
        d_same_point = np.abs(cv_gray[np.ix_(YS, XS)].astype("int32")
                              - frozen_gray_and_decimate(img0).astype("int32"))
        cv_resized = cv2.resize(cv_gray, (len(XS), len(YS)), interpolation=cv2.INTER_NEAREST)
        d_resize = np.abs(cv_resized.astype("int32")
                          - frozen_gray_and_decimate(img0).astype("int32"))
        print("OpenCV 对照（frame 0，差异属预期，一律以冻结口径为准）：")
        print(f"  cv2.cvtColor(RGB2GRAY) + 同点抽取 : 最大差 {int(d_same_point.max()):3d}，"
              f"不同像素 {(d_same_point > 0).mean() * 100:5.1f}%")
        print(f"  cv2.cvtColor + cv2.resize(NEAREST) : 最大差 {int(d_resize.max()):3d}，"
              f"不同像素 {(d_resize > 0).mean() * 100:5.1f}%")
    except ImportError:
        print("OpenCV 对照：跳过（未安装 cv2）")

    print()
    if bad_frames:
        print(f"[FAIL] 灰度 + 缩放口径不一致：{len(bad_frames)} 帧有差异 —— "
              f"契约 §5.2 第 4 项不能表态")
        return 1
    print(f"检测通过：{n} 帧全部逐像素全等（容差 0）—— "
          f"契约 §5.2 第 4 项：A 线可表态认可 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
