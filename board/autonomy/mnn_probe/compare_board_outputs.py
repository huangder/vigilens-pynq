"""Extract the ARM MNN probe outputs from recorded serial logs and compare them.

WHY THIS FILE EXISTS
--------------------
`docs/54` P0 asks for the ARM-side MNN outputs (original and pruned Peppa) to be
compared against the host engines, *with hashes bound to the frozen assets*.
The numbers were already on disk inside the captured serial transcripts, but
nothing in the repository could turn a `VIGI_FILE_BEGIN ... VIGI_FILE_END`
gzip/base64 block into bytes, so the only published comparison was the PC one.

WHAT THIS DOES NOT PROVE
------------------------
This is an offline re-reading of logs that were produced by a board run. It
cannot authenticate those logs, cannot re-run the board, and says nothing about
accuracy, camera input, or the 30 fps budget. It only answers: *given these
logged bytes, how far are they from each host engine's output for the same
frozen input?*

Usage (repository root):

    python board/autonomy/mnn_probe/compare_board_outputs.py \
        --log metrics/evidence/2026-10-31_autonomy_handoff_mnn_original.log \
        --log metrics/evidence/2026-10-31_autonomy_handoff_mnn_pruned.log \
        --out metrics/evidence/2026-10-31_mnn_board_output_comparison.json
"""
from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hashlib
import json
import math
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

ELEMENTS = 143
OUTPUT_BYTES = ELEMENTS * 4
MAX_UNCOMPRESSED = 1 << 20              # 1 MiB, as required by the handoff
WHITELIST = {
    "peppa_mnn_t2.output0.f32": "original",
    "peppa_mnn_pruned_t2.output0.f32": "pruned",
}
# Frozen assets the ARM runs claim to have used (docs/54 §3.3 and §4).
FROZEN = {
    "original": {"model_sha256": "2a22689a611d4d4b17da3f5e6824508d2e9ea0d08b197ce1fdc30836b8a683fc",
                 "probe_sha256": "3d42f95f68c13c0599e1547a919bd404a62983454ec5f4d735aa27f221790ad9"},
    "pruned": {"model_sha256": "be908e37a2dba5fb1b43fcf242f54db5290800f999ff18c2406432726f19b138",
               "probe_sha256": "3d42f95f68c13c0599e1547a919bd404a62983454ec5f4d735aa27f221790ad9"},
}
INPUT_SHA256 = "1eebed2a407647faa48624fb9b0756b6afd1867bacbf9499643dc763fa7a47dd"
INPUT_PATH = ".tmp/autonomy/candidates/peppa_input_synthetic.f32"

# Host references. A missing file is recorded as absent, never silently skipped
# into a "passed" verdict.
REFERENCES = {
    "original": [
        ("mnn_host", ".tmp/autonomy/mnn/peppa_host.output0.f32"),
        ("onnxruntime_host", ".tmp/autonomy/candidates/peppa_output_onnxruntime.f32"),
        ("opencv_dnn_host", ".tmp/autonomy/candidates/peppa_output_opencv.f32"),
    ],
    "pruned": [
        ("mnn_host", ".tmp/autonomy/mnn/pruned/peppa_pruned_mnn_host.output0.f32"),
        ("onnxruntime_host", ".tmp/autonomy/mnn/pruned/peppa_pruned_ort.output0.f32"),
        ("ncnn_host", ".tmp/autonomy/mnn/pruned/peppa_pruned_ncnn.output0.f32"),
    ],
}

BLOCK_RE = re.compile(r"^VIGI_FILE_BEGIN (\S+)\n([\s\S]*?)^VIGI_FILE_END$", re.MULTILINE)


class BlockError(ValueError):
    """A logged file block is malformed, oversized, or not on the whitelist."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def decode_block(encoded: str) -> bytes:
    """Strictly decode one gzip+base64 payload with a hard 1 MiB ceiling."""
    compact = "".join(encoded.split())
    try:
        packed = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as error:
        raise BlockError(f"invalid base64: {error}") from error
    try:
        with gzip.GzipFile(fileobj=_BytesReader(packed)) as stream:
            data = stream.read(MAX_UNCOMPRESSED + 1)
    except (OSError, EOFError) as error:
        raise BlockError(f"invalid gzip stream: {error}") from error
    if len(data) > MAX_UNCOMPRESSED:
        raise BlockError(f"decompressed payload exceeds {MAX_UNCOMPRESSED} bytes")
    return data


class _BytesReader:
    """Minimal file-like object so gzip decompression cannot touch the disk."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._raw) - self._pos
        chunk = self._raw[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


def extract_blocks(text: str) -> dict[str, bytes]:
    """Return {whitelisted name: bytes}; reject duplicates and unknown names."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    found: dict[str, bytes] = {}
    for match in BLOCK_RE.finditer(text):
        name, encoded = match.group(1), match.group(2)
        if name not in WHITELIST:
            raise BlockError(f"file block not on whitelist: {name}")
        if name in found:
            raise BlockError(f"duplicate file block: {name}")
        data = decode_block(encoded)
        if len(data) != OUTPUT_BYTES:
            raise BlockError(f"{name}: expected {OUTPUT_BYTES} bytes, got {len(data)}")
        found[name] = data
    if not found:
        raise BlockError("no VIGI_FILE block found")
    return found


def probe_records(text: str) -> list[dict]:
    """Every probe JSON line in the transcript, validated for what it claims."""
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("kind") != "mnn_model_tensor_probe":
            continue
        _validate_probe(row)
        records.append(row)
    if not records:
        raise BlockError("no mnn_model_tensor_probe JSON record found")
    return records


def _validate_probe(row: dict) -> None:
    if row.get("backend") != "CPU" or row.get("precision") != "Precision_High":
        raise BlockError("probe is not CPU/Precision_High")
    if row.get("fp16") is not False or row.get("int8") is not False:
        raise BlockError("probe must be FP32 without quantisation")
    if row.get("input_shape") != [1, 3, 160, 160] or row.get("input_layout") != "NCHW":
        raise BlockError("probe input shape/layout mismatch")
    outputs = row.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise BlockError("probe must report exactly one output tensor")
    tensor = outputs[0]
    if (tensor.get("elements") != ELEMENTS or tensor.get("host_element_count") != ELEMENTS
            or tensor.get("storage") != "little_endian_float32"):
        raise BlockError("probe output tensor metadata mismatch")
    if row.get("iterations") != 300:
        raise BlockError("unexpected iteration count in probe JSON")
    for key in ("mean_ms", "p50_ms", "p95_ms"):
        value = row.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise BlockError(f"invalid {key} in probe JSON")
    if row["p50_ms"] > row["p95_ms"]:
        raise BlockError("probe percentiles are inconsistent")
    for key in ("accuracy_verified", "full_pipeline_realtime_verified",
                "input_provenance_verified", "channel_order_verified",
                "landmark_pipeline_verified"):
        if row.get(key) is not False:
            raise BlockError(f"probe claims {key} without evidence")


def floats(raw: bytes) -> tuple[float, ...]:
    if len(raw) % 4:
        raise BlockError("float payload is not a multiple of 4 bytes")
    values = struct.unpack(f"<{len(raw) // 4}f", raw)
    if not all(math.isfinite(value) for value in values):
        raise BlockError("non-finite value in tensor payload")
    return values


def compare(board: tuple[float, ...], reference: tuple[float, ...]) -> dict:
    if len(board) != len(reference):
        raise BlockError(f"element count mismatch: {len(board)} vs {len(reference)}")
    diffs = [abs(a - b) for a, b in zip(board, reference)]
    return {"elements": len(diffs),
            "max_abs_difference": max(diffs),
            "mean_abs_difference": sum(diffs) / len(diffs),
            "max_first136_abs_difference": max(diffs[:136])}


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def analyse(log_paths: list[Path], references: dict | None = None) -> dict:
    if len(log_paths) != 2:
        raise BlockError("expected exactly the original and pruned transcripts")
    expected_names = set(WHITELIST)
    collected: dict[str, dict] = {}
    for path in log_paths:
        text = path.read_text(encoding="utf-8")
        for name, raw in extract_blocks(text).items():
            if name in collected:
                raise BlockError(f"{name} appears in more than one transcript")
            kind = WHITELIST[name]
            records = probe_records(text)
            threads = sorted(row["threads_requested"] for row in records)
            if threads != [1, 2]:
                raise BlockError(f"{name}: expected thread runs 1 and 2, got {threads}")
            timing = next(row for row in records if row["threads_requested"] == 2)
            model_sha = FROZEN[kind]["model_sha256"]
            probe_sha = FROZEN[kind]["probe_sha256"]
            if model_sha not in text or probe_sha not in text:
                raise BlockError(f"{name}: transcript does not bind the frozen model/probe hash")
            if INPUT_SHA256 not in text:
                raise BlockError(f"{name}: transcript does not bind the frozen input hash")
            collected[name] = {"kind": kind, "log": _relative(path),
                               "log_sha256": sha256(path.read_bytes()),
                               "bytes": len(raw), "sha256": sha256(raw),
                               "values": floats(raw), "timing_threads_2": timing}
    missing = expected_names - set(collected)
    if missing:
        raise BlockError(f"missing transcript block(s): {sorted(missing)}")

    report: dict = {
        "kind": "mnn_board_output_comparison",
        "source": "logged ARM serial transcripts (offline re-reading, not a new board run)",
        "board_rerun": False,
        "input": {"path": INPUT_PATH, "sha256": INPUT_SHA256},
        "board_outputs": {},
        "references": {},
        "comparisons": {},
        "new_tolerance_introduced": False,
        "accuracy_verified": False,
        "full_pipeline_realtime_verified": False,
    }

    for name, item in collected.items():
        report["board_outputs"][name] = {
            "asset": item["kind"], "log": item["log"], "log_sha256": item["log_sha256"],
            "bytes": item["bytes"], "sha256": item["sha256"],
            "all_finite": True,
            "elements": len(item["values"]),
            "arm_timing_threads_2": {key: item["timing_threads_2"][key]
                                     for key in ("mean_ms", "p50_ms", "p95_ms", "max_rss_kib")},
        }

    for kind, refs in (REFERENCES if references is None else references).items():
        board_name = next(name for name, k in WHITELIST.items() if k == kind)
        board_values = collected[board_name]["values"]
        rows = []
        for engine, relative in refs:
            path = ROOT / relative
            if not path.exists():
                rows.append({"engine": engine, "reference": relative, "present": False})
                continue
            raw = path.read_bytes()
            rows.append({"engine": engine, "reference": relative, "present": True,
                         "sha256": sha256(raw), **compare(board_values, floats(raw))})
        report["references"][kind] = rows
        report["comparisons"][kind] = {row["engine"]: row for row in rows}

    names = list(WHITELIST)
    first, second = (collected[name]["values"] for name in names)
    report["board_original_vs_pruned"] = {
        "byte_identical": collected[names[0]]["sha256"] == collected[names[1]]["sha256"],
        "max_abs_difference": max(abs(a - b) for a, b in zip(first, second)),
    }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, action="append", required=True,
                        help="serial transcript (repeat once for original and pruned)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = analyse([path if path.is_absolute() else ROOT / path for path in args.log])
    except (BlockError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                        encoding="utf-8")
    print(json.dumps({"ok": True, "out": _relative(args.out),
                      "board_sha256": {name: row["sha256"]
                                       for name, row in report["board_outputs"].items()},
                      "mnn_host": {kind: rows.get("mnn_host", {}).get("max_abs_difference")
                                   for kind, rows in report["comparisons"].items()}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
