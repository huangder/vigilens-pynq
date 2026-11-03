"""HR window selection must not change behavioral windows or the FIR rate."""
import math
import re
from pathlib import Path

import pytest

from backend.config import load_config, rppg_window_seconds
from backend.rppg_color import make_rppg
from board.autonomy.export_vital_config import build, export
from board.autonomy.export_config import config_values as measure_config


@pytest.mark.parametrize("method", ["green", "pos"])
def test_short_window_is_independent_and_legacy_config_is_preserved(method):
    cfg = {**load_config(), "rppg_method": method}
    assert make_rppg(cfg).need == 75
    assert cfg["window_seconds"] == 30
    assert cfg["fps_nominal"] == 30
    cfg.pop("rppg_window_seconds")
    cfg["window_seconds"] = 12
    assert make_rppg(cfg).need == 360


def test_custom_window_is_exported_identically_without_changing_behavior(tmp_path):
    source = tmp_path / "custom.yaml"
    base = (Path(__file__).resolve().parents[2] / "config.yaml").read_text(encoding="utf-8")
    source.write_text(re.sub(r"rppg_window_seconds: \S+", "rppg_window_seconds: 12", base), encoding="utf-8")
    cfg = load_config(source)
    assert make_rppg(cfg).need == 360
    assert build(source)["window_seconds"] == 12
    assert measure_config(source)["behavior"]["window_seconds"] == 30
    legacy = "\n".join(line for line in base.splitlines() if not line.startswith("rppg_window_seconds:"))
    source.write_text(legacy.replace("window_seconds: 30", "window_seconds: 12", 1), encoding="utf-8")
    assert build(source)["window_seconds"] == 12


@pytest.mark.parametrize("method", ["green", "pos"])
def test_previous_five_second_candidate_remains_supported(method):
    cfg={**load_config(), "rppg_method":method, "rppg_window_seconds":5}
    assert make_rppg(cfg).need == 150


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, 1 / 60])
def test_invalid_window_is_rejected_by_python_and_native_export(tmp_path, value):
    cfg = {**load_config(), "rppg_window_seconds": value}
    with pytest.raises(ValueError, match="rppg_window_seconds"):
        rppg_window_seconds(cfg)
    source, output = tmp_path / "bad.yaml", tmp_path / "bad.h"
    source.write_text(f"rppg_window_seconds: {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rppg_window_seconds"):
        export(source, output)
    assert not output.exists()
