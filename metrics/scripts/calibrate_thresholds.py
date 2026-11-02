r"""阈值标定工具：从真实素材里收特征 → 人工打标 → 反推 `config.yaml` 的建议值。

## 为什么要有这个脚本

`config.yaml` 里 `ear_close_threshold` / `mar_threshold` / `pose_yaw_max_deg` /
`pose_pitch_max_deg` / `light_score_min` / `motion_score_max` 至今是**占位值**。
占位值的麻烦在于"看不出差多远"：EAR 写 0.19、真实分界可能是 0.24，
光读代码永远发现不了。标定必须走三步：**收特征 → 打标 → 拟合**。

## 三步怎么用

```powershell
. .\env.ps1

# 1) 收特征（有真人视频就行；没有 video 也能用摄像头 --source 0）
python metrics/scripts/calibrate_thresholds.py --collect data/raw/blink.mp4 `
    --out metrics/logs/feat_blink.csv --limit 900

# 2) 用 Excel / VSCode 打开 CSV，把 label 列按下面的词表逐行填上：
#    ear:    open | closed
#    mar:    neutral | yawn
#    yaw:    ok | turn        （pitch 同）
#    light:  usable | unusable
#    motion: usable | unusable
#    —— 只填你要标定的那一列有意义的部分，其余行留空即可（会被忽略）。

# 3) 拟合（只打印建议值，**不改 config.yaml**）
python metrics/scripts/calibrate_thresholds.py --fit metrics/logs/feat_blink.csv --what ear
```

## 硬规矩（与《05》铁律 1 一致）

- 本脚本**绝不写 `config.yaml`**：阈值是共享文件，改它要写依据、要公告。
  这里只输出"建议值 + 证据数字（分位数、TPR/TNR、J）"，由人抄进配置并记录来源。
- 输出里的数字只能来自**你喂进去的真实素材**。没有打过标的行会显示
  `skipped`，不会用默认值补。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FEATURE_COLUMNS = [
    "frame_id", "ts", "has_face", "visible",
    "ear_left", "ear_right", "ear_avg", "mar",
    "yaw", "pitch", "roll",
    "light_score", "motion_score", "overall",
    "label",
]

#: `--what` → (特征列, 阳性标签, 阴性标签, "lower_is_positive", config 键名)
SPECS = {
    "ear":    ("ear_avg",     "closed", "open",     True,  "ear_close_threshold"),
    "mar":    ("mar",         "yawn",   "neutral",  False, "mar_threshold"),
    "yaw":    ("abs_yaw",     "turn",   "ok",       False, "pose_yaw_max_deg"),
    "pitch":  ("abs_pitch",   "turn",   "ok",       False, "pose_pitch_max_deg"),
    "light":  ("light_score", "usable", "unusable", False, "light_score_min"),
    "motion": ("motion_score", "unusable", "usable", False, "motion_score_max"),
}


# ---------------------------------------------------------------------------
# 1) 收特征
# ---------------------------------------------------------------------------

def collect(source: str, out: str, limit: int | None) -> int:
    """逐帧跑 `landmark + quality`，把**原料特征**写到 CSV（label 列留空）。"""
    from backend.capture import open_frame_source
    from backend.config import load_config
    from backend.face_landmark import make_landmarker
    from backend.quality import QualityScorer
    from backend.behavior_metrics import BehaviorTracker

    cfg = load_config()
    w_img, h_img = int(cfg["fpga"]["img_width"]), int(cfg["fpga"]["img_height"])
    frames, desc = open_frame_source(source, width=w_img, height=h_img,
                                     fps=float(cfg["fps_nominal"]), limit=limit)
    print(f"[collect] source = {desc}")
    mk = make_landmarker(width=w_img, height=h_img)
    q = QualityScorer(cfg)
    bm = BehaviorTracker(cfg)

    n = 0
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FEATURE_COLUMNS)
        w.writeheader()
        for frame in frames:
            obs = mk.detect(frame.image, frame.frame_id)
            beh = bm.update(frame.frame_id, frame.ts, obs)
            qs = q.update(frame.frame_id, frame.ts, frame, obs)
            pose = getattr(obs, "pose", {}) or {}
            row = {
                "frame_id": frame.frame_id,
                "ts": f"{frame.ts:.6f}",
                "has_face": int(bool(getattr(obs, "visible", 0.0) >= 0.5)),
                "visible": getattr(obs, "visible", 0.0),
                "ear_left": beh.get("ear_left"),
                "ear_right": beh.get("ear_right"),
                "ear_avg": round(0.5 * (float(beh.get("ear_left") or 0.0)
                                        + float(beh.get("ear_right") or 0.0)), 4),
                "mar": beh.get("mar"),
                "yaw": pose.get("yaw"),
                "pitch": pose.get("pitch"),
                "roll": pose.get("roll"),
                "light_score": qs.get("light_score"),
                "motion_score": qs.get("motion_score"),
                "overall": qs.get("overall"),
                "label": "",
            }
            w.writerow(row)
            n += 1
            if n % 100 == 0:
                print(f"[collect] {n} frames", flush=True)
    print(f"[collect] done: {n} rows -> {out}")
    print("[collect] 下一步：给 label 列打标（词表见 --help 或模块 docstring），再跑 --fit")
    return n


def from_jsonl(jsonl: str, summary: str, out: str) -> int:
    """Export real MediaPipe contract features with blank labels."""
    from backend.config import load_config
    from backend.contract import validate_frame
    with open(summary, encoding="utf-8") as fh:
        meta = json.load(fh)
    if meta.get("landmark_source") != "mediapipe":
        raise ValueError("summary.landmark_source must be mediapipe")
    expected = meta.get("frames_processed")
    if not isinstance(expected, int) or expected < 0:
        raise ValueError("summary.frames_processed must be a nonnegative integer")
    face_min = float(load_config()["face_visible_min"])
    rows = []
    with open(jsonl, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            frame = json.loads(line)
            errors = validate_frame(frame)
            if errors:
                raise ValueError(f"line {i}: contract validation failed: {'; '.join(errors)}")
            if frame["status"] in ("done", "disconnected"):
                continue
            face, beh, q = frame["face"], frame["behavior"], frame["quality"]
            left, right = beh["ear_left"], beh["ear_right"]
            rows.append({"frame_id": frame["frame_id"], "ts": frame["ts"],
                "has_face": int(face["visible"] >= face_min), "visible": face["visible"],
                "ear_left": left, "ear_right": right,
                "ear_avg": None if left is None or right is None else (float(left)+float(right))/2,
                "mar": beh["mar"], "yaw": face["pose"]["yaw"], "pitch": face["pose"]["pitch"],
                "roll": face["pose"]["roll"], "light_score": q["light_score"],
                "motion_score": q["motion_score"], "overall": q["overall"], "label": ""})
    if len(rows) != expected:
        raise ValueError(f"exported rows {len(rows)} != summary.frames_processed {expected}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FEATURE_COLUMNS)
        writer.writeheader(); writer.writerows(rows)
    print(f"[from-jsonl] {len(rows)} MediaPipe rows -> {out}; labels blank")
    return len(rows)


# ---------------------------------------------------------------------------
# 2) 拟合
# ---------------------------------------------------------------------------

def _to_float(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fit(path: str, what: str) -> int:
    """读 CSV、按 `label` 分组、打印建议阈值与证据数字（不写任何文件）。"""
    from backend.calibration import fit_threshold

    if what not in SPECS:
        print(f"[fit] 未知的 --what {what!r}；可选：{', '.join(SPECS)}")
        return 2
    col, pos_tag, neg_tag, lower_is_pos, cfg_key = SPECS[what]
    pos_tag, neg_tag = pos_tag.lower(), neg_tag.lower()

    pos: list[float] = []
    neg: list[float] = []
    skipped = 0
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            label = (row.get("label") or "").strip().lower()
            if not label:
                skipped += 1
                continue
            if what in ("yaw", "pitch"):
                val = _to_float(row.get(col.replace("abs_", "")) or "")
                val = abs(val) if val is not None else None
            else:
                val = _to_float(row.get(col) or "")
            if val is None:
                skipped += 1
                continue
            if not math.isfinite(val):
                raise ValueError(f"CSV feature {col} contains NaN/Inf; refusing threshold suggestion")
            if label == pos_tag:
                pos.append(val)
            elif label == neg_tag:
                neg.append(val)
            else:
                skipped += 1

    print(f"[fit] what={what} 特征列={col} 阳性={pos_tag}({len(pos)}) 阴性={neg_tag}({len(neg)}) 跳过={skipped}")
    try:
        res = fit_threshold(pos, neg, lower_is_positive=lower_is_pos)
    except ValueError as exc:
        print(f"[fit] 无法拟合：{exc}")
        return 1

    d = res.as_dict()
    print(f"[fit] 建议 {cfg_key}: {res.threshold}")
    print(f"[fit] Youden J = {res.youden_j}  (TPR {res.tpr}, TNR {res.tnr}, ties {res.ties})")
    print(f"[fit] 阳性分位 p05/p50/p95 = {d['pos_quantiles']}")
    print(f"[fit] 阴性分位 p05/p50/p95 = {d['neg_quantiles']}")
    print("[fit] ⚠️ 这是**该数据集上**的最优分界，不是普适阈值；"
          "抄进 config.yaml 时必须同时写数据来源与日期（《05》铁律 1）。")
    print(json.dumps({"key": cfg_key, "value": res.threshold,
                      "evidence": d}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    from backend.console import enable_utf8_console   # 中文输出不依赖控制台代码页

    enable_utf8_console()
    ap = argparse.ArgumentParser(description="阈值标定：收特征 → 打标 → 拟合")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--collect", metavar="SOURCE", help="帧源（视频路径 / 摄像头序号 / synthetic）")
    g.add_argument("--fit", metavar="CSV", help="已打标（label 列）的特征 CSV")
    g.add_argument("--from-jsonl", metavar="JSONL", help="导出契约 JSONL 特征")
    ap.add_argument("--summary", help="--from-jsonl 对应的 pipeline summary")
    # ⚠️ `--what` 不能放进互斥组：它是 `--fit` 的**修饰项**，不是第三种动作。
    ap.add_argument("--what", help="配合 --fit：ear / mar / yaw / pitch / light / motion")
    ap.add_argument("--out", help="--collect 的输出 CSV")
    ap.add_argument("--limit", type=int, default=None, help="--collect 最多收多少帧")
    args = ap.parse_args(argv)

    if args.collect:
        if not args.out:
            print("--collect 需要同时给 --out <csv>")
            return 2
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        return 0 if collect(args.collect, args.out, args.limit) >= 0 else 1

    if args.from_jsonl:
        if not args.summary or not args.out:
            print("--from-jsonl requires --summary and --out")
            return 2
        try:
            from_jsonl(args.from_jsonl, args.summary, args.out)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(f"[from-jsonl] rejected: {exc}")
            return 1
        return 0

    if not args.what:
        print("--fit 需要同时给 --what ear|mar|yaw|pitch|light|motion")
        return 2
    return fit(args.fit, args.what)


if __name__ == "__main__":
    raise SystemExit(main())
