#!/usr/bin/env python3
"""把 clip 送上开发板并跑 phase 10：薄封装 `board/s4_bench/s4_run.py video --clip`。

为什么单独一个入口：
  `make_clip.py`、`video_clip.py` 属于“视频转运”层，`s4_run.py` 属于“S4 裸机取证”层。
  上层只依赖这个入口，将来换成以太网/UDP 传输时，不需要改 `make_clip.py`。

用法（仓库根，板子已上电、JTAG 已连）：
    python board\\video_link\\run_video.py --clip metrics/logs/video_clips/720p_0
    python board\\video_link\\run_video.py --clip ... --dry-run   # 只生成 Tcl，不碰板子

⚠️ 这一步会真的写 DDR、跑 PL。没有板子时只能 `--dry-run`，退出码 0 只代表 Tcl 生成成功。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
S4_RUN = ROOT / "board" / "s4_bench" / "s4_run.py"

sys.path.insert(0, str(HERE))
from video_clip import verify_clip  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="视频 clip → 开发板 phase 10")
    ap.add_argument("--clip", required=True, help="make_clip.py 的输出目录")
    ap.add_argument("--dry-run", action="store_true", help="只生成 Tcl，不连板")
    args = ap.parse_args(argv)

    clip = Path(args.clip).resolve()
    v = verify_clip(clip)
    if not v["ok"]:
        print("clip 校验失败：" + "; ".join(v["reasons"]))
        return 1
    print(f"clip 校验通过：{v['bytes']} B，sha256 {v['sha256'][:16]}…")

    if not S4_RUN.exists():
        print(f"缺驱动：{S4_RUN}")
        return 1
    cmd = [sys.executable, str(S4_RUN), "video", "--clip", str(clip)]
    if args.dry_run:
        cmd.append("--gen")
    print("执行：" + " ".join(cmd))
    cp = subprocess.run(cmd, cwd=str(ROOT))
    return cp.returncode


if __name__ == "__main__":
    raise SystemExit(main())
