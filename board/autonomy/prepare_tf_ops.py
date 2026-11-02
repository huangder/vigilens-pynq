"""Extract CPU attribute parsers from MediaPipe's official TensorFlow patch.

The three CPU kernels are compiled directly from the MediaPipe source checkout.
Only whole new headers and verbatim Parse*Attributes function definitions are
copied from third_party/org_tensorflow_custom_ops.diff. No TensorFlow checkout
files are patched and no interpolation/landmark math is reimplemented here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


PREFIX = "tensorflow/lite/delegates/gpu/common/mediapipe/"
OPS = {
    "landmarks_to_transform_matrix": "LandmarksToTransformMatrix",
    "transform_landmarks": "TransformLandmarks",
    "transform_tensor_bilinear": "TransformTensorBilinear",
}


def new_patch_file(patch: str, path: str) -> str:
    """Extract an entire added file; reject existing-file or mixed-line hunks."""
    marker = f"diff --git a/{path} b/{path}\n"
    if patch.count(marker) != 1:
        raise ValueError(f"Expected exactly one patch entry: {path}")
    section = patch.split(marker, 1)[1].split("\ndiff --git ", 1)[0]
    if "--- /dev/null\n" not in section:
        raise ValueError(f"Not a new file: {path}")
    match = re.search(r"^@@ -0,0 \+1,(\d+) @@\n", section, re.MULTILINE)
    if not match:
        raise ValueError(f"Unsupported new-file hunk: {path}")
    lines = section[match.end():].splitlines()
    if len(lines) != int(match.group(1)) or any(not line.startswith("+") for line in lines):
        raise ValueError(f"Incomplete or non-addition hunk: {path}")
    return "\n".join(line[1:] for line in lines) + "\n"


def parser_function(source: str, name: str) -> str:
    """Extract an exact top-level parser definition with no body rewriting.

    Official v0.10.21 parser functions close at column zero. Reject ambiguous
    definitions and leave all inner braces, strings, whitespace and math intact.
    """
    pattern = rf"^absl::Status {re.escape(name)}\([^{{]+\{{\n.*?^\}}"
    matches = re.findall(pattern, source, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError(f"Expected one parser definition: {name}")
    return matches[0] + "\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(mediapipe_source: Path, overlay: Path) -> dict:
    mediapipe_source = mediapipe_source.resolve()
    overlay = overlay.resolve()
    patch_path = mediapipe_source / "third_party/org_tensorflow_custom_ops.diff"
    patch = patch_path.read_text(encoding="utf-8")
    generated = []
    cpu_sources = []
    parsers = []
    # Validate every source before writing any output.
    pending = {}
    for op, function in OPS.items():
        header = new_patch_file(patch, PREFIX + op + ".h")
        source = new_patch_file(patch, PREFIX + op + ".cc")
        pending[overlay / (PREFIX + op + ".h")] = header
        # Keep the original include declarations: this preserves dependencies
        # and avoids inventing replacement definitions for official types.
        includes = "\n".join(line for line in source.splitlines()
                             if line.startswith("#include "))
        functions = [parser_function(source, f"Parse{function}V{v}Attributes")
                     for v in (1, 2)]
        destination = overlay / "cpu_parsers" / (op + ".cc")
        pending[destination] = (
            "// Extracted verbatim parser functions from MediaPipe v0.10.21.\n"
            "// Source: third_party/org_tensorflow_custom_ops.diff (Apache-2.0).\n"
            + includes + "\n\nnamespace tflite {\nnamespace gpu {\n\n"
            + "\n".join(functions) + "\n}  // namespace gpu\n}  // namespace tflite\n"
        )
        parsers.append(destination.as_posix())
        cpu = mediapipe_source / "mediapipe/util/tflite/operations" / (op + ".cc")
        if not cpu.is_file():
            raise ValueError(f"Missing official CPU kernel: {cpu}")
        cpu_sources.append(cpu.as_posix())
    for path, content in pending.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        generated.append({"path": path.as_posix(), "sha256": sha256(path)})
    result = {"patch_path": patch_path.as_posix(), "patch_sha256": sha256(patch_path),
              "mediapipe_source": mediapipe_source.as_posix(),
              "overlay": overlay.as_posix(), "generated_files": generated,
              "cpu_sources": cpu_sources, "parser_sources": parsers,
              "source_hashes": {p: sha256(Path(p)) for p in cpu_sources}}
    (overlay / "ops_manifest.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mediapipe-source", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.mediapipe_source, args.overlay), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
