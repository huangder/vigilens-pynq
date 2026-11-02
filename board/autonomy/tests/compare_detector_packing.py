"""Compare same-input packing modes as a development check, never accuracy."""
import argparse
import json
from pathlib import Path


def compare(left, right):
    a = {r["frame_id"]: r for r in left}
    b = {r["frame_id"]: r for r in right}
    count_changes, bbox_changes, coordinates, scores = [], [], [], []
    details = []
    for fid in sorted(a.keys() | b.keys()):
        if fid not in a or fid not in b:
            details.append(dict(frame_id=fid, present_modes=[m for m, r in [(0,a),(1,b)] if fid in r]))
            continue
        boxes_a, boxes_b = a[fid]["boxes"], b[fid]["boxes"]
        if len(boxes_a) != len(boxes_b):
            count_changes.append(fid)
        if [x[:4] for x in boxes_a] != [x[:4] for x in boxes_b]:
            bbox_changes.append(fid)
        # Compare score-ranked outputs; no matching or threshold adjustment.
        for ba, bb in zip(boxes_a, boxes_b):
            coordinates.extend(abs(x-y) for x,y in zip(ba[:4],bb[:4]))
            scores.append(abs(ba[4]-bb[4]))
        if boxes_a != boxes_b:
            details.append(dict(frame_id=fid, packing0=boxes_a, packing1=boxes_b))
    return dict(frames_mode0=len(a), frames_mode1=len(b),
                count_change_frame_ids=count_changes, bbox_change_frame_ids=bbox_changes,
                max_coordinate_difference_px=max(coordinates,default=0),
                max_score_absolute_difference=max(scores,default=0), differences=details)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode0", type=Path)
    p.add_argument("mode1", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    a,b = [json.loads(x.read_text()) for x in [args.mode0,args.mode1]]
    assert a["packing"] == 0 and b["packing"] == 1
    for key in ("parameters", "video_sha256", "model_sha256", "library_sha256"):
        assert a[key] == b[key], f"comparison inputs differ: {key}"
    for report in (a,b):
        for key in ("repeatability_verified", "padded_stride_verified", "refine_black_frame_verified",
                    "black_frame_no_face", "invalid_parameter_rejected"):
            assert report[key], key
    report = dict(kind="packing_same_input_development_comparison_not_accuracy",
                  video_sha256=a["video_sha256"],library_sha256=a["library_sha256"],
                  model_sha256=a["model_sha256"],parameters=a["parameters"],
                  full=compare(a["results"],b["results"]),
                  refine=compare(a["refine_checks"],b["refine_checks"]),
                  limitations=["Host instruction set, not ARM parity or board speed.",
                               "Scores and box differences are not human ground truth.",
                               "Outputs compared by score order; multi-face matching is not implemented."])
    args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
