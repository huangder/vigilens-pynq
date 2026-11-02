"""probe_pose_solver.py —— 头部姿态求解器对照（对应 docs/16 BUG-007 的真机复现补充）。

**它回答什么**：`backend/face_landmark.py:_pose_from_landmarks` 在**正脸正立**的画面上，
为什么解出 `yaw ≈ 175°`（照理应该 ≈ 0°）？

**做法**：同一帧、同一组 FaceMesh 关键点、同一套 3D 模型点、同一个相机内参，
**只换 `cv2.solvePnP` 的求解标志**，并把两种欧拉角分解都打出来：
  · 代码式（`face_landmark.py` 现在用的三个 atan2）
  · classic learnopencv 的 `rotationMatrixToEulerAngles`（x=pitch, y=yaw, z=roll）

需要 `data/raw/*.mp4`（**不入库**）。没有素材时会明确报错并退出码 2。

用法：  .venv\\Scripts\\python.exe metrics\\scripts\\probe_pose_solver.py [视频路径] [帧号]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_VIDEO = REPO_ROOT / "data" / "raw" / "blink.mp4"
DEFAULT_FRAME = 60

# 与 backend/face_landmark.py 逐字一致（模型点 + 关键点下标）
MODEL = np.array([
    [0.0, 0.0, 0.0], [0.0, -63.6, -12.5], [-43.3, 32.7, -26.0],
    [43.3, 32.7, -26.0], [-28.9, -28.9, -24.1], [28.9, -28.9, -24.1],
], dtype="double")
IDX = [1, 199, 33, 263, 61, 291]


def code_angles(rvec) -> tuple[float, float, float]:
    """`face_landmark.py` 现在的取法：pitch=atan2(-R20,sy)、yaw=atan2(R10,R00)、roll=atan2(R21,R22)。"""
    rmat, _ = cv2.Rodrigues(rvec)
    sy = math.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
    return (round(math.degrees(math.atan2(-rmat[2, 0], sy)), 2),
            round(math.degrees(math.atan2(rmat[1, 0], rmat[0, 0])), 2),
            round(math.degrees(math.atan2(rmat[2, 1], rmat[2, 2])), 2))


def classic_angles(rvec) -> tuple[float, float, float]:
    """classic learnopencv：x=pitch、y=yaw、z=roll。"""
    rmat, _ = cv2.Rodrigues(rvec)
    sy = math.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
    return (round(math.degrees(math.atan2(rmat[2, 1], rmat[2, 2])), 2),
            round(math.degrees(math.atan2(-rmat[2, 0], sy)), 2),
            round(math.degrees(math.atan2(-rmat[1, 0], rmat[0, 0])), 2))


def main(argv: list[str]) -> int:
    video = Path(argv[1]) if len(argv) > 1 else DEFAULT_VIDEO
    frame_no = int(argv[2]) if len(argv) > 2 else DEFAULT_FRAME
    if not video.exists():
        print(f"[FAIL] 找不到素材 {video}（data/raw/*.mp4 不入库，需自行准备一段正脸视频）。",
              file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(str(video))
    img = None
    for _ in range(frame_no + 1):
        ok, img = cap.read()
        if not ok:
            print(f"[FAIL] 视频读不到第 {frame_no} 帧（素材太短？）", file=sys.stderr)
            cap.release()
            return 2
    cap.release()
    h, w = img.shape[:2]

    mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=1,
                                           refine_landmarks=True)
    res = mesh.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    if not res.multi_face_landmarks:
        print("[FAIL] 这一帧没检出人脸，换一帧再试。", file=sys.stderr)
        return 2
    lm = res.multi_face_landmarks[0].landmark

    img_pts = np.array([[lm[i].x * w, lm[i].y * h] for i in IDX], dtype="double")
    focal = w                                    # 与 face_landmark.py 一致
    cam = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1]], dtype="double")

    print(f"{video.name} 第 {frame_no} 帧 {w}x{h}（请人工确认该帧是正脸正立）")
    print(f"{'求解标志':<28}{'代码式 p/y/r':<30}{'classic x/y/z':<30}")
    for name, flag in (("SOLVEPNP_ITERATIVE（当前代码）", cv2.SOLVEPNP_ITERATIVE),
                       ("SOLVEPNP_EPNP", cv2.SOLVEPNP_EPNP),
                       ("SOLVEPNP_SQPNP", cv2.SOLVEPNP_SQPNP)):
        try:
            ok, rvec = cv2.solvePnP(MODEL, img_pts, cam, np.zeros((4, 1)), flags=flag)[:2]
        except cv2.error as e:
            print(f"{name:<28}cv2.error: {str(e).splitlines()[-1][:50]}")
            continue
        print(f"{name:<28}{str(code_angles(rvec)):<30}{str(classic_angles(rvec)):<30}  ok={ok}")
    print("\n判读：ITERATIVE 落到镜像解（yaw≈175°）；换 EPNP/SQPNP 后 yaw≈0 附近。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
