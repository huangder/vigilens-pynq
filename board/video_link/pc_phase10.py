"""pc_phase10.py —— 在 PC 上模拟板端 phase 10 的逐帧结果。

**这不是上板，也不是"板端实测"。** 它只复现 phase 10 的**数据语义**：
  · 每一帧按冻结口径转灰度（`Y=(77R+150G+29B+128)>>8` + 3/5 或 3/8 采样）；
  · 对灰度字节算 `sum` 与 32 位 FNV-1a（与 `s4_bench.c` 的循环逐字节一致）；
  · 从第二帧起算帧差运动量（`motion_quality` 的整数口径，阈值严格大于）。

它**不**模拟 DMA、AXI-Stream、PL 时序、JTAG 带宽，也不产生 `res.bin`。
用途是：板子不在手边时，先证明 clip 打包/灰度/哈希/运动量这条链在 PC 上自洽，
并把"PC 模拟、非上板"写进输出，避免和板端日志混为一谈。

运行：
    .venv\\Scripts\\python.exe board/video_link/run_pc_sim.py --clip <clip目录>
或作为库：
    from pc_phase10 import simulate_clip, write_pc_result
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

try:  # 作为包导入
    from .video_clip import (
        TIERS,
        ClipError,
        clip_digests,
        fnv1a32,
        get_tier,
        gray_from_rgb,
        iter_clip_frames,
        load_manifest,
        verify_clip,
    )
except ImportError:  # 作为脚本 / 测试直接导入
    from video_clip import (  # type: ignore
        TIERS,
        ClipError,
        clip_digests,
        fnv1a32,
        get_tier,
        gray_from_rgb,
        iter_clip_frames,
        load_manifest,
        verify_clip,
    )

SCHEMA = "vigilens.pc_phase10/1"
DEFAULT_MAX_FRAMES = 60  # 与固件 VID_MAX_FRAMES 对齐
DEFAULT_MOTION_THRESH = 16  # config.yaml motion_thresh_gray，与 HLS 默认寄存器一致


def _motion(prev: np.ndarray | None, cur: np.ndarray, thresh: int) -> dict[str, Any]:
    """与 `motion_quality` 相同的整数帧差口径；第一帧没有 prev，输出无效。"""
    if prev is None or prev.shape != cur.shape:
        return {
            "valid": False,
            "diff_total": 0,
            "motion_pixels": 0,
            "motion_ratio_q16": 0,
            "count": int(cur.size),
        }
    diff = np.abs(cur.astype(np.int32) - prev.astype(np.int32))
    count = int(diff.size)
    if count == 0:
        return {
            "valid": False,
            "diff_total": 0,
            "motion_pixels": 0,
            "motion_ratio_q16": 0,
            "count": 0,
        }
    motion_pixels = int((diff > int(thresh)).sum())
    return {
        "valid": True,
        "diff_total": int(diff.sum()),
        "motion_pixels": motion_pixels,
        "motion_ratio_q16": (motion_pixels << 16) // count,
        "count": count,
    }


def simulate_clip(
    clip_dir: str | Path,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    motion_thresh: int = DEFAULT_MOTION_THRESH,
    compare_digests: bool = True,
) -> dict[str, Any]:
    """模拟 phase 10 的 N 帧处理，返回与板端日志同构的结果。

    `compare_digests=True` 时用 `clip_digests()` 独立再算一遍 sum/hash；
    两边不一致就把该帧标成 mismatch（`vid_fails` 计数），但仍然输出全部帧的记录。
    """
    clip = Path(clip_dir)
    if not clip.exists():
        raise ClipError(f"clip 目录不存在：{clip}")
    if max_frames < 1:
        raise ValueError("max_frames 必须 >= 1")
    if max_frames > DEFAULT_MAX_FRAMES:
        raise ValueError(f"max_frames 最大 {DEFAULT_MAX_FRAMES}（与固件 VID_MAX_FRAMES 对齐）")

    verified = verify_clip(clip)
    if not verified.get("ok"):
        raise ClipError("clip 校验失败：" + "；".join(verified.get("reasons", [])))

    manifest = load_manifest(clip)
    tier_name = str(manifest["tier"])
    tier = get_tier(tier_name)
    expected = clip_digests(clip) if compare_digests else []

    per_frame: list[dict[str, Any]] = []
    log_lines: list[str] = []
    prev_gray: np.ndarray | None = None
    fails = 0
    for i, rgb in enumerate(iter_clip_frames(clip)):
        if i >= max_frames:
            break
        gray = gray_from_rgb(rgb, tier)
        raw = gray.tobytes(order="C")
        gray_sum = int(gray.sum(dtype=np.uint64))
        gray_hash = fnv1a32(raw)
        motion = _motion(prev_gray, gray, motion_thresh)
        prev_gray = gray

        match = True
        if compare_digests:
            if i >= len(expected):
                match = False
            else:
                match = gray_sum == expected[i]["sum"] and gray_hash == expected[i]["hash"]
        if not match:
            fails += 1

        fid = i + 1
        per_frame.append(
            {
                "fid": fid,
                "sum": gray_sum,
                "hash": gray_hash,
                "hash_hex": f"0x{gray_hash:08X}",
                "digest_match": match,
                **motion,
            }
        )
        log_lines.append(f"vid f={fid} sum={gray_sum} hash=0x{gray_hash:08X} fid={fid}")

    n = len(per_frame)
    return {
        "schema": SCHEMA,
        "mode": "pc_sim",  # 关键：下游/报告必须能看出这不是板端
        "is_board_run": False,
        "note": "PC 模拟 phase 10 的数据语义；无 DMA / 无 AXI-Stream / 无 PL 时序，不是上板实测",
        "clip": str(clip.resolve()),
        "tier": tier_name,
        "width": int(manifest["width"]),
        "height": int(manifest["height"]),
        "gray_width": tier.gray_width,
        "gray_height": tier.gray_height,
        "frames": n,
        "manifest_frames": int(manifest["frames"]),
        "frame_bytes": int(manifest["frame_bytes"]),
        "gray_bytes": tier.gray_width * tier.gray_height,
        "motion_thresh": int(motion_thresh),
        "vid_ok": n - fails,
        "vid_fails": fails,
        "digest_match": fails == 0,
        "fid_first": 1 if n else 0,
        "fid_last": n,
        "per_frame": per_frame,
        "log": log_lines,
    }


def write_pc_result(result: dict[str, Any], out_dir: str | Path) -> dict[str, str]:
    """把模拟结果写成 JSON + 与串口日志同格式的文本。"""
    import json

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "pc_phase10.json"
    log_path = out / "pc_phase10.txt"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    header = (
        "# PC 模拟 phase 10（非上板）\n"
        f"# clip={result['clip']}\n"
        f"# tier={result['tier']} {result['width']}x{result['height']} -> "
        f"{result['gray_width']}x{result['gray_height']}\n"
        f"# frames={result['frames']} vid_ok={result['vid_ok']} vid_fails={result['vid_fails']}\n"
        f"# note={result['note']}\n"
    )
    log_path.write_text(header + "\n".join(result["log"]) + "\ndone_video\n", encoding="utf-8")
    return {"json": str(json_path), "log": str(log_path)}
