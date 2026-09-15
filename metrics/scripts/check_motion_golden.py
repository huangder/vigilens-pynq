"""check_motion_golden.py —— A↔C 帧差运动量对拍（契约 `docs/interface.md` 3.3 节）。

为什么需要它：
    `motion_quality` 是唯一一个 **A 线自己也在算**的 IP（`backend/quality.py` 声称提供
    "帧差运动量黄金参考"），所以两边口径必须逐位一致，否则 C 线拿这份参考证不出任何东西。
    冻结语义（契约 3.3）：
      · 输入是 `rgb2gray` **缩小后**的灰度（384×288），不是原始 640×480；
      · 阈值**严格大于**：`|cur-prev| > motion_thresh` 才算运动像素；
      · `diff_total = Σ|cur-prev|`；`motion_ratio_q16 = (motion_pixels << 16) // count`（向下取整）；
      · 第 0 帧输出**无效**（片内 prev_buf 上电未定义）→ 黄金参考从第 1 帧起。

    本脚本做三件事：
      ① 用 A 线自己的 `quality.frame_diff_motion` 按冻结口径复算 → 与黄金参考对拍（判据）；
      ② 量出"阈值 25（当前 config）vs 16（黄金参考）"造成的差异；
      ③ 量出"在 640×480 上算 vs 在 384×288 上算"造成的差异。
    ②③ 是拿给三方会签用的**实测数字**（契约 §5.2 第 5 项就等这个）。

前置（向量由固定 seed 生成，可随时重建）：
    python fpga/sim/gen_motion_vectors.py

用法（仓库根）：
    python metrics/scripts/check_motion_golden.py
退出码：0 = 冻结口径下逐帧全等；1 = 有差异或前置缺失。

⚠️ 本脚本只做**比对**，不修改任何 C 线产物，也不改 `config.yaml`。
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
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

GRAY_PATH = REPO_ROOT / "fpga" / "sim" / "data_motion" / "gray.bin"
RGB_PATH = REPO_ROOT / "fpga" / "sim" / "data_motion" / "rgb_frames.bin"
GOLDEN_PATH = REPO_ROOT / "fpga" / "sim" / "data_motion" / "golden_motion.csv"

W, H = 640, 480
OW, OH = 384, 288          # rgb2gray 缩小后的工作尺寸（契约 3.4）
GOLDEN_THRESH = 16         # 黄金参考生成时用的 motion_thresh（见 data_motion/meta.txt）
C_PRIME = "114,587,299"    # BT.601 系数，仅用于注释说明


def load_golden() -> list[dict[str, str]]:
    with GOLDEN_PATH.open(encoding="utf-8", newline="") as fh:
        rows = [ln for ln in fh if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(rows))


def diff_against(gray_frames, golden: list[dict[str, str]], thresh: int) -> list[tuple[int, tuple, tuple]]:
    """按契约语义逐帧求帧差，返回与黄金参考不一致的 (frame, mine, want) 列表。"""
    from quality import frame_diff_motion

    bad = []
    for row in golden:
        fi = int(row["frame"])
        m = frame_diff_motion(gray_frames[fi - 1], gray_frames[fi], thresh)
        mine = (m["diff_total"], m["motion_pixels"], m["motion_ratio_q16"], m["count"])
        want = (int(row["diff_total"]), int(row["motion_pixels"]),
                int(row["motion_ratio_q16"]), int(row["count"]))
        if mine != want:
            bad.append((fi, mine, want))
    return bad


def main() -> int:
    try:
        import numpy as np
    except ImportError:
        print("[FAIL] 需要 numpy：pip install -r requirements.txt（或用项目 .venv）", file=sys.stderr)
        return 1

    for p in (GRAY_PATH, RGB_PATH, GOLDEN_PATH):
        if not p.exists():
            print(f"[FAIL] 缺少文件：{p}")
            print("       先生成：python fpga/sim/gen_motion_vectors.py")
            return 1

    from config import load_config
    from quality import to_gray

    cfg_thresh = int(load_config()["motion_thresh_gray"])

    gray_small = np.fromfile(GRAY_PATH, dtype=np.uint8).reshape(-1, OH, OW)      # 冻结口径输出
    rgb_full = np.fromfile(RGB_PATH, dtype=np.uint8).reshape(-1, H, W, 3)
    golden = load_golden()

    print("=== A↔C 帧差运动量对拍（契约 3.3 节）===")
    print(f"黄金 : {GOLDEN_PATH.relative_to(REPO_ROOT)}  ({len(golden)} 帧对，第 0 帧按契约排除)")
    print(f"工作尺寸 : 黄金参考 {OH}×{OW}（rgb2gray 缩小后）   阈值为严格大于")
    print()

    # ---- ① 冻结口径：384×288 + 黄金阈值 16 -----------------------------------
    print(f"① 冻结口径（工作尺寸 {OH}×{OW}，阈值 {GOLDEN_THRESH}）")
    bad1 = diff_against(gray_small, golden, GOLDEN_THRESH)
    if bad1:
        print(f"   ❌ {len(bad1)}/{len(golden)} 帧对不一致")
        for fi, mine, want in bad1[:3]:
            print(f"      frame {fi}: 本机 {mine}  黄金 {want}")
    else:
        print(f"   ✅ {len(golden)}/{len(golden)} 帧对逐项全等（容差 0）")
    print()

    # ---- ② 阈值差异：当前 config 的 25 vs 黄金的 16 --------------------------
    print(f"② 阈值差异（同样 {OH}×{OW} 输入，只改 motion_thresh）")
    bad2 = diff_against(gray_small, golden, cfg_thresh)
    print(f"   阈值 {GOLDEN_THRESH}（黄金参考）→ 不一致 {len(bad1)}/{len(golden)} 帧")
    print(f"   阈值 {cfg_thresh}（config.yaml 现值）→ 不一致 {len(bad2)}/{len(golden)} 帧")
    if bad2:
        fi, mine, want = bad2[0]
        print(f"   例：frame {fi}  阈值{cfg_thresh} 得 motion_pixels={mine[1]}，"
              f"黄金（阈值{GOLDEN_THRESH}）={want[1]}")
        d = [abs(m[1] - w[1]) for _, m, w in bad2]
        print(f"   motion_pixels 绝对偏差：最小 {min(d)}，最大 {max(d)}")
    print()

    # ---- ③ 分辨率差异：640×480 全分辨率 vs 384×288 ---------------------------
    print("③ 分辨率差异（同样阈值，改输入分辨率为 640×480）")
    # 模拟 A 线在真实视频上的现状：对全分辨率帧取灰度（当前实现用 cv2 的 BGR2GRAY）
    gray_full = [to_gray(f[..., ::-1].copy()) for f in rgb_full]   # RGB→BGR，避免通道序干扰
    bad3 = diff_against(gray_full, golden, GOLDEN_THRESH)
    s = np.array(bad3[0][1]) if bad3 else None
    print(f"   全分辨率帧数统计：count 字段 = {len(gray_full[0].ravel())}（黄金参考是 {OH * OW}）")
    print(f"   不一致 {len(bad3)}/{len(golden)} 帧 —— "
          f"两者的 count 都不同，数值**根本不可比**，不是阈值微调能解决的")
    print()

    print("待三方会签的实测结论（契约 §5.2 第 5 项）：")
    print(f"  · 黄金参考的 motion_thresh = {GOLDEN_THRESH}，config.yaml 的 motion_thresh_gray = {cfg_thresh}"
          f" → 二者必须取其一；改 16 需重生成黄金参考，改 25 则黄金参考作废");
    print(f"  · 工作尺寸必须统一为 rgb2gray 缩小后的 {OH}×{OW}；"
          f"在全分辨率上算出的帧差与黄金参考不可比")
    print(f"  · 灰度必须走冻结式 (77R+150G+29B+128)>>8，不能用 cv2.cvtColor"
          f"（见 check_gray_formula.py 的实测差异）")
    print()
    ok = not bad1
    if ok:
        print(f"检测通过：冻结口径下 {len(golden)}/{len(golden)} 帧对逐项全等（容差 0）；"
              f"差异表见上方 —— 契约 §5.2 第 5 项：结论已具备会签依据 ✅")
    else:
        print("检测未通过：冻结口径下仍有差异 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
