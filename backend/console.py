"""console.py —— 让命令行输出在 Windows 控制台上不会崩。

问题（实测踩到）：Windows 控制台默认代码页是 936(GBK) 或 437(英文机)，
`print("❌ ...")` 里的 ❌(U+274C) / ⚠️(U+26A0) 在 GBK 里没有对应字符，
于是脚本直接抛 UnicodeEncodeError 崩掉 —— 而这跟项目逻辑毫无关系。
在英文区 Windows 上更糟：连中文都会崩。

因此所有会向终端打印中文/符号的入口都先调一次 enable_utf8_console()。
调用是幂等的，且对 pytest 的捕获模式无害（reconfigure 只作用于真实终端流）。
"""

from __future__ import annotations

import sys


def enable_utf8_console() -> bool:
    """把 stdout/stderr 切成 UTF-8。返回是否成功。

    只做两件事：
      - errors="replace"：万一还有编不出的字符，显示成 '?'，**而不是让程序崩**；
      - encoding="utf-8"：中文/符号在 Windows Terminal / VS Code 里正常显示。
    """
    ok = True
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            ok = False
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 —— 某些包装流不允许 reconfigure
            ok = False
    return ok
