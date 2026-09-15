"""check_a_line_all.py —— A 线自检入口（一条命令跑完，可进 CI）。

为什么需要它：
    `backend/README.md` 的"怎么跑"一列有十来条散落命令（pytest、9 个模块自检、
    端到端回放、黄金参考对拍……），提交前逐条敲既费事又容易漏。
    本脚本把它们串成一次执行，并给出一个**总退出码**，让"A 线是否健康"变成一条命令。

覆盖四段：
    [A] 仓库四项自检（`AGENTS.md` §6.1）
    [B] `backend/` 9 个模块自检
    [C] 端到端合成回放 + **重复运行逐字节一致**（《02》A3 的验收口径）
    [D] P1 黄金参考对拍（5 项，A↔C 口径）

用法（仓库根）：
    . .\\env.ps1
    python metrics/scripts/check_a_line_all.py
退出码：0 = 全部通过；1 = 有失败项。缺 node 时 [A] 的 JS 两项会标 SKIP（不算失败，但会提示）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "metrics" / "scripts"
LOGS = REPO_ROOT / "metrics" / "logs"

MODULES = ["config.py", "decision.py", "mock.py", "capture.py", "face_landmark.py",
           "behavior_metrics.py", "quality.py", "storage.py", "contract.py"]


def resolve_python() -> str:
    """解释器一律优先用项目 `.venv\\Scripts\\python.exe`。

    原因：`.tools\\python312` 是 **embeddable** 版（带 `python312._pth`），它**不会**把脚本
    所在目录加进 `sys.path`，于是 `python backend/config.py` 这类"零配置直接跑"的入口
    会全线报 `ModuleNotFoundError`。`.venv` 是 virtualenv 建的，行为正常，
    并且经 `_vigilens_base.pth` 桥接到基础环境的 site-packages，依赖一个不少。
    """
    venv_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv_py) if venv_py.exists() else sys.executable


def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    """执行命令，返回 (退出码, 合并输出)。"""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(REPO_ROOT), timeout=timeout)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def last_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1].strip() if lines else "(无输出)"


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    py = resolve_python()
    node = shutil.which("node")
    results: list[tuple[str, str, bool, str]] = []      # (段, 名称, 通过, 摘要)
    skipped: list[str] = []
    t0 = time.time()

    print("=== A 线自检（一条命令跑完）===")
    print(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}   Python：{sys.version.split()[0]}")
    print(f"解释器：{py}")
    print()

    # ---------- [A] 仓库四项自检 ------------------------------------------
    print("[A] 仓库四项自检（AGENTS.md 6.1）")
    code, out = run([py, "-m", "pytest", "-q"])
    results.append(("A", "pytest", code == 0, last_line(out)))
    print(f"    {'[PASS]' if code == 0 else '[FAIL]'} pytest  -> {last_line(out)}")

    if node:
        code, out = run([node, "frontend/mock.js", "--selftest"])
        ok = code == 0 and '"ok": true' in out
        results.append(("A", "mock.js --selftest", ok, '"ok": true' if ok else last_line(out)))
        print(f"    {'[PASS]' if ok else '[FAIL]'} mock.js --selftest  -> {'ok: true' if ok else last_line(out)}")

        code, out = run([py, "metrics/scripts/check_frontend_wiring.py"])
        results.append(("A", "check_frontend_wiring", code == 0, last_line(out)))
        print(f"    {'[PASS]' if code == 0 else '[FAIL]'} check_frontend_wiring  -> {last_line(out)}")

        js_out = LOGS / "_selftest_js_frames.jsonl"
        with js_out.open("w", encoding="utf-8") as fh:
            subprocess.run([node, "frontend/mock.js", "--limit", "6"], stdout=fh,
                           stderr=subprocess.DEVNULL, cwd=str(REPO_ROOT), check=False)
        code, out = run([py, "metrics/scripts/check_frontend_contract.py", str(js_out)])
        results.append(("A", "check_frontend_contract", code == 0, last_line(out)))
        print(f"    {'[PASS]' if code == 0 else '[FAIL]'} check_frontend_contract  -> {last_line(out)}")
    else:
        for nm in ("mock.js --selftest", "check_frontend_wiring", "check_frontend_contract"):
            skipped.append(nm)
        print("    [SKIP] 前端三项 —— node 不在 PATH（先执行 . .\\env.ps1）")

    # ---------- [B] backend 模块自检 --------------------------------------
    print()
    print(f"[B] backend/ 模块自检（{len(MODULES)} 个）")
    bad = []
    for m in MODULES:
        code, out = run([py, str(REPO_ROOT / "backend" / m)])
        ok = code == 0
        results.append(("B", m, ok, "退出码 0" if ok else last_line(out)))
        print(f"    {'[PASS]' if ok else '[FAIL]'} {m}")
        if not ok:
            bad.append(m)
    if bad:
        print(f"    失败详情（{len(bad)} 个）：")
        for m in bad:
            code, out = run([py, str(REPO_ROOT / "backend" / m)])
            print(f"      {m}: {last_line(out)}")

    # ---------- [C] 端到端 + 重复运行一致性 --------------------------------
    print()
    print("[C] 端到端合成回放 + 重复运行逐字节一致")
    runs = {}
    for tag in ("a", "b"):
        cmd = [py, "backend/run_pipeline.py", "--source", "synthetic", "--pattern", "blink",
               "--seconds", "30", "--stub", "--quiet",
               "--json", str(LOGS / f"_selftest_{tag}.json"),
               "--csv", str(LOGS / f"_selftest_{tag}.csv"),
               "--summary", str(LOGS / f"_selftest_{tag}_summary.json")]
        code, out = run(cmd)
        runs[tag] = (code, out)
        if code != 0:
            results.append(("C", f"端到端运行 {tag}", False, last_line(out)))
            print(f"    [FAIL] 端到端运行 {tag} -> {last_line(out)}")

    if all(runs[t][0] == 0 for t in runs):
        ja = (LOGS / "_selftest_a.json").read_bytes()
        jb = (LOGS / "_selftest_b.json").read_bytes()
        ca = (LOGS / "_selftest_a.csv").read_bytes()
        cb = (LOGS / "_selftest_b.csv").read_bytes()
        same = (ja == jb) and (ca == cb)
        summary = json.loads((LOGS / "_selftest_a_summary.json").read_text(encoding="utf-8"))
        results.append(("C", "端到端一致性", same,
                        f"末帧 JSON {'相同' if ja == jb else '不同'} / CSV {'相同' if ca == cb else '不同'}"))
        print(f"    {'[PASS]' if same else '[FAIL]'} 末帧 JSON {'逐字节相同' if ja == jb else '**不同**'}，"
              f"CSV {'逐字节相同' if ca == cb else '**不同**'}")
        print(f"    处理帧数 {summary.get('frames_processed')}，"
              f"逻辑时长 {summary.get('logical_duration_s')}s，"
              f"关键点来源 {summary.get('landmark_source')}，"
              f"状态分布 {summary.get('status_counts')}")

    # ---------- [D] P1 黄金参考对拍 ----------------------------------------
    print()
    print("[D] P1 A<->C 黄金参考对拍（5 项）")
    code, out = run([py, str(SCRIPTS / "check_a_line_p1_all.py")])
    results.append(("D", "P1 黄金参考对拍", code == 0, last_line(out)))
    for ln in out.splitlines():
        # 连"前置：测试向量缺失，已按固定 seed 重新生成"一起回显 ——
        # 否则会自动往 fpga/sim/data*/ 写出十几 MB 生成物却一句都不说。
        if ln.strip().startswith(("[PASS]", "[FAIL]", "前置", "[OK]", "[SKIP]")):
            print("    " + ln.strip())
    print(f"    {'[PASS]' if code == 0 else '[FAIL]'} {last_line(out)}")

    # ---------- 总结论 ------------------------------------------------------
    failed = [(seg, name, why) for seg, name, ok, why in results if not ok]
    elapsed = time.time() - t0
    print()
    print("=" * 62)
    if skipped:
        print(f"  [SKIP] {len(skipped)} 项：{', '.join(skipped)}"
              + ("（node 不在 PATH，先执行 . .\\env.ps1）" if len(skipped) == 3 else ""))
    if failed:
        print(f"A 线自检未通过：{len(failed)} 项失败（共 {len(results)} 项，用时 {elapsed:.1f}s）")
        for seg, name, why in failed:
            print(f"  [{seg}] {name}: {why}")
        return 1
    print(f"A 线自检全部通过：{len(results)} 项（用时 {elapsed:.1f}s）"
          + (f"，另有 {len(skipped)} 项跳过" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
