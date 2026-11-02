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
        rep.add("环境", "依赖 pyserial", INFO, "未安装 —— Python 串口工具不可用；现有板端 PowerShell 串口工具不依赖它")
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
    # 年龄档案旁路：schema 铁律 + 基线纪律 + 数据库 provenance + 耦合模型方向 + 跨语言校验。
    # 它与 check_frontend_contract 是同一个思路，只是对象换成了"年龄档案件"。
    ("check_age_sidecar.py", "年龄档案旁路（年龄档案/收录/同龄基线比对 + Python↔JS 校验一致）"),
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
    # C8 上板自检的**离线口径自检**（2026-10-01 新增）。为什么值得进回归：
    # 上板脚本以前只能 py_compile，口径对不对要等板子到手才知道；而它的期望值全部来自
    # regmap（档位化后会随 VIGILENS_TIER 变）。这条先把"帧尺寸/灰度尺寸/单帧字节数"三者
    # 的自洽验掉，免得像 tb_rgb2gray.cpp Layer 1 那样**写死 3/5、看着自洽、其实错**。
    ("board/bringup_check.py", "--selftest"),
    # C10 软硬件比对的**离线数据自检**（2026-10-01 新增）：验"黄金参考与当前档位是否配套"。
    # 为什么值得进回归：拿错档位的黄金参考会在板子上表现成"PL 算错了"，实际是数据配错 ——
    # 本项目已经踩过三次同类（tb Layer1 写死 3/5、rgb2gray cosim 3/5 数据跑 3/8、
    # fir_filter 45 Hz 数据跑 30 Hz 系数表）。本自检能**离线**把这种配错抓出来。
    ("board/hw_sw_compare.py", "--selftest"),
    # C9 DMA 测试的**离线自检**（2026-10-01 新增）：钉住确定性测试图的**具体字节** ——
    # 门限 7 的"两次长跑逐位一致"用的就是这张图；图若悄悄变了，会变成"两次都一样地错"、
    # **照样报 PASS**。另外验 --golden-gray 是否与当前档位 GRAY_PIXELS 配套。
    ("board/dma_test.py", "--selftest"),
    # 无 PYNQ 环境的 /dev/mem mmap 加载器的**离线自检**（2026-10 新增）：用匿名 mmap 模拟 /dev/mem，
    # 验 MmapMMIO 32bit 读写、页内偏移、越界拒绝、地址映射校验（缺项/重叠/范围/对齐）、
    # load_mmap 与 load_overlay 的 read_reg/write_reg 复用、以及 .hwh 解析与 instance→logical 映射。
    ("board/overlay/mmap_overlay.py", "--selftest"),
    # USB(UVC) 相机验收工具的**离线自检**（2026-10-23 新增，docs/37 §5 W1）：用假设备注入跑
    # MJPEG 切帧、帧间隔分位、`v4l2-ctl --list-formats-ext` 解析、720p60 判定、亮度频谱峰检测
    # 与 JPEG 解码基准的真逻辑。
    # 为什么值得进回归：这段解析的结论会直接写成"这颗相机支不支持 720p60"，
    # 解析器写错 = **采购判据错**，而且错得很安静（少认一档 fps 就把合格相机判成不合格）。
    # 它不需要相机、不需要板卡，本机天天能跑。
    ("metrics/scripts/check_uvc_camera.py", "--selftest"),
    # rPPG 时间基准的**受控实验**（2026-10-23 新增，台账 BUG-029 / docs/37 R14）：
    # 同一段 1.2 Hz 正弦，按 30 / 30.14 / 13 / 8 / 3.7 fps 五种"真实采样率"喂进去，
    # 断言"容差内必须报 72 bpm、容差外必须 null"。脚本自带验收断言，不合判据就退 1。
    # 为什么值得进回归：这条口径错的时候**不会报错，只会报出一个看起来正常的错数**
    # （13 fps 下 72 → 166 bpm、conf 0.9998），没有任何日志会提醒你。
    ("metrics/scripts/probe_rppg_timebase.py", None),
)


def check_tools(rep: Report, py: str) -> None:
    print("[3/6] 工具自检 ...")
    for entry in TOOL_SELFTESTS:
        script, flag = entry[0], entry[1]
        shells = entry[2] if len(entry) > 2 else None
        if shells:
            shell = next((shutil.which(s) for s in shells if shutil.which(s)), None)
            if not shell:
                rep.add("工具自检", f"{script} {flag or ''}".strip(), SKIP,
                        f"本机没有 {' / '.join(shells)}，跳过")
                continue
            args = [shell, "-NoProfile", "-File", script] + ([flag] if flag else [])
        else:
            args = [py, script] + ([flag] if flag else [])
        code, out = run(args, timeout=180)
        rep.add("工具自检", f"{script} {flag or ''}".strip(), PASS if code == 0 else FAIL,
                last_meaningful(out, 1) if out else f"退出码 {code}")


# ---------------------------------------------------------------------------
# [4] 语法 / 静态检查
# ---------------------------------------------------------------------------

SYNTAX_TARGETS = (
    "backend/api.py", "backend/run_pipeline.py",
    "board/bringup_check.py", "board/dma_test.py", "board/hw_sw_compare.py",
    "board/overlay/load_overlay.py",
    "board/overlay/mmap_overlay.py",
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

        # preview.js 的坐标换算自检：**含 1280×720 一档**。它红了意味着
        # "720p 画的定位框和画面错位"——页面不会报错，只会把框画歪，所以必须机器钉住。
        code, out = run([node, "frontend/preview.js"], timeout=60)
        rep.add("语法", "frontend/preview.js 坐标换算自检（含 720p）",
                PASS if code == 0 else FAIL,
                last_meaningful(out, 3) or ("OK" if code == 0 else "自检失败"))


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
    ("Mizar-Z7020（板卡）", "python board/s4_bench/s4_run.py init（JTAG + xsdb；断电后先跑这一条）",
     "PS/PL 是否活着、DDR 能否读写 —— S4 九道门限的第一步"),
    ("Mizar-Z7020（720p 档）", "python board/s4_bench/s4_720.py recm / stream",
     "720p 档灰度链路逐字节对拍 + 每帧耗时"),
    ("真实摄像头（UVC）", "metrics/scripts/run_demo.py --list 找序号，再 --source <序号>",
     "真实帧喂 A 线；必须确认 landmark_source=mediapipe"),
    ("Vitis HLS", "call D:\\Xilinx\\2026.1\\Vitis\\settings64.bat；vitis-run --mode hls --tcl fpga\\run_hls.tcl",
     "各 HLS IP 的 csim/csynth/cosim"),
    ("板端 Buildroot Linux / USB→PL", "见 board/w7_buildroot/DIRECT_LOOP.md；已有独立板端原始日志",
     "真实摄像头取帧、JPEG 解码、DMA/PL 对拍；不由本离线总入口重复操作硬件"),
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
    print("提示：本次离线回归未执行硬件项；已有板端结论请查 metrics/evidence/ 与 fpga/report/，见 [6/6]。")
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
