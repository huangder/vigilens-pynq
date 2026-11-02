"""Compile and test portable replay protocol against Python module composition."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_export_config as e
import test_behavior as b
from backend.config import load_config
from backend.behavior_metrics import BehaviorTracker
from backend.quality import QualityScorer
from backend.decision import DecisionEngine

spec = importlib.util.spec_from_file_location("fixture_generator", ROOT/"board/autonomy/make_replay_fixture.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class MeasureReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vg_replay_")
        cls.folder = Path(cls.temp.name)
        cls.program = cls.folder/("replay.exe" if os.name == "nt" else "replay")
        e.exporter.export(ROOT/"config.yaml", cls.folder/"vg_config.h")
        args = shlex.split(os.environ.get("CC", "gcc"))
        args += ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(cls.folder), "-I", str(ROOT/"board/autonomy")]
        if os.name == "nt": args += ["-static-libgcc"]
        args += [str(ROOT/f"board/autonomy/{name}.c") for name in ("measure_replay", "measure", "behavior", "quality", "decision")]
        args += ["-lm", "-o", str(cls.program)]
        subprocess.run(args, check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_text(self, text):
        file = self.folder/"input.txt"
        file.write_text(text, encoding="ascii")
        return subprocess.run([str(self.program), str(file)], capture_output=True, text=True, encoding="utf-8")

    def test_synthetic_fixture_matches_python_and_is_deterministic(self):
        cfg = load_config()
        fixture = self.folder/"fixture.txt"
        rows = generator.generate(fixture)
        before = fixture.read_bytes()
        generator.generate(fixture)
        self.assertEqual(before, fixture.read_bytes())
        process = subprocess.run([str(self.program), str(fixture)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(process.returncode, 0, process.stderr)
        records = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(records), len(rows))
        tracker, scorer, engine = BehaviorTracker(cfg), QualityScorer(cfg), DecisionEngine(cfg)
        last_id = last_ts = None
        statuses, reject_codes = set(), set()
        for record_id, (row, actual) in enumerate(zip(rows, records)):
            frame_id, ts, mean, q16, valid, eyes_present, visible, lp, rp, mp, yaw, pitch = row[:12]
            self.assertEqual(actual["kind"], "measure_replay")
            self.assertTrue(actual["synthetic"])
            self.assertEqual(actual["config_sha256"], hashlib.sha256((ROOT/"config.yaml").read_bytes()).hexdigest())
            self.assertEqual((actual["record"], actual["frame_id"], actual["ts"]), (record_id, frame_id, ts))
            if last_id is not None and (frame_id<=last_id or ts<=last_ts): rc = -3
            elif q16>65536: rc = -1
            elif not valid: rc = -2
            else: rc = 0
            self.assertEqual(actual["rc"], rc)
            if rc:
                reject_codes.add(rc)
                self.assertNotIn("behavior", actual)
                continue
            last_id, last_ts = frame_id, ts
            eye = lambda offset: {f"p{k+1}": tuple(row[offset+2*k:offset+2*k+2]) for k in range(6)}
            mouth = {key: tuple(row[36+2*k:38+2*k]) for k, key in enumerate(("left", "right", "top", "bottom"))}
            obs = SimpleNamespace(visible=visible, eyes={"left": eye(12) if lp else {}, "right": eye(24) if rp else {}} if eyes_present else {}, mouth=mouth if mp else {})
            behavior = tracker.update(frame_id, ts, obs)
            scorer._prev_gray = None
            scorer._last_motion["motion_ratio_q16"] = q16
            with patch("backend.quality.to_gray", return_value=SimpleNamespace(mean=lambda: mean*255.)):
                quality = scorer.update(frame_id, ts, object(), obs)
            quality.pop("_mode")
            decision = engine.decide(frame_id=frame_id, behavior=behavior, quality=quality,
                                    face={"visible": visible, "pose": {"yaw": yaw, "pitch": pitch, "roll": 0.}})
            self.assertEqual(actual["behavior"], behavior, record_id)
            self.assertEqual(actual["quality"], quality, record_id)
            self.assertEqual(actual["decision"], decision, record_id)
            self.assertEqual(actual["vital"], dict.fromkeys(("hr_bpm", "hr_conf", "rr_per_min", "rr_conf")))
            self.assertLess(len(json.dumps(actual, ensure_ascii=False).encode()), 8192)
            statuses.add(actual["decision"]["status"])
        self.assertEqual(reject_codes, {-1, -2, -3})
        self.assertEqual(statuses, {"normal", "unreliable", "adjust_posture", "fatigue_risk"})
        self.assertGreater(behavior["blink_count"], 0)
        self.assertGreater(behavior["long_close_count"], 0)
        self.assertGreaterEqual(behavior["yawn_count"], 2)

    def test_parser_rejects_malformed_headers_lengths_nonfinite_and_bad_numbers(self):
        row = generator.records(load_config(), frames=2)[1]
        valid = " ".join(map(str, row))
        for text in ("", "WRONG synthetic 1\n", "VIGILENS_REPLAY_V1 real 1\n", "VIGILENS_REPLAY_V1 synthetic 100001\n",
                     "VIGILENS_REPLAY_V1 synthetic 1\n", f"VIGILENS_REPLAY_V1 synthetic 2\n{valid}\n",
                     f"VIGILENS_REPLAY_V1 synthetic 1\n{valid}\nextra\n",
                     f"VIGILENS_REPLAY_V1 synthetic 1\n{valid} extra\n",
                     "VIGILENS_REPLAY_V1 synthetic 1\n"+"1 "*3000+"\n"):
            self.assertEqual(self.run_text(text).returncode, 2)
        for index, value in ((0, "-1"), (0, str(2**64)), (1, "NaN"), (2, "inf"), (3, str(2**32)),
                             (4, "2"), (7, "-1"), (12, "-inf"), (43, "1oops")):
            invalid = [str(v) for v in row]
            invalid[index] = value
            self.assertEqual(self.run_text("VIGILENS_REPLAY_V1 synthetic 1\n"+" ".join(invalid)+"\n").returncode, 2)

    def test_cli_usage(self):
        self.assertEqual(subprocess.run([str(self.program)], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
