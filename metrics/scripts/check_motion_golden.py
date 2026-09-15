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
      ② 核对 `config.yaml` 的 motion_thresh 是否已与黄金参考一致；
      ③ **A 线自己的整条运动量路径**（`quality.to_gray` 从原始帧算灰度+抽取，再帧差）
         与黄金参考逐项比对 —— 这是"口径已统一"的验收。

    沿革：2026-09-15 本脚本量出三处口径差（阈值 25 vs 16 / 分辨率 640×480 vs 384×288 /
    `cv2.cvtColor` vs 冻结式），作为契约 §5.2 第 5 项的会签依据；**2026-09-16 三方拍板统一到
    C 线那套**，A 线随即改了 `quality.to_gray` 与 `config.yaml`，此后 ②③ 应稳定为"一致"。

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

    # ---- ② 阈值是否已与黄金参考一致 ------------------------------------------
    print(f"② 阈值一致性（同样 {OH}×{OW} 输入，只改 motion_thresh）")
    bad2 = diff_against(gray_small, golden, cfg_thresh)
    print(f"   黄金参考 motion_thresh = {GOLDEN_THRESH}；config.yaml 现值 = {cfg_thresh}"
          f"  -> {'一致 ✅' if cfg_thresh == GOLDEN_THRESH else '不一致 ❌'}")
    print(f"   按 config 现值算：不一致 {len(bad2)}/{len(golden)} 帧")
    print()

    # ---- ③ A 线**自己的**整条运动量路径 vs 黄金参考（口径统一的验收）----------
    print("③ A 线自己的灰度路径（quality.to_gray）vs 黄金参考")
    # 真实链路上 capture 给的是 BGR；这里 rgb_frames.bin 是 RGB，先翻转通道再喂 to_gray，
    # 与真实链路一致（to_gray 内部按 BGR 的 R=2 / G=1 / B=0 取值）。
    mine_gray = [to_gray(f[..., ::-1].copy()) for f in rgb_full]
    diff_px = sum(int((m != g).sum()) for m, g in zip(mine_gray, gray_small))
    print(f"   灰度逐像素：不一致 {diff_px} 个像素（应 0）"
          f"；尺寸 A 线 {mine_gray[0].shape} vs 黄金 {gray_small[0].shape}")
    # 再把 A 线自己的灰度喂进 A 线自己的帧差，用 config 的阈值比运动量
    bad3 = diff_against(mine_gray, golden, cfg_thresh)
    if bad3:
        for fi, mine, want in bad3[:3]:
            print(f"   [FAIL] frame {fi}: 本机 {mine}  黄金 {want}")
    else:
        print(f"   运动量：{len(golden)}/{len(golden)} 帧对逐项全等（用 config 的阈值 {cfg_thresh}）")
    print()

    print("口径状态（2026-09-16 三方拍板：统一到 C 线那套）")
    print(f"  · motion_thresh：黄金 {GOLDEN_THRESH} / config.yaml {cfg_thresh}"
          f"  -> {'一致 ✅' if cfg_thresh == GOLDEN_THRESH else '**仍不一致 ❌**'}")
    print(f"  · 工作尺寸：A 线 to_gray 现输出 {mine_gray[0].shape[1]}×{mine_gray[0].shape[0]}"
          f"，黄金参考 {OW}×{OH}")
    print("  · 灰度式：冻结式 (77R+150G+29B+128)>>8（不再用 cv2.cvtColor）")
    print()

    ok = (not bad1) and (not bad3) and diff_px == 0 and cfg_thresh == GOLDEN_THRESH
    if ok:
        print("检测通过：A 线**整条运动量路径**（图像 → 冻结式灰度 + 3/5 抽取 → 帧差）"
              "与黄金参考逐项全等（容差 0）—— 契约 §5.2 第 5 项的口径已统一 ✅")
    else:
        print("检测未通过：A 线与黄金参考仍有差异 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
