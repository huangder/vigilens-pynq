"""灰度档位回归：A 线 `quality.to_gray` 必须与 C 线 `rgb2gray` 的黄金参考逐字节相同。

为什么值得单独一组测试（`docs/38` §3.2 W6 / W2）：

- 契约 §0 的档位表把灰度工作尺寸定成 **480×270**（720p 抽 3/8、1080p 抽 1/4），
  而 `quality.to_gray` 长期只实现 **3/5 → 384×288**。这意味着一路 720p 源进来，
  A 线算出的运动量与 C 线 `rgb2gray` 的输出**根本不是同一个尺寸**，无法对拍。
- 抽取比是**编译期参数**（HLS 侧）和**尺寸相关分支**（Python 侧），
  "3/5 能对上"推不出"3/8 能对上" —— `c6:245` 就吃过一次"数据 3/5、IP 3/8"的亏。

判据：`fpga/sim/data_motion_*/` 里的 `rgb_frames.bin`（RGB888）喂给 `to_gray`，
结果与同目录 `gray.bin` **逐字节 0 处不等**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

import quality  # noqa: E402  —— 需要 backend/ 在 sys.path（pytest.ini 已配）

ROOT = Path(__file__).resolve().parents[2]
TIERS = {
    "720p60": (1280, 720, (8, 3)),
    "1080p45": (1920, 1080, (4, 1)),
}


def test_decim_for_size_reads_contract_tiers():
    assert quality.decim_for_size(1280, 720) == (8, 3)
    assert quality.decim_for_size(1920, 1080) == (4, 1)
    # 现行档与"不认识的尺寸"都回落 3/5 —— 不许悄悄换口径
    assert quality.decim_for_size(640, 480) == (5, 3)
    assert quality.decim_for_size(800, 600) == (5, 3)


def test_to_gray_default_keeps_640x480_behaviour():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    out = quality.to_gray(img)
    assert out.shape == (288, 384)      # 3/5 → 384×288（宽×高）


@pytest.mark.parametrize("tier", sorted(TIERS))
def test_to_gray_matches_c_golden(tier: str):
    width, height, decim = TIERS[tier]
    data_dir = ROOT / "fpga" / "sim" / f"data_motion_{tier}"
    rgb_path = data_dir / "rgb_frames.bin"
    gray_path = data_dir / "gray.bin"
    if not rgb_path.exists() or not gray_path.exists():
        pytest.skip(f"缺黄金参考：{rgb_path} / {gray_path}（可由 fpga/sim/gen_motion_vectors.py 重建）")

    out_w, out_h = 480, 270
    rgb_bytes = width * height * 3
    gray_bytes = out_w * out_h
    rgb_raw = rgb_path.read_bytes()
    gray_raw = gray_path.read_bytes()
    frames = len(gray_raw) // gray_bytes
    assert len(rgb_raw) == frames * rgb_bytes, "rgb_frames.bin 与 gray.bin 帧数不配套"

    mismatched_total = 0
    for i in range(frames):
        rgb = np.frombuffer(rgb_raw[i * rgb_bytes:(i + 1) * rgb_bytes], dtype=np.uint8)
        rgb = rgb.reshape(height, width, 3)
        # 黄金参考是 **RGB** 顺序；`to_gray` 约定吃 OpenCV 的 **BGR** ⇒ 先翻通道
        bgr = rgb[:, :, ::-1]
        got = quality.to_gray(bgr, decim=decim)
        assert got.shape == (out_h, out_w)
        want = np.frombuffer(gray_raw[i * gray_bytes:(i + 1) * gray_bytes], dtype=np.uint8)
        mismatched_total += int(np.count_nonzero(got.reshape(-1) != want))

    assert mismatched_total == 0, f"{tier}: 与 C 线黄金参考有 {mismatched_total} 处像素不等"


def test_to_gray_rejects_bad_decim():
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    with pytest.raises(ValueError):
        quality.to_gray(img, decim=(0, 1))
    with pytest.raises(ValueError):
        quality.to_gray(img, decim=(4, 5))
