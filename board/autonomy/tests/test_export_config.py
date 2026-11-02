"""Compile exported headers and verify exact config provenance/native values.

python -m unittest discover -s board/autonomy/tests -p test_export_config.py -v
"""
import ctypes as ct
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_measure as m
from backend.config import load_config

spec = importlib.util.spec_from_file_location("vg_export_config", ROOT/"board/autonomy/export_config.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class ExportConfig(unittest.TestCase):
    def test_cpp_application_links_the_c_modules(self):
        with tempfile.TemporaryDirectory(prefix="vg_cpp_") as temp:
            folder = Path(temp)
            exporter.export(ROOT/"config.yaml", folder/"generated.h")
            objects = []
            cc = shlex.split(os.environ.get("CC", "gcc"))
            for name in ("behavior", "quality", "decision", "measure"):
                obj = folder/(name+".o")
                subprocess.run(cc+["-std=c11", "-Wall", "-Wextra", "-Werror", "-c",
                                    str(ROOT/"board/autonomy"/(name+".c")), "-o", str(obj)],
                               check=True, capture_output=True, text=True)
                objects.append(str(obj))
            main = folder/"main.cc"
            main.write_text('#include "generated.h"\nint main() {\n'
                            'vg_measure* e=vg_measure_create(&vg_exported_config);\n'
                            'if (!e) return 1;\n'
                            'vg_measure_destroy(e);\nreturn 0;\n}\n')
            exe = folder/("application.exe" if os.name == "nt" else "application")
            subprocess.run(shlex.split(os.environ.get("CXX", "g++"))+
                           ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT/"board/autonomy"),
                            str(main), *objects, "-lm", "-o", str(exe)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(exe)], check=True, capture_output=True, text=True)

    def compile_and_read(self, source_path, folder):
        header = folder/"generated.h"
        manifest = exporter.export(source_path, header)
        self.assertEqual(manifest["source_sha256"], hashlib.sha256(source_path.read_bytes()).hexdigest())
        self.assertFalse(manifest["thresholds_calibrated_by_export"])
        self.assertEqual(json.loads(header.with_suffix(".json").read_text()), manifest)
        before = header.read_bytes()
        exporter.export(source_path, header)
        self.assertEqual(before, header.read_bytes())
        source = folder/"read.c"
        source.write_text('#include "generated.h"\n#include "generated.h"\n'
                          'const vg_measure_config *read_exported(void) {return &vg_exported_config;}\n', encoding="utf-8")
        library = folder/("read.dll" if os.name == "nt" else "read.so")
        command = shlex.split(os.environ.get("CC", "gcc"))
        command += ["-std=c11", "-Wall", "-Wextra", "-Werror", "-shared"]
        command += ["-static-libgcc"] if os.name == "nt" else ["-fPIC"]
        command += ["-I", str(ROOT/"board/autonomy"), str(source), "-o", str(library)]
        subprocess.run(command, check=True, capture_output=True, text=True)
        lib = ct.CDLL(str(library))
        try:
            lib.read_exported.restype = ct.POINTER(m.Config)
            config = lib.read_exported().contents
            for group, fields in manifest["groups"].items():
                for key, value in fields.items():
                    self.assertEqual(getattr(getattr(config, group), key), value, (group, key))
        finally:
            if os.name == "nt":
                import _ctypes
                _ctypes.FreeLibrary(lib._handle)
        return manifest

    def test_repository_config_exact_compiled_values_and_digest(self):
        with tempfile.TemporaryDirectory(prefix="vg_export_") as temp:
            result = self.compile_and_read(ROOT/"config.yaml", Path(temp))
            cfg = load_config()
            self.assertEqual(result["groups"]["behavior"]["min_close_ms"], cfg["min_close_ms"])
            self.assertEqual(result["groups"]["quality"]["weight_motion"], cfg["quality_weights"]["motion"])

    def test_altered_thresholds_legacy_ms_alias_and_hold_clamp(self):
        with tempfile.TemporaryDirectory(prefix="vg_export_") as temp:
            folder = Path(temp)
            source = folder/"custom.yaml"
            source.write_text("min_close_ms: null\nmin_close_frames: 7\nfps_nominal: 56\n"
                              "pose_hold_frames: 0\near_close_threshold: 0.23912345678912345\n"
                              "quality_weights:\n  light: 7.25\n  motion: 0.125\n  face: 2.5\n", encoding="utf-8")
            result = self.compile_and_read(source, folder)
            self.assertEqual(result["groups"]["behavior"]["min_close_ms"], 125.)
            self.assertEqual(result["groups"]["decision"]["pose_hold_frames"], 1)
            self.assertEqual(result["groups"]["quality"]["weight_light"], 7.25)

    def test_max_native_integer_constants_compile_without_overflow(self):
        with tempfile.TemporaryDirectory(prefix="vg_export_") as temp:
            folder = Path(temp)
            source = folder/"max.yaml"
            source.write_text(f"fatigue_yawn_count: {2**64-1}\nfatigue_long_close_count: {2**64-1}\n"
                              f"pose_hold_frames: {2**32-1}\n", encoding="utf-8")
            result = self.compile_and_read(source, folder)
            self.assertEqual(result["groups"]["decision"]["fatigue_yawn_count"], 2**64-1)

    def test_out_of_range_integers_fail_without_output(self):
        with tempfile.TemporaryDirectory(prefix="vg_export_") as temp:
            folder = Path(temp)
            source, output = folder/"bad.yaml", folder/"output.h"
            for key, value in (("fatigue_yawn_count", -1), ("fatigue_long_close_count", 2**64),
                               ("pose_hold_frames", 2**32)):
                source.write_text(f"{key}: {value}\n", encoding="utf-8")
                with self.assertRaises(ValueError): exporter.export(source, output)
                self.assertFalse(output.exists())

    def test_nonfinite_and_missing_source_fail_without_output(self):
        with tempfile.TemporaryDirectory(prefix="vg_export_") as temp:
            folder = Path(temp)
            source, output = folder/"bad.yaml", folder/"output.h"
            with self.assertRaises(FileNotFoundError): exporter.export(source, output)
            for token in ("nan", "inf", "-inf"):
                source.write_text(f"ear_close_threshold: {token}\n", encoding="utf-8")
                with self.assertRaises(ValueError): exporter.export(source, output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
