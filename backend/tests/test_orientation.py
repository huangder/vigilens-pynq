"""test_orientation.py —— 图像方向归一化回归（台账 BUG-013）。

问题：相机装反 / 手机倒着拿 / 竖屏素材被读成横屏时，mediapipe 一帧脸都检不到
（台账实测 180° 倒置检出率 **0%**），于是整段测量变成"人脸可见率 0.00 → 不可靠"。

修法：打开帧源时用**开头几帧**试 0/90/180/270 四个朝向，挑检出帧数最多的那个，
之后整条流统一旋正。这里不需要真人脸素材 —— 用一个"只在方向正确时才检出"的
**假检测器**，就能把探测逻辑钉死。
"""

from __future__ import annotations

import pytest

cv2 = pytest.importorskip("cv2", reason="旋转要用 OpenCV")
np = pytest.importorskip("numpy")

from backend.capture import Frame, OrientingSource, choose_rotation, rotate_frame  # noqa: E402


def _img(marker: str, h: int = 40, w: int = 60) -> np.ndarray:
    """造一张小图，把一个白色方块放在指定角上（当作"脸在哪"）。"""
    a = np.zeros((h, w, 3), dtype=np.uint8)
    if marker == "tl":       # 左上
        a[0:8, 0:8] = 255
    elif marker == "br":     # 右下
        a[h - 8:h, w - 8:w] = 255
    elif marker == "tr":     # 右上
        a[0:8, w - 8:w] = 255
    elif marker == "bl":     # 左下
        a[h - 8:h, 0:8] = 255
    return a


def _marker(img: np.ndarray) -> str:
    """读出白块在哪个角（旋转后用它判断朝向）。"""
    h, w = img.shape[:2]
    quads = {"tl": img[0:8, 0:8].max(), "tr": img[0:8, w - 8:w].max(),
             "bl": img[h - 8:h, 0:8].max(), "br": img[h - 8:h, w - 8:w].max()}
    if max(quads.values()) == 0:
        return "none"          # 整张全黑 = 没有"脸"，别退化成"左上角有脸"
    return max(quads, key=lambda k: quads[k])


def _detect_only_when_marker_at_top_left(img: np.ndarray) -> bool:
    """"假 mediapipe"：只有当"脸"（白块）在**左上**时才说检测到了。"""
    return _marker(img) == "tl"


def _frames(marker: str, n: int = 12) -> list[Frame]:
    return [Frame(frame_id=i, ts=float(i), image=_img(marker)) for i in range(n)]


# ---------------------------------------------------------------------------
# 纯函数：旋转本身
# ---------------------------------------------------------------------------

def test_rotate_frame_180_flips_the_marker() -> None:
    assert _marker(rotate_frame(_img("tl"), 180)) == "br"


def test_rotate_frame_90_and_270_swap_dimensions() -> None:
    a = _img("tl")                       # 40 x 60
    assert rotate_frame(a, 90).shape[:2] == (60, 40)
    assert rotate_frame(a, 270).shape[:2] == (60, 40)


def test_rotate_frame_zero_and_unknown_image_types_are_passthrough() -> None:
    a = _img("tl")
    assert rotate_frame(a, 0) is a, "0° 必须原样返回（连拷贝都不要）"
    assert rotate_frame("不是图像", 90) == "不是图像", "合成帧源不是 ndarray，不能报错"


def test_rotate_frame_rejects_illegal_angle() -> None:
    with pytest.raises(ValueError):
        rotate_frame(_img("tl"), 45)


# ---------------------------------------------------------------------------
# 探测逻辑：哪个角度能检出脸
# ---------------------------------------------------------------------------

def test_choose_rotation_picks_the_upright_angle() -> None:
    """素材被转了 180°（白块在右下）→ 探测应给出 180，因为转回来才检得到。"""
    best, scores = choose_rotation([_img("br")] * 4, _detect_only_when_marker_at_top_left)
    assert best == 180, scores
    assert scores[180] == 4 and scores[0] == 0, scores


def test_choose_rotation_keeps_zero_when_nothing_detected() -> None:
    """一帧都检不出（比如没有真人脸）→ 保持 0，宁可不动也不要瞎转。"""
    best, scores = choose_rotation([_img("none")] * 4, _detect_only_when_marker_at_top_left)
    assert best == 0 and set(scores.values()) == {0}, scores


def test_choose_rotation_prefers_zero_on_tie() -> None:
    """0° 与 180° 命中数相同 → 选 0（宁可不动）。"""
    def always(_img_: np.ndarray) -> bool:
        return True

    best, scores = choose_rotation([_img("tl")] * 3, always)
    assert best == 0, scores


def test_choose_rotation_survives_a_throwing_detector() -> None:
    """检测器抛异常时按"这帧没命中"处理，不让探测把管线干掉。"""
    def boom(_img_: np.ndarray) -> bool:
        raise RuntimeError("mediapipe 挂了")

    best, scores = choose_rotation([_img("tl")], boom)
    assert best == 0 and scores[0] == 0


# ---------------------------------------------------------------------------
# OrientingSource：探测 + 整条流旋转，且不许丢帧
# ---------------------------------------------------------------------------

def test_auto_mode_rotates_the_whole_stream_and_keeps_every_frame() -> None:
    src = OrientingSource(iter(_frames("br", n=12)), detect=_detect_only_when_marker_at_top_left,
                          rotate="auto", probe_frames=4)
    src.prime()
    assert src.degrees == 180, src.scores
    assert src.probed_frames == 4

    out = list(src)
    assert len(out) == 12, "探测用的前 4 帧必须补回来，不许丢帧"
    assert [f.frame_id for f in out] == list(range(12)), "frame_id 必须原样保留、顺序不变"
    assert {_marker(f.image) for f in out} == {"tl"}, "整条流都应被旋正"


def test_forced_rotation_skips_probing() -> None:
    src = OrientingSource(iter(_frames("tl", n=5)), detect=_detect_only_when_marker_at_top_left,
                          rotate=180, probe_frames=4)
    src.prime()
    assert src.degrees == 180 and src.probed_frames == 0, "强制角度不该再去探测"
    assert {_marker(f.image) for f in src} == {"br"}


def test_forced_rotation_accepts_the_cli_string_form() -> None:
    """`--rotate 180` 传进来是**字符串**：必须照样生效，不许静默失效（实测踩过）。"""
    src = OrientingSource(iter(_frames("tl", n=3)), rotate="180", probe_frames=4)
    assert src.degrees == 180
    assert {_marker(f.image) for f in src} == {"br"}


def test_rotate_cli_string_zero_means_untouched() -> None:
    src = OrientingSource(iter(_frames("tl", n=3)), rotate="0")
    assert src.degrees == 0
    assert {_marker(f.image) for f in src} == {"tl"}


def test_illegal_rotate_value_is_rejected_loudly() -> None:
    with pytest.raises(ValueError):
        OrientingSource(iter(_frames("tl", n=1)), rotate="45")


def test_no_detector_means_no_probing_and_no_rotation() -> None:
    """stub 关键点没有真实检测能力 → 不探测、不旋转（合成帧源也走这条路）。"""
    src = OrientingSource(iter(_frames("tl", n=5)), detect=None, rotate="auto", probe_frames=4)
    src.prime()
    assert src.degrees == 0 and src.probed_frames == 0
    assert len(list(src)) == 5


def test_prime_is_idempotent_and_probe_is_bounded() -> None:
    """prime 可以重复调用；探测代价有上限（帧数 × 4 个角度），不是逐帧探测。"""
    calls = {"n": 0}

    def counting(img: np.ndarray) -> bool:
        calls["n"] += 1
        return _marker(img) == "tl"

    src = OrientingSource(iter(_frames("br", n=20)), detect=counting, rotate="auto", probe_frames=3)
    src.prime()
    src.prime()                       # 幂等
    assert calls["n"] == 3 * 4, f"探测次数应为 帧数×4，实际 {calls['n']}"
    assert len(list(src)) == 20
