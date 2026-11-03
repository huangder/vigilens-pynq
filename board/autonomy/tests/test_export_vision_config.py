"""Export the board-side detection cadence and reject unusable values.

python -m unittest discover -s board/autonomy/tests -p test_export_vision_config.py -v
"""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "vg_export_vision_config", ROOT / "board/autonomy/export_vision_config.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def macro(header, name):
    match = re.search(rf"#define {name} (\S+)", header)
    if not match:
        raise AssertionError(f"missing {name}")
    return float.fromhex(match.group(1))


class ExportVisionConfig(unittest.TestCase):
    def test_optional_pipeline_modes_are_explicit_and_bounded(self):
        with tempfile.TemporaryDirectory(prefix='vg_pipeline_cfg_') as temp:
            source=Path(temp)/'config.yaml';header=Path(temp)/'out.h'
            original=(ROOT/'config.yaml').read_text(encoding='utf-8')
            original=re.sub(r'^  (tracker_image_mode|tracker_region_expansion|preview_async|preview_worker_nice|analysis_slots):.*\n','',original,flags=re.M)
            source.write_text(original,encoding='utf-8');exporter.export(source,header)
            text=header.read_text(encoding='utf-8')
            for key,value in [('VG_TRACKER_ROI_HALF',0),('VG_PREVIEW_ASYNC',0),('VG_ANALYSIS_SLOTS',2),('VG_RGB_POOL_BUFFERS',6)]:
                self.assertIn(f'#define {key} {value}',text)
            extra='  tracker_image_mode: roi_half\n  preview_async: true\n  preview_worker_nice: 10\n  analysis_slots: 4\n'
            source.write_text(re.sub(r'(^board_vision:.*\n)',lambda m:m[1]+extra,original,count=1,flags=re.M),encoding='utf-8')
            exporter.export(source,header);text=header.read_text(encoding='utf-8')
            for key,value in [('VG_TRACKER_ROI_HALF',1),('VG_PREVIEW_ASYNC',1),('VG_PREVIEW_WORKER_NICE',10),('VG_ANALYSIS_SLOTS',4),('VG_RGB_POOL_BUFFERS',8)]:
                self.assertIn(f'#define {key} {value}',text)
            for key,value in [('tracker_image_mode','full'),('tracker_region_expansion','0.5'),
                              ('preview_async','1'),('preview_worker_nice','20'),('preview_worker_nice','true'),
                              ('analysis_slots','1'),('analysis_slots','9'),('analysis_slots','true'),('analysis_slots','3.5')]:
                source.write_text(re.sub(r'(^board_vision:.*\n)',lambda m:m[1]+f'  {key}: {value}\n',original,count=1,flags=re.M),encoding='utf-8')
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):exporter.export(source,header)

    def test_unsupported_capture_geometry_and_native_rate_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for key,value in (('camera_width','800'),('camera_width','960'),('camera_height','540'),('camera_format','unknown'),('camera_format','yuyv')):
                source=Path(temp)/'bad.yaml'
                text=(ROOT/'config.yaml').read_text(encoding='utf-8')
                text=re.sub(r'camera_width:\s*\S+','camera_width: 1280',text,count=1)
                text=re.sub(r'camera_height:\s*\S+','camera_height: 720',text,count=1)
                text=re.sub(r'camera_format:\s*\S+','camera_format: mjpg',text,count=1)
                text=re.sub(rf'{key}:\s*\S+',f'{key}: {value}',text,count=1)
                source.write_text(text,encoding='utf-8')
                with self.assertRaises(ValueError):exporter.export(source,Path(temp)/'out.h')

    def test_repository_cadence_is_exported_and_reproducible(self):
        with tempfile.TemporaryDirectory(prefix="vg_vision_") as temp:
            header = Path(temp) / "vg_vision_config.h"
            exporter.export(ROOT / "config.yaml", header)
            text = header.read_text(encoding="utf-8")
            manifest = json.loads(header.with_suffix(".json").read_text(encoding="utf-8"))
            board = manifest["board_vision"]
            self.assertEqual(macro(text, "VG_MIN_FACE_FAST"), board["min_face_fast"])
            self.assertEqual(macro(text, "VG_FAST_SIDE_MARGIN"), board["fast_side_margin"])
            self.assertEqual(macro(text, "VG_DEEP_DETECT_SECONDS"), board["deep_detect_seconds"])
            self.assertEqual(macro(text, "VG_PREVIEW_INTERVAL_S"), 1/board["preview_hz"])
            self.assertIn('vg_exported_blink_config',text)
            self.assertGreaterEqual(board["min_face_fast"], board["min_face"])
            self.assertGreater(board["fast_side_margin"], 0)
            self.assertLessEqual(board["fast_side_margin"], 1)
            self.assertGreater(board["deep_detect_seconds"], 0)
            self.assertFalse(manifest["accuracy_verified"])
            self.assertEqual(macro(text,"VG_ERT_PATCH_MIN_STD"),board["ert_patch_min_std"])
            self.assertEqual(macro(text,"VG_ERT_PATCH_MAX_ERROR"),board["ert_patch_max_error"])
            self.assertIn(f"#define VG_ERT_PATCH_SIDE {board['ert_patch_side']}",text)
            from math import ceil
            nominal=board.get('capture_fps',exporter.load_config(ROOT/"config.yaml")["fps_nominal"])
            self.assertIn(f"#define VG_TRACKER_HISTORY_FRAMES {ceil(board['tracker_anchor_max_age_s']*nominal)+1}",text)
            exporter.export(ROOT / "config.yaml", header)
            self.assertEqual(text, header.read_text(encoding="utf-8"))

    def test_finer_fast_tier_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="vg_vision_") as temp:
            folder = Path(temp)
            source = folder / "custom.yaml"
            text = (ROOT / "config.yaml").read_text(encoding="utf-8")
            text = re.sub(r"min_face_fast:\s*\S+", "min_face_fast: 20", text, count=1, flags=re.M)
            source.write_text(text, encoding="utf-8")
            output = folder / "vg_vision_config.h"
            with self.assertRaises(ValueError):
                exporter.export(source, output)
            self.assertFalse(output.exists())

    def test_nonpositive_cadence_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="vg_vision_") as temp:
            folder = Path(temp)
            for key, value in (("deep_detect_seconds", "0.0"), ("deep_detect_seconds", "-1.0"),
                               ("fast_side_margin", "0.0"), ("fast_side_margin", "1.5")):
                source = folder / "custom.yaml"
                text = (ROOT / "config.yaml").read_text(encoding="utf-8")
                text = re.sub(rf"{key}:\s*\S+", f"{key}: {value}", text, count=1, flags=re.M)
                source.write_text(text, encoding="utf-8")
                output = folder / "vg_vision_config.h"
                with self.assertRaises(ValueError):
                    exporter.export(source, output)
                self.assertFalse(output.exists())

    def test_invalid_tracking_parameters_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="vg_tracking_cfg_") as temp:
            for key,value in (("tracker_radius",8),("tracker_levels",0),("tracker_iterations",33),
                              ("tracker_model_hz",0),("tracker_min_fraction",1.1),("tracker_fb_error",0),
                              ("tracker_anchor_max_age_s",1e30),("ert_patch_side",1),
                              ("ert_patch_side",129),("ert_patch_side",2.5),
                              ("ert_patch_min_std",0),("ert_patch_max_error",256),
                              ("preview_hz",0),("preview_hz",31),("preview_hz","nan"),
                              ("ear_close_ratio",0),("ear_open_ratio",0.5),("ear_open_ratio",1),
                              ("ear_baseline_tau_s",0),("blink_min_close_ms",0),
                              ("blink_min_open_ms",-1),("blink_max_gap_ms",0),
                              ("ear_baseline_reset_ms",100),("ear_threshold_mode","unknown"),
                              ("ear_close_absolute",0),("ear_open_absolute",.29),
                              ("ear_close_absolute","nan"),("blink_long_close_ms",50),
                              ("blink_long_close_ms","nan")):
                source=Path(temp)/"invalid.yaml"
                text=(ROOT/"config.yaml").read_text(encoding="utf-8")
                text=re.sub(rf"{key}:\s*\S+",f"{key}: {value}",text,count=1,flags=re.M)
                source.write_text(text,encoding="utf-8")
                with self.assertRaises(ValueError):
                    exporter.export(source,Path(temp)/"out.h")


if __name__ == "__main__":
    unittest.main()
