"""test_uvc_source.py —— `uvc:<idx>` 帧源的协商逻辑（docs/37 W2）。

**为什么这些测试值得写**：W2 的整个价值是"区分相机**请求**到什么和**实际**协商到什么"。
真机只会给你一个结果（本机 720p30），而"相机拒绝 60 时日志会不会说谎"这种事
只有在假设备上才能穷举。这里注入假 cv2，跑真代码路径。

铁律 1 的口径：假设备注入**不是**伪造数据 —— 它验证的是"我们的代码怎么读真设备"，
所有关于相机能力的结论仍然只能来自真机（见 `docs/39`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import capture  # noqa: E402


class FakeCap:
    """够用的假 VideoCapture：记录 set 顺序，回报固定 get 值。"""

    def __init__(self, values=None, *, opened=True) -> None:
        self.values = dict(values or {})
        self.sets: list[tuple[int, float]] = []
        self.opened = opened
        self.released = False

    def set(self, prop, value):  # noqa: D401 - 与 cv2 同名即可
        self.sets.append((prop, value))
        return True

    def get(self, prop):
        return self.values.get(prop, 0)

    def isOpened(self):
        return self.opened

    def release(self):
        self.released = True


class FakeCv2:
    """只放 capture.py 真正用到的常量/函数。"""

    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_FOURCC = 6
    CAP_MSMF = 1400
    CAP_V4L2 = 200

    def __init__(self, cap) -> None:
        self._cap = cap
        self.calls: list[tuple[int, int | None]] = []

    def VideoCapture(self, index, api=None):  # noqa: N802 - 与 cv2 同名
        self.calls.append((index, api))
        return self._cap

    @staticmethod
    def VideoWriter_fourcc(*chars):  # noqa: N802 - 与 cv2 同名
        value = 0
        for i, ch in enumerate(chars):
            value |= ord(ch) << (8 * i)
        return value


def test_parse_uvc_index_accepts_prefix_and_rejects_bare_index():
    assert capture.parse_uvc_index("uvc:0") == 0
    assert capture.parse_uvc_index("UVC:2") == 2
    assert capture.parse_uvc_index("  uvc:7  ") == 7
    # 裸序号**不是** uvc 源（走老的 camera# 分支），不能悄悄当成 uvc
    assert capture.parse_uvc_index("0") is None
    assert capture.parse_uvc_index("data/raw/blink.mp4") is None


def test_parse_uvc_index_rejects_non_numeric():
    with pytest.raises(ValueError):
        capture.parse_uvc_index("uvc:camera")
    with pytest.raises(ValueError):
        capture.parse_uvc_index("uvc:")


@pytest.mark.parametrize("value, expected", [
    (0, None),
    (0x16, None),                       # MSMF 实测的无意义 fourcc
    (0xFFFF, None),                     # 不可打印
    (0x47504A4D, "MJPG"),               # 'M','J','P','G' 小端
    (0x32315659, "YV12"),
])
def test_fourcc_str_only_returns_printable(value, expected):
    assert capture.fourcc_str(value) == expected


def test_open_uvc_capture_sets_fourcc_before_geometry():
    """顺序有讲究：先 MJPG 再尺寸 —— 有些 UVC 驱动只在 MJPG 下开放 720p60。"""
    cap = FakeCap({3: 1280, 4: 720, 5: 30.0, 6: 0x16})
    cv2_like = FakeCv2(cap)

    got, info = capture.open_uvc_capture(cv2_like, 0, width=1280, height=720, fps=60.0)

    assert got is cap
    props = [p for p, _ in cap.sets]
    assert props == [FakeCv2.CAP_PROP_FOURCC,
                     FakeCv2.CAP_PROP_FRAME_WIDTH,
                     FakeCv2.CAP_PROP_FRAME_HEIGHT,
                     FakeCv2.CAP_PROP_FPS]
    assert cap.sets[0][1] == 0x47504A4D          # 请求的是 MJPG
    assert cap.sets[3][1] == 60.0


def test_open_uvc_capture_reports_negotiated_values_not_requested():
    """相机把 60 降到 30 时，info 必须**同时**留住请求值与协商值。"""
    cap = FakeCap({3: 1280, 4: 720, 5: 30.0, 6: 0})
    _, info = capture.open_uvc_capture(FakeCv2(cap), 0, width=1280, height=720, fps=60.0)

    assert info["requested"] == {"w": 1280, "h": 720, "fps": 60.0, "fourcc": "MJPG"}
    assert info["negotiated"]["fps"] == 30.0
    assert info["negotiated"]["fourcc"] is None   # 读不到就是 None，不许编


def test_open_uvc_capture_survives_fourcc_set_failure():
    """驱动不支持设 FOURCC 不该让整条链路起不来，但必须有 warning。"""

    class PickyCap(FakeCap):
        def set(self, prop, value):
            if prop == FakeCv2.CAP_PROP_FOURCC:
                raise OSError("driver says no")
            return super().set(prop, value)

    cap = PickyCap({3: 640, 4: 480, 5: 30.0})
    got, info = capture.open_uvc_capture(FakeCv2(cap), 1, width=1280, height=720, fps=60.0)
    assert got is cap
    assert (info["negotiated"]["w"], info["negotiated"]["h"]) == (640, 480)


def test_open_uvc_capture_falls_back_to_default_backend_when_constant_missing():
    class NoApiCv2(FakeCv2):
        CAP_MSMF = None
        CAP_V4L2 = None

    cv2_like = NoApiCv2(FakeCap({3: 640, 4: 480, 5: 30.0}))
    capture.open_uvc_capture(cv2_like, 0, width=640, height=480, fps=30.0)
    assert cv2_like.calls == [(0, None)]


def test_describe_uvc_flags_fallback_explicitly():
    _, info = capture.open_uvc_capture(
        FakeCv2(FakeCap({3: 1280, 4: 720, 5: 30.0, 6: 0x16})), 0,
        width=1280, height=720, fps=60.0,
    )
    text = capture.describe_uvc(info)
    assert "1280x720@30.0" in text
    assert "已回落" in text           # 请求 60、协商 30 ⇒ 必须写出来
    assert "不可读" in text           # fourcc 读不到也要写出来，不能编


def test_describe_uvc_has_no_fallback_mark_when_mode_matches():
    _, info = capture.open_uvc_capture(
        FakeCv2(FakeCap({3: 1280, 4: 720, 5: 60.0, 6: 0x47504A4D})), 0,
        width=1280, height=720, fps=60.0,
    )
    text = capture.describe_uvc(info)
    assert "已回落" not in text
    assert "fourcc=MJPG" in text


def test_uvc_branch_raises_helpful_error_when_camera_missing():
    """打不开时必须给"怎么枚举设备"，而不是抛一个 OpenCV 的静默 False。"""
    import types

    fake = types.ModuleType("cv2")
    for name in ("CAP_PROP_FRAME_WIDTH", "CAP_PROP_FRAME_HEIGHT", "CAP_PROP_FPS",
                 "CAP_PROP_FOURCC", "CAP_MSMF", "CAP_V4L2"):
        setattr(fake, name, getattr(FakeCv2, name))
    fake.VideoCapture = lambda index, api=None: FakeCap(opened=False)  # noqa: E731
    fake.VideoWriter_fourcc = FakeCv2.VideoWriter_fourcc

    original_have = capture._have_cv2
    capture._have_cv2 = lambda: True
    sys.modules["cv2"] = fake
    try:
        with pytest.raises(RuntimeError, match="check_uvc_camera.py --list"):
            capture.open_frame_source("uvc:9")
    finally:
        sys.modules.pop("cv2", None)
        capture._have_cv2 = original_have


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="MSMF 只在 Windows 上存在")
def test_uvc_backend_is_msmf_on_windows():
    api, name = capture._uvc_backend(FakeCv2(None))
    assert (api, name) == (FakeCv2.CAP_MSMF, "MSMF")
