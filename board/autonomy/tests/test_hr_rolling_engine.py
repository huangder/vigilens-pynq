"""Compile/run native recovery at both configured time bases; synthetic only."""
from pathlib import Path
import shutil
import subprocess

import pytest

from board.autonomy.export_config import export as export_measure
from board.autonomy.export_vital_config import export as export_vital

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("board_profile", [False, True], ids=["reference30", "board60"])
def test_native_rolling_recovery_uses_new_data_and_retained_votes(tmp_path, board_profile):
    compiler = shutil.which("gcc") or shutil.which("cc")
    if not compiler:
        pytest.skip("C compiler unavailable")
    export_measure(ROOT / "config.yaml", tmp_path / "vg_config.h")
    export_vital(ROOT / "config.yaml", tmp_path / "vg_vital_config.h", board_profile=board_profile)
    source = ROOT / "board/autonomy"
    executable = tmp_path / "rolling_recovery.exe"
    files = ["tests/autonomy_engine_test.c", "autonomy_engine.c", "measure.c", "behavior.c",
             "quality.c", "decision.c", "vital.c", "pose_pnp.c", "contract_json.c"]
    subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-I", str(source), "-I", str(tmp_path),
                    *[str(source / name) for name in files], "-lm", "-o", str(executable)],
                   check=True, capture_output=True, text=True, timeout=120)
    result = subprocess.run([str(executable), "--rolling-recovery"], check=True,
                            capture_output=True, text=True, timeout=60)
    assert "ROLLING_RECOVERY_PASS" in result.stdout
    floor = subprocess.run([str(executable), "--report-floor"], check=True,
                           capture_output=True, text=True, timeout=60)
    assert "REPORT_FLOOR_PASS" in floor.stdout
    if board_profile:
        full = subprocess.run([str(executable), "--full60"], check=True,
                              capture_output=True, text=True, timeout=60)
        assert "FULL60_ENGINE_PASS" in full.stdout
    else:
        original = subprocess.run([str(executable)], check=True,
                                  capture_output=True, text=True, timeout=60)
        assert "PASS autonomy native integration" in original.stdout
