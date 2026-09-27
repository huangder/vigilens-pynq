"""probe_rppg_timebase.py —— rPPG 时间基准受控实验（对应 docs/16 BUG-029）。

**它回答什么**：`backend/vital.py` 的 `GreenRppg` 按 `config.yaml` 的 `fps_nominal`（=30）
换算频率与窗口长度。如果采集链路实际只有 8 fps，报出来的心率会变成什么？

**做法**：给同一个 `GreenRppg` 喂**同一个 1.2 Hz（=72 bpm）正弦**，`frame_id` 一律连续递增
（与 `backend/run_pipeline.py` 的行为一致：每收到一帧 +1，与真实间隔无关），
**只改"这些样本实际是按多快采到的"**。

⚠️ 输入是**人为构造的信号**，用来暴露口径问题；这不是"测出来的心率"。

用法：  .venv\\Scripts\\python.exe metrics\\scripts\\probe_rppg_timebase.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from vital import GreenRppg  # noqa: E402

CFG_FPS = 30.0           # config.yaml: fps_nominal
WINDOW_S = 30.0          # config.yaml: window_seconds
BAND = (0.7, 3.5)        # config.yaml: hr_band_hz
F_TRUE = 1.2             # 真实心率 = 72 bpm


def run(fs_real: float, samples: int) -> dict:
    """按 fs_real 真实采样率采 samples 个 1.2 Hz 正弦样本，喂给 30 Hz 口径的估计器。"""
    rppg = GreenRppg(fps=CFG_FPS, window_seconds=WINDOW_S, hr_band_hz=BAND)
    for i in range(samples):
        v = int(round(3000.0 * math.sin(2 * math.pi * F_TRUE * i / fs_real)))
        rppg.push(v, i)                      # frame_id 连续递增 —— 与 run_pipeline 一致
    out = rppg.estimate()
    return {
        "真实采样率": fs_real,
        "窗口内样本数": rppg.samples,
        "窗口真实时长_s": round(samples / fs_real, 1),
        "代码以为的时长_s": round(samples / CFG_FPS, 1),
        "报告 hr_bpm": out["hr_bpm"],
        "hr_conf": out["hr_conf"],
    }


def main() -> int:
    samples = int(round(WINDOW_S * CFG_FPS))          # = 900
    print(f"输入信号：{F_TRUE} Hz 正弦 = {F_TRUE * 60:.0f} bpm；"
          f"窗口 need = {WINDOW_S:g}s x {CFG_FPS:g}fps = {samples} 样本")
    print(f"{'真实采样率':<12}{'窗口真实时长':<14}{'代码以为':<10}{'报告 hr_bpm':<14}{'hr_conf':<10}")
    for fs in (30.0, 13.0, 8.0, 3.7):
        row = run(fs, samples)
        print(f"{row['真实采样率']:<12}{row['窗口真实时长_s']:<14}{row['代码以为的时长_s']:<10}"
              f"{str(row['报告 hr_bpm']):<14}{str(row['hr_conf']):<10}")
    print("\n判读：只有 30 fps 那一行是对的；13 fps 把 72 bpm 报成 166 bpm（conf 0.9998）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
