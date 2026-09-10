"""check_frontend_wiring.py —— 前端接线自检：app.js 引用的 DOM id 是否都存在于 index.html？

为什么需要它：
    前端最典型的低级事故是 `document.getElementById("chart")` 拼错成 `"charts"`，
    结果是**页面不报错、某个区域永远空白**。在没打开浏览器的情况下，
    这类问题最容易漏掉。本脚本把"id 必须对得上"变成可自动检查的项（可进 CI）。

用法（仓库根）：
    python metrics/scripts/check_frontend_wiring.py
退出码：0 = 接线一致；1 = 有引用不存在的 id。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Windows 控制台默认是 GBK 代码页，打印 ✅/❌/⚠️ 会直接 UnicodeEncodeError 崩掉脚本。
# 命令行工具必须自己保证可打印，而不是要求用户去改 chcp 65001。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 —— 老解释器没有 reconfigure，忽略即可
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"


def ids_in_html(html: str) -> set[str]:
    return set(re.findall(r'\bid\s*=\s*"([^"]+)"', html))


def refs_in_js(js: str) -> set[str]:
    refs: set[str] = set()
    # $("x") —— 本项目自己的简写
    refs |= set(re.findall(r'\$\(\s*"([^"]+)"\s*\)', js))
    # getElementById("x") / querySelector("#x")
    refs |= set(re.findall(r'getElementById\(\s*"([^"]+)"\s*\)', js))
    refs |= set(re.findall(r'querySelector\(\s*"#([A-Za-z0-9_-]+)"', js))
    return refs


def dynamic_ids(js: str) -> set[str]:
    """识别拼接出来的 id（如 "card_" + d.key），单独报告，避免误判为缺失。"""
    return set(re.findall(r'\$\("([A-Za-z0-9_-]+_)"\s*\+', js)) | \
           set(re.findall(r'getElementById\("([A-Za-z0-9_-]+_)"\s*\+', js))


def main() -> int:
    html_path = FRONTEND / "index.html"
    js_path = FRONTEND / "app.js"
    if not html_path.exists() or not js_path.exists():
        print(f"找不到 {html_path} 或 {js_path}")
        return 2

    html = html_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")

    html_ids = ids_in_html(html)
    refs = refs_in_js(js)
    dynt = dynamic_ids(js)

    missing = sorted(r for r in refs if r not in html_ids)
    # 动态 id（如 card_xxx）由 JS 自己创建，HTML 里当然没有。
    # 正确的检查不是"HTML 里要有 card_xxx"，而是"JS 里确实给元素赋过这个前缀的 id"，
    # 否则就是拼前缀拼错了 / 忘了赋值 —— 那才是真 bug。
    dynamic_missing = sorted(p for p in dynt if not re.search(r'\.id\s*=\s*"' + re.escape(p) + r'"', js))

    print(f"index.html 中 id 数量      : {len(html_ids)}")
    print(f"app.js 中静态引用 id 数量  : {len(refs)}")
    print(f"动态拼接 id 前缀           : {sorted(dynt) or '（无）'}（由 JS 创建，不校验 HTML）")

    bad = False
    if missing:
        bad = True
        print("\n[FAIL] app.js 引用了 index.html 里不存在的 id（症状：页面不报错但区域永远空白）：")
        for m in missing:
            print(f"     - {m}")
    if dynamic_missing:
        bad = True
        print("\n[FAIL] 以下动态 id 前缀在 app.js 里找不到对应的 `xxx.id = \"前缀\"` 赋值：")
        for m in dynamic_missing:
            print(f"     - {m}*")

    # 顺带检查 index.html 是否引了不存在的脚本文件
    for src in re.findall(r'<script[^>]+src\s*=\s*"([^"]+)"', html):
        if not (FRONTEND / src).exists():
            bad = True
            print(f"\n[FAIL] index.html 引用的脚本不存在：{src}")

    if bad:
        print("\n前端接线检查：失败。")
        return 1
    print("\n前端接线检查：通过 —— 所有静态 id 引用都能在 index.html 找到对应元素。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
