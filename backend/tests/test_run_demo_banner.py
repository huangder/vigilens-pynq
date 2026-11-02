"""`run_demo.py` 的横幅必须从**实际状态**推导（docs/16 BUG-001 / BUG-002）。

为什么单独测：横幅是给人看的"操作说明"，它说错方向比程序报错更浪费时间 ——
BUG-001 是"合成源下仍宣称画面在推"，BUG-002 是"默认值写死、config.yaml 改了它不说"。
两个缺陷都落在同一段纯文本上，所以把文案抽成纯函数（`video_plan` /
`banner_video_lines` / `footer_video_lines`）后在这里钉死。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "metrics" / "scripts" / "run_demo.py"


def _load_run_demo():
    spec = importlib.util.spec_from_file_location("run_demo_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_demo = _load_run_demo()

CFG = {"video_push_hz": 12.5, "video_jpeg_quality": 71, "video_max_width": 800}


def _banner(plan: dict, base: str = "http://127.0.0.1:8123") -> str:
    return "\n".join(run_demo.banner_video_lines(plan, base))


def _footer(plan: dict) -> str:
    return "\n".join(run_demo.footer_video_lines(plan))


# ---- BUG-001：合成帧源说"关闭"，不能出现 video.mjpg 的承诺 -----------------


def test_synthetic_source_banner_does_not_promise_picture():
    plan = run_demo.video_plan(source="synthetic", no_video=False, cfg=CFG, synthetic=True)
    text = _banner(plan)
    assert plan["state"] == "off_synthetic"
    assert "video.mjpg" not in text          # 原来就是这一行在说谎
    assert "关闭" in text
    assert "占位" in text
    assert "占位" in _footer(plan)
    assert "video.mjpg" not in _footer(plan)


def test_no_video_flag_still_wins_and_says_so():
    plan = run_demo.video_plan(source="data/raw/blink.mp4", no_video=True,
                               cfg=CFG, synthetic=False)
    assert plan["state"] == "off_flag"
    assert "video.mjpg" not in _banner(plan)
    assert "--no-video" in _banner(plan)


def test_real_source_banner_shows_url_and_actual_params():
    plan = run_demo.video_plan(source="data/raw/blink.mp4", no_video=False,
                               cfg=CFG, synthetic=False)
    assert plan["state"] == "on"
    text = _banner(plan)
    assert "http://127.0.0.1:8123/video.mjpg" in text
    assert "12.5 Hz" in text and "q71" in text and "最长边 800" in text
    assert "video.mjpg" in _footer(plan)


# ---- BUG-002：默认值来自 config.yaml，CLI 覆盖优先 -------------------------


def test_defaults_come_from_config_yaml_not_hardcoded():
    cfg = run_demo.load_config()
    assert cfg["_source"].endswith("config.yaml")
    plan = run_demo.video_plan(source="data/raw/blink.mp4", no_video=False,
                               cfg=cfg, synthetic=False)
    assert plan["hz"] == float(cfg["video_push_hz"])
    assert plan["quality"] == int(cfg["video_jpeg_quality"])
    assert plan["max_width"] == int(cfg["video_max_width"])


def test_changing_config_changes_banner():
    """BUG-002 的验收动作：把 config 的 video_max_width 改成别的值 → 横幅跟着变。"""
    cfg = dict(run_demo.load_config())
    cfg["video_max_width"] = 1024
    plan = run_demo.video_plan(source="data/raw/blink.mp4", no_video=False,
                               cfg=cfg, synthetic=False)
    assert "最长边 1024" in _banner(plan)


def test_cli_overrides_beat_config():
    plan = run_demo.video_plan(source="data/raw/blink.mp4", no_video=False, cfg=CFG,
                               synthetic=False, video_hz=25, video_quality=60,
                               video_max_width=480)
    text = _banner(plan)
    assert "25 Hz" in text and "q60" in text and "最长边 480" in text
    assert "q71" not in text and "最长边 800" not in text


# ---- 判定只有一份：横幅与管线必须用同一个"合成帧源"判定 -------------------


@pytest.mark.parametrize("source", ["synthetic", "demo", "0", "uvc:0",
                                    "data/raw/blink.mp4", "mjpeg:http://127.0.0.1:1/video.mjpg"])
def test_banner_and_capture_agree_on_synthetic(source):
    from capture import will_be_synthetic

    plan = run_demo.video_plan(source=source, no_video=False, cfg=CFG)  # synthetic=None → 走公共判定
    assert plan["synthetic"] == will_be_synthetic(source)
    assert plan["state"] == ("off_synthetic" if will_be_synthetic(source) else "on")


def test_will_be_synthetic_true_for_synthetic_and_demo():
    from capture import will_be_synthetic

    assert will_be_synthetic("synthetic") is True
    assert will_be_synthetic("demo") is True
    assert will_be_synthetic("SYNTHETIC") is True
