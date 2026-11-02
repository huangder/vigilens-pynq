"""视频转运层的离线测试（不需要开发板、不需要摄像头）。

覆盖三件事：
  1. RGB 布局与 BGR→RGB 转换 —— 转错不会崩，只会让板端灰度静默偏移；
  2. clip 的 manifest / sha256 / 逐帧读取 —— 传输前后必须能证明是同一份字节；
  3. 灰度参考式与逐帧 FNV-1a —— 板端 phase 10 回传这两个数，主机用同一式对拍。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "board" / "video_link"))

from video_clip import (  # noqa: E402
    TIERS,
    ClipError,
    clip_digests,
    fnv1a32,
    gray_from_rgb,
    iter_clip_frames,
    plan_transfer,
    verify_clip,
    write_clip,
)


def _bgr_frames(n: int, w: int, h: int, seed: int = 7) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.integers(0, 256, (h, w, 3), dtype=np.uint8) for _ in range(n)]


@pytest.fixture
def workdir():
    """不用 pytest 的 tmp_path：它在 Windows 上建 0700 目录，受限沙箱会拒绝写入。"""
    d = Path(tempfile.gettempdir()) / ("vigilens_vt_" + uuid.uuid4().hex)
    d.mkdir()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_tiers_geometry_matches_fpga_contract():
    assert (TIERS["vga"].gray_width, TIERS["vga"].gray_height) == (384, 288)
    assert (TIERS["720p"].gray_width, TIERS["720p"].gray_height) == (480, 270)
    assert TIERS["vga"].sample == 5 and TIERS["720p"].sample == 8
    # 两个档位的 keep 都是 3，差的是 sample（3/5 vs 3/8）
    assert TIERS["vga"].keep == TIERS["720p"].keep == 3


def test_write_clip_is_rgb_frame_major(workdir):
    w, h = TIERS["vga"].width, TIERS["vga"].height
    frames = _bgr_frames(2, w, h)
    m = write_clip(frames, workdir, tier="vga", source="unit-test")

    assert m["schema"] == "vigilens.video_clip/1"
    assert m["frames"] == 2 and m["frame_bytes"] == w * h * 3
    assert m["bytes"] == 2 * w * h * 3
    assert m["color_order"] == "RGB"

    raw = (workdir / "frames.bin").read_bytes()
    assert raw[:3] == bytes([frames[0][0, 0, 2], frames[0][0, 0, 1], frames[0][0, 0, 0]])
    assert len(raw) == m["bytes"]
    # manifest 的 sha256 必须是对 frames.bin 整体算的，不是对某一帧
    assert m["sha256"] == hashlib.sha256(raw).hexdigest()

    back = list(iter_clip_frames(workdir))
    assert len(back) == 2
    assert np.array_equal(back[0], frames[0][:, :, ::-1])


def test_verify_clip_detects_tamper(workdir):
    w, h = TIERS["vga"].width, TIERS["vga"].height
    write_clip(_bgr_frames(1, w, h), workdir, tier="vga")
    assert verify_clip(workdir)["ok"] is True

    with (workdir / "frames.bin").open("ab") as fh:
        fh.write(b"\x00")
    v = verify_clip(workdir)
    assert v["ok"] is False
    assert any("大小" in r for r in v["reasons"])


def test_gray_formula_and_phase_sampling():
    w, h = TIERS["vga"].width, TIERS["vga"].height
    rgb = _bgr_frames(1, w, h, seed=11)[0][:, :, ::-1]
    got = gray_from_rgb(rgb, "vga")

    r = rgb[:, :, 0].astype(np.uint32)
    g = rgb[:, :, 1].astype(np.uint32)
    b = rgb[:, :, 2].astype(np.uint32)
    y = ((77 * r + 150 * g + 29 * b + 128) >> 8).astype(np.uint8)
    ys = (np.arange(h) % 5) < 3
    xs = (np.arange(w) % 5) < 3
    assert np.array_equal(got, y[np.ix_(ys, xs)])
    assert got.shape == (288, 384)


def test_gray_rejects_wrong_size():
    with pytest.raises(ClipError):
        gray_from_rgb(np.zeros((10, 10, 3), np.uint8), "vga")


def test_fnv1a32_known_vector():
    # FNV-1a 32 位标准测试向量
    assert fnv1a32(b"") == 0x811C9DC5
    assert fnv1a32(b"a") == 0xE40C292C
    assert fnv1a32(b"abc") == 0x1A47E90B


def test_clip_digests_match_manual(workdir):
    w, h = TIERS["vga"].width, TIERS["vga"].height
    frames = _bgr_frames(2, w, h, seed=3)
    write_clip(frames, workdir, tier="vga")
    ds = clip_digests(workdir)
    assert [d["frame"] for d in ds] == [1, 2]
    for d, bgr in zip(ds, frames):
        gray = gray_from_rgb(bgr[:, :, ::-1], "vga")
        raw = gray.tobytes()
        assert d["sum"] == int(gray.sum(dtype=np.uint64))
        assert d["hash"] == fnv1a32(raw)
        assert d["gray_bytes"] == 384 * 288


def test_plan_transfer_uses_manifest_bytes(workdir):
    w, h = TIERS["vga"].width, TIERS["vga"].height
    write_clip(_bgr_frames(2, w, h), workdir, tier="vga")
    p = plan_transfer(workdir, link_bytes_per_s=600_000)
    assert p["bytes"] == 2 * w * h * 3
    assert p["estimated_seconds"] == pytest.approx(p["bytes"] / 600_000)
