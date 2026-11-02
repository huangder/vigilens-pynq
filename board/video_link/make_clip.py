#!/usr/bin/env python3
"""命令行：摄像头 / 视频文件 → 开发板可接收的 clip 目录。

用法（仓库根）：
    # 先看档位
    python board\\video_link\\make_clip.py --list-tiers

    # 主机 USB 摄像头（UVC），取 10 帧 720p
    python board\\video_link\\make_clip.py --source 0 --tier 720p --frames 10

    # 一段已经录好的视频（仍算"摄像头采集的视频"）
    python board\\video_link\\make_clip.py --source data/raw/blink.mp4 --tier 720p --frames 10

    # 没有摄像头时，用确定性测试图案验证链路（**合成帧，报告里不能写成摄像头**）
    python board\\video_link\\make_clip.py --test-pattern --tier 720p --frames 10

产物默认写到 `metrics/logs/video_clips/<tier>_<源名>/`（`metrics/logs/` 不入库）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

from video_clip import (  # noqa: E402
    TIERS,
    ClipError,
    get_tier,
    plan_transfer,
    write_clip,
)


def _safe(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("_")
    return s or "clip"


def _test_frames(n: int, width: int, height: int):
    """确定性测试图案：竖直渐变 + 每帧右移的白条 + 棋盘暗角。

    只用于验证"传输 + 板端灰度哈希"链路；它**不是**摄像头画面。
    """
    yy = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
    xx = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    checker = (((np.arange(height)[:, None] // 32) + (np.arange(width)[None, :] // 32)) % 2) * 18
    for i in range(n):
        base = np.empty((height, width, 3), dtype=np.uint8)
        base[:, :, 0] = np.clip(xx.astype(np.int16) + 20, 0, 255)          # B
        base[:, :, 1] = np.clip(yy.astype(np.int16) + checker, 0, 255)     # G
        base[:, :, 2] = np.clip(255 - xx.astype(np.int16) // 2, 0, 255)    # R
        bar = (i * 11) % width
        base[:, max(0, bar - 3): bar + 3, :] = 255
        yield base


def _source_frames(source: str, *, width: int, height: int, fps: int, frames: int, loop: bool):
    from capture import open_frame_source, will_be_synthetic

    if will_be_synthetic(source):
        raise ClipError(
            f"源 {source!r} 是合成帧源（没有像素），不能打包；"
            "用 --test-pattern，或换真实摄像头/视频文件"
        )
    it, desc = open_frame_source(
        source, width=width, height=height, fps=fps, limit=frames, loop=loop
    )
    print(f"帧源：{desc}")
    for fr in it:
        yield fr.image


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="视频 → 开发板 clip 打包")
    ap.add_argument("--source", help="摄像头序号（0）、uvc:0、视频文件、mjpeg:URL")
    ap.add_argument("--test-pattern", action="store_true", help="用确定性测试图案代替摄像头")
    ap.add_argument("--tier", default="720p", choices=sorted(TIERS), help="几何档位")
    ap.add_argument("--frames", type=int, default=10, help="最多取多少帧（默认 10）")
    ap.add_argument("--fps", type=int, default=None, help="只写进 manifest；默认取档位值")
    ap.add_argument("--loop", action="store_true", help="视频文件循环读取")
    ap.add_argument("--allow-resize", action="store_true", help="源尺寸不符时用 OpenCV 缩放")
    ap.add_argument("--out", help="输出目录（默认 metrics/logs/video_clips/<tier>_<源名>）")
    ap.add_argument("--list-tiers", action="store_true", help="列出档位后退出")
    args = ap.parse_args(argv)

    if args.list_tiers:
        for t in TIERS.values():
            print(
                f"{t.name:5s} {t.width}×{t.height} @ {t.fps} fps → 灰度 "
                f"{t.gray_width}×{t.gray_height}（{t.sample}/{t.keep} 抽取）"
            )
            print(f"      bitstream: {t.bitstream}")
            print(f"      firmware : {t.firmware}")
        return 0

    if not args.source and not args.test_pattern:
        ap.error("必须给 --source，或显式用 --test-pattern")
    if args.source and args.test_pattern:
        ap.error("--source 与 --test-pattern 只能选一个")
    if args.frames <= 0:
        ap.error("--frames 必须 > 0")

    t = get_tier(args.tier)
    label = "test-pattern" if args.test_pattern else args.source
    out = Path(args.out) if args.out else (
        ROOT / "metrics" / "logs" / "video_clips" / f"{t.name}_{_safe(str(label))}"
    )
    if (out / "frames.bin").exists():
        print(f"⚠️ 覆盖已有 clip：{out}")

    try:
        frames = (
            _test_frames(args.frames, t.width, t.height)
            if args.test_pattern
            else _source_frames(
                str(args.source),
                width=t.width,
                height=t.height,
                fps=args.fps or t.fps,
                frames=args.frames,
                loop=args.loop,
            )
        )
        manifest = write_clip(
            frames,
            out,
            tier=t,
            fps=args.fps,
            source=str(label),
            max_frames=args.frames,
            allow_resize=args.allow_resize,
        )
    except (ClipError, FileNotFoundError, RuntimeError) as e:
        print(f"失败：{e}")
        return 1

    print(f"已写入 {out}")
    print(
        f"  {manifest['frames']} 帧 × {manifest['width']}×{manifest['height']} "
        f"= {manifest['bytes']} B（{manifest['bytes'] / 1e6:.2f} MB）"
    )
    print(f"  sha256 {manifest['sha256']}")
    plan = plan_transfer(out)
    print(
        f"  JTAG 传输估算：{plan['estimated_seconds']:.1f} s"
        f"（按 {plan['link_bytes_per_s'] / 1e6:.2f} MB/s；以实跑为准）"
    )
    print(f"下一步：python board\\video_link\\run_video.py --clip {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
