# -*- coding: utf-8 -*-
"""offline_check.py —— 在 **PC 上**用假模块跑一遍 `openmv_capture_test.py`（离线自检）。

为什么需要它
------------
`openmv_capture_test.py` 是**在相机上**跑的 MicroPython 脚本，PC 上没法直接运行；
可它偏偏是最需要"改完先验一遍"的东西 —— 真机只有一台，而且 v5 固件把 `sensor` 换成了 `csi`，
同一份脚本走**两条不同分支**。没有本文件的话，"改完能不能跑"只能靠真机试错。

它做的事：造一个假的 `csi`（v5）或 `sensor`（v4）模块，**按 2026-09-24 真机实测到的行为**模拟 H7：

  · `gc.mem_free()` ≈ 308944 B（相机打开后 ≈ 307136 B —— 实测帧缓冲不占 MicroPython 堆）
  · 请求 614400 B（VGA/RGB565）→ `RuntimeError: Frame buffer overflow, ...`（**真机原话**）
  · 请求 153600 B（QVGA/RGB565）→ 成功，`bytearray()` 长度 = w*h*2

然后真的调用脚本的 `main()`，检查它**没有抛异常、没有算出荒唐结论**：
组合顺序、失败行是否带上内存算术、契约小结、`_CAM_API` 作用域、两条 API 分支等。

⚠️ **诚实边界（必读）**
  · 本文件**不产生任何硬件性能数字** —— 帧率/内存/字节数全是假模块给的，**不得引用为实测**。
  · 它**不能替代真机**：真机的真实帧率、JPEG 实际字节数、传感器型号只能由
    `openmv_capture_test.py` 在相机上跑出来（`AGENTS.md` 铁律 1）。
  · 它**已经真抓到过一个真机也会犯的 bug**（2026-09-24）：
    脚本 `main()` 里 `except` 分支给 `_CAM_API` 赋值，却没声明 `global`，
    于是 Python 把 `_CAM_API` 当成局部变量 → **成功路径**读它抛 `UnboundLocalError`，
    被自己的 `except Exception` 抓住，打印成"相机 API : 探测失败"这种假故障，
    并让 `report.json` 的 `camera_api` 变成 null。**真机上同样会发生。**

用法（仓库根执行，无需硬件、无需第三方库）
------------------------------------------
    python board/openmv/offline_check.py            # 两条分支都跑（默认）
    python board/openmv/offline_check.py csi        # 只跑 v5 的 csi.CSI 分支
    python board/openmv/offline_check.py sensor     # 只跑 v4 的 sensor 模块分支

期望输出末行：`RESULT: PASS (28/28)`（两条分支各 14 项）。
"""

import gc
import os
import sys
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "board", "openmv", "openmv_capture_test.py")

MEM_FREE_H7 = 308944          # 真机实测：启动时 gc.mem_free()
MEM_FREE_AFTER_CAM = 307136   # 真机实测：QVGA/RGB565 跑完后的 gc.mem_free()

_SIZES = {"QQVGA": (160, 120), "QVGA": (320, 240), "VGA": (640, 480)}
_BPP = {"GRAYSCALE": 1, "RGB565": 2, "JPEG": 0}
_cam_opened = [False]
_sleep = time.sleep   # ⚠️ 必须先取出来：下面 `_snapshot(time=...)` 的形参名会**遮蔽**模块名 `time`


class _Img(object):
    """假 image 对象：只实现脚本真正用到的那几个方法。"""

    def __init__(self, w, h, pf):
        self._w, self._h, self._pf = w, h, pf
        bpp = _BPP.get(pf, 0)
        n = w * h * bpp if bpp else (w * h // 6 + 1000)
        self._buf = bytearray(n)
        if self._buf:
            self._buf[0] = 0x5A

    def width(self):
        return self._w

    def height(self):
        return self._h

    def bytearray(self):
        return self._buf

    def compress(self, quality=90):
        return _Img(self._w, self._h, "JPEG")

    def get_pixel(self, x, y, rgbtuple=False):
        if rgbtuple:
            return (255, 128, 64)
        return 0xFFFF


def _make_camera(api):
    """造一个假相机模块：v5 的 `csi` 给 `CSI` 类，v4 的 `sensor` 给模块级函数。"""
    mod = types.ModuleType(api)
    for name, val in (("GRAYSCALE", 0), ("RGB565", 1), ("JPEG", 2), ("BAYER", 3),
                      ("QQVGA", 10), ("QVGA", 11), ("VGA", 12)):
        setattr(mod, name, val)

    state = {"pf": None, "fs": None, "fb": 1}
    names = {v: k for k, v in mod.__dict__.items() if isinstance(v, int)}

    def _reset():
        state["pf"] = state["fs"] = None
        state["fb"] = 1
        _cam_opened[0] = True

    def _snapshot(time=None):
        # ⚠️ 形参必须叫 `time` —— 脚本是用关键字 `snapshot(time=ms)` 调用它的（warmup 用）。
        if time is not None:
            _cam_opened[0] = True
            return None
        w, h = _SIZES[names[state["fs"]]]
        pf_name = names[state["pf"]]
        bpp = _BPP.get(pf_name, 0)
        need = w * h * bpp if bpp else (w * h // 6 + 1000)
        if need > MEM_FREE_H7:
            raise RuntimeError("Frame buffer overflow, try reducing the frame size.")
        _sleep(0.001)
        return _Img(w, h, pf_name)

    if api == "csi":
        mod.CSI = lambda: types.SimpleNamespace(
            reset=_reset,
            pixformat=lambda v: state.__setitem__("pf", v),
            framesize=lambda v: state.__setitem__("fs", v),
            set_framebuffers=lambda n: state.__setitem__("fb", n),
            get_id=lambda: 0x7725,
            snapshot=_snapshot)
    else:
        mod.reset = _reset
        mod.set_pixformat = lambda v: state.__setitem__("pf", v)
        mod.set_framesize = lambda v: state.__setitem__("fs", v)
        mod.set_framebuffers = lambda n: state.__setitem__("fb", n)
        mod.get_id = lambda: 0x7725
        mod.snapshot = _snapshot
        mod.skip_frames = lambda time=0: _snapshot(time=time)
        mod.set_auto_gain = lambda *a, **k: None
    return mod


def _patch_micropython():
    """把 MicroPython 专有的 time.ticks_* / gc.mem_free / os.uname 补齐（CPython 上没有）。"""
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_us = lambda: int(time.monotonic() * 1e6)
    time.ticks_add = lambda t, d: t + d
    time.ticks_diff = lambda a, b: a - b
    gc.mem_free = lambda: MEM_FREE_AFTER_CAM if _cam_opened[0] else MEM_FREE_H7
    os.uname = lambda: types.SimpleNamespace(
        sysname="micropython", nodename="OPENMV4", release="1.28.0",
        version="v1.28.0-0-gabcdef on 2026-01-01; OPENMV4 with STM32H743",
        machine="OPENMV4 with STM32H743")


class _Tee(object):
    """一边打印一边留底 —— 检查项要断言在捕获到的文本上。"""

    def __init__(self, real_out, sink):
        self._real, self._sink = real_out, sink

    def write(self, s):
        self._sink.append(s)
        return self._real.write(s)

    def flush(self):
        return self._real.flush()


def _run_one(api):
    """跑一趟指定 API 分支，返回 (通过的检查数, 检查总数, 失败项名字列表)。"""
    _cam_opened[0] = False
    _patch_micropython()

    sys.modules[api] = _make_camera(api)
    sys.modules.pop("sensor" if api == "csi" else "csi", None)
    omv = types.ModuleType("omv")
    omv.version = "5.0.0-fake"
    omv.board_type = lambda: "OPENMV4"
    omv.arch = lambda: "cortex-m7"
    sys.modules["omv"] = omv

    with open(SCRIPT, "r", encoding="utf-8") as f:
        src = f.read()
    lines = src.rstrip().split("\n")
    assert lines[-1].strip() == "main()", "脚本末尾不再是裸 main()，本自检需要同步"
    src = "\n".join(lines[:-1])          # 去掉裸 main() 调用，改由本文件控制节奏

    ns = {"__name__": "__main__", "__file__": SCRIPT}
    exec(compile(src, SCRIPT, "exec"), ns)
    ns["MATRIX_SECONDS"] = 0.05           # 把 2s/组压到 50ms，本自检只验逻辑不验性能
    ns["_cam_warmup"] = lambda ms: None   # 省掉每组 800ms 的等曝光

    out = []
    captured = {}
    real_run_matrix = ns["run_matrix"]

    def _wrapped():
        captured["results"] = real_run_matrix()
        return captured["results"]

    ns["run_matrix"] = _wrapped
    real_out = sys.stdout
    sys.stdout = _Tee(real_out, out)
    try:
        print("")
        print("#" * 70)
        print("# 假模块运行：api=%s（下面是脚本在真机上会打印的内容，数字全部来自假模块）" % api)
        print("#" * 70)
        ns["main"]()                      # 走真入口：先打环境事实，再跑 matrix
    finally:
        sys.stdout = real_out
    text = "".join(out)
    results = captured.get("results") or []
    n_rows = len(ns["_matrix_rows"]())

    checks = [
        ("打印了组合顺序", "本轮 MATRIX_STAGE=auto" in text),
        ("每组开跑前有归属行（崩了能定位到是哪一组）", "[1/%d] JPEG" % n_rows in text),
        ("大组合排在最后（RGB565/VGA 是第 %d 组）" % n_rows, "[%d/%d] RGB565" % (n_rows, n_rows) in text),
        ("VGA/RGB565 失败行带上了内存算术",
         "Frame buffer overflow" in text and "614400" in text),
        ("失败行也记了 mem_free", ("308944" in text) or ("307136" in text)),
        ("GRAYSCALE/VGA 边界组合成功（307200 < 308944）",
         any(r.get("ok") and r["pixformat"] == "GRAYSCALE" and r["framesize"] == "VGA"
             for r in results)),
        ("契约可行性小结出现", "契约可行性小结" in text),
        ("小结算出「装得下契约帧」= 无",
         "「整帧装得下契约帧（≥921600 B）」的成功组合：**无**" in text),
        ("小结算出 ≥30fps 的组合", "「实测均值帧率 ≥ 30 fps」的成功组合：" in text),
        ("换算最短可检出闭眼", "最短可检出闭眼" in text),
        ("给出「不可能作为契约像素源」的推论", "不可能**作为契约" in text),
        ("打印了 API 分支（不是假故障）",
         ("相机 API  : csi（csi.CSI 类 API（v5+））" if api == "csi"
          else "相机 API  : sensor（sensor 模块 API（v4.x））") in text
         and "探测失败" not in text),
        ("打印了环境事实（含 omv 固件版本）", "omv.version" in text and "5.0.0-fake" in text),
        ("打印了传感器 ID", "0x7725" in text),
    ]
    bad = [n for n, ok in checks if not ok]
    return len(checks) - len(bad), len(checks), bad


def main():
    # 相机侧脚本会打印 ⚠️ / ✅ 这类符号，而 Windows 控制台默认 GBK 编不出来（UnicodeEncodeError）。
    # 真机是 OpenMV IDE（UTF-8）不受影响；本机自检必须显式转 UTF-8。
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if not os.path.isfile(SCRIPT):
        print("找不到 %s" % SCRIPT)
        return 1
    apis = [sys.argv[1]] if len(sys.argv) > 1 else ["csi", "sensor"]

    print("")
    print("=" * 70)
    print("OpenMV 采集测试脚本 · 离线自检（假模块，无硬件）")
    print("被测文件：%s" % SCRIPT)
    print("=" * 70)

    passed = total = 0
    bad_all = []
    for api in apis:
        ok_n, tot, bad = _run_one(api)
        passed += ok_n
        total += tot
        bad_all += ["%s:%s" % (api, b) for b in bad]
        print("")
        print("-" * 70)
        print("分支 %-7s ：%d/%d" % (api, ok_n, tot))
        for n in bad:
            print("    FAIL %s" % n)

    print("")
    print("=" * 70)
    print("⚠️ 本自检只证明**脚本逻辑**不炸；帧率/内存/字节数一律以真机实测为准。")
    print("RESULT: %s (%d/%d)" % ("PASS" if not bad_all else "FAIL", passed, total))
    return 1 if bad_all else 0


if __name__ == "__main__":
    raise SystemExit(main())
