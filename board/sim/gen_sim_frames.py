#!/usr/bin/env python3
"""生成 XSim 数据通路仿真的输入帧与 Python 期望值。

产物（默认写到临时目录，不入库）：
    frames.bin      3 帧 640x480 RGB888，frame-major
    manifest.json   clip 清单（sha256 / 尺寸 / 档位）
    expected.json   ROI 和、灰度 sum、motion_quality 期望值

默认用**确定性测试图案**；给 `--source` 时改用真实视频/摄像头片段。
无论哪种，帧文件只落在临时目录，**不入库**（真实人脸视频受隐私红线约束）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "board" / "video_link"))

from make_clip import _source_frames, _test_frames  # noqa: E402
from pc_phase10 import simulate_clip  # noqa: E402
from video_clip import get_tier, iter_clip_frames, write_clip  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 XSim 输入帧 + 期望值")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "vigilens_xsim" / "clip"))
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--tier", default="vga", choices=["vga", "720p"])
    ap.add_argument("--source", default="", help="真实视频/摄像头源；省略则用测试图案")
    ap.add_argument("--allow-resize", action="store_true", help="源尺寸不符时允许 OpenCV 缩放")
    args = ap.parse_args(argv)

    if args.frames < 2:
        ap.error("--frames 至少 2（motion_quality 第 1 帧无有效帧差）")
    tier = get_tier(args.tier)
    out = Path(args.out)
    source_label = args.source or "xsim-test-pattern"
    if args.source:
        frames = _source_frames(
            args.source,
            width=tier.width,
            height=tier.height,
            fps=tier.fps,
            frames=args.frames,
            loop=False,
        )
    else:
        frames = _test_frames(args.frames, tier.width, tier.height)
    manifest = write_clip(
        frames,
        out,
        tier=tier,
        source=source_label,
        max_frames=args.frames,
        allow_resize=args.allow_resize,
    )

    sim = simulate_clip(out, max_frames=args.frames, compare_digests=True)
    roi = []
    for rgb in iter_clip_frames(out):
        roi.append(
            {
                "sum_r": int(rgb[:, :, 0].sum(dtype="uint64")),
                "sum_g": int(rgb[:, :, 1].sum(dtype="uint64")),
                "sum_b": int(rgb[:, :, 2].sum(dtype="uint64")),
                "count": int(rgb.shape[0] * rgb.shape[1]),
            }
        )

    expected = {
        "schema": "vigilens.xsim_expected/1",
        "note": "RTL 仿真期望值；PLC 数据通路，不是上板实测",
        "tier": tier.name,
        "width": tier.width,
        "height": tier.height,
        "gray_width": tier.gray_width,
        "gray_height": tier.gray_height,
        "frames": sim["frames"],
        "motion_thresh": sim["motion_thresh"],
        "roi": roi,
        "rgb": [
            {
                "pixels": sim["gray_width"] * sim["gray_height"],
                "sum": p["sum"],
                "hash": p["hash"],
            }
            for p in sim["per_frame"]
        ],
        "motion": [
            {
                "valid": p["valid"],
                "diff_total": p["diff_total"],
                "motion_pixels": p["motion_pixels"],
                "motion_ratio_q16": p["motion_ratio_q16"],
                "count": p["count"],
            }
            for p in sim["per_frame"]
        ],
        "clip": str(out.resolve()),
        "frames_bin": str((out / "frames.bin").resolve()),
        "manifest_sha256": manifest["sha256"],
    }
    (out / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # 测试台用 `include "sim_config.vh"` 读尺寸/帧数，避免依赖 xvlog 的 -d 传参。
    (out / "sim_config.vh").write_text(
        "\n".join(
            [
                f"`define SIM_W {tier.width}",
                f"`define SIM_H {tier.height}",
                f"`define SIM_GW {tier.gray_width}",
                f"`define SIM_GH {tier.gray_height}",
                f"`define SIM_NFRAMES {sim['frames']}",
                "",
            ]
        ),
        encoding="ascii",
    )

    print(f"XSim clip: {out}")
    print(f"  frames      : {expected['frames']}")
    print(f"  frames.bin  : {out / 'frames.bin'}")
    print(f"  expected    : {out / 'expected.json'}")
    print(f"  sha256      : {manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
