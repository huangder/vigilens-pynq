"""run_pipeline.py —— A 线端到端入口：视频回放 → 契约 JSON（《04》第 4 节 A 线最小交付）。

    VideoCapture(回放视频) → 人脸框 → EAR → 契约 JSON → 打印/写文件

几个刻意的设计决定，都会影响《02》的验收口径，写在这里避免以后被当成 bug：

1. **逻辑时间戳 = frame_id / fps，不用墙上时钟。**
   PERCLOS / 眨眼率都是滑动窗口统计，如果时间戳取 time.time()，同一段视频
   跑两次会得到不同数值 —— 直接违反《02》A3 的"同视频重复运行结果一致"。
   因此本文件用帧序号推时间轴；真实实时采集时才用墙上时钟（--wall-clock）。

2. **上游关键点是 stub 时，必须在输出里说清楚。**
   每次运行结束都打印"本次数据来源"，并在 summary JSON 里标 `landmark_source`。
   报告/PPT 引用数字时必须知道哪些是占位数据（《05》AI 使用约束的口径）。

3. **质量门控不过时不允许输出心率/呼吸**（decision.gate_vitals），
   所以 `vital.*` 大面积为 null 是**正确行为**，不是 bug。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .behavior_metrics import BehaviorTracker
    from .capture import open_frame_source
    from .config import REPO_ROOT, load_config
    from .contract import new_frame
    from .decision import DecisionEngine
    from .face_landmark import make_landmarker
    from .quality import QualityScorer
    from .storage import MetricsStorage, write_snapshot
except ImportError:  # python backend/run_pipeline.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from behavior_metrics import BehaviorTracker
    from capture import open_frame_source
    from config import REPO_ROOT, load_config
    from contract import new_frame
    from decision import DecisionEngine
    from face_landmark import make_landmarker
    from quality import QualityScorer
    from storage import MetricsStorage, write_snapshot


def build_frame(
    *,
    frame_id: int,
    ts: float,
    obs: Any,
    behavior: dict,
    quality: dict,
    engine: DecisionEngine,
) -> tuple[dict, dict]:
    """把各模块输出装配成一帧契约 JSON。返回 (frame, decision)。"""
    beh_public = BehaviorTracker.strip_private(behavior)
    q_public = QualityScorer.strip_private(quality)
    face = {
        "visible": round(float(obs.visible), 3),
        "bbox": [int(v) for v in obs.bbox],
        "pose": obs.pose,
    }

    # 心率/呼吸：本阶段 A 线尚未实现 rPPG（《02》风险表：rPPG 严格放最后）。
    # 这里只做"门控行为"的占位：没有真值就一律 null，绝不编数字。
    vital = engine.gate_vitals(q_public["overall"], None)

    dec = engine.decide(frame_id=frame_id, behavior=beh_public, quality=q_public, face=face)
    frame = new_frame(
        ts=round(ts, 3),
        frame_id=frame_id,
        face=face,
        behavior=beh_public,
        vital=vital,
        quality=q_public,
        status=dec["status"],
        advice=dec["advice"],
        reason=dec["reason"],
    )
    frame["_triggers"] = dec["triggers"]   # 非契约字段：只在内存里传，落盘前会被剔除
    return frame, dec


def strip_internal(frame: dict) -> dict:
    """去掉 `_` 开头的内部字段，得到严格符合契约的帧。"""
    return {k: v for k, v in frame.items() if not k.startswith("_")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VigiLens A 线：视频回放 → 契约 JSON / CSV")
    ap.add_argument("--source", default="synthetic",
                    help="视频文件路径 / 摄像头序号 / synthetic（默认，不需要任何依赖）")
    ap.add_argument("--pattern", default="blink", choices=["blink", "yawn", "still", "turn"],
                    help="stub 关键点的行为模式（装好 mediapipe 后此项被忽略）")
    ap.add_argument("--stub", action="store_true", help="强制使用 stub 关键点（对比用）")
    ap.add_argument("--fps", type=float, default=None, help="逻辑帧率，默认取 config.yaml")
    ap.add_argument("--seconds", type=float, default=None, help="只跑前 N 秒（按 --fps 换算成帧数）")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 帧")
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--wall-clock", action="store_true",
                    help="用墙上时钟做时间戳（真实实时采集用；会牺牲可复现性）")
    ap.add_argument("--json", dest="json_out", default="metrics/logs/last.json", help="最新一帧快照路径")
    ap.add_argument("--jsonl", default=None, help="逐帧追加输出（如 metrics/logs/stream.jsonl）")
    ap.add_argument("--csv", default=None, help="展平 CSV 输出（如 metrics/csv/metrics.csv）")
    ap.add_argument("--summary", default=None, help="把本次运行摘要写成 JSON（证据归档用）")
    ap.add_argument("--print-every", type=int, default=0, help="每 N 帧打印一行（0 = 不打印）")
    ap.add_argument("--quiet", action="store_true", help="不打印进度，只打印最终摘要")
    args = ap.parse_args(argv)

    cfg = load_config()
    fps = float(args.fps if args.fps is not None else cfg["fps_nominal"])
    width = int(args.width if args.width is not None else cfg["fpga"]["img_width"])
    height = int(args.height if args.height is not None else cfg["fpga"]["img_height"])

    limit = args.limit
    if limit is None and args.seconds is not None:
        limit = int(round(args.seconds * fps))

    def say(*a: Any) -> None:
        if not args.quiet:
            print(*a)

    say(f"=== VigiLens A 线：视频回放 → 契约 JSON ===")
    say(f"config : {cfg['_source']}  (PyYAML={'有' if cfg['_yaml_available'] else '无，用内置解析器'})")
    say(f"逻辑帧率: {fps} fps   逻辑时间戳: {'墙上时钟（不可复现）' if args.wall_clock else 'frame_id/fps（可复现）'}")

    frames_iter, desc = open_frame_source(args.source, width=width, height=height, fps=fps, limit=limit)
    say(f"帧源   : {desc}")

    landmarker = make_landmarker(width=width, height=height, pattern=args.pattern, fps=fps, force_stub=args.stub)
    lm_source = "stub" if type(landmarker).__name__ == "StubLandmarker" else "mediapipe"
    if lm_source == "stub":
        say("[warn] 本次人脸关键点来自 StubLandmarker —— EAR/MAR 的数值是**占位几何量**，")
        say("       下游状态机与规则是真实代码。报告引用数字时必须标注这一点。")

    tracker = BehaviorTracker(cfg)
    scorer = QualityScorer(cfg)
    engine = DecisionEngine(cfg)

    json_out = REPO_ROOT / args.json_out
    jsonl_out = REPO_ROOT / args.jsonl if args.jsonl else None
    csv_out = REPO_ROOT / args.csv if args.csv else None

    status_counter: Counter[str] = Counter()
    t_start = time.perf_counter()
    last_frame: dict | None = None
    n = 0

    with MetricsStorage(jsonl_out, csv_out) as store:
        for frame in frames_iter:
            ts = time.time() if args.wall_clock else frame.frame_id / fps
            obs = landmarker.detect(frame.image, frame.frame_id)
            beh = tracker.update(frame.frame_id, ts, obs)
            qua = scorer.update(frame.frame_id, ts, frame, obs)
            full, dec = build_frame(frame_id=frame.frame_id, ts=ts, obs=obs,
                                    behavior=beh, quality=qua, engine=engine)
            clean = strip_internal(full)
            store.write(clean)
            write_snapshot(json_out, clean)
            status_counter[dec["status"]] += 1
            last_frame = clean
            n += 1
            if args.print_every and n % args.print_every == 0:
                say(f"  t={ts:6.1f}s f{frame.frame_id:5d} {clean['status']:15s} "
                    f"EAR={clean['behavior']['ear_left']:.3f} PERCLOS={clean['behavior']['perclos']:.3f} "
                    f"Q={clean['quality']['overall']:.2f} | {clean['reason'][:52]}")

    if last_frame is None:
        print("[FAIL] 没有处理任何帧：帧源是空的。检查 --source 路径 / --limit 是否被设成 0。", file=sys.stderr)
        return 2

    elapsed = time.perf_counter() - t_start
    hist = last_frame["behavior"]
    summary = {
        "landmark_source": lm_source,
        "frame_source": desc,
        "pattern": args.pattern,
        "frames_processed": n,
        "logical_fps": fps,
        "logical_duration_s": round(n / fps, 2),
        "wall_elapsed_s": round(elapsed, 2),
        "throughput_fps": round(n / elapsed, 1) if elapsed > 0 else None,
        "status_counts": dict(status_counter),
        "final": {
            "blink_count": hist["blink_count"],
            "blink_rate_per_min": hist["blink_rate_per_min"],
            "perclos": hist["perclos"],
            "yawn_count": hist["yawn_count"],
            "long_close_count": hist["long_close_count"],
            "quality_overall": last_frame["quality"]["overall"],
            "status": last_frame["status"],
        },
        "outputs": {"json": str(json_out), "jsonl": str(jsonl_out) if jsonl_out else None,
                    "csv": str(csv_out) if csv_out else None},
        "note": ("landmark_source=stub 时，EAR/MAR 为占位几何量；"
                 "vital.* 全为 null 是因为 rPPG 未实现且质量门控要求 ≥ %s" % cfg["vital_require_quality"]),
    }

    print("\n=== 运行摘要 ===")
    print(f"处理帧数  : {n}（逻辑时长 {summary['logical_duration_s']}s，墙上耗时 {elapsed:.2f}s）")
    print(f"关键点来源: {lm_source}" + ("  ← 占位数据，勿直接用于报告" if lm_source == "stub" else ""))
    print(f"状态分布  : " + "  ".join(f"{k}={v}" for k, v in status_counter.most_common()))
    print(f"末帧指标  : 眨眼 {hist['blink_count']} 次 / {hist['blink_rate_per_min']} per min，"
          f"PERCLOS {hist['perclos']:.3f}，哈欠 {hist['yawn_count']}，长闭眼 {hist['long_close_count']}")
    print(f"末帧质量  : overall {last_frame['quality']['overall']:.2f} "
          f"(light {last_frame['quality']['light_score']:.2f} / motion {last_frame['quality']['motion_score']:.2f})")
    print(f"末帧状态  : {last_frame['status']} —— {last_frame['advice']}")
    print(f"产物      : {json_out}" + (f" | {jsonl_out}" if jsonl_out else "")
          + (f" | {csv_out}" if csv_out else ""))

    if args.summary:
        sp = REPO_ROOT / args.summary
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"摘要      : {sp}")
    if store.errors:
        print(f"[warn] 有 {len(store.errors)} 帧被契约校验拦下（未写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
