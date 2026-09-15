"""check_a_line_p1_all.py —— P1（A↔C 黄金参考对拍）一键复跑 + 证据归档。

为什么需要它：
    按《05》铁律 1，每个写进记录与报告的数字都必须能追到真实运行。
    P1 的结论要进 report/llm_log/ 与契约 5.2 的会签，若手工把 5 个脚本的输出抄进 JSON，
    既费事又容易抄错。所以这里做成一键复跑：真实执行 5 个对拍脚本，采集真实退出码与
    真实输出，写入 metrics/evidence/ 的 JSON。**证据文件没有被手工编辑过**，它是本命令的产物。

覆盖的契约章节：
    4.4 灰度 + 缩放口径      -> check_gray_formula.py
    4.2 roi_statistic 黄金   -> check_roi_golden.py
    3.3 motion_quality 黄金  -> check_motion_golden.py
    4.6 Q1.15 量化口径       -> check_q15_golden.py
    4.5 fir_filter 逐样本    -> check_fir_golden.py

前置（向量由固定 seed 生成，可随时重建）：
    python fpga/sim/gen_frames.py
    python fpga/sim/gen_motion_vectors.py
    python fpga/sim/gen_fir_vectors.py

用法（仓库根）：
    python metrics/scripts/check_a_line_p1_all.py              # 只跑 + 写 metrics/logs/（不入库）
    python metrics/scripts/check_a_line_p1_all.py --evidence   # 额外把结果**归档**到 metrics/evidence/
退出码：0 = 5 项全部通过；1 = 有任意一项未通过（结果 JSON 仍会写出，便于定位）。

⚠️ 为什么要 `--evidence` 开关：
    证据文件带运行时间戳，**每次跑都会产生不同内容**。若无条件写进 `metrics/evidence/`（已入库），
    那么"提交前自检"本身就会把工作区搞脏，`git status` 再也分不清哪些是自己的改动。
    所以：日常自检只写 `metrics/logs/`（`.gitignore` 已覆盖），
    需要**归档某一次运行**时才加 `--evidence`，然后把那个文件一并提交。
"""

from __future__ import annotations

import json
import platform
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
EVIDENCE = REPO_ROOT / "metrics" / "evidence"

CHECKS = [
    ("灰度+缩放口径", "契约 4.4", "check_gray_formula.py"),
    ("roi_statistic 黄金参考", "契约 4.2", "check_roi_golden.py"),
    ("motion_quality 黄金参考", "契约 3.3", "check_motion_golden.py"),
    ("Q1.15 量化口径", "契约 4.6", "check_q15_golden.py"),
    ("fir_filter 逐样本", "契约 4.5", "check_fir_golden.py"),
]


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    results = []

    print("=== A 线 P1：A<->C 黄金参考对拍（一键复跑）===")
    print(f"时间：{started}")
    print()

    for name, contract_ref, script in CHECKS:
        path = SCRIPTS / script
        r = subprocess.run([sys.executable, str(path)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", cwd=str(REPO_ROOT))
        out = (r.stdout or "").strip()
        summary = out.splitlines()[-1].strip() if out else "(无输出)"
        mark = "[PASS]" if r.returncode == 0 else "[FAIL]"
        print(f"  {mark} {name:<24} [{contract_ref}]  退出码 {r.returncode}")
        print(f"         {summary}")
        results.append({
            "name": name,
            "contract_section": contract_ref,
            "script": f"metrics/scripts/{script}",
            "command": f"python metrics/scripts/{script}",
            "exit_code": r.returncode,
            "passed": r.returncode == 0,
            "summary": summary,
            "stdout": out,
            "stderr": (r.stderr or "").strip(),
        })

    all_passed = all(item["passed"] for item in results)
    try:
        import numpy
        numpy_version = numpy.__version__
    except ImportError:
        numpy_version = None

    payload = {
        "artifact": "A 线 P1：A<->C 黄金参考对拍结果",
        "generated_by": "metrics/scripts/check_a_line_p1_all.py",
        "generated_at": started,
        "note": "本文件由上述命令真实执行产生，未手工编辑；数字均为本机真实运行输出。",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": numpy_version,
        },
        "prerequisites": [
            "python fpga/sim/gen_frames.py",
            "python fpga/sim/gen_motion_vectors.py",
            "python fpga/sim/gen_fir_vectors.py",
        ],
        "all_passed": all_passed,
        "checks": results,
    }

    # 默认只写 metrics/logs/（不入库）；加 --evidence 才归档到 metrics/evidence/。
    # 理由见文件头：带时间戳的结果每次跑都不同，无条件写已入库目录会把工作区搞脏。
    archive = "--evidence" in sys.argv
    if archive:
        out_path = EVIDENCE / f"{time.strftime('%Y-%m-%d')}_a_line_p1_golden_checks.json"
        payload["archived"] = True
    else:
        out_path = REPO_ROOT / "metrics" / "logs" / "_a_line_p1_latest.json"
        payload["archived"] = False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    passed_n = sum(1 for i in results if i["passed"])
    print()
    print(f"结果已写入：{out_path.relative_to(REPO_ROOT)}"
          + ("（已归档，可提交）" if archive else "（不入库；要归档请加 --evidence）"))
    print(f"总结论：{'全部通过' if all_passed else '存在未通过项'}（{passed_n}/{len(results)} 项通过）")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
