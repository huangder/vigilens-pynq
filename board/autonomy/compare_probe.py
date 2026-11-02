"""Compare model tensors; retain the target environment and never certify accuracy."""
from __future__ import annotations
import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import sys


def read_outputs(prefix: Path):
    info = json.loads(Path(str(prefix) + ".json").read_text())
    outputs = []
    for slot, description in enumerate(info["outputs"]):
        raw = Path(str(prefix) + f".output{slot}.f32").read_bytes()
        if len(raw) != description["elements"] * 4:
            raise ValueError("Output size differs from metadata")
        values = array("f")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        if any(not math.isfinite(v) for v in values):
            raise ValueError("Nonfinite output")
        outputs.append(values)
    return info, outputs


def compare(host_prefix: Path, arm_prefix: Path, target_environment="arm_qemu"):
    if target_environment not in ("arm_qemu", "cortex_a9_board"):
        raise ValueError("Unsupported target environment")
    host, h = read_outputs(host_prefix)
    arm, a = read_outputs(arm_prefix)
    if not host["outputs"] or host["outputs"] != arm["outputs"]:
        raise ValueError("Host/ARM model output layouts differ or are empty")
    if host["synthetic_input"] != arm["synthetic_input"] or host["input_bytes"] != arm["input_bytes"]:
        raise ValueError("Host/ARM input metadata differs")
    if not host["synthetic_input"]:
        raise ValueError("This comparison requires --zeros inputs; external tensors are not bound by metadata")
    differences = []
    for slot, (hv, av) in enumerate(zip(h, a)):
        delta = [abs(x-y) for x, y in zip(hv, av)]
        differences.append({"slot": slot, "elements": len(hv),
                            "max_abs_difference": max(delta, default=0),
                            "mean_abs_difference": sum(delta) / len(delta) if delta else 0,
                            "identical": all(x == y for x, y in zip(hv, av))})
    return {"validation": f"host_vs_{target_environment}_tensor_execution", "structural_match": True,
            "outputs": differences, "host": host, "target": arm,
            "target_environment": target_environment,
            "hardware_runtime_verified": False, "landmark_pipeline_verified": False,
            "accuracy_verified": False,
            "limitations": ["Raw attention tensors have not been refined/projected into full-frame landmarks.",
                            "Environment labels are caller supplied; use the board transcript as hardware execution evidence.",
                            "No numerical tolerance or accuracy criterion is introduced by this report."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host_prefix", type=Path)
    parser.add_argument("arm_prefix", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--host-executable", type=Path, required=True)
    parser.add_argument("--arm-executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-environment", choices=("arm_qemu", "cortex_a9_board"), default="arm_qemu")
    args = parser.parse_args()
    report = compare(args.host_prefix, args.arm_prefix, args.target_environment)
    report["sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                        (args.model, args.host_executable, args.arm_executable)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"structural_match": report["structural_match"], "outputs": report["outputs"]}))


if __name__ == "__main__":
    main()
