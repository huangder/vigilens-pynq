#!/usr/bin/env python3
"""把 tb_pl_chain.v 的 SIM 行与 expected.json 对拍。

用法：
    python board/sim/check_xsim_log.py --log sim.log --expected <clip>/expected.json

退出码：0 = 全对；1 = 有不一致或缺少帧。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LINE_RE = re.compile(r"^SIM f=(\d+)\s+(.*)$")
KV_RE = re.compile(r"(\w+)=(\d+)")


def read_text(path: Path) -> str:
    """XSim/PowerShell 可能写 UTF-16 LE（带 BOM），也可能写 UTF-8。"""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8", errors="replace")


def parse_log(text: str) -> dict[int, dict[str, int]]:
    frames: dict[int, dict[str, int]] = {}
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        f = int(m.group(1))
        frames[f] = {k: int(v) for k, v in KV_RE.findall(m.group(2))}
    return frames


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="XSim PL 数据通路日志对拍")
    ap.add_argument("--log", required=True)
    ap.add_argument("--expected", required=True)
    args = ap.parse_args(argv)

    log_path = Path(args.log)
    exp_path = Path(args.expected)
    if not log_path.exists():
        print(f"FAIL 日志不存在：{log_path}")
        return 1
    if not exp_path.exists():
        print(f"FAIL 期望值不存在：{exp_path}")
        return 1

    expected = json.loads(read_text(exp_path))
    got = parse_log(read_text(log_path))
    n = int(expected["frames"])
    problems: list[str] = []

    if len(got) != n:
        problems.append(f"帧数不符：日志 {len(got)}，期望 {n}")
    for i in range(1, n + 1):
        g = got.get(i)
        if g is None:
            problems.append(f"f={i} 缺失")
            continue
        e_roi = expected["roi"][i - 1]
        e_rgb = expected["rgb"][i - 1]
        e_mot = expected["motion"][i - 1]
        checks = [
            ("roi_sum_r", e_roi["sum_r"]),
            ("roi_sum_g", e_roi["sum_g"]),
            ("roi_sum_b", e_roi["sum_b"]),
            ("roi_count", e_roi["count"]),
            ("rgb_pixels", e_rgb["pixels"]),
            ("rgb_sum", e_rgb["sum"]),
            ("mot_count", e_mot["count"]),
            ("roi_fid", i),
            ("rgb_fid", i),
            ("mot_fid", i),
        ]
        # 第 1 帧 motion 无效（prev_buf 片上未定义），不参与数值对拍
        if i >= 2 and e_mot["valid"]:
            checks += [
                ("mot_diff", e_mot["diff_total"]),
                ("mot_pixels", e_mot["motion_pixels"]),
                ("mot_ratio", e_mot["motion_ratio_q16"]),
            ]
        for key, want in checks:
            have = g.get(key)
            if have != want:
                problems.append(f"f={i} {key}: RTL={have} 期望={want}")

    if problems:
        print("XSim 对拍：FAIL")
        for p in problems:
            print("  - " + p)
        return 1

    print(f"XSim 对拍：PASS（{n} 帧；ROI/灰度 sum/尺寸/motion/frame_id 全对）")
    for i in range(1, n + 1):
        e_mot = expected["motion"][i - 1]
        if i == 1:
            print(f"  f={i}: motion 无效（预期）")
        else:
            print(
                f"  f={i}: motion_pixels={e_mot['motion_pixels']} "
                f"ratio={e_mot['motion_ratio_q16']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
