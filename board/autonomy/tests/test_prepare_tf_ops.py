import hashlib
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "prepare_tf_ops.py"
spec = importlib.util.spec_from_file_location("prepare_tf_ops", SCRIPT)
prepare_ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_ops)


def test_extract_added_file_exactly():
    patch = ("diff --git a/example.h b/example.h\nnew file mode 100644\n"
             "--- /dev/null\n+++ b/example.h\n@@ -0,0 +1,2 @@\n+one\n+two\n")
    assert prepare_ops.new_patch_file(patch, "example.h") == "one\ntwo\n"
    with pytest.raises(ValueError, match="Incomplete"):
        prepare_ops.new_patch_file(patch.replace("+two", " two"), "example.h")
    with pytest.raises(ValueError, match="Not a new file"):
        prepare_ops.new_patch_file(patch.replace("--- /dev/null", "--- a/example.h"), "example.h")


def test_parser_keeps_body_verbatim():
    definition = ("absl::Status ParseFixtureV2Attributes(\n"
                  "    const void* p) {\n"
                  "  if (p) {\n    return absl::OkStatus();\n  }\n"
                  "  return absl::OkStatus();\n}\n")
    assert prepare_ops.parser_function("// Prefix\n" + definition + "// Tail\n",
                                       "ParseFixtureV2Attributes") == definition
    with pytest.raises(ValueError, match="Expected one"):
        prepare_ops.parser_function(definition + definition, "ParseFixtureV2Attributes")


def test_official_patch_extraction_hashes_and_functions(tmp_path):
    root = Path(__file__).resolve().parents[3]
    mp_source = root / ".tmp/autonomy/mediapipe-0.10.21"
    patch_path = mp_source / "third_party/org_tensorflow_custom_ops.diff"
    if not patch_path.is_file():
        pytest.skip("Official MediaPipe source is not present locally")
    original_hash = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    result = prepare_ops.prepare(mp_source, tmp_path / "overlay")
    assert result["patch_sha256"] == original_hash
    assert hashlib.sha256(patch_path.read_bytes()).hexdigest() == original_hash
    assert len(result["cpu_sources"]) == len(result["parser_sources"]) == 3
    assert len(result["generated_files"]) == 6
    patch = patch_path.read_text(encoding="utf-8")
    for op, function in prepare_ops.OPS.items():
        official = prepare_ops.new_patch_file(patch, prepare_ops.PREFIX + op + ".cc")
        generated = (tmp_path / "overlay/cpu_parsers" / (op + ".cc")).read_text()
        for version in (1, 2):
            exact = prepare_ops.parser_function(official, f"Parse{function}V{version}Attributes")
            assert exact in generated
        # No GPU graph transformation/parser class implementations are linked.
        assert "::ApplyToNode(" not in generated
        assert "OperationParser::Parse(" not in generated
    for item in result["generated_files"]:
        assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
