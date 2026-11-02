"""run_pc_sim.py —— 板子不在手边时，在 PC 上跑 phase 10 的等价数据链。

默认只做 PL 数据语义模拟：
    .venv\\Scripts\\python.exe board/video_link/run_pc_sim.py --clip <clip目录>

加 `--pipeline` 会接着用同一份 clip 跑 A 线管线（`backend/run_pipeline.py --source clip:...`），
并把 `pc_phase10.json` 的 `motion_ratio` 通过 `--pl-motion-json` 覆盖
`quality.motion_score`，于是"视频 -> 模拟 PL -> quality/behavior/vital -> 契约 JSON -> 网页"
在 PC 上整链可跑；再加 `--post auto` 可推到 B 线网页（需先起 `backend/api.py`）。

⚠️ 输出始终带 `"mode": "pc_sim"` / `"is_board_run": false`。
不要把它写成上板结论；板端是否真的跑通，仍以 `s4_run.py video` 的串口日志为准。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pc_phase10 import DEFAULT_MAX_FRAMES, DEFAULT_MOTION_THRESH, simulate_clip, write_pc_result  # noqa: E402


def _enable_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _default_out(clip: Path) -> Path:
    return clip.parent / (clip.name + "_pc_sim")


def _run_pipeline(
    clip: Path,
    out: Path,
    *,
    limit: int,
    post: str | None,
    post_hz: float | None,
    pl_motion_json: Path | None = None,
) -> tuple[int, dict[str, Any] | None]:
    summary = out / "summary.json"
    cmd = [
        sys.executable,
        str(ROOT / "backend" / "run_pipeline.py"),
        "--source", f"clip:{clip}",
        "--limit", str(limit),
        "--quiet",
        "--json", str(out / "last.json"),
        "--jsonl", str(out / "stream.jsonl"),
        "--summary", str(summary),
    ]
    if pl_motion_json is not None:
        # 用 phase-10 的运动量覆盖 quality.motion_score，形成
        # “视频 -> PL 运动量 -> 质量分 -> 契约帧”的闭环；无效帧由 A 线跳过。
        cmd += ["--pl-motion-json", str(pl_motion_json)]
    if post:
        cmd += ["--post", post]
        if post_hz is not None:
            cmd += ["--post-hz", str(post_hz)]
    print("[pipeline] " + " ".join(cmd))
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    data = None
    if summary.exists():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
    return rc, data


def main(argv: list[str] | None = None) -> int:
    _enable_utf8()
    ap = argparse.ArgumentParser(description="PC 端 phase 10 模拟（非上板）")
    ap.add_argument("--clip", required=True, help="make_clip.py 生成的 clip 目录（含 manifest.json）")
    ap.add_argument("--out", default=None, help="输出目录；默认 <clip>_pc_sim")
    ap.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES,
                    help=f"最多模拟多少帧（1~{DEFAULT_MAX_FRAMES}，默认 {DEFAULT_MAX_FRAMES}）")
    ap.add_argument("--motion-thresh", type=int, default=DEFAULT_MOTION_THRESH,
                    help=f"帧差运动阈值（默认 {DEFAULT_MOTION_THRESH}，与 config/HLS 对齐）")
    ap.add_argument("--pipeline", action="store_true",
                    help="模拟后继续跑 A 线管线（clip: 帧源），产出契约 JSON")
    ap.add_argument("--post", default=None,
                    help="配合 --pipeline：auto 或 http://127.0.0.1:8000/api/ingest")
    ap.add_argument("--post-hz", type=float, default=None)
    args = ap.parse_args(argv)

    clip = Path(args.clip)
    out = Path(args.out) if args.out else _default_out(clip)
    try:
        result = simulate_clip(
            clip,
            max_frames=args.max_frames,
            motion_thresh=args.motion_thresh,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[pc-sim] 失败：{e}")
        return 2

    paths = write_pc_result(result, out)
    print(f"[pc-sim] clip      : {result['clip']}")
    print(f"[pc-sim] tier      : {result['tier']} {result['width']}x{result['height']} -> "
          f"{result['gray_width']}x{result['gray_height']}")
    print(f"[pc-sim] frames    : {result['frames']}  vid_ok={result['vid_ok']}  "
          f"vid_fails={result['vid_fails']}  digest_match={result['digest_match']}")
    print(f"[pc-sim] json      : {paths['json']}")
    print(f"[pc-sim] log       : {paths['log']}")
    print("[pc-sim] 注意：这是 PC 模拟，不是板端实测。")

    if not result["digest_match"]:
        print("[pc-sim] 错误：模拟结果与 clip_digests 不一致，先查打包/灰度参考。")
        return 3

    if args.pipeline:
        rc, summary = _run_pipeline(
            clip.resolve(), out,
            limit=int(result["frames"]),
            post=args.post,
            post_hz=args.post_hz,
            pl_motion_json=Path(paths["json"]),
        )
        if summary is not None:
            summary["pc_sim"] = {
                "mode": "pc_sim",
                "is_board_run": False,
                "clip": result["clip"],
                "phase10_json": paths["json"],
                "vid_ok": result["vid_ok"],
                "vid_fails": result["vid_fails"],
                "digest_match": result["digest_match"],
                "quality_motion_source": "pl_sim",
                "note": result["note"],
            }
            (out / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        if rc != 0:
            print(f"[pipeline] 退出码 {rc}（契约 JSON 可能已落盘，见 {out}）")
            return rc
        print(f"[pipeline] 完成：{out / 'last.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
