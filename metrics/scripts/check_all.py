"""check_all.py —— 项目总测试入口：一条命令跑完全部**可离线执行**的检查。

它和 `check_a_line_all.py` 的分工：
    · `check_a_line_all.py` 是 **A 线内部**的入口（pytest / 模块自检 / 确定性 / 黄金参考 / M2）
    · 本脚本是**项目级**入口：在它之上再覆盖 B 线专项、旁路画面、各工具自检，
      并把「回归测试」与「就绪状态」**分开报**，最后给出一个总退出码。

⚠️ **两类检查必须分开，否则总入口永远是红的**：
    · **回归测试**（必须通过）：代码/契约行为，坏了就是坏了 → 计入退出码
    · **就绪状态**（只报告）：缺素材、缺板卡、缺工具链，是"还没到那一步"而不是"坏了"
      → **不计入退出码**。典型例子：`check_p4_readiness` 在没有 4 段真实视频时返回 1，
        那是**预期状态**，不是回归失败。

⚠️ 本脚本**不产生任何性能数字**，只是把已有检查串起来跑并汇总。

用法（仓库根）：
    .venv\\Scripts\\python.exe metrics/scripts/check_all.py
    .venv\\Scripts\\python.exe metrics/scripts/check_all.py --fast     # 跳过最慢的 M2 端到端
退出码：0 = 全部回归通过；1 = 有回归失败。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"


def resolve_python() -> str:
    venv_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv_py) if venv_py.exists() else sys.executable


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, section: str, name: str, status: str, detail: str = "") -> None:
        self.rows.append((section, name, status, detail))


def _child_env() -> dict:
    """给子进程的环境变量。

    为什么动这个：mediapipe 会 `import matplotlib`，而 Windows 沙箱下 matplotlib 的默认
    缓存目录不可写，于是**每个子进程都往 stderr 吐一段 atexit 噪声**。
    噪声本身无害，但混在测试输出里很容易被误读成"测试失败了"。
    指到本项目自己的可写目录（`metrics/logs/` 不入库）。
    """
    env = os.environ.copy()
    env["MPLCONFIGDIR"] = str(REPO_ROOT / "metrics" / "logs" / "_mplcache")
    return env


def run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(REPO_ROOT), timeout=timeout,
                       env=_child_env())
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def last_meaningful(out: str, n: int = 3) -> str:
    """取输出里最后几行有内容的行，压成一行，便于放进表格。"""
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return " | ".join(lines[-n:])[:200]


# ---------------------------------------------------------------------------
# [0] 环境
# ---------------------------------------------------------------------------

def check_env(rep: Report, py: str) -> None:
    print("[0/6] 环境与依赖 ...")
    import platform
    rep.add("环境", "Python", INFO, f"{platform.python_version()} @ {py}")
    for mod in ("cv2", "numpy", "mediapipe", "yaml", "fastapi", "uvicorn", "pytest"):
        try:
            m = importlib.import_module(mod)
            rep.add("环境", f"依赖 {mod}", PASS, getattr(m, "__version__", "?"))
        except Exception as e:  # noqa: BLE001
            rep.add("环境", f"依赖 {mod}", FAIL if mod in ("numpy",) else INFO,
                    f"未安装（{type(e).__name__}）")
    try:
        import serial  # noqa: F401
        rep.add("环境", "依赖 pyserial", PASS, "串口模式可用")
    except Exception:  # noqa: BLE001
        rep.add("环境", "依赖 pyserial", INFO, "未安装 —— 只影响 OpenMV 串口模式，UVC 模式不受影响")
    node = shutil.which("node")
    rep.add("环境", "node", PASS if node else SKIP,
            (node or "未找到 —— JS 两项检查会被跳过"))


# ---------------------------------------------------------------------------
# [1] A 线总入口（含 pytest / 模块自检 / 确定性 / 黄金参考 / M2）
# ---------------------------------------------------------------------------

def check_a_line(rep: Report, py: str, timeout: int) -> None:
    print("[1/6] A 线总入口 check_a_line_all.py ...")
    t0 = time.time()
    code, out = run([py, "metrics/scripts/check_a_line_all.py"], timeout=timeout)
    dt = time.time() - t0
    ok = code == 0
    rep.add("A 线", "check_a_line_all（A~E 五段）", PASS if ok else FAIL,
            f"退出码 {code}，用时 {dt:.1f}s | {last_meaningful(out, 2)}")


# ---------------------------------------------------------------------------
# [2] B 线专项（A 线总入口没覆盖的三项）
# ---------------------------------------------------------------------------

B_LINE_CHECKS = (
    ("check_b_line_thresholds.py", "B 线门控阈值一处定义 → /api/status → 前端兜底三处一致"),
    ("check_b_line_ws_disconnect.py", "B 线 /ws 断流兜底（发合法契约的 disconnected，序号不倒退）"),
    ("check_video_bypass.py", "旁路画面链路 + 契约未被污染（15 项）"),
)


def check_b_line(rep: Report, py: str, timeout: int) -> None:
    print("[2/6] B 线专项 ...")
    for script, desc in B_LINE_CHECKS:
        t0 = time.time()
        code, out = run([py, f"metrics/scripts/{script}"], timeout=timeout)
        rep.add("B 线", desc, PASS if code == 0 else FAIL,
                f"退出码 {code}，用时 {time.time() - t0:.1f}s | {last_meaningful(out, 1)}")


# ---------------------------------------------------------------------------
# [3] 工具自检（不依赖硬件）
# ---------------------------------------------------------------------------

TOOL_SELFTESTS = (
    ("backend/contract.py", None),
    ("backend/config.py", None),
    ("backend/publish.py", None),
    ("board/regmap.py", "--selftest"),
    ("board/openmv/vigilens_link.py", "--selftest"),
    ("board/openmv/host_capture_test.py", "--selftest"),
    ("board/openmv/raw_to_contract.py", "--selftest"),
)


def check_tools(rep: Report, py: str) -> None:
    print("[3/6] 工具自检 ...")
    for script, flag in TOOL_SELFTESTS:
        args = [py, script] + ([flag] if flag else [])
        code, out = run(args, timeout=180)
        rep.add("工具自检", f"{script} {flag or ''}".strip(), PASS if code == 0 else FAIL,
                last_meaningful(out, 1) if out else f"退出码 {code}")


# ---------------------------------------------------------------------------
# [4] 语法 / 静态检查
# ---------------------------------------------------------------------------

SYNTAX_TARGETS = (
    "backend/api.py", "backend/run_pipeline.py",
    "board/openmv/openmv_stream.py", "board/openmv/openmv_capture_test.py",
    "board/bringup_check.py", "board/dma_test.py", "board/hw_sw_compare.py",
    "board/overlay/load_overlay.py",
)


def check_syntax(rep: Report, py: str) -> None:
    print("[4/6] 语法检查 ...")
    bad = []
    for f in SYNTAX_TARGETS:
        code, out = run([py, "-c",
                         "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())", f],
                        timeout=60)
        if code != 0:
            bad.append(f)
    rep.add("语法", f"{len(SYNTAX_TARGETS)} 个 Python 目标可解析",
            PASS if not bad else FAIL, "全部 OK" if not bad else f"失败：{bad}")

    node = shutil.which("node")
    if node:
        code, out = run([node, "--check", "frontend/app.js"], timeout=60)
        rep.add("语法", "frontend/app.js（node --check）", PASS if code == 0 else FAIL,
                last_meaningful(out, 1) or "OK")


# ---------------------------------------------------------------------------
# [5] 就绪状态（**不计入退出码**）
# ---------------------------------------------------------------------------

def check_readiness(rep: Report, py: str) -> None:
    print("[5/6] 就绪状态（只报告，不计失败）...")

    code, out = run([py, "metrics/scripts/check_p4_readiness.py"], timeout=120)
    rep.add("就绪", "P4 标定素材（4 视频 + 4 标注）",
            INFO, "已就绪" if code == 0 else last_meaningful(out, 2))

    bit = REPO_ROOT / "board" / "bitstream"
    bits = list(bit.glob("*.bit")) + list(bit.glob("*.hwh")) if bit.is_dir() else []
    rep.add("就绪", "board/bitstream/ 里的 bitstream", INFO,
            f"找到 {len(bits)} 个文件" if bits else
            "**为空** —— PL 上板的一切结论都不存在（先按 board/README.md 跑 build_bd.tcl）")

    vitis = Path(r"D:\Xilinx\2026.1\Vitis\bin\vitis-run.bat")
    rep.add("就绪", "Vitis HLS 工具链", INFO,
            "已安装" if vitis.exists() else
            r"未找到 D:\Xilinx\2026.1\Vitis —— csim/csynth/cosim 需在完整权限终端跑")

    data_raw = REPO_ROOT / "data" / "raw"
    vids = [p.name for p in data_raw.glob("*") if p.suffix.lower() in
            (".mp4", ".avi", ".mov", ".mkv")] if data_raw.is_dir() else []
    rep.add("就绪", "data/raw/ 真实视频", INFO,
            f"{len(vids)} 个：{vids}" if vids else "为空 —— 真实视频相关的结论尚无法产出")


# ---------------------------------------------------------------------------
# [6] 需硬件 / 需人工（本机跑不了，给出确切命令）
# ---------------------------------------------------------------------------

HARDWARE_ITEMS = (
    ("OpenMV Cam H7", "board/openmv/openmv_capture_test.py（MODE=\"matrix\"，在 OpenMV IDE 里跑）",
     "采集能力矩阵：真实分辨率/帧率/内存"),
    ("Mizar-Z7020（PS）", "接 USB-UART 开 115200 8N1 看 console；Vivado Hardware Manager 认 JTAG",
     "PS bring-up：板子是否活、是否有 PYNQ 环境"),
    ("Mizar-Z7020（PL）", "Vivado 建最小工程：board/openmv/pl_uart_echo.v + mizar_z7_openmv_uart.xdc",
     "PL 首次上板：1 kHz 测频反证 50 MHz 时钟 + UART 字节回环"),
    ("真实摄像头（UVC）", "metrics/scripts/run_demo.py --list 找序号，再 --source <序号>",
     "真实帧喂 A 线；必须确认 landmark_source=mediapipe"),
    ("Vitis HLS", "call D:\\Xilinx\\2026.1\\Vitis\\settings64.bat；vitis-run --mode hls --tcl fpga\\run_hls.tcl",
     "四个 IP 的 csim/csynth/cosim"),
)


def print_manual(rep: Report) -> None:
    print("\n[6/6] 需硬件 / 需人工的项（本机跑不了，命令已给全）")
    for name, how, what in HARDWARE_ITEMS:
        print(f"    · {name}\n        怎么做: {how}\n        能证明什么: {what}")


def main() -> int:
    ap = argparse.ArgumentParser(description="VigiLens 项目总测试入口")
    ap.add_argument("--json", default=None, help="把结果写成 JSON（便于归档）")
    ap.add_argument("--timeout", type=int, default=900, help="单个检查的超时秒数")
    args = ap.parse_args()

    py = resolve_python()
    print("=" * 82)
    print("VigiLens / 知倦 —— 项目总测试入口（只跑可离线执行的部分）")
    print(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}   解释器：{py}")
    print("=" * 82 + "\n")

    rep = Report()
    t0 = time.time()
    check_env(rep, py)
    check_a_line(rep, py, args.timeout)
    check_b_line(rep, py, args.timeout)
    check_tools(rep, py)
    check_syntax(rep, py)
    check_readiness(rep, py)
    print_manual(rep)
    dt = time.time() - t0

    # ---- 汇总 ----
    sections: list[str] = []
    for r in rep.rows:
        if r[0] not in sections:
            sections.append(r[0])

    print("\n" + "=" * 82)
    print("汇总")
    print("=" * 82)
    only = [s for s in sections if s not in ("就绪",)]
    for sec in only:
        rows = [r for r in rep.rows if r[0] == sec]
        bad = [r for r in rows if r[2] == FAIL]
        print(f"\n[{sec}] {len(rows) - len(bad)}/{len(rows)} 通过")
        for _s, name, status, detail in rows:
            mark = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP", INFO: "INFO"}[status]
            print(f"  [{mark}] {name}")
            if detail and status in (FAIL, INFO, SKIP):
                print(f"          {detail}")

    ready = [r for r in rep.rows if r[0] == "就绪"]
    print("\n[就绪状态]（**不计入退出码**：缺素材/缺板卡是「还没到那一步」，不是「坏了」）")
    for _s, name, _st, detail in ready:
        print(f"  · {name}: {detail}")

    n_fail = sum(1 for r in rep.rows if r[2] == FAIL)
    n_pass = sum(1 for r in rep.rows if r[2] == PASS)
    print("\n" + "=" * 82)
    print(f"回归结论：{'✅ 全部通过' if n_fail == 0 else f'❌ 有 {n_fail} 项失败'}"
          f"（PASS {n_pass} / FAIL {n_fail}，用时 {dt:.1f}s）")
    print("提示：硬件相关项本机跑不了，见上面 [6/6]；它们的结论目前**一个都不存在**。")
    print("=" * 82)

    if args.json:
        p = Path(args.json)
        if not p.is_absolute():
            p = REPO_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "elapsed_s": round(dt, 1), "passed": n_pass, "failed": n_fail,
             "rows": [{"section": s, "name": n, "status": st, "detail": d}
                      for s, n, st, d in rep.rows]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果 JSON：{p}")

    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
