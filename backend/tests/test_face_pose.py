"""test_face_pose.py —— 头部姿态的**已知答案**回归（BUG-006 / BUG-007）。

为什么用"合成关键点"而不是真实视频：
    这两个 bug 的根因是**坐标系基准与轴标签**，不是"数值不好看"。只要拿一个
    已知姿态的 3D 脸模型投影出关键点，喂进同一个函数，答案就是确定的 ——
    不需要相机、不需要人脸视频、不需要 mediapipe（只用 numpy + OpenCV），
    因此这条回归**在 CI/任何机器上都能跑**。

历史（都在这一个函数里，改它之前先看 `docs/16_测试问题台账.md`）：
  · BUG-006：模型点写成"Y 朝上"（相机是 Y 朝下）→ 基准整体偏 180°，
    正立正面脸解出 `roll ≈ ±180°`，让 98% 的帧被判成"头姿异常"；
  · BUG-007：下巴用了索引 **199**（内部点）而不是 **152**（脸轮廓上的下巴尖）；
    且 `yaw / pitch / roll` 三个分解式子**贴错了标签** → 转头显示在 pitch、歪头显示在 yaw。
"""

from __future__ import annotations

import math

import pytest

cv2 = pytest.importorskip("cv2", reason="姿态回归需要 OpenCV（合成关键点用）")
np = pytest.importorskip("numpy")

from backend.face_landmark import MediaPipeLandmarker  # noqa: E402

W, H = 640, 480


class _LM:
    """mediapipe landmark 的最小替身：只需要归一化的 x / y。"""

    __slots__ = ("x", "y")

    def __init__(self, x: float = 0.5, y: float = 0.5) -> None:
        self.x, self.y = x, y


def _landmarks(*, yaw_deg: float = 0.0, pitch_deg: float = 0.0, roll_deg: float = 0.0,
               scale: int = 1) -> list[_LM]:
    """按 R = Rz(roll)·Ry(yaw)·Rx(pitch) 造一个"已知姿态"的脸，投影成关键点。"""
    model = np.array(MediaPipeLandmarker._POSE_MODEL, dtype="double")
    w, h = W * scale, H * scale
    cam = np.array([[float(w), 0, w / 2.0], [0, float(w), h / 2.0], [0, 0, 1]], dtype="double")
    rz = cv2.Rodrigues(np.array([[0.0, 0.0, math.radians(roll_deg)]]))[0]
    ry = cv2.Rodrigues(np.array([[0.0, math.radians(yaw_deg), 0.0]]))[0]
    rx = cv2.Rodrigues(np.array([[math.radians(pitch_deg), 0.0, 0.0]]))[0]
    pts, _ = cv2.projectPoints(model, cv2.Rodrigues(rz @ ry @ rx)[0],
                               np.array([[0.0], [0.0], [600.0]]), cam, np.zeros((4, 1)))
    out = [_LM() for _ in range(478)]
    for k, idx in enumerate(MediaPipeLandmarker._POSE_IDX):
        out[idx] = _LM(float(pts[k][0][0]) / w, float(pts[k][0][1]) / h)
    return out


def _pose(**kw) -> dict:
    return MediaPipeLandmarker._pose_from_landmarks(_landmarks(**kw), W, H)


# ---------------------------------------------------------------------------
# BUG-006：基准不能偏 180°
# ---------------------------------------------------------------------------

def test_upright_frontal_face_reads_zero() -> None:
    """正立、正对镜头的人脸必须是 (0, 0, 0)。修之前这里是 roll ≈ ±180°。"""
    p = _pose()
    assert p == {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}, p


@pytest.mark.parametrize("angle", [-20.0, -10.0, -5.0, 5.0, 10.0, 20.0])
def test_in_plane_tilt_shows_up_in_roll(angle: float) -> None:
    """歪头（面内旋转）只能出现在 `roll` 上，且符号一致。"""
    p = _pose(roll_deg=angle)
    assert p["roll"] == pytest.approx(angle, abs=0.5), p
    assert abs(p["yaw"]) < 0.5 and abs(p["pitch"]) < 0.5, p


@pytest.mark.parametrize("angle", [-20.0, -10.0, 10.0, 20.0])
def test_head_turn_shows_up_in_yaw(angle: float) -> None:
    """转头（绕竖直轴）只能出现在 `yaw` 上。修之前它跑到了 `pitch` 上。"""
    p = _pose(yaw_deg=angle)
    assert p["yaw"] == pytest.approx(angle, abs=0.5), p
    assert abs(p["roll"]) < 0.5 and abs(p["pitch"]) < 0.5, p


@pytest.mark.parametrize("angle", [-15.0, -7.5, 7.5, 15.0])
def test_nod_shows_up_in_pitch(angle: float) -> None:
    """点头（绕水平轴）只能出现在 `pitch` 上。"""
    p = _pose(pitch_deg=angle)
    assert p["pitch"] == pytest.approx(angle, abs=0.5), p
    assert abs(p["yaw"]) < 0.5 and abs(p["roll"]) < 0.5, p


# ---------------------------------------------------------------------------
# BUG-007：不许在两个分支之间跳变、不许出现"脸在相机背后"的解
# ---------------------------------------------------------------------------

def test_pose_is_continuous_across_sweep() -> None:
    """连续转头时角度必须**连续**：修之前同一段视频会在 176° / −2° 之间来回跳。"""
    prev = _pose(yaw_deg=-20.0)
    for step in range(-18, 21, 2):
        cur = _pose(yaw_deg=float(step))
        for axis in ("yaw", "pitch", "roll"):
            assert abs(cur[axis] - prev[axis]) < 5.0, f"yaw={step}° 时 {axis} 跳变：{prev} → {cur}"
        assert all(abs(cur[a]) <= 90.0 for a in ("yaw", "pitch", "roll")), \
            f"yaw={step}° 解出了镜像支：{cur}"
        prev = cur


def test_every_solved_pose_is_in_front_of_the_camera() -> None:
    """所有解出来的姿态都必须在相机前方（`tvec[2] > 0`）—— 镜像解不允许被返回。

    注：不能靠"把模型放到 Z<0 再投影"来构造反例（负 Z 的点投影出来与它的镜像重合，
    求解器照样会给出一个合法的正 Z 解）。所以这里改成断言**性质**：扫描姿态时 Z 恒为正。
    """
    model = np.array(MediaPipeLandmarker._POSE_MODEL, dtype="double")
    cam = np.array([[float(W), 0, W / 2.0], [0, float(W), H / 2.0], [0, 0, 1]], dtype="double")
    checked = 0
    for yaw in (-25.0, -10.0, 0.0, 10.0, 25.0):
        for pitch in (-15.0, 0.0, 15.0):
            lms = _landmarks(yaw_deg=yaw, pitch_deg=pitch, roll_deg=5.0)
            pts = np.array([[lms[i].x * W, lms[i].y * H] for i in MediaPipeLandmarker._POSE_IDX])
            solved = MediaPipeLandmarker._solve_head_pose(pts, cam)
            assert solved is not None, f"yaw={yaw} pitch={pitch} 应能解出姿态"
            assert float(solved[1][2][0]) > 0.0, "解出了相机背后的镜像解"
            checked += 1
    assert checked == 15


def test_degenerate_landmarks_return_zeros_instead_of_garbage() -> None:
    """6 个点重合（检测塌了）时返回 0，不抛异常、也不返回垃圾角度。"""
    lms = [_LM() for _ in range(478)]
    for idx in MediaPipeLandmarker._POSE_IDX:
        lms[idx] = _LM(0.5, 0.5)
    assert MediaPipeLandmarker._pose_from_landmarks(lms, W, H) == {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}


def test_reproj_guard_is_resolution_independent() -> None:
    """同一个姿态在**不同分辨率**下都必须被接受（护栏不能把姿态清成 0）。

    背景（实测踩到的坑）：护栏原来用**像素**阈值 `pose_reproj_max_px = 8.0`，
    而真人素材（720×1280）的平均重投影误差中位是 **10.4 px** ⇒ 94.4% 的帧被拦下、
    姿态全变成 (0,0,0) —— 表面看"姿态很完美"，实际是没在测。
    改成"误差 / 双眼外角间距"的**比值**后与分辨率无关。
    """
    base = _pose(yaw_deg=12.0, roll_deg=6.0)
    for scale in (1, 2, 4):
        scaled = MediaPipeLandmarker._pose_from_landmarks(
            _landmarks(yaw_deg=12.0, roll_deg=6.0, scale=scale), W * scale, H * scale)
        assert scaled["yaw"] == pytest.approx(base["yaw"], abs=0.5), f"scale={scale} 时被护栏误杀"
        assert scaled["roll"] == pytest.approx(base["roll"], abs=0.5), f"scale={scale} 时被护栏误杀"
    assert abs(base["yaw"]) > 5.0, "这个姿态本该测出明显角度，全 0 说明被护栏清掉了"


def test_reference_model_uses_the_real_chin_and_camera_axes() -> None:
    """模型本身的护栏：下巴必须是 152（不是 199），且 Y 轴朝下（相机系）。"""
    assert MediaPipeLandmarker._POSE_IDX[1] == 152, "下巴索引必须是 152（199 只是内部点）"
    chin_y = MediaPipeLandmarker._POSE_MODEL[1][1]
    chin_z = MediaPipeLandmarker._POSE_MODEL[1][2]
    assert chin_y > 0, "相机系里 Y 朝下：下巴的 Y 必须为正（写成负数就又偏 180° 了）"
    assert chin_z > 0 and MediaPipeLandmarker._POSE_MODEL[0][2] == 0.0, \
        "鼻尖在最前（Z 最小）、下巴在后，否则模型是反的"
