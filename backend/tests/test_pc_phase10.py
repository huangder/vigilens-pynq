"""PC 端 phase 10 模拟与 `clip:` 帧源的离线测试（不需要开发板）。

这里刻意**不**断言"上板跑通"：只证明 PC 模拟的灰度/哈希/运动量与
clip 打包结果自洽，并且 `clip:` 源能被 A 线管线当成普通 BGR 帧读。
"""
from __future__ import annotations

import shutil
import json
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "board" / "video_link"))
sys.path.insert(0, str(ROOT / "backend"))

from capture import is_video_file_source, open_frame_source, will_be_synthetic  # noqa: E402
from pc_phase10 import simulate_clip  # noqa: E402
from quality import QualityScorer  # noqa: E402
from run_pipeline import load_pl_motion  # noqa: E402
from video_clip import TIERS, write_clip  # noqa: E402


def _blank(n: int, w: int = 640, h: int = 480) -> list[np.ndarray]:
    return [np.zeros((h, w, 3), dtype=np.uint8) for _ in range(n)]


@pytest.fixture
def workdir():
    d = Path(tempfile.gettempdir()) / ("vigilens_pc10_" + uuid.uuid4().hex)
    d.mkdir()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_simulate_matches_digests_and_frame1_motion_is_invalid(workdir):
    frames = _blank(3)
    frames[1][0, :100] = 255  # 第 2 帧：一行 100 个白像素
    write_clip(frames, workdir, tier="vga", source="pc10-unit")

    r = simulate_clip(workdir, max_frames=3, motion_thresh=16)
    assert r["mode"] == "pc_sim" and r["is_board_run"] is False
    assert r["frames"] == 3 and r["vid_fails"] == 0 and r["digest_match"] is True

    f1, f2, f3 = r["per_frame"]
    assert f1["valid"] is False and f1["motion_ratio_q16"] == 0  # 与 HLS 第 0 帧无效一致
    # 640 列里只有 x%5<3 的 384 列被保留；第 0 行保留 60 个白像素
    assert f2["valid"] is True and f2["motion_pixels"] == 60
    assert f2["motion_ratio_q16"] == (60 << 16) // (TIERS["vga"].gray_width * TIERS["vga"].gray_height)
    # 第 3 帧与第 2 帧相同；只要 digest 对得上就不应产生 mismatch
    assert f3["digest_match"] is True


def test_motion_threshold_is_strictly_greater(workdir):
    # 灰度 16 与 0 的差 == 阈值 16：按 HLS 口径"严格大于"不计入运动像素
    frames = _blank(2)
    frames[1][:] = 16
    write_clip(frames, workdir, tier="vga", source="pc10-thresh16")
    r16 = simulate_clip(workdir, max_frames=2, motion_thresh=16)
    assert r16["per_frame"][1]["motion_pixels"] == 0

    out17 = workdir / "d17"
    frames17 = _blank(2)
    frames17[1][:] = 17
    write_clip(frames17, out17, tier="vga", source="pc10-thresh17")
    r17 = simulate_clip(out17, max_frames=2, motion_thresh=16)
    assert r17["per_frame"][1]["motion_pixels"] == TIERS["vga"].gray_width * TIERS["vga"].gray_height
    assert r17["per_frame"][1]["motion_ratio_q16"] == 65536


def test_log_lines_match_board_format(workdir):
    write_clip(_blank(2), workdir, tier="vga", source="pc10-log")
    r = simulate_clip(workdir, max_frames=2)
    assert r["log"][0].startswith("vid f=1 sum=")
    assert " hash=0x" in r["log"][0] and r["log"][0].endswith(" fid=1")


def test_clip_source_is_not_synthetic_and_yields_bgr(workdir):
    frames = _blank(2)
    frames[1][0, 0] = 255
    write_clip(frames, workdir, tier="vga", source="pc10-capture")
    src = f"clip:{workdir}"

    assert will_be_synthetic(src) is False
    assert is_video_file_source(src) is False

    it, desc = open_frame_source(src, limit=2)
    got = list(it)
    assert "PC 模拟帧源" in desc
    assert len(got) == 2
    assert got[0].image.shape == (480, 640, 3)
    assert got[0].image[0, 0].tolist() == [0, 0, 0]
    # clip 存 RGB(255,255,255)；转回 BGR 仍是 [255,255,255]
    assert got[1].image[0, 0].tolist() == [255, 255, 255]


def test_load_pl_motion_skips_invalid_frame(workdir):
    p = workdir / "pc_phase10.json"
    p.write_text(json.dumps({
        "per_frame": [
            {"fid": 1, "valid": False, "motion_ratio_q16": 0},
            {"fid": 2, "valid": True, "motion_ratio_q16": 16384},
            {"fid": 3, "valid": True, "motion_ratio": 0.5},
        ]
    }), encoding="utf-8")
    assert load_pl_motion(p) == {2: 0.25, 3: 0.5}


def test_apply_motion_ratio_recomputes_motion_and_overall():
    sc = QualityScorer({"quality_weights": {"light": 0.5, "motion": 0.3, "face": 0.2},
                        "motion_score_gain": 4.0})
    q = {"light_score": 0.8, "motion_score": 0.2, "overall": 0.5}
    out = sc.apply_motion_ratio(q, 0.25, face_score=0.5)
    assert out["motion_score"] == 1.0
    # (0.5*0.8 + 0.3*(1-1.0) + 0.2*0.5) / 1.0 = 0.5
    assert out["overall"] == 0.5
    assert out["_motion_source"] == "pl_sim"
