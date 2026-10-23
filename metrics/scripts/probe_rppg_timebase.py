"""probe_rppg_timebase.py —— rPPG 时间基准受控实验（对应 docs/16 BUG-029）。

**它回答什么**：`backend/vital.py` 的 `GreenRppg` 把"窗口首尾跨了多久"当成采样间隔。
如果这个时间来自 `frame_id / fps_nominal`（离线口径），那么**只要帧号连续，
反推出的采样率恒等于标称 30 fps**，与真实到达速率无关 —— 链路只有 8 fps 时，
频率轴会被放大 30/8 倍，报出来的心率是"看起来正常但错的数"（13 fps 下 72 → 166 bpm）。

**现在怎么修的**：活帧源（摄像头 / 网络流）把 `capture.Frame.ts`（真实到达时间）
一起交给 `push(ts=...)`；`estimate()` 用它算真实采样率，一旦与 `fps_nominal`
偏差超过 `config.yaml: rppg_fs_tol`（默认 10%）就**四项全 null**，并写明原因。

**做法**：给同一个 `GreenRppg` 喂**同一个 1.2 Hz（=72 bpm）正弦**，
`frame_id` 一律连续递增（与 `run_pipeline.py` 行为一致），**只改"这些样本实际是按多快采到的"**。
每档跑两遍：一遍只给 `frame_id`（旧口径），一遍连 `ts` 一起给（新口径）。

⚠️ 输入是**人为构造的信号**，用来暴露口径问题；这不是"测出来的心率"。

用法：  .venv\\Scripts\\python.exe metrics\\scripts\\probe_rppg_timebase.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

try:
    from console import enable_utf8_console  # type: ignore
    enable_utf8_console()
except Exception:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from vital import GreenRppg  # noqa: E402

CFG_FPS = 30.0           # config.yaml: fps_nominal
WINDOW_S = 30.0          # config.yaml: window_seconds
BAND = (0.7, 3.5)        # config.yaml: hr_band_hz
FS_TOL = 0.10            # config.yaml: rppg_fs_tol
F_TRUE = 1.2             # 真实心率 = 72 bpm


def run(fs_real: float, samples: int, *, with_ts: bool) -> dict:
    """按 fs_real 真实采样率采 samples 个 1.2 Hz 正弦样本，喂给 30 Hz 口径的估计器。"""
    rppg = GreenRppg(fps=CFG_FPS, window_seconds=WINDOW_S, hr_band_hz=BAND, fs_tol=FS_TOL)
    for i in range(samples):
        v = int(round(3000.0 * math.sin(2 * math.pi * F_TRUE * i / fs_real)))
        ts = i / fs_real if with_ts else None
        rppg.push(v, i, ts=ts)            # frame_id 连续递增 —— 与 run_pipeline 一致
    out = rppg.estimate()
    return {
        "真实采样率": fs_real,
        "窗口内样本数": rppg.samples,
        "窗口真实时长_s": round(samples / fs_real, 1),
        "time_base": rppg.last_time_base,
        "fs_eff": None if rppg.last_fs_eff is None else round(rppg.last_fs_eff, 2),
        "reject": rppg.last_reject,
        "报告 hr_bpm": out["hr_bpm"],
        "hr_conf": out["hr_conf"],
    }


def main() -> int:
    samples = int(round(WINDOW_S * CFG_FPS))          # = 900
    print(f"输入信号：{F_TRUE} Hz 正弦 = {F_TRUE * 60:.0f} bpm；"
          f"窗口 need = {WINDOW_S:g}s x {CFG_FPS:g}fps = {samples} 样本；"
          f"容差 rppg_fs_tol = {FS_TOL:.0%}")
    hdr = (f"{'真实采样率':<12}{'旧口径(仅fid)':<16}{'新口径(带ts)':<16}"
           f"{'fs_eff':<10}{'reject':<14}{'hr_conf':<10}")
    print(hdr)
    bad = 0
    for fs in (30.0, 30.14, 13.0, 8.0, 3.7):
        old = run(fs, samples, with_ts=False)
        new = run(fs, samples, with_ts=True)
        print(f"{fs:<12}{str(old['报告 hr_bpm']):<16}{str(new['报告 hr_bpm']):<16}"
              f"{str(new['fs_eff']):<10}{str(new['reject'] or '-'):<14}{str(new['hr_conf']):<10}")
        # 验收（docs/16 BUG-029）：偏差超容差的档必须 null，容差内必须按真实频率报数
        within = abs(fs - CFG_FPS) <= FS_TOL * CFG_FPS
        if within and new["报告 hr_bpm"] is None:
            bad += 1
        if (not within) and new["报告 hr_bpm"] is not None:
            bad += 1
    print()
    print("判读：旧口径在 13 fps 下把 72 bpm 报成 166 bpm（conf 0.9998）—— 那正是 BUG-029 的证据；")
    print("      新口径只在真实采样率与标称值相差 ≤ 容差时报数，其余一律 null（并给出 reject 原因）。")
    if bad:
        print(f"❌ 有 {bad} 档不符合验收判据")
        return 1
    print("✅ 全部档位符合验收判据（容差内报数、容差外 null）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
