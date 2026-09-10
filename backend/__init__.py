"""backend/__init__.py —— A 线 / B 线 Python 包标记。

存在意义：
  - 让 `python -m backend.mock`、`pytest` 能按包方式 import（相对 import 生效）；
  - 同时各模块保留 `try: from .x import y / except ImportError: from x import y` 兜底，
    使得 `python backend/mock.py` 这种"零配置直接跑"的方式同样可行 ——
    三个人的机器环境不一定一样，入口越少越好。

⚠️ 本文件**不要**放 import 副作用（不要在这里 import cv2/mediapipe）。
"""
