"""check_b_line_thresholds.py —— B 线门控阈值「一处定义」的守卫检查。

为什么需要它：前端是静态页面、读不到 yaml，`app.js` 里必然留一份 `THRESHOLDS` 离线兜底副本。
这份副本与 `config.yaml` 的同步是**静默失效**的 —— 谁在 yaml 里改了键名（例如把
`light_score_min` 写成 `min_light_score`），后端下发的字典就少了那个键，前端收不到、
默默退回旧副本，于是**该报警却不报警**，而且没有任何报错。

检查项（4 条）：
    1. `config.yaml` 的 8 个阈值键，`/api/status.thresholds` 一个不少（键名逐字一致）
    2. 下发的**数值**与 `config.yaml` 一致（不是下发了个默认值）
    3. `app.js` 里 `THRESHOLDS` 的键 ⊆ 下发键（前端要用的，后端必须都给）
    4. 内置副本的数值与 `config.yaml` 一致（离线兜底也要是对的）

用法（仓库根）：
    python metrics/scripts/check_b_line_thresholds.py
退出码：0 = 4 项全过；1 = 有失败；2 = 服务没起来。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

# 与 api.py 的 thresholds() 保持同一份键表（改了那边要改这里 —— 这条断言本身就是检查的一部分）
KEYS = (
    "perclos_warning", "face_visible_min", "quality_min_score", "light_score_min",
    "motion_score_max", "vital_require_quality", "fatigue_long_close_count",
    "ws_disconnect_timeout_s",
)


def _js_threshold_keys_and_values() -> tuple[set[str], dict[str, float]]:
    """从 app.js 里抠出 THRESHOLDS 的键与数值（不执行 JS，只做文本解析）。"""
    js = (REPO_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"var THRESHOLDS = \{(.*?)\n  \};", js, re.S)
    if not m:
        return set(), {}
    keys: set[str] = set()
    vals: dict[str, float] = {}
    for line in m.group(1).splitlines():
        # 末行可能没有尾逗号（如 fatigue_long_close_count 那行）——逗号必须可选，
        # 否则会静默少统计一个键，检查自己制造假阴性
        mm = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(-?[0-9.]+)\s*,?", line)
        if mm:
            keys.add(mm.group(1))
            vals[mm.group(1)] = float(mm.group(2))
    return keys, vals


def _wait_server(base: str, timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urlopen(base + "/api/status", timeout=5) as r:  # noqa: S310
                if json.loads(r.read().decode("utf-8")).get("ok") is True:
                    return True
        except (URLError, OSError, ValueError):
            pass
        time.sleep(0.3)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B 线门控阈值「一处定义」守卫检查")
    ap.add_argument("--port", type=int, default=8898)
    args = ap.parse_args(argv)

    from config import load_config

    cfg = load_config()
    base = f"http://127.0.0.1:{args.port}"
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "backend/api.py", "--no-mock", "--port", str(args.port)],
        cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    try:
        if not _wait_server(base):
            print(f"[FAIL] api.py 没起来（{base}）")
            return 2
        with urlopen(base + "/api/status", timeout=5) as r:  # noqa: S310
            served = (json.loads(r.read().decode("utf-8")).get("thresholds") or {})

        missing = [k for k in KEYS if k not in served]
        if missing:
            failures.append(f"第 1 项：/api/status.thresholds 缺键 {missing}")
        else:
            print(f"  1) 8 个阈值键齐全：{', '.join(KEYS)}")

        wrong = [f"{k}: 下发 {served.get(k)!r} ≠ config {cfg.get(k)!r}"
                 for k in KEYS if k in served and served[k] != cfg.get(k)]
        if wrong:
            failures.append("第 2 项：下发值与 config.yaml 不一致 —— " + "；".join(wrong))
        else:
            print("  2) 下发值与 config.yaml 逐项一致")

        js_keys, js_vals = _js_threshold_keys_and_values()
        if not js_keys:
            failures.append("第 3 项：没能从 app.js 里解析出 THRESHOLDS（模板被改写了？）")
        else:
            lack = sorted(js_keys - set(served))
            if lack:
                failures.append(f"第 3 项：前端要用、后端却没下发的键：{lack}")
            else:
                print(f"  3) 前端用到的 {len(js_keys)} 个键后端都下发了")
            diff = {k: (js_vals[k], cfg.get(k)) for k in js_vals if k in cfg and js_vals[k] != cfg.get(k)}
            if diff:
                failures.append(f"第 4 项：离线兜底副本与 config.yaml 不一致 {diff}")
            else:
                print("  4) 离线兜底副本与 config.yaml 一致")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if failures:
        print("[FAIL] B 线阈值守卫检查未通过：")
        for f in failures:
            print("   -", f)
        return 1
    print("B 线阈值守卫检查：通过 —— 4/4（config.yaml → /api/status → app.js 兜底，三处一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
