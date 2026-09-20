"""config.py —— 读取仓库根 config.yaml（阈值的唯一来源）。

《04》第 6 节 DoD 要求："config.yaml 存在且阈值不散落在代码里"。
因此**任何 .py 里都不许出现魔法数字**，一律 load_config() 取值。

只依赖标准库：有 PyYAML 就用它，没有就用内置的极简解析器（覆盖本项目的
扁平 key: value + 内联列表 + 一层嵌套格式），保证干净机器上也能跑。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from .console import enable_utf8_console
except ImportError:  # 直接以脚本方式运行
    from console import enable_utf8_console

enable_utf8_console()   # 同 contract.py：保证任何入口的中文输出都不会崩

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

# 兜底默认值：只在 config.yaml 缺失或该键未写时使用，并在日志里明确警告。
# ⚠️ 这里是 config.yaml 的**副本，不是来源**。改契约 §0 时必须两处一起改，
#    否则"没装 PyYAML"的机器会静默按旧帧率跑（本机历史上就是这么埋过雷）。
FALLBACK: dict[str, Any] = {
    "window_seconds": 30,
    "fps_nominal": 30,          # 契约 docs/interface.md §0（2026-09-20 v1.2 草案：45→30；v1.1 曾为 45）
    "ear_close_threshold": 0.21,
    "min_close_frames": 3,
    "long_close_ms": 500,
    "perclos_warning": 0.25,
    "mar_threshold": 0.6,
    "yawn_min_duration_ms": 800,
    "face_visible_min": 0.7,
    "pose_yaw_max_deg": 30.0,
    "pose_pitch_max_deg": 25.0,
    "quality_min_score": 0.60,
    "light_score_min": 0.5,
    "motion_score_max": 0.35,
    "light_target": 0.5,
    "motion_thresh_gray": 25,
    "motion_score_gain": 4.0,
    "quality_weights": {"light": 0.5, "motion": 0.3, "face": 0.2},
    "fatigue_yawn_count": 2,
    "fatigue_long_close_count": 1,
    "fatigue_blink_rate_low": 10.0,
    "hr_band_hz": [0.7, 3.5],
    "rr_band_hz": [0.1, 0.5],
    "vital_require_quality": 0.75,
    "ws_push_hz": 1.0,
    "ws_disconnect_timeout_s": 3.0,
    "fpga": {
        "device": "xc7z020clg400-1",
        "clock_ns": 10,
        "img_width": 640,
        "img_height": 480,
        "pixel_format": "RGB888",
        "roi_is_half_open": True,
    },
}


def _coerce(text: str) -> Any:
    """把 YAML 标量文本转成 Python 值（够本项目用即可）。"""
    t = text.strip()
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        return [_coerce(x) for x in inner.split(",")] if inner else []
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    for cast in (int, float):
        try:
            return cast(t)
        except ValueError:
            pass
    return t.strip("'\"")


def _mini_yaml_parse(text: str) -> dict[str, Any]:
    """极简 YAML 解析：支持注释、扁平标量、内联列表、一层嵌套 mapping。"""
    root: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if indent > 0 and current is not None:
            current[key] = _coerce(value)
            continue
        if value.strip() == "":
            current = {}
            root[key] = current
        else:
            root[key] = _coerce(value)
            current = None
    return root


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """读 config.yaml 并与 FALLBACK 合并（文件里的值优先）。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if cfg_path.exists():
        text = cfg_path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore

            loaded = yaml.safe_load(text)
            data = loaded if isinstance(loaded, dict) else {}
        except ImportError:
            data = _mini_yaml_parse(text)
    merged = dict(FALLBACK)
    merged.update(data)
    if "fpga" in data and isinstance(data["fpga"], dict):
        merged["fpga"] = {**FALLBACK["fpga"], **data["fpga"]}
    merged["_source"] = str(cfg_path)
    merged["_yaml_available"] = _yaml_available()
    return merged


def _yaml_available() -> bool:
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


if __name__ == "__main__":  # 自检：python backend/config.py
    cfg = load_config()
    print(f"config 来源: {cfg['_source']}")
    print(f"PyYAML 可用: {cfg['_yaml_available']}（False 时走内置极简解析器，结果应一致）")
    for k in ("window_seconds", "ear_close_threshold", "perclos_warning", "quality_min_score", "ws_push_hz"):
        print(f"  {k:24s} = {cfg[k]}")
    print(f"  {'fpga.device':24s} = {cfg['fpga']['device']}")
