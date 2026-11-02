"""test_bbox_semantics.py —— 人脸框覆盖什么，以及额头 ROI 到底落在哪（BUG-010 / BUG-011）。

这两条是同一根链条上的两环：
    `face_landmark.detect()` 算出的 `bbox` → `vital.forehead_roi()` 取框顶那一块 → rPPG 的绿通道样本。
旧实现里 `bbox` 只框"眼角→嘴角"，框高只有整张脸的约 63%，于是"额头 ROI"落在眼睛上，
采到的绿通道被眨眼污染 —— 这就是 BUG-011（P1）。

本文件只用**纯函数 + 合成关键点**，不需要 mediapipe、不需要视频、不需要板卡：
框的语义是几何定义问题，给定点就给定答案。
"""

from __future__ import annotations

import pytest

from backend.face_landmark import MP_FACE_OVAL, bbox_from_points
from backend.vital import forehead_roi, roi_channel_sum


# ---------------------------------------------------------------------------
# 一、面部轮廓点集本身
# ---------------------------------------------------------------------------
def test_face_oval_is_a_unique_index_set() -> None:
    """轮廓点必须是去重的、点数固定的点号集合（改这张表就是改框的定义）。"""
    assert len(MP_FACE_OVAL) == 36
    assert len(set(MP_FACE_OVAL)) == 36
    # 两个"必须在内"的锚点：10 = 额头顶，152 = 下巴尖（BUG-007 的教训：别拿内部点当边缘）
    assert 10 in MP_FACE_OVAL
    assert 152 in MP_FACE_OVAL
    # 左右脸缘极值点在官方轮廓里
    assert 234 in MP_FACE_OVAL and 454 in MP_FACE_OVAL


# ---------------------------------------------------------------------------
# 二、外接框的几何
# ---------------------------------------------------------------------------
def test_bbox_covers_forehead_and_chin() -> None:
    """框必须同时包住额头顶与下巴尖 —— 这是 BUG-010 的验收口径。"""
    pts = [(160, 100), (100, 150), (220, 150), (120, 380), (200, 380), (160, 400)]
    x, y, w, h = bbox_from_points(pts, 640, 480, pad_frac=0.0)
    assert (x, y) == (100, 100)
    assert x + w == 220 and y + h == 400
    # 额头顶 (160,100) 与下巴 (160,400) 都落在框内（半开区间）
    for px, py in ((160, 100), (160, 399)):
        assert x <= px < x + w and y <= py < y + h


def test_bbox_height_is_most_of_the_face_not_the_eye_mouth_band() -> None:
    """旧框（眼角→嘴角）只占整脸高度的约 63%；新框必须覆盖 ≥ 90%。"""
    forehead_y, eye_y, mouth_y, chin_y = 100, 250, 330, 400
    full = [(100, forehead_y), (220, forehead_y), (100, chin_y), (220, chin_y)]
    x, y, w, h = bbox_from_points(full, 640, 480, pad_frac=0.0)
    assert h == chin_y - forehead_y
    old_h = int((mouth_y - eye_y) + 0.15 * (mouth_y - eye_y))
    assert h / old_h >= 1.4, "新框应当明显高于旧的眼-口框"


def test_bbox_clamps_to_image_and_handles_degenerate_points() -> None:
    """贴着画面边缘的点集要被裁进画面；退化点集返回 (0,0,0,0)。"""
    x, y, w, h = bbox_from_points([(-50, -50), (700, 500)], 640, 480, pad_frac=0.0)
    assert (x, y) == (0, 0)
    assert x + w <= 640 and y + h <= 480
    assert bbox_from_points([], 640, 480) == (0, 0, 0, 0)
    assert bbox_from_points([(10, 10), (10, 10)], 640, 480) == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# 三、额头 ROI 必须避开眼睛（BUG-011 的直接判据）
# ---------------------------------------------------------------------------
def test_forehead_roi_lands_above_the_eyes() -> None:
    """整脸框 (100,100,200,300)：额头带必须整体在眼睛（约 40%~50% 高）之上。"""
    x0, y0, x1, y1 = forehead_roi((100, 100, 200, 300), 640, 480)
    eye_band_top = 100 + int(300 * 0.40)
    assert y1 <= eye_band_top, f"额头 ROI 底边 {y1} 压到眼睛带（{eye_band_top}）"
    # 宽度取中间一半，左右各留 25%
    assert (x0, x1) == (150, 250)


def test_forehead_roi_contains_no_eye_landmark() -> None:
    """用一张合成"脸"验证：ROI 里不能出现任何眼睛/瞳孔关键点。"""
    import numpy as np

    img = np.zeros((480, 640, 3), dtype=np.uint8)
    # 整脸框（媒体管轮廓外接框的典型比例：宽 200、高 300）
    bbox = (100, 100, 200, 300)
    roi = forehead_roi(bbox, 640, 480)
    # 眼睛在整脸框 40% 高处；把 ROI 内像素标成 200，然后检查眼睛点所在行
    img[roi[1]:roi[3], roi[0]:roi[2], 1] = 200
    eye_y = 100 + int(300 * 0.42)
    assert img[eye_y, :, :].sum() == 0, "眼睛所在行不应被额头 ROI 覆盖"
    # 而且 ROI 确实非空（否则上面的断言会因为"什么都没画"而假通过）
    assert roi_channel_sum(img, roi, 1)[1] > 0


def test_forehead_roi_follows_bbox_that_includes_forehead() -> None:
    """同一个人，旧框（眼-口）与新框（整脸）应给出**不同**的 ROI —— 且新框的 ROI 更高。"""
    # 旧框：只到眼角上方一点点
    old_roi = forehead_roi((120, 240, 160, 190), 640, 480)
    # 新框：额头到下巴
    new_roi = forehead_roi((100, 100, 200, 300), 640, 480)
    assert new_roi[1] < old_roi[1], "整脸框的额头带应当比眼-口框更高"
    assert new_roi[3] < old_roi[1], "新框的 ROI 底边应当仍低于旧框顶边"


def test_forehead_roi_clamped_and_empty_for_invalid_bbox() -> None:
    """越界框裁进画面；无效框给空 ROI（与 roi_statistic 语义一致）。"""
    a = forehead_roi((600, 460, 100, 100), 640, 480)
    assert 0 <= a[0] <= a[2] <= 640 and 0 <= a[1] <= a[3] <= 480
    assert forehead_roi((10, 10, 0, 50), 640, 480) == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# BUG-011 的正解：额头采样区由**额头关键点**决定，不由整脸框推
# ---------------------------------------------------------------------------

def _face_points(eye_y: float, brow_y: float) -> dict[int, tuple[float, float]]:
    """一张"正脸、yaw=0"的合成关键点表：额头点在高处，眼睛在 eye_y。"""
    return {
        10: (320.0, 100.0),      # 额顶
        9: (320.0, brow_y),      # 眉间
        151: (320.0, brow_y),
        8: (320.0, brow_y + 1),
        107: (250.0, brow_y + 4),  # 左眉外端
        336: (390.0, brow_y + 4),  # 右眉外端
        296: (355.0, brow_y + 2),
        334: (285.0, brow_y + 2),
        # 眼睛（不应被采到）
        33: (250.0, eye_y), 133: (300.0, eye_y),
        362: (340.0, eye_y), 263: (390.0, eye_y),
        468: (275.0, eye_y), 473: (365.0, eye_y),
    }


def test_forehead_roi_from_points_excludes_eye_landmarks() -> None:
    """额头 ROI 必须只覆盖眉线以上；眼睛/瞳孔点一个都不能落在里面。"""
    from backend.face_landmark import MP_FOREHEAD, forehead_roi_from_points

    pts = _face_points(eye_y=210.0, brow_y=175.0)
    roi = forehead_roi_from_points([pts[i] for i in MP_FOREHEAD], 640, 480)
    assert roi is not None
    for i in (33, 133, 362, 263, 468, 473):
        x, y = pts[i]
        assert not (roi[0] <= x < roi[2] and roi[1] <= y < roi[3]), f"关键点 {i} 落在了额头 ROI 里"


def test_forehead_roi_from_points_beats_the_bbox_band_on_a_turned_head() -> None:
    """实测几何（blink.mp4 首帧，yaw≈50°）：框推法会吃到瞳孔，关键点法不会。

    数据来自真人视频探针：整脸框 (216,101,218,294)，右瞳孔 473 在 (364.4,177.1)，
    而额头关键点落在 y≈110~168 —— 框的"上 30%"（y 124~189）正好把瞳孔包进去，
    这就是 BUG-011 在斜头时的真实现场（51/60 帧命中）。
    """
    from backend.face_landmark import MP_FOREHEAD, forehead_roi_from_points
    from backend.vital import forehead_roi

    pts = {
        10: (310.4, 110.0), 9: (320.0, 150.0), 151: (320.0, 151.0), 8: (320.0, 152.0),
        107: (250.0, 168.0), 336: (390.0, 166.0), 296: (355.0, 164.0), 334: (285.0, 163.0),
    }
    iris = (364.4, 177.1)  # 473

    roi = forehead_roi_from_points([pts[i] for i in MP_FOREHEAD], 640, 480)
    assert roi is not None
    assert not (roi[0] <= iris[0] < roi[2] and roi[1] <= iris[1] < roi[3]), "关键点 ROI 不该吃到瞳孔"

    band = forehead_roi((216, 101, 218, 294), 640, 480)       # 旧写法：整脸框上 30%
    assert band[0] <= iris[0] < band[2] and band[1] <= iris[1] < band[3], \
        "对照组（框推法）应当被瞳孔点命中，否则这条测试证明不了两者有区别"


def test_forehead_roi_from_points_degenerate_returns_none() -> None:
    """退化点集（空 / 宽高为 0 / 缩到 MIN_ROI_PX 以下）一律 None，由调用方回退或丢弃该帧。"""
    from backend.face_landmark import forehead_roi_from_points

    assert forehead_roi_from_points([], 640, 480) is None
    same = [(100.0, 100.0)] * 8
    assert forehead_roi_from_points(same, 640, 480) is None
    tiny = [(100.0, 100.0), (104.0, 100.0), (100.0, 104.0)]     # 4×4，缩完仍不足 8 px
    assert forehead_roi_from_points(tiny, 640, 480) is None


def test_face_observation_carries_forehead_and_defaults_to_none() -> None:
    """`FaceObservation.forehead` 默认 None（stub 检测器不产它），pipeline 才知道要回退。"""
    from backend.face_landmark import FaceObservation

    obs = FaceObservation(visible=1.0, bbox=(1, 2, 3, 4), pose={})
    assert obs.forehead is None
