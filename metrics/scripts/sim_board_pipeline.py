"""PC simulation of the S1+S2+S3 board pipeline (JPEG forward / audit / threading).

用法（仓库根）：
    python metrics/scripts/sim_board_pipeline.py --source synthetic --frames 120
    python metrics/scripts/sim_board_pipeline.py --source data/raw/blink.mp4 --serve 9100

第一条命令跑四组对照并打印表：当前 2026-10-29 的 raw+串行 基线、S1（JPEG 透传）、
S1+S2（审计帧）、S1+S2+S3（三线程流水），同时给出解析模型值作交叉核对。

第二条命令扮演"假板端"：把 clip 编成 JPEG，按板端实测的单段耗时（31.6/19.9/16.7 ms）
模拟流水线，然后按 VLP 协议发给 `backend/run_pipeline.py --source board:127.0.0.1:9100`。

⚠️ 这不是上板证据：耗时是**仿真**的，只有 JPEG 字节、PL 公式、协议与 PC 解码是真的。
真实板端数字仍然只能来自 `metrics/evidence/2026-10-29_network_camera_pl_300.log` 这类日志。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import numpy as np

from backend.board_sim import (RGB_BYTES, SimConfig, encode_camera_frames,  # noqa: E402
                               simulate, stream_to_client)

WIDTH, HEIGHT = 1280, 720


def synthetic_frames(count: int):
    """A moving disc on a gradient: cheap to encode, enough motion for PL.

    Yielded one at a time: 120 frames of 1280x720 BGR would be 331 MB if a
    caller kept them all, and the simulator only needs the JPEG bytes.
    """
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    base = np.stack([xx / WIDTH * 180, yy / HEIGHT * 120,
                     (xx + yy) / (WIDTH + HEIGHT) * 90], axis=2).astype(np.uint8)
    for i in range(count):
        frame = base.copy()
        cx = int(WIDTH * (0.2 + 0.6 * (i % 60) / 59))
        cy = int(HEIGHT * (0.3 + 0.4 * ((i * 7) % 60) / 59))
        r = 90
        y0, y1 = max(0, cy - r), min(HEIGHT, cy + r)
        x0, x1 = max(0, cx - r), min(WIDTH, cx + r)
        frame[y0:y1, x0:x1] = (230, 210, 190)
        yield frame


def video_frames(path: str, count: int):
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频：{path}")
    seen = 0
    last: np.ndarray | None = None
    while seen < count:
        ok, frame = cap.read()
        if not ok:
            if last is None:
                break
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
            if not ok:
                break
        last = cv2.resize(frame, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
        seen += 1
        yield last
    cap.release()
    if seen == 0:
        raise SystemExit(f"视频里没有可用帧：{path}")
    while seen < count:                            # short clip: repeat last frame
        seen += 1
        yield last.copy()


def model_fps(cfg: SimConfig, payload_bytes: float) -> float:
    """Closed-form throughput for the same assumptions the simulator paces with."""
    send = payload_bytes / (cfg.link_mbps * 1_000_000 / 8.0)
    audit = (cfg.audit_ms / 1000.0 / cfg.audit_every) if cfg.audit_every else 0.0
    pl_total = cfg.pl_ms / 1000.0 + audit
    if cfg.pipeline == "serial":
        return 1.0 / (cfg.decode_ms / 1000.0 + pl_total + send)
    return min(cfg.camera_fps, 1.0 / max(cfg.decode_ms / 1000.0, pl_total + send))


def run_one(name: str, jpegs: list[bytes], cfg: SimConfig) -> dict:
    result = simulate(jpegs, cfg, sink=None)
    payload = float(np.median([len(j) for j in jpegs[:cfg.frames]])) if cfg.codec == "jpeg" else RGB_BYTES
    summary = result.summary()
    summary["name"] = name
    summary["model_fps"] = round(model_fps(cfg, payload), 2)
    return summary


def print_table(rows: list[dict]) -> None:
    print()
    print(f"{'方案':<34}{'实测 fps':>9}{'模型 fps':>9}{'每帧 ms':>9}"
          f"{'解码/PL/发 ms':>19}{'丢帧':>6}{'审计':>6}")
    print("-" * 96)
    for row in rows:
        per_frame = 1000.0 / row["fps"] if row["fps"] else float("nan")
        stages = "/".join(f"{row[key]['median']:.1f}" if row[key].get("n") else "-"
                          for key in ("decode_ms", "pl_ms", "send_ms"))
        print(f"{row['name']:<34}{row['fps']:>9.2f}{row['model_fps']:>9.2f}"
              f"{per_frame:>9.1f}{stages:>19}{row['frames_dropped']:>6}{row['audit_frames']:>6}")
    print()
    print("判据：R1 门限 = fps_nominal×0.95 = 28.5 fps（docs/15 §3.5）。")
    print("模型列 = 1/(各段之和) 或 1/max(解码, PL+发送)，用同一组仿真耗时算出的对照值。")
    overruns = {row["name"]: row["stage_overruns"] for row in rows if row["stage_overruns"]}
    if overruns:
        print("⚠️ 有阶段实测超过板端预算（说明是 PC 侧真算不过来，仿真值只是下限）：")
        for name, detail in overruns.items():
            print(f"   {name}: {detail}")


def main() -> int:
    ap = argparse.ArgumentParser(description="PC 仿真 S1(JPEG 透传)+S2(审计帧)+S3(三线程流水)")
    ap.add_argument("--source", default="synthetic",
                    help="synthetic 或视频路径（默认 synthetic）")
    ap.add_argument("--frames", type=int, default=120, help="仿真帧数（默认 120）")
    ap.add_argument("--serve", type=int, default=0,
                    help="作为假板端监听该端口，等待 run_pipeline --source board:127.0.0.1:PORT")
    ap.add_argument("--codec", choices=("jpeg", "raw"), default="jpeg")
    ap.add_argument("--pipeline", choices=("threaded", "serial"), default="threaded")
    ap.add_argument("--camera-fps", type=float, default=60.0,
                    help="相机产出节奏（DECXIN 实际协商 60fps）")
    ap.add_argument("--decode-ms", type=float, default=31.6)
    ap.add_argument("--pl-ms", type=float, default=19.9)
    ap.add_argument("--audit-ms", type=float, default=16.7)
    ap.add_argument("--audit-every", type=int, default=30, help="0=完全关闭审计")
    ap.add_argument("--link-mbps", type=float, default=100.0, help="仿真链路速率")
    ap.add_argument("--no-emulate", action="store_true", help="不 sleep，只测 PC 上限")
    ap.add_argument("--quality", type=int, default=75, help="仿真相机 JPEG 质量")
    ap.add_argument("--json", default="", help="把结果写成 JSON（可选）")
    args = ap.parse_args()

    frames = (synthetic_frames(args.frames) if args.source == "synthetic"
              else video_frames(args.source, args.frames))
    jpegs = encode_camera_frames(frames, quality=args.quality)
    sizes = [len(j) for j in jpegs]
    print(f"源：{args.source}｜帧数 {len(jpegs)}｜JPEG 中位 {np.median(sizes):.0f} B"
          f"（原始 {RGB_BYTES} B，放大 {RGB_BYTES / np.median(sizes):.1f}×）")

    base = dict(frames=args.frames, camera_fps=args.camera_fps,
                decode_ms=args.decode_ms, pl_ms=args.pl_ms, audit_ms=args.audit_ms,
                link_mbps=args.link_mbps, emulate=not args.no_emulate,
                motion_threshold=16)

    if args.serve:
        cfg = SimConfig(codec=args.codec, pipeline=args.pipeline,
                        audit_every=args.audit_every, **base)
        result = stream_to_client(jpegs, cfg, args.serve)
        summary = result.summary()
        summary["model_fps"] = round(model_fps(cfg, float(np.median(sizes))), 2)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.json:
            Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        return 0 if result.audit_mismatches == 0 else 1

    rows = [
        run_one("raw + serial（现状基线）", jpegs, SimConfig(codec="raw", pipeline="serial",
                                                           audit_every=1, **base)),
        run_one("jpeg + serial（S1）", jpegs, SimConfig(codec="jpeg", pipeline="serial",
                                                       audit_every=1, **base)),
        run_one("jpeg + serial + 审计30（S1+S2）", jpegs, SimConfig(codec="jpeg", pipeline="serial",
                                                                 audit_every=args.audit_every, **base)),
        run_one("jpeg + 三线程 + 审计30（S1+S2+S3）", jpegs, SimConfig(codec="jpeg", pipeline="threaded",
                                                                   audit_every=args.audit_every, **base)),
    ]
    print_table(rows)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
