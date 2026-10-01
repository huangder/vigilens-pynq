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

4. **M2（P5）的交接点是 `--post <URL>`，不是进程内总线。**
   A 线测量与 B 线服务是两个进程，`hub.HUB` 跨不过去；正式通道是 B 线
   `api.py` 已经留好的 `POST /api/ingest`。加 `--post` 之后，每帧按
   `ws_push_hz`（逻辑时间 1 帧/秒）推过去，B 线网页上看到的就是本次运行的真实数字。

   推送失败的处理有**两条**约束，缺一不可：
     a) **不许静默丢帧** —— 失败要打印、要进 summary、最终以**退出码 3** 收场；
     b) **不许把测量一起弄丢** —— B 线服务没起是 B 线的事，A 线的测量产物
        （JSON / CSV / 证据）必须照常跑完落盘，否则"忘了先起服务"会让整段测量白跑，
        还可能留下一个只有 1 行的半截 CSV 被误当成一次完整测量（数据真实性风险）。
   因此失败后**停止后续推送**（避免逐帧重试刷爆日志），但测量继续到最后。

5. **收尾帧 status=done 只进 jsonl 流与推送，不进 CSV。**
   CSV 是逐帧测量表（P4 标定/黄金比对按行数统计），done 不是一次测量结果；
   jsonl 是时间流，B 线要能在流里看到第 6 种状态。
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
    from .age_contract import dumps as age_dumps
    from .age_contract import validate_age_state
    from .age_infer import AgeProfileStore
    from .behavior_metrics import BehaviorTracker
    from .capture import open_frame_source
    from .config import REPO_ROOT, load_config
    from .contract import new_frame
    from .decision import DecisionEngine
    from .face_landmark import make_landmarker
    from .fatigue_coupling import CoupledRiskModel
    from .fatigue_db import FatigueDB, load_reference
    from .quality import QualityScorer
    from .publish import (FramePoster, PostError, PreviewPoster, SidecarPoster,
                          VideoPusher, age_url_from_ingest, frame_url_from_ingest,
                          full_url, preview_url)
    from .storage import MetricsStorage, write_snapshot
    from .vital import GreenRppg, forehead_roi, q15_from_roi_sum, roi_channel_sum
except ImportError:  # python backend/run_pipeline.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from age_contract import dumps as age_dumps
    from age_contract import validate_age_state
    from age_infer import AgeProfileStore
    from behavior_metrics import BehaviorTracker
    from capture import open_frame_source
    from config import REPO_ROOT, load_config
    from contract import new_frame
    from decision import DecisionEngine
    from face_landmark import make_landmarker
    from fatigue_coupling import CoupledRiskModel
    from fatigue_db import FatigueDB, load_reference
    from quality import QualityScorer
    from publish import (FramePoster, PostError, PreviewPoster, SidecarPoster,
                         VideoPusher, age_url_from_ingest, frame_url_from_ingest,
                         full_url, preview_url)
    from storage import MetricsStorage, write_snapshot
    from vital import GreenRppg, forehead_roi, q15_from_roi_sum, roi_channel_sum

# 收尾帧（契约 §2 第 6 种状态）的固定话术，与 mock.py 的 _ADVICE["done"] 一致
DONE_ADVICE = "测量完成"
EXIT_POST_FAILED = 3        # 推送失败：不属于"代码崩了"，但必须显式非零退出


def build_frame(
    *,
    frame_id: int,
    ts: float,
    obs: Any,
    behavior: dict,
    quality: dict,
    engine: DecisionEngine,
    vital_input: dict | None = None,
) -> tuple[dict, dict]:
    """把各模块输出装配成一帧契约 JSON。返回 (frame, decision)。"""
    beh_public = BehaviorTracker.strip_private(behavior)
    q_public = QualityScorer.strip_private(quality)
    face = {
        "visible": round(float(obs.visible), 3),
        "bbox": [int(v) for v in obs.bbox],
        "pose": obs.pose,
    }

    # 心率：由 rPPG 估计器给出（P3）；**必须过 gate_vitals 这道门**才允许出现在契约里。
    # 质量不够、窗口没填满、频谱里没有真峰 —— 任一不满足都会让 vital.* 变回 null。
    # 呼吸率目前恒为 null：契约 §3.5 冻结"63 阶 @45 fps 做不了呼吸带"，需要另一组系数。
    vital = engine.gate_vitals(q_public["overall"], vital_input)

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


def encode_preview_jpeg(image: Any, quality: int = 80) -> bytes | None:
    """真实 ndarray → JPEG；SyntheticImage 等非图像源明确返回 None。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
        return None
    ok, encoded = cv2.imencode(
        ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, int(quality)))]
    )
    return encoded.tobytes() if ok else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VigiLens A 线：视频回放 → 契约 JSON / CSV")
    ap.add_argument("--source", default="synthetic",
                    help="视频文件路径 / 摄像头序号 / synthetic（默认，不需要任何依赖）"
                         " / mjpeg:URL（网页旁路画面，如 mjpeg:http://127.0.0.1:8031/video.mjpg）")
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
    ap.add_argument("--post", default=None,
                    help="M2：把每帧 POST 到 B 线 /api/ingest（如 http://127.0.0.1:8000/api/ingest）；"
                         "写 auto 等价于 http://127.0.0.1:8000/api/ingest")
    ap.add_argument("--post-hz", type=float, default=None,
                    help="推送节奏，默认取 config.yaml 的 ws_push_hz（契约 1 帧/秒，按逻辑时间）")
    ap.add_argument("--post-retries", type=int, default=2, help="连接失败的重试次数（默认 2）")
    ap.add_argument("--preview-post", default=None,
                    help="同源 JPEG 预览旁路（如 http://127.0.0.1:8000/api/preview）；"
                         "写 auto 等价于本机默认地址")
    ap.add_argument("--preview-hz", type=float, default=12.0,
                    help="同源预览发送帧率（默认 12 fps，独立于指标 1 Hz）")
    ap.add_argument("--preview-jpeg-quality", type=int, default=80,
                    help="同源预览 JPEG 质量 1~100（默认 80）")
    ap.add_argument("--realtime", action="store_true",
                    help="文件/合成回放按逻辑帧率节奏播放；摄像头本身已按自然采集节奏运行")
    ap.add_argument("--no-done", action="store_true",
                    help="不发收尾帧（status=done）。默认会发：M2 要求六态都能在网页上出现")
    ap.add_argument("--push-video", action="store_true",
                    help="M3：同时把当前画面作为 **旁路 JPEG** 推到 B 线 /api/frame，"
                         "网页上就能看到真实画面（而不再只是占位网格）。"
                         "必须与 --post 一起用（地址从 --post 推导）。"
                         "⚠️ 旁路画面失败**不改变退出码**，只计数并进 summary —— "
                         "画面是给人看的，指标才是测量结果")
    ap.add_argument("--video-hz", type=float, default=None,
                    help="旁路画面推送节奏，默认取 config.yaml 的 video_push_hz（8 Hz）")
    ap.add_argument("--video-quality", type=int, default=None,
                    help="旁路 JPEG 质量 1..100，默认取 config.yaml 的 video_jpeg_quality（80）")
    ap.add_argument("--video-max-width", type=int, default=None,
                    help="旁路画面最长边（等比缩放），默认取 config.yaml 的 video_max_width（640）")
    ap.add_argument("--frame-id-offset", type=int, default=0,
                    help="帧号起点偏移（M2 连跑多段时必须用：契约要求 frame_id/ts 跨帧单调递增，"
                         "第二段从 0 重新开始会让 B 线趋势曲线的 frame_id 回退）")
    # ---- 年龄分层疲劳对照（旁路功能：**不进冻结的 9 字段契约帧**）----------------
    ap.add_argument("--age", action="store_true",
                    help="启用年龄档案旁路：图像年龄推断（1 Hz 节流）+ 人脸收录判断 + "
                         "同龄基线比对。产物写 --age-jsonl / --age-json，可选 --db 落库。"
                         "缺省不加此开关时，本脚本行为与从前一致。")
    ap.add_argument("--age-confirm", type=int, default=None, metavar="N",
                    help="非交互地正式确认年龄（6~100）。确认后按用户要求**停用图像推测**。"
                         "交互式确认走 B 线网页的 POST /api/age。"
                         "命令行确认发生在逻辑时间 0 点，保证同一段回放可复现。")
    ap.add_argument("--age-jsonl", default=None,
                    help="年龄档案件逐帧追加（如 metrics/logs/age.jsonl）。"
                         "**不是**契约帧，绝不写进 stream.jsonl")
    ap.add_argument("--age-json", default=None,
                    help="最新一份年龄档案件快照（如 metrics/logs/age.json）")
    ap.add_argument("--db", dest="db_path", default=None,
                    help="把本次测量写入年龄分层疲劳数据库（SQLite）。"
                         "不给则只在内存里算比对、不落库")
    args = ap.parse_args(argv)

    cfg = load_config()
    fps = float(args.fps if args.fps is not None else cfg["fps_nominal"])
    width = int(args.width if args.width is not None else cfg["fpga"]["img_width"])
    height = int(args.height if args.height is not None else cfg["fpga"]["img_height"])
    post_hz = float(cfg["ws_push_hz"] if args.post_hz is None else args.post_hz)
    fid_offset = int(args.frame_id_offset)

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

    # 合成帧源不是图像（只有 shape 与一个亮度值），真 MediaPipe 在上面看不到任何人脸：
    # 硬跑只会白跑一遍并抛 cv2.error。这里沿用项目既有的"链路优先 + 明确告知"做法
    # （与 capture.py 在缺 OpenCV 时回退到合成帧源同一个模式），自动改用 StubLandmarker。
    synthetic_src = desc.startswith("synthetic")
    auto_stub = synthetic_src and not args.stub
    if auto_stub:
        say("[warn] 帧源是合成帧（SyntheticImage，不是图像）—— 已自动改用 StubLandmarker。")
        say("       这是设计如此：--pattern 的眨眼/哈欠演示本来就只对 StubLandmarker 生效。")
        say("       要看真实 MediaPipe 结果，请用真实视频或摄像头：--source <视频文件> 或 --source 0。")

    landmarker = make_landmarker(width=width, height=height, pattern=args.pattern, fps=fps,
                                 force_stub=args.stub or auto_stub)
    lm_source = "stub" if type(landmarker).__name__ == "StubLandmarker" else "mediapipe"
    if lm_source == "stub":
        say("[warn] 本次人脸关键点来自 StubLandmarker —— EAR/MAR 的数值是**占位几何量**，")
        say("       下游状态机与规则是真实代码。报告引用数字时必须标注这一点。")

    tracker = BehaviorTracker(cfg)
    scorer = QualityScorer(cfg)
    engine = DecisionEngine(cfg)
    # rPPG（P3）：输入是**绿通道 ROI 累加**经契约 §4.6 量化后的 Q1.15 序列，
    # 带通复用冻结 FIR，滑窗填满后才可能出数（窗口长度 = config 的 window_seconds）。
    rppg = GreenRppg.from_config(cfg, fps=fps)

    # ---- 年龄分层疲劳对照（旁路；缺省不加 --age 时这一整段都不存在）----------
    # 设计要点见 docs/28：年龄档案件**不进**契约帧（那 9 个字段是冻结的），
    # 只能走 --age-jsonl / --age-json / POST /api/age / SQLite 这四条旁路。
    age_store: AgeProfileStore | None = None
    couple: CoupledRiskModel | None = None
    fatigue_db: FatigueDB | None = None
    session_id: str | None = None
    age_jsonl_fh = None
    age_jsonl_path: Path | None = None
    age_json_path: Path | None = None
    age_posted = 0
    age_compare_counts: Counter[str] = Counter()
    sidecar_poster = None
    if args.age:
        norms = load_reference(cfg=cfg)
        couple = CoupledRiskModel(cfg, norms)
        # 会话号刻意**确定性**：同一段回放重复导入同一会话（INSERT OR REPLACE），
        # 这样"再跑一次会不会多出一条脏数据"这个问题根本不会出现。
        session_id = f"run-{Path(str(args.source)).stem or 'src'}-{fid_offset}"
        if args.db_path:
            fatigue_db = FatigueDB(REPO_ROOT / args.db_path if not Path(args.db_path).is_absolute()
                                   else args.db_path, cfg=cfg)
            fatigue_db.load_reference_into_db(norms)
        age_store = AgeProfileStore(
            cfg,
            on_event=(lambda ev: fatigue_db.log_age_event(ev, session_id=session_id))
            if fatigue_db is not None else None,
            on_subject=(lambda su: fatigue_db.upsert_subject(su)) if fatigue_db is not None else None,
        )
        if args.age_jsonl:
            age_jsonl_path = REPO_ROOT / args.age_jsonl
            age_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            age_jsonl_fh = age_jsonl_path.open("w", encoding="utf-8")
        if args.age_json:
            age_json_path = REPO_ROOT / args.age_json
        say(f"年龄档案: {'启用' if age_store.enabled else '未启用（config.age_enabled=false）'}"
            f"｜引擎 {age_store.engine.name if age_store.engine else 'none'}"
            + ("（占位引擎，无精度证据）" if age_store.engine and age_store.engine.placeholder else "")
            + f"｜参照基线 {norms.version}"
            + (f"｜数据库 {args.db_path}" if fatigue_db is not None else "｜不落库"))
        if age_store.engine is None:
            say("[warn] 没有任何可用的年龄推断引擎（缺模型权重）—— 档案仍可用，"
                "但 estimate 段会是 engine=none，前端显示『本次未做推断』。"
                "缺权重时取权重：python metrics/scripts/fetch_age_model.py")
        if args.age_confirm is not None:
            # 命令行确认：发生在**逻辑时间 0 点**，因此同一段回放跑两次结果一致
            # （交互式确认走 B 线网页，那条路径按墙上时钟记 confirmed_at，本就不可复现）。
            age_store.set_manual(int(args.age_confirm), ts=0.0)
            say(f"年龄确认: 命令行指定 {args.age_confirm} 岁 → 参照组 "
                f"{age_store.profile['band']}，图像推测已停用（locked="
                f"{age_store.profile['locked']}）")
        if fatigue_db is not None:
            fatigue_db.start_session(
                session_id=session_id, started_at=0.0, fps=fps,
                subject=age_store.profile.get("subject"),
                age_years=age_store.profile.get("age_years"),
                age_band=age_store.profile.get("band"),
                age_source=age_store.profile.get("source"),
                confirmed_by=age_store.profile.get("confirmed_by"),
                engine=age_store.stats()["engine"],
                engine_placeholder=age_store.stats()["engine_placeholder"],
                landmark_source=lm_source, frame_source=desc,
                provenance=("stub" if lm_source == "stub" else "live"),
                reference_version=norms.version,
                notes="run_pipeline --age 写入；provenance 由 landmark_source 判定")

    # M2（P5）：跨进程把真实帧推给 B 线。`auto` = 本机默认端口，省得记地址。
    poster: FramePoster | None = None
    if args.post:
        post_url = full_url() if args.post == "auto" else args.post
        poster = FramePoster(post_url, hz=post_hz, retries=args.post_retries)
        say(f"推送   : {post_url}（{post_hz} 帧/秒，按逻辑时间节流；失败即报错，不静默丢帧）")

    preview_poster: PreviewPoster | None = None
    if args.preview_post:
        purl = preview_url() if args.preview_post == "auto" else args.preview_post
        preview_poster = PreviewPoster(purl, hz=args.preview_hz)
        say(f"预览   : {purl}（{args.preview_hz:g} fps，同源 JPEG 旁路；失败不截断测量）")

    # M3：旁路画面。与契约帧走**同一个服务、不同端点**，地址从 --post 推导。
    vpusher: VideoPusher | None = None
    if args.push_video:
        if poster is None:
            print("[FAIL] --push-video 需要同时给 --post（画面地址从 --post 推导）。",
                  file=sys.stderr)
            return 2
        v_hz = float(cfg.get("video_push_hz") if args.video_hz is None else args.video_hz)
        v_q = int(cfg.get("video_jpeg_quality") if args.video_quality is None else args.video_quality)
        v_w = int(cfg.get("video_max_width") if args.video_max_width is None else args.video_max_width)
        vpusher = VideoPusher(frame_url_from_ingest(poster.url), hz=v_hz, quality=v_q, max_width=v_w)
        if synthetic_src:
            # 合成帧源不是图像（SyntheticImage），没有 shape/像素 → 编码必然失败。
            # 明确告知并**关掉**，而不是每帧刷一条错误。
            say("[warn] 帧源是合成帧，没有真实像素 —— 已关闭 --push-video（网页会显示占位画面）。")
            say("       要看真实画面请用真实视频或摄像头：--source <视频文件> 或 --source 0。")
            vpusher = None
        elif not vpusher._ensure_cv2():
            say(f"[warn] {vpusher._encode_failed_reason} —— 已关闭 --push-video。")
            say("       装依赖：pip install -r requirements.txt")
            vpusher = None
        else:
            vpusher.start()
            say(f"旁路画面: {vpusher.url}（{v_hz} Hz，JPEG q{v_q}，最长边 {v_w}）"
                f" ← 网页上会显示真实画面；失败只计数，不影响退出码")

    post_failed = False      # 推送彻底失败过 → 结局非零退出（但仍跑完测量）
    post_stopped = False     # 已停止后续推送（不做逐帧重试）
    preview_stopped = False
    preview_unavailable = False

    # 年龄档案推送：地址从 --post 推导（同一台服务），失败**不改退出码**
    # —— 理由与旁路画面一致：档案是给人看的解释，指标才是测量结果。
    sidecar_poster: SidecarPoster | None = None
    if args.age and poster is not None:
        sidecar_poster = SidecarPoster(age_url_from_ingest(poster.url))
        say(f"档案推送: {sidecar_poster.url}（失败只计数，不影响退出码）")

    def push(frame: dict, *, force: bool = False) -> None:
        """推一帧给 B 线。失败**不打断测量**（见文件头第 4 条）。

        第一次彻底失败就出声并停推，之后每帧只是跳过；结局由退出码 3
        与 summary 里的 `post.errors` 负责说清楚。
        """
        nonlocal post_failed, post_stopped
        if poster is None or post_stopped:
            return
        try:
            if force:
                poster.post(frame)          # 收尾帧不受节流影响
            else:
                poster.maybe_post(frame)
        except PostError as e:
            post_failed = True
            post_stopped = True
            print(f"[FAIL] {e}", file=sys.stderr)
            print(f"[warn] 已停止推送；测量继续跑完并照常落盘，"
                  f"本次运行以退出码 {EXIT_POST_FAILED} 结束。", file=sys.stderr)

    json_out = REPO_ROOT / args.json_out
    jsonl_out = REPO_ROOT / args.jsonl if args.jsonl else None
    csv_out = REPO_ROOT / args.csv if args.csv else None

    status_counter: Counter[str] = Counter()
    t_start = time.perf_counter()
    last_frame: dict | None = None
    n = 0
    replay_started = time.perf_counter()

    with MetricsStorage(jsonl_out, csv_out) as store:
        for frame in frames_iter:
            if args.realtime and not str(args.source).strip().isdigit():
                target = frame.frame_id / fps
                remain = target - (time.perf_counter() - replay_started)
                if remain > 0:
                    time.sleep(remain)
            # 对外的帧号 = 帧源帧号 + 偏移。M2 连跑多段时用它保证 frame_id/ts
            # 跨段单调递增（契约 §1：前端断点重连靠这两个字段对齐）。
            # 帧源自己的 frame_id 仍喂给 landmarker：stub 的眨眼/哈欠节拍按**本段**时间走，
            # 加偏移不该改变这一段里"第几秒眨眼"。
            fid = frame.frame_id + fid_offset
            ts = time.time() if args.wall_clock else fid / fps
            obs = landmarker.detect(frame.image, frame.frame_id)
            beh = tracker.update(fid, ts, obs)
            qua = scorer.update(fid, ts, frame, obs)

            # rPPG 的输入样本：额头 ROI 的**绿通道**累加 → Q1.15（契约 §4.6 的唯一口径）。
            # 空 ROI / 非图像帧 → count==0 → 按契约**丢弃该帧**（不喂样本、不补值）。
            _roi = forehead_roi(obs.bbox, width, height)
            _sum_g, _cnt = roi_channel_sum(frame.image, _roi, channel=1)
            rppg.push(q15_from_roi_sum(_sum_g, _cnt) if _cnt > 0 else None, fid)

            full, dec = build_frame(frame_id=fid, ts=ts, obs=obs,
                                    behavior=beh, quality=qua, engine=engine,
                                    vital_input=rppg.estimate())
            clean = strip_internal(full)
            store.write(clean)
            write_snapshot(json_out, clean)
            push(clean)

            # ---- 年龄档案旁路（契约帧已经落盘/推送完毕，这里只动旁路产物）------
            if age_store is not None and couple is not None:
                # 顺序刻意如此：**先比对**（用本帧的指标）→ 再推进收录窗口 →
                # 再（按 1 Hz 节流）做图像推断。这样 sidecar 里的 comparison 与
                # 同一份契约帧严格同源，不会出现"档案比指标晚一帧"的错位。
                cmp_block = couple.compare(
                    behavior=clean["behavior"], quality=clean["quality"],
                    face=clean["face"], band=age_store.effective_band(),
                    status=clean["status"], frame_id=fid, ts=float(clean["ts"]))
                age_compare_counts[cmp_block["risk_level"]] += 1
                age_store.observe(frame_id=fid, ts=float(clean["ts"]), image=frame.image,
                                  bbox=clean["face"]["bbox"],
                                  quality_overall=float(clean["quality"]["overall"]),
                                  face_visible=float(clean["face"]["visible"]))
                # ⚠️ **必须先问 `inference_allowed()` 再调 `maybe_infer()`**：
                # 年龄一经正式确认就停用图像推测（用户明确要求）。`maybe_infer` 在锁定后
                # 若仍被调用，会**故意**把 inference_calls_after_lock 加一并让 schema 报错
                # —— 那是给第三方调用方准备的绊线，不是给本文件的正常路径用的。
                # （本文件首版就是没问就调，结果 --age-confirm 一开就整段红着结束。）
                if age_store.inference_allowed():
                    age_store.maybe_infer(frame_id=fid, logical_t=fid / fps, image=frame.image,
                                          bbox=clean["face"]["bbox"], ts=float(clean["ts"]))
                sidecar = age_store.sidecar(ts=float(clean["ts"]), frame_id=fid,
                                           comparison=cmp_block)
                errs = validate_age_state(sidecar)
                if errs:
                    # 不静默产出坏数据：直接抛，让这次运行红着结束（与 storage 同纪律）
                    raise ValueError("年龄档案件不符合 age_contract：\n  - " + "\n  - ".join(errs))
                if age_jsonl_fh is not None:
                    age_jsonl_fh.write(age_dumps(sidecar) + "\n")
                if age_json_path is not None:
                    write_snapshot(age_json_path, sidecar)
                if fatigue_db is not None:
                    fatigue_db.add_frame(session_id, clean, commit=False)
                    fatigue_db.add_comparison(session_id, fid, cmp_block)
                    if n % 30 == 0:
                        fatigue_db.conn.commit()
                if sidecar_poster is not None and sidecar_poster.maybe_post(sidecar):
                    age_posted += 1

            if preview_poster is not None and not preview_stopped:
                jpeg = encode_preview_jpeg(frame.image, args.preview_jpeg_quality)
                if jpeg is None:
                    if not preview_unavailable:
                        preview_unavailable = True
                        print("[warn] 当前帧源不是可编码的真实图像；同源预览未发送（指标测量继续）。",
                              file=sys.stderr)
                else:
                    try:
                        sh = tuple(getattr(frame.image, "shape", (height, width)))
                        source_height, source_width = int(sh[0]), int(sh[1])
                        preview_poster.maybe_post(
                            jpeg, frame_id=fid, ts=float(clean["ts"]), bbox=clean["face"]["bbox"],
                            source_width=source_width, source_height=source_height,
                            face_visible=float(clean["face"]["visible"]), status=clean["status"],
                        )
                    except (PostError, ValueError) as e:
                        preview_stopped = True
                        if isinstance(e, ValueError) and preview_poster is not None:
                            preview_poster.errors.append(str(e))
                        print(f"[warn] {e}", file=sys.stderr)
                        print("[warn] 已停止同源预览推送；指标测量、JSON/CSV 与正式 1 Hz 推送继续。",
                              file=sys.stderr)
            if vpusher is not None:
                # 旁路画面：非阻塞投递，编码与网络都在后台线程。这里**不等**它。
                vpusher.offer(frame.image, frame_id=fid)
            status_counter[dec["status"]] += 1
            last_frame = clean
            n += 1
            if args.print_every and n % args.print_every == 0:
                say(f"  t={ts:6.1f}s f{fid:5d} {clean['status']:15s} "
                    f"EAR={clean['behavior']['ear_left']:.3f} PERCLOS={clean['behavior']['perclos']:.3f} "
                    f"Q={clean['quality']['overall']:.2f} | {clean['reason'][:52]}")

        # ---- 收尾帧（契约 §2 第 6 种状态 done）--------------------------
        # 只进 jsonl 流 + 推送，**不进 CSV / 不覆盖 last.json**：CSV 是逐帧测量表
        # （P4 标定与黄金结果比对按行数统计），last.json 是"最新一次测量快照"。
        done_info: dict[str, Any] | None = None
        if not args.no_done and last_frame is not None:
            done = new_frame(
                ts=round(last_frame["ts"] + 1.0 / fps, 3),
                frame_id=int(last_frame["frame_id"]) + 1,
                face=last_frame["face"],
                behavior=last_frame["behavior"],
                vital=engine.gate_vitals(last_frame["quality"]["overall"], last_frame["vital"]),
                quality=last_frame["quality"],
                status="done",
                advice=DONE_ADVICE,
                reason=(f"本次运行正常结束：共处理 {n} 帧（逻辑时长 {n / fps:.1f}s），"
                        f"关键点来源 {lm_source}"),
            )
            store.write_stream_only(done)
            push(done, force=True)
            done_info = {"frame_id": done["frame_id"], "ts": done["ts"], "status": done["status"],
                         "written_to": "jsonl" + ("+post" if poster else "")}

    if last_frame is None:
        if vpusher is not None:
            vpusher.stop()
        print("[FAIL] 没有处理任何帧：帧源是空的。检查 --source 路径 / --limit 是否被设成 0。", file=sys.stderr)
        return 2

    if vpusher is not None:
        vpusher.stop()

    # ---- 年龄档案收尾：落库 + 关闭旁路文件 ------------------------------
    age_summary: dict[str, Any] | None = None
    if age_store is not None:
        if age_jsonl_fh is not None:
            age_jsonl_fh.close()
            age_jsonl_fh = None
        if fatigue_db is not None:
            # 会话可能是在跑的过程中才完成收录 / 才被确认年龄的，因此收尾时把
            # **最终**档案写回会话行（否则库里会留一个"当时没年龄"的会话，
            # 而它其实是有年龄的 —— 那会让按年龄分组统计直接漏掉这一段）。
            fatigue_db.update_session_age(
                session_id, subject=age_store.profile.get("subject"),
                age_years=age_store.profile.get("age_years"),
                age_band=age_store.profile.get("band"),
                age_source=age_store.profile.get("source"),
                confirmed_by=age_store.profile.get("confirmed_by"),
                engine=age_store.stats()["engine"],
                engine_placeholder=age_store.stats()["engine_placeholder"])
            fatigue_db.finish_session(
                session_id, ended_at=float(last_frame["ts"]) if last_frame else 0.0,
                summary={"status_counts": dict(status_counter),
                         "logical_duration_s": round(n / fps, 2)})
            # ⚠️ 取计数必须在 close() **之前**（关掉连接后再查会 ProgrammingError，
            # 本文件首版就是踩了这个，摘要直接崩在最后一行）。
            db_counts = fatigue_db.counts()
            fatigue_db.close()
        else:
            db_counts = None
        est = age_store.estimate
        age_summary = {
            "enabled": age_store.enabled,
            "engine": age_store.stats()["engine"],
            "engine_placeholder": age_store.stats()["engine_placeholder"],
            "profile": dict(age_store.profile),
            "enrollment_state": age_store.enrollment.get("state"),
            "enrollment_samples": age_store.enrollment.get("samples"),
            "enrollment_reason": age_store.enrollment.get("reason"),
            "estimate": (est.to_estimate_block() if est is not None else None),
            "inference_calls": age_store.inference_calls,
            "inference_refused_locked": age_store.inference_refused_locked,
            "risk_level_counts": dict(age_compare_counts),
            "db": ({"path": str(args.db_path), "session_id": session_id,
                    "counts": db_counts} if args.db_path else None),
            "sidecar_post": sidecar_poster.stats() if sidecar_poster is not None else None,
            "outputs": {"jsonl": str(age_jsonl_path) if age_jsonl_path else None,
                        "json": str(age_json_path) if age_json_path else None},
            "note": ("年龄推断只是建议，未经用户确认不得用于选择参照组；"
                     "placeholder=true 的引擎（heuristic）无任何精度证据，不得引用其数值。"
                     if age_store.engine and age_store.engine.placeholder else
                     "年龄推断只是建议，未经用户确认不得用于选择参照组。"),
        }

    elapsed = time.perf_counter() - t_start
    hist = last_frame["behavior"]
    summary = {
        "landmark_source": lm_source,
        "frame_source": desc,
        "pattern": args.pattern,
        "frames_processed": n,
        "frame_id_offset": fid_offset,
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
        # M2（P5）：收尾帧与推送统计 —— "推了几帧 / 被节流几帧 / 有没有报错"一眼可见
        "done_frame": done_info,
        "post": ({**poster.stats(), "ok": not post_failed, "stopped_after_failure": post_stopped}
                 if poster is not None else None),
        "preview_post": ({**preview_poster.stats(),
                          "ok": not preview_stopped and not preview_unavailable,
                          "stopped_after_failure": preview_stopped,
                          "image_available": not preview_unavailable}
                         if preview_poster is not None else None),
        # 旁路画面统计：刻意放在独立的键里，**不混进 post** ——
        # post 的 ok 参与退出码判定，画面不参与，两者混在一起会让人读错。
        "video": (vpusher.stats() if vpusher is not None else None),
        # 年龄档案旁路（不进契约帧）：本次用了哪个引擎、档案到哪一步、风险档位分布
        "age": age_summary,
        # rPPG 窗口状态：回答"为什么 vital 是 null"（窗口没满 / 没有真峰 / 质量不够）
        "rppg_window_samples": rppg.samples,
        "rppg_window_need": rppg.need,
        "note": ("landmark_source=stub 时，EAR/MAR 为占位几何量；"
                 "vital.hr_* 为 null 的可能原因：窗口未填满（需 %d 个有效样本）、"
                 "频谱中没有占主导的峰、或质量低于门控阈值 %s。"
                 "vital.rr_* 恒为 null：契约 §3.5 冻结 63 阶 @45 fps 做不了呼吸带。"
                 % (rppg.need, cfg["vital_require_quality"])),
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
    if done_info:
        print(f"收尾帧    : status=done frame_id={done_info['frame_id']} "
              f"→ {done_info['written_to']}（不进 CSV / 不覆盖 last.json）")
    if poster is not None:
        print(f"推送      : 成功 {poster.posted} 帧 / 节流跳过 {poster.throttled} 帧 "
              f"→ {poster.url}（HTTP 次数 {poster.attempts}）"
              + ("  ← **推送失败，已停止推送**" if post_failed else ""))
    if preview_poster is not None:
        print(f"同源预览  : 成功 {preview_poster.posted} 帧 / 节流跳过 {preview_poster.throttled} 帧 "
              f"→ {preview_poster.url}"
              + ("  ← 当前源无真实图像" if preview_unavailable else "")
              + ("  ← 推送失败后已停止" if preview_stopped else ""))
    if vpusher is not None:
        v = vpusher.stats()
        print(f"旁路画面  : 投递 {v['offered']} / 编码 {v['encoded']} / 送达 {v['posted']} "
              f"/ 丢弃 {v['dropped']}  → {v['url']}")
        if v["errors"]:
            print(f"            ⚠️ 画面有 {len(v['errors'])} 类错误（**不影响本次测量**）：{v['errors'][0]}")
    if age_summary is not None:
        prof = age_summary["profile"]
        band_txt = prof.get("band") or "未定（按全人群兜底基线比对）"
        print(f"年龄档案  : 引擎 {age_summary['engine'] or 'none'}"
              + ("  ← 占位引擎，无精度证据" if age_summary["engine_placeholder"] else "")
              + f"｜年龄 {prof.get('age_years')}（{band_txt}）｜已确认 {prof.get('confirmed')}"
              + f"（by {prof.get('confirmed_by')}）｜锁定 {prof.get('locked')}")
        print(f"人脸收录  : {age_summary['enrollment_state']}"
              f"（样本 {age_summary['enrollment_samples']}）—— {age_summary['enrollment_reason']}")
        print(f"同龄比对  : 风险档位分布 " + ("  ".join(f"{k}={v}" for k, v in
              sorted(age_summary["risk_level_counts"].items())) or "（无）")
              + "  ← unknown = 质量门控不过，**不报数字**（正确行为）")
        if age_summary["db"]:
            print(f"数据库    : {age_summary['db']['path']}（会话 {age_summary['db']['session_id']}，"
                  f"{age_summary['db']['counts']}）")
        if age_summary["sidecar_post"]:
            sp = age_summary["sidecar_post"]
            print(f"档案推送  : 成功 {sp['posted']} 次 / HTTP {sp['attempts']} 次 → {sp['url']}"
                  + ("  ← **推送失败，已停止**" if sp["stopped_after_failure"] else ""))

    if args.summary:
        sp = REPO_ROOT / args.summary
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"摘要      : {sp}")
    if store.errors:
        print(f"[warn] 有 {len(store.errors)} 帧被契约校验拦下（未写入）")
    if post_failed:
        print(f"[FAIL] 推送未完成：测量产物已完整落盘，但 B 线没收到全部帧 "
              f"（退出码 {EXIT_POST_FAILED}）。", file=sys.stderr)
        return EXIT_POST_FAILED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
