"""Evaluate recorded human actions against independently reviewed annotations.

No annotations -> no accuracy conclusion. This does not diagnose fatigue or
claim population accuracy from one staged session. Event matching requires
one-to-one interval overlap; frame scores additionally expose overly long hits.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.config import load_config
from backend.contract import validate_frame
from backend.decision import DecisionEngine
from metrics.scripts.human_session import dump, sha256, validate_labels


def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision,
            "recall": recall, "f1": f1}


def mask(rows: list[dict], events: set[str], count: int) -> list[bool]:
    result = [False] * count
    for row in rows:
        if row["event"] in events:
            result[row["start_frame"]:row["end_frame"] + 1] = [True] * (
                row["end_frame"] - row["start_frame"] + 1)
    return result


def match_events(preds, truth) -> dict:
    """Maximum one-to-one overlap matching, independent of annotation order."""
    assigned = {}

    def augment(i, seen):
        for j, (a, b) in enumerate(truth):
            if j in seen or preds[i][1] < a or preds[i][0] > b:
                continue
            seen.add(j)
            if j not in assigned or augment(assigned[j], seen):
                assigned[j] = i
                return True
        return False

    tp = sum(augment(i, set()) for i in range(len(preds)))
    return {**prf(tp, len(preds) - tp, len(truth) - tp),
            "predicted_events": len(preds), "human_events": len(truth),
            "matching": "maximum one-to-one interval overlap; inspect frame scores too"}


def frame_scores(pred, truth, ignored) -> dict:
    pairs = [(p, t) for p, t, skip in zip(pred, truth, ignored) if not skip]
    return {**prf(sum(p and t for p, t in pairs), sum(p and not t for p, t in pairs),
                   sum(t and not p for p, t in pairs)), "evaluated_frames": len(pairs),
            "true_negative_frames": sum(not p and not t for p, t in pairs)}


def predicted_events(frames: list[dict], cfg: dict) -> dict:
    """Recover counted events from production counters, not a second detector."""
    eyes = [f["face"]["visible"] > 0 and
            (f["behavior"]["ear_left"] + f["behavior"]["ear_right"]) / 2 < cfg["ear_close_threshold"]
            for f in frames]
    mouths = [f["face"]["visible"] > 0 and f["behavior"]["mar"] > cfg["mar_threshold"] for f in frames]
    result = {}
    for event, counter, flags in (("blink", "blink_count", eyes),
                                  ("long_close", "long_close_count", eyes),
                                  ("yawn", "yawn_count", mouths)):
        found = []
        previous = 0
        for i, frame in enumerate(frames):
            current = frame["behavior"][counter]
            if current < previous or current - previous > 1:
                raise ValueError("event counter reset or jumped within the recording")
            if current > previous:
                # Eyes are counted on reopening; yawns are counted mid-opening.
                j = i if event == "yawn" else i - 1
                end = max(0, j)
                while j >= 0 and flags[j]:
                    j -= 1
                start = j + 1
                if start > end:
                    start = end = i  # Counter exists but preceding observation was unavailable.
                if event == "yawn":
                    while end + 1 < len(frames) and flags[end + 1]:
                        end += 1
                found.append((start, end))
            previous = current
        result[event] = found
    result["closed_mask"] = eyes
    result["mouth_mask"] = mouths
    return result


def perclos_error(frames: list[dict], labels: list[dict], timestamps: list[float], window: float) -> dict:
    closed = mask(labels, {"blink", "long_close"}, len(frames))
    ignored = mask(labels, {"ignore"}, len(frames))
    q = deque()
    n_closed = n_ignore = 0
    errors, complete_window_errors = [], []
    for i, ts in enumerate(timestamps):
        q.append(i)
        n_closed += closed[i]
        n_ignore += ignored[i]
        while timestamps[q[0]] < ts - window:
            old = q.popleft()
            n_closed -= closed[old]
            n_ignore -= ignored[old]
        if n_ignore:
            continue
        error = abs(float(frames[i]["behavior"]["perclos"]) - n_closed / len(q))
        errors.append(error)
        if ts - timestamps[0] >= window:
            complete_window_errors.append(error)
    return {"definition": "closed-frame fraction in original-timestamp window, inclusive cutoff",
            "evaluated_frames": len(errors), "mean_absolute_error": sum(errors) / len(errors) if errors else None,
            "max_absolute_error": max(errors) if errors else None,
            "full_window_frames": len(complete_window_errors),
            "full_window_mean_absolute_error": (sum(complete_window_errors) / len(complete_window_errors)
                                                  if complete_window_errors else None)}


def evaluate_quick(directory: Path, frames: list[dict], result: dict) -> dict:
    """Counts are human references; planned phase ranges are navigation only."""
    review = json.loads((directory / "quick_human_review.json").read_text(encoding="utf-8"))
    counts_path = directory / "quick_counts.json"
    if (review.get("annotation_source") != "human_segment_counts" or review.get("reviewed_all") is not True
            or not str(review.get("reviewer", "")).strip()
            or review.get("frame_level_ground_truth") is not False
            or review["wire_sha256"] != result["wire_sha256"]
            or review["counts_sha256"] != sha256(counts_path)):
        raise ValueError("coarse human review missing or fingerprints changed")
    rows = json.loads(counts_path.read_text(encoding="utf-8"))
    previous_end = -1
    comparisons, differences = [], []
    keys = {"blink": "blink_count", "long_close": "long_close_count", "yawn": "yawn_count"}
    totals = {event: {"human_known_count": 0, "algorithm_on_known_segments": 0,
                      "known_segments": 0, "unknown_segments": 0} for event in keys}
    for row in rows:
        a, b = row["start_frame"], row["end_frame"]
        if (row.get("confirmed") is not True or type(a) is not int or type(b) is not int
                or a != previous_end + 1 or not a <= b < len(frames)):
            raise ValueError("coarse review must cover nonoverlapping consecutive capture segments")
        previous_end = b
        counts, predicted, delta = row["counts"], {}, {}
        for event, key in keys.items():
            predicted[event] = frames[b]["behavior"][key] - (frames[a - 1]["behavior"][key] if a else 0)
            actual = counts[event]
            if actual is not None and (type(actual) is not int or actual < 0):
                raise ValueError("human counts must be nonnegative integers or null")
            if actual is None:
                delta[event] = None
                totals[event]["unknown_segments"] += 1
            else:
                delta[event] = predicted[event] - actual
                totals[event]["known_segments"] += 1
                totals[event]["human_known_count"] += actual
                totals[event]["algorithm_on_known_segments"] += predicted[event]
                if delta[event]:
                    differences.append({"section": row["id"], "event": event, "difference": delta[event]})
        portion = frames[a:b + 1]
        comparisons.append({"id": row["id"], "title": row["title"], "start_frame": a, "end_frame": b,
            "human_counts": counts, "algorithm_counts": predicted, "algorithm_minus_human": delta,
            "execution": row["execution"], "status_counts": dict(Counter(f["status"] for f in portion)),
            "quality_mean": sum(f["quality"]["overall"] for f in portion) / len(portion),
            "note": "planned phase title is context, not frame-level ground truth"})
    if previous_end != len(frames) - 1:
        raise ValueError("coarse review does not reach the end of the recording")
    for total in totals.values():
        total["algorithm_minus_human_on_known_segments"] = total["algorithm_on_known_segments"] - total["human_known_count"]
    result.update(human_annotation_status="human_segment_counts_reviewed",
        human_counts_sha256=review["counts_sha256"], segment_count_comparisons=comparisons,
        count_totals=totals, count_differences=differences,
        acceptance_verdict="HUMAN_COARSE_COUNT_DIFFERENCES_FOUND" if differences
                           else "COARSE_COUNTS_MATCH_FINE_ACCEPTANCE_PENDING",
        frame_level_accuracy=None, perclos_error=None, full_acceptance_complete=False,
        limitation="Counts do not identify individual TP/FP/FN or closed-eye durations; equal counts can hide offsetting errors.")
    return result


def automatic_checks(frames: list[dict], index: list[dict], cfg: dict) -> dict:
    """Check observable invariants; detector predictions never become labels."""
    import math
    import numpy as np

    engine = DecisionEngine(cfg)
    failures = {name: [] for name in ("counter_monotonicity", "counter_increment_without_face",
        "missing_face_closure_reset", "decision_rule_replay", "vital_gate", "perclos_window_consistency")}
    previous = {name: 0 for name in ("blink_count", "long_close_count", "yawn_count")}
    window = deque()
    definitely_closed = possibly_closed = ambiguous = 0
    ambiguous_frames = 0
    no_face_statuses = Counter()
    # EAR and PERCLOS are serialized to four decimal places by the production
    # tracker. This half-unit is numeric precision, not a detector threshold.
    half_unit = 10 ** -4 / 2
    for i, (frame, item) in enumerate(zip(frames, index)):
        fid, behavior, face, quality = frame["frame_id"], frame["behavior"], frame["face"], frame["quality"]
        visible = face["visible"] > 0
        for name in previous:
            change = behavior[name] - previous[name]
            if change not in (0, 1):
                failures["counter_monotonicity"].append(fid)
            if change and not visible:
                failures["counter_increment_without_face"].append(fid)
            previous[name] = behavior[name]
        if not visible:
            no_face_statuses[frame["status"]] += 1
            if behavior["blink_state"] != "OPEN":
                failures["missing_face_closure_reset"].append(fid)
        decision = engine.decide(frame_id=fid, behavior=behavior, quality=quality, face=face)
        if any(frame[key] != decision[key] for key in ("status", "advice", "reason")):
            failures["decision_rule_replay"].append(fid)
        if not engine.vitals_allowed(quality["overall"], face, frame["status"]):
            if any(value is not None for value in frame["vital"].values()):
                failures["vital_gate"].append(fid)
        mean_ear = (behavior["ear_left"] + behavior["ear_right"]) / 2
        low = int(visible and mean_ear + half_unit < cfg["ear_close_threshold"])
        high = int(visible and mean_ear - half_unit < cfg["ear_close_threshold"])
        uncertain = high - low
        ambiguous_frames += uncertain
        window.append((item["ts"], low, high, uncertain))
        definitely_closed += low
        possibly_closed += high
        ambiguous += uncertain
        while window[0][0] < item["ts"] - cfg["window_seconds"]:
            _, old_low, old_high, old_ambiguous = window.popleft()
            definitely_closed -= old_low
            possibly_closed -= old_high
            ambiguous -= old_ambiguous
        measured = behavior["perclos"]
        expected_low = definitely_closed / len(window) - half_unit
        expected_high = possibly_closed / len(window) + half_unit
        rounding_slack = math.ulp(max(1.0, abs(measured)))
        if not expected_low - rounding_slack <= measured <= expected_high + rounding_slack:
            failures["perclos_window_consistency"].append(fid)
    intervals = np.diff([item["ts"] for item in index])
    timing = None if len(intervals) == 0 else {
        "output_fps": float(len(intervals) / intervals.sum()),
        "interval_p95_ms": float(np.percentile(intervals, 95) * 1000),
        "max_interval_ms": float(intervals.max() * 1000)}
    return {"pass": not any(failures.values()),
            "checks": {key: {"pass": not ids, "violation_count": len(ids),
                             "first_frame_ids": ids[:20]} for key, ids in failures.items()},
            "serialization_ambiguous_ear_frames": ambiguous_frames,
            "missing_face_frames": sum(no_face_statuses.values()),
            "missing_face_status_counts": dict(no_face_statuses), "source_timing": timing,
            "scope": "contract/rule/timestamp/self-consistency only; no independent accuracy ground truth"}


def evaluate(directory: Path, run: Path, *, skip_human_labels: bool = False) -> dict:
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    capture = read(directory / "capture.json")
    replay = read(run / "replay.json")
    summary = read(run / "summary.json")
    if not capture["complete"] or capture["wire_sha256"] != sha256(directory / "private.vlp"):
        raise ValueError("capture fingerprint mismatch")
    if (replay["wire_sha256"] != capture["wire_sha256"] or replay["pipeline_exit"] != 0
            or replay["transport_errors"] or replay.get("inputs_changed_during_run", True)):
        raise ValueError("replay incomplete or inputs changed during run")
    if sha256(run / "config.snapshot.yaml") != replay["config_sha256"]:
        raise ValueError("replay config fingerprint mismatch")
    cfg = load_config(run / "config.snapshot.yaml")
    index = [json.loads(line) for line in (directory / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    if capture["index_sha256"] != sha256(directory / "index.jsonl"):
        raise ValueError("capture frame index changed")
    frames = [json.loads(line) for line in (run / "stream.jsonl").read_text(encoding="utf-8").splitlines()]
    for frame in frames:
        errors = validate_frame(frame)
        if errors:
            raise ValueError("invalid contract frame: " + "; ".join(errors))
    frames = [f for f in frames if f["status"] not in {"done", "disconnected"}]
    if (summary["landmark_source"] != "mediapipe" or not summary["board_source"]["end_received"]
            or len(frames) != capture["frames"] or len(frames) != len(index)
            or summary["frames_processed"] != len(frames)):
        raise ValueError("real MediaPipe / complete matching frame coverage required")
    if any(f["frame_id"] != i or f["ts"] != round(index[i]["ts"], 3) for i, f in enumerate(frames)):
        raise ValueError("frame identities or original source timestamps changed")
    result = {"wire_sha256": capture["wire_sha256"], "config_sha256": replay["config_sha256"],
              "pipeline_jsonl_sha256": sha256(run / "stream.jsonl"), "frames": len(frames),
              "source_span_s": capture["source_span_s"], "landmark_source": "mediapipe",
              "face_visible_frames": sum(f["face"]["visible"] >= cfg["face_visible_min"] for f in frames),
              "status_counts": dict(Counter(f["status"] for f in frames)),
              "final_counts": {k: frames[-1]["behavior"][k] for k in ("blink_count", "long_close_count", "yawn_count")},
              "rppg_reject": summary.get("rppg_reject"), "rppg_conf": summary.get("rppg_conf"),
              "vital_nonnull_hr_frames": sum(f["vital"]["hr_bpm"] is not None for f in frames),
              "hr_reference_error": None, "hr_reference_status": "not_measured_no_synchronous_reference",
              "rr_validation": "not_implemented_in_current_pipeline",
              "human_annotation_status": "missing", "acceptance_verdict": "INCOMPLETE_NO_HUMAN_LABELS",
              "full_acceptance_complete": False,
              "scope": "single-session staged actions; not fatigue diagnosis or population accuracy"}
    if skip_human_labels:
        checks = automatic_checks(frames, index, cfg)
        result.update(human_annotation_status="omitted_by_user", automatic_checks=checks,
            accuracy=None, precision=None, recall=None, perclos_reference_error=None,
            accuracy_validation="NOT_ASSESSED_NO_INDEPENDENT_REFERENCE",
            acceptance_verdict="AUTOMATIC_CHECKS_COMPLETED_WITHOUT_ACCURACY_VALIDATION" if checks["pass"]
                               else "AUTOMATIC_CHECKS_FOUND_ISSUES")
        return result
    review_file = directory / "human_review.json"
    if not review_file.exists():
        if (directory / "quick_human_review.json").exists():
            return evaluate_quick(directory, frames, result)
        return result
    review = read(review_file)
    if (review.get("annotation_source") != "human_manual" or review.get("reviewed_all") is not True
            or not str(review.get("reviewer", "")).strip() or review["wire_sha256"] != capture["wire_sha256"]
            or review["annotations_sha256"] != sha256(directory / "annotations.csv")):
        raise ValueError("independent human review missing or fingerprints changed")
    with (directory / "annotations.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["start_frame"], row["end_frame"] = int(row["start_frame"]), int(row["end_frame"])
    labels = validate_labels(rows, len(frames))
    ignored = mask(labels, {"ignore"}, len(frames))
    predicted = predicted_events(frames, cfg)
    scores = {}
    for event in ("blink", "long_close", "yawn"):
        truth = [(r["start_frame"], r["end_frame"]) for r in labels if r["event"] == event]
        if any(any(ignored[a:b + 1]) for a, b in truth):
            raise ValueError("positive event overlaps an uncertain interval; resolve the annotation first")
        kept = [(a, b) for a, b in predicted[event] if not any(ignored[a:b + 1])]
        scores[event] = {**match_events(kept, truth),
                         "excluded_predictions_touching_ignore": len(predicted[event]) - len(kept),
                         "predicted_intervals": kept, "human_intervals": truth}
    closed_truth = mask(labels, {"blink", "long_close"}, len(frames))
    mouth_truth = mask(labels, {"yawn"}, len(frames))
    eye_frame_scores = frame_scores(predicted["closed_mask"], closed_truth, ignored)
    mouth_frame_scores = frame_scores(predicted["mouth_mask"], mouth_truth, ignored)
    scenario = {}
    for event in ("normal", "talking", "turn", "occluded", "dark", "moving"):
        selected = mask(labels, {event}, len(frames))
        ids = [i for i in range(len(frames)) if selected[i] and not ignored[i]]
        if not ids:
            scenario[event] = {"evaluated_frames": 0, "status": "NOT_COVERED"}
            continue
        q = [frames[i]["quality"]["overall"] for i in ids]
        scenario[event] = {"evaluated_frames": len(ids),
            "quality_mean": sum(q) / len(q),
            "status_counts": dict(Counter(frames[i]["status"] for i in ids)),
            "face_visible_fraction": sum(frames[i]["face"]["visible"] >= cfg["face_visible_min"] for i in ids) / len(ids),
            "quality_usable_fraction": sum(v >= cfg["quality_min_score"] for v in q) / len(q),
            "unreliable_fraction": sum(frames[i]["status"] == "unreliable" for i in ids) / len(ids),
            "adjust_posture_fraction": sum(frames[i]["status"] == "adjust_posture" for i in ids) / len(ids),
            "yawn_counter_events": sum(selected[b] and not ignored[b] for a, b in predicted["yawn"])}
    gate_violations = [f["frame_id"] for f in frames if f["status"] == "unreliable"
                       and any(v is not None for v in f["vital"].values())]
    missing = [e for e in ("blink", "long_close", "yawn", "turn", "occluded", "normal", "talking", "dark", "moving")
               if not any(r["event"] == e for r in labels)]
    event_pass = all(s["human_events"] > 0 and s["fp"] == 0 and s["fn"] == 0 for s in scores.values())
    result.update(human_annotation_status="human_reviewed", annotations_sha256=review["annotations_sha256"],
        event_scores=scores, closed_frame_scores=eye_frame_scores, mouth_frame_scores=mouth_frame_scores,
        perclos=perclos_error(frames, labels, [x["ts"] for x in index], float(cfg["window_seconds"])),
        scenarios=scenario, quality_gate_vital_violations=gate_violations, missing_scenarios=missing,
        action_counter_check_pass=event_pass,
        acceptance_verdict="REVIEWED_ACTION_ERRORS_FOUND" if not event_pass or gate_violations
                           else "REVIEWED_ACTION_COUNTS_MATCH_SCOPE_LIMITED",
        full_acceptance_complete=False,
        remaining_validation="cross-person held-out validation, quantitative pose/quality criteria and synchronous HR reference")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--directory", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--skip-human-labels", action="store_true",
                    help="自动检查模式：不请求标注、不生成准确率，不把算法输出当真值")
    args = ap.parse_args()
    result = evaluate(args.directory, args.run, skip_human_labels=args.skip_human_labels)
    dump(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
