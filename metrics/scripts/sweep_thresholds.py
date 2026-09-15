"""sweep_thresholds.py —— P4 阈值标定：用人工标注当真值，扫描候选阈值并给出建议值。

为什么需要它：
    `config.yaml` 里 `ear_close_threshold` 等一串阈值都是**占位值**，必须用真实视频标定。
    手动试值既慢又难比较，本脚本把"哪个阈值更好"变成一张可复现的表：
    对每个候选值重放指标序列 → 与标注区间比对 → 给出事件级与帧级的 P/R/F1。

输入：
    --csv  逐帧指标 CSV（由 `metrics/scripts/make_golden.py` 或 run_pipeline --csv 产出）
    --ann  标注 CSV（格式见 data/README.md：`start_frame,end_frame,event,note`）
    --param 要标定的参数（见下表）

支持的参数与判定口径：

    | 参数 | 观测量 | 标注事件 | 判定 |
    |---|---|---|---|
    | `ear_close_threshold` | 双眼 EAR 均值 | `blink` / `long_close` | 低于阈值 = 闭眼 |
    | `mar_threshold`       | MAR | `yawn` | 高于阈值 = 张口 |
    | `pose_yaw_max_deg`    | \\|yaw\\| | `turn` | 高于阈值 = 姿态越界 |
    | `face_visible_min`    | 人脸可见率 | `occluded` | 低于阈值 = 不可见 |

口径说明（**别优化了错的指标**）：
    · **选值用帧级 F1**：阈值本质是"逐帧分类"（这一帧算不算闭眼/张口），帧级 F1 直接衡量它。
    · **事件级用于复核**：把连续的"命中帧"聚成事件与标注一对一匹配，对应真正的验收口径
      （眨眼计数对不对）。但**它单独用会退化** —— 见下面的警告。
    · `ear_close_threshold` 支持 `--min-run`（默认取 config 的 `min_close_frames`）去抖 —— 与
      `behavior_metrics` 的连续帧去抖同义；不去抖会把单帧抖动算成一次眨眼。

    ⚠️ **为什么不能只用事件级选值**（2026-09-15 端到端演练时实测到的）：
        给一段"人脸静止、没有眨眼"的视频配一条假的眨眼标注时，**把阈值调到让整段都判成闭眼**
        会得到一个覆盖全序列的预测段 —— 它既"命中"了那条标注、又没有多余的预测段，
        于是事件级 P/R/F1 = **1.000**。即"全判成闭眼"能拿满分。
        帧级 F1 不会被这样骗过（那种解法的帧精确率极低），所以用它选值。

用法（仓库根）：
    python metrics/scripts/sweep_thresholds.py --self-test        # 内置已知答案，验证工具本身
    python metrics/scripts/sweep_thresholds.py --csv data/golden/blink_metrics.csv \
        --ann data/annotations/blink.csv --param ear_close_threshold
退出码：0 = 扫描完成；1 = 输入缺失或参数不支持。

⚠️ 本脚本只读输入、只打印建议，**不改 config.yaml** —— 选定后由人写进 config.yaml 并注明依据。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]


def _frange(lo: float, hi: float, step: float) -> list[float]:
    out, v, eps = [], lo, step / 100.0
    while v <= hi + eps:
        out.append(round(v, 4))
        v += step
    return out


PARAMS: dict[str, dict] = {
    "ear_close_threshold": dict(metric="ear_mean", events=("blink", "long_close"),
                                direction="below", values=_frange(0.10, 0.35, 0.01),
                                debounce_from_config="min_close_frames"),
    "mar_threshold": dict(metric="mar", events=("yawn",),
                          direction="above", values=_frange(0.30, 0.90, 0.05)),
    "pose_yaw_max_deg": dict(metric="abs_yaw", events=("turn",),
                             direction="above", values=_frange(5.0, 60.0, 5.0)),
    "face_visible_min": dict(metric="face_visible", events=("occluded",),
                             direction="below", values=_frange(0.30, 0.95, 0.05)),
}


def derive_columns(rows: list[dict]) -> list[dict]:
    """补上扫描要用的派生列：ear 均值、|yaw|（人脸丢失时 EAR 记 0，按"未闭合"处理）。"""
    for r in rows:
        try:
            el, er = float(r["ear_left"]), float(r["ear_right"])
            r["ear_mean"] = (el + er) / 2.0 if (el > 0 and er > 0) else 0.0
            r["abs_yaw"] = abs(float(r["yaw"]))
        except (KeyError, ValueError):
            raise SystemExit("[FAIL] 指标里缺少必要列（ear_left / ear_right / yaw）")
    return rows


def load_metrics(path: Path) -> list[dict]:
    """读逐帧指标 CSV，并补上派生列。"""
    with path.open(encoding="utf-8", newline="") as fh:
        return derive_columns(list(csv.DictReader(fh)))


def load_intervals(path: Path, events: tuple[str, ...]) -> list[tuple[int, int]]:
    """读标注里属于 `events` 的区间，返回 [(start, end)]（**闭区间**，与 data/README 的示例一致）。"""
    out: list[tuple[int, int]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    for row in csv.DictReader(lines):
        if (row.get("event") or "").strip() not in events:
            continue
        try:
            a, b = int(row["start_frame"]), int(row["end_frame"])
        except (KeyError, ValueError):
            continue
        out.append((min(a, b), max(a, b)))
    return out


def flag_series(values: list[float], th: float, direction: str, min_run: int = 1) -> list[bool]:
    """按阈值判定每帧是否"命中"，并按 `min_run` 做连续帧去抖（run 短于 min_run 的整段丢掉）。"""
    raw = [(v < th) if direction == "below" else (v > th) for v in values]
    if min_run <= 1:
        return raw
    out = [False] * len(raw)
    i = 0
    while i < len(raw):
        if not raw[i]:
            i += 1
            continue
        j = i
        while j < len(raw) and raw[j]:
            j += 1
        if j - i >= min_run:
            for k in range(i, j):
                out[k] = True
        i = j
    return out


def match(flagged: list[bool], intervals: list[tuple[int, int]]) -> tuple[int, int, int]:
    """事件级匹配：返回 (TP, FP, FN)。

    命中帧的**连续段**算一个预测事件；与任一标注区间有交集即算 TP（一对一，不重复计数）。
    标注区间若没有任何预测段与之相交，算一个 FN。
    """
    preds: list[tuple[int, int]] = []
    i = 0
    while i < len(flagged):
        if not flagged[i]:
            i += 1
            continue
        j = i
        while j < len(flagged) and flagged[j]:
            j += 1
        preds.append((i, j - 1))
        i = j

    used_ann: set[int] = set()
    tp = 0
    for p0, p1 in preds:
        hit = next((k for k, (a0, a1) in enumerate(intervals)
                    if k not in used_ann and not (p1 < a0 or p0 > a1)), None)
        if hit is None:
            continue
        used_ann.add(hit)
        tp += 1
    fp = len(preds) - tp
    fn = len(intervals) - len(used_ann)
    return tp, fp, fn


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def frame_prf(flagged: list[bool], intervals: list[tuple[int, int]]) -> tuple[float, float, float]:
    truth = [False] * len(flagged)
    for a0, a1 in intervals:
        for k in range(max(0, a0), min(len(truth) - 1, a1) + 1):
            truth[k] = True
    tp = sum(1 for f, t in zip(flagged, truth) if f and t)
    fp = sum(1 for f, t in zip(flagged, truth) if f and not t)
    fn = sum(1 for f, t in zip(flagged, truth) if (not f) and t)
    return _prf(tp, fp, fn)


def separation_margin(values: list[float], intervals: list[tuple[int, int]],
                      direction: str) -> float | None:
    """标注区间内外的**可分性裕度**：>0 表示存在能把两者分开的阈值，≤0 表示完全分不开。

    这条判据**不需要任何阈值参数**，纯粹是"数据里有没有可分的信号"：
      · 方向"低于"：需要 `min(区间外) > max(区间内)`，裕度 = 两者之差；
      · 方向"高于"：需要 `min(区间内) > max(区间外)`。

    为什么必须有它：若两边取值完全重叠，**任何阈值都一样没用**，
    此时工具若还煞有介事地给一个"最佳值"，就是在骗人（实测见文件头那条警告）。
    """
    inside, outside = [], []
    for i, v in enumerate(values):
        (inside if any(a <= i <= b for a, b in intervals) else outside).append(v)
    if not inside or not outside:
        return None
    return (min(outside) - max(inside)) if direction == "below" else (min(inside) - max(outside))


def sweep(rows: list[dict], intervals: list[tuple[int, int]], param: str,
          min_run: int = 1, quiet: bool = False) -> tuple[list[dict], dict]:
    """对候选值逐个评估，返回 (全部结果, 最佳结果)。"""
    spec = PARAMS[param]
    vals = [float(r[spec["metric"]]) for r in rows]
    results: list[dict] = []
    for th in spec["values"]:
        flagged = flag_series(vals, th, spec["direction"], min_run)
        tp, fp, fn = match(flagged, intervals)
        ep, er, ef = _prf(tp, fp, fn)
        fp_, fr_, ff_ = frame_prf(flagged, intervals)
        results.append(dict(th=th, tp=tp, fp=fp, fn=fn, ep=ep, er=er, ef=ef, ff=ff_,
                            flag_frac=(sum(flagged) / len(flagged)) if flagged else 0.0))

    # ⚠️ 用**帧级 F1** 选值，事件级只做并列时的参考：见文件头"为什么不能只用事件级选值"。
    best = max(results, key=lambda r: (r["ff"], r["ef"]))
    if not quiet:
        print(f"  阈值        帧F1    事件F1  事件P   事件R   判为事件%   TP/FP/FN")
        for r in results:
            star = " ★" if r is best else "  "
            print(f"  {r['th']:<10.2f}  {r['ff']:.3f}   {r['ef']:.3f}   {r['ep']:.3f}   "
                  f"{r['er']:.3f}   {r['flag_frac'] * 100:6.1f}%   "
                  f"{r['tp']}/{r['fp']}/{r['fn']}{star}")
    return results, best


def self_test() -> int:
    """内置已知答案：闭眼帧 EAR≈0.15、睁眼≈0.30，标注区间已知。

    一个好的扫描器应当选出一个落在 (0.15, 0.30) 之间、且事件级 P=R=F1=1 的阈值。
    """
    print("=== self-test：用已知答案验证扫描器本身 ===")
    rows: list[dict] = []
    intervals = [(100, 104), (300, 303), (500, 507)]      # 三段"眨眼"
    for i in range(600):
        closed = any(a <= i <= b for a, b in intervals)
        ear = 0.15 if closed else 0.30
        rows.append({"frame_id": i, "ear_left": ear, "ear_right": ear,
                     "yaw": 0.0, "mar": 0.1, "face_visible": 1.0})
    derive_columns(rows)
    _, best = sweep(rows, intervals, "ear_close_threshold", min_run=1)
    lo, hi = 0.15, 0.30
    ok = (lo < best["th"] < hi) and best["ef"] == 1.0 and best["tp"] == len(intervals)
    print(f"\n  选出的阈值 = {best['th']:.2f}（应落在 {lo}~{hi} 之间）")
    print(f"  事件级 F1 = {best['ef']:.3f}（应为 1.000）, TP = {best['tp']}（应为 {len(intervals)}）")

    # 反向验证：把阈值扫到极端应当明显变差，否则说明比对逻辑没生效
    worst = min(PARAMS["ear_close_threshold"]["values"])
    flagged = flag_series([float(r["ear_mean"]) for r in rows], worst, "below", 1)
    tp, fp, fn = match(flagged, intervals)
    print(f"  反例：阈值 {worst:.2f}（过低）-> TP/FP/FN = {tp}/{fp}/{fn}（应漏检，FN>0）")
    ok = ok and fn > 0

    # ---- 退化数据：可分性判据必须拦住它 -------------------------------------
    # 实测来源：2026-09-15 端到端演练，grace_hopper 静止人脸整段 EAR 恒为 0.1961，
    # 配一条"这里眨过眼"的假标注。此时把阈值调高会让整段都判成闭眼，
    # 得到一个覆盖全序列的预测段 —— 事件级 P/R/F1 反而全是 1.000。
    # 帧级选值能避免选中它，但真正该做的是**判定"这组数据不可分"并拒绝给值**。
    flat = [0.1961] * 600
    m_flat = separation_margin(flat, intervals, "below")
    print(f"  退化数据（EAR 恒 0.1961）可分性裕度 = {m_flat:.4f}（应 <= 0 -> 工具拒绝给建议值）")
    ok = ok and (m_flat is not None and m_flat <= 0)

    m_ok = separation_margin([float(r["ear_mean"]) for r in rows], intervals, "below")
    print(f"  正常数据可分性裕度 = {m_ok:.4f}（应 > 0 -> 可以扫描）")
    ok = ok and m_ok > 0

    print("\n" + ("[PASS] 扫描器自检通过" if ok else "[FAIL] 扫描器自检未通过") )
    return 0 if ok else 1


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="P4 阈值扫描（用标注当真值）")
    ap.add_argument("--csv")
    ap.add_argument("--ann")
    ap.add_argument("--param", choices=sorted(PARAMS), default="ear_close_threshold")
    ap.add_argument("--min-run", type=int, default=None,
                    help="连续帧去抖；默认对 EAR 取 config 的 min_close_frames，其余为 1")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not args.csv or not args.ann:
        print("[FAIL] 需要 --csv 与 --ann（或加 --self-test 验证工具本身）")
        return 1
    csv_path, ann_path = REPO_ROOT / args.csv, REPO_ROOT / args.ann
    for p in (csv_path, ann_path):
        if not p.exists():
            print(f"[FAIL] 找不到文件：{p}")
            return 1

    min_run = args.min_run
    if min_run is None:
        key = PARAMS[args.param].get("debounce_from_config")
        if key:
            sys.path.insert(0, str(REPO_ROOT / "backend"))
            from config import load_config
            min_run = int(load_config()[key])
        else:
            min_run = 1

    rows = load_metrics(csv_path)
    intervals = load_intervals(ann_path, PARAMS[args.param]["events"])

    print(f"=== P4 阈值扫描：{args.param} ===")
    print(f"指标 CSV : {csv_path.relative_to(REPO_ROOT)}（{len(rows)} 帧）")
    print(f"标注 CSV : {ann_path.relative_to(REPO_ROOT)}"
          f"（{len(intervals)} 个 {PARAMS[args.param]['events']} 区间）")
    print(f"去抖 min_run = {min_run}（连续帧少于它的整段丢弃）")
    print()
    if not intervals:
        print("[FAIL] 标注里没有与本次扫描匹配的事件 —— 先补标注，别拿空真值去优化。")
        return 1

    vals = [float(r[PARAMS[args.param]["metric"]]) for r in rows]
    margin = separation_margin(vals, intervals, PARAMS[args.param]["direction"])
    if margin is not None and margin <= 0:
        print(f"[FAIL] 标注区间内外的取值完全重叠（可分性裕度 {margin:.4f} ≤ 0）——")
        print("       这意味着**任何阈值都分不开**这两组帧，扫描结果没有意义。")
        print("       常见原因：① 这段视频里其实没有该事件（标注写错/写错视频）；")
        print("                 ② 上游指标没跑对（例如人脸根本没检出、EAR 恒为 0）。")
        print("       先核对素材与标注，再回来扫。**这里不给建议值。**")
        return 1
    if margin is not None:
        print(f"可分性裕度 = {margin:.4f}（>0 才说明存在可分开两者的阈值）")
    print()

    _results, best = sweep(rows, intervals, args.param, min_run)
    print()
    print(f"建议值：**{args.param} = {best['th']:g}**"
          f"（事件级 F1 {best['ef']:.3f}，TP/FP/FN = {best['tp']}/{best['fp']}/{best['fn']}）")
    if best["flag_frac"] > 0.5:
        print(f"⚠️ 注意：该阈值会把 **{best['flag_frac'] * 100:.1f}%** 的帧都判成事件。"
              "事件通常是稀疏的，占比过高往往说明阈值选偏了或数据有问题，请人工复核。")
    print(f"当前 config.yaml 的值见 `python backend/config.py`；选定后请改 config.yaml 并注明依据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
