# -*- coding: utf-8 -*-
"""offline_stream_check.py —— 在 **PC 上**用假模块跑一遍 `openmv_stream.py` 的 `stream()`。

为什么需要它
------------
`openmv_stream.py` 只能在相机上跑（`import sensor / image / pyb`），可它承载的正是
`docs/18` **路线②**（灰度直发、相机侧不压缩）—— 也就是 `docs/16` BUG-027 的修法本身。
没有硬件时，"改完到底会不会跑、`compress()` 有没有被调用"只能靠**假模块跑真脚本**。

它与 `offline_check.py` 的分工：那份管 `openmv_capture_test.py`（采集能力矩阵），
本文件管 `openmv_stream.py`（联调推流）。两者都**不产生任何硬件数字**。

它断言的事（每条都对应一个真实会犯的错）
----------------------------------------
  T1 灰度模式：`set_pixformat(GRAYSCALE)` + 帧尺寸默认 **QQVGA**，发出来的是 `TYPE_GRAY`，
     载荷 == `pack_gray(w, h, 像素)`（宽高头 + w*h 灰度字节）
  T2 **灰度模式下一次都不调用 `img.compress()`** —— 这就是路线②的**全部意义**；
     一旦有人"顺手"把它加回来，BUG-027 就复活了，所以这条必须钉死
  T3 JPEG 模式（对照）：仍然走 `compress(quality=JPEG_QUALITY)`，帧型是 `TYPE_JPEG`
  T4 JPEG 单帧压缩失败**不会让整条流退出**（BUG-027 的缓解措施，防止回退）
  T5 stats 模式：`TYPE_STATS`，ROI 传的是半开区间算出来的 (x, y, w, h)（契约 §0.1 第 2 条）
  T6 `payload` 传错时立刻 `ValueError`，且**不碰相机**（不许"静静地发错东西"）

用法（仓库根执行，无需硬件、无需第三方库）
------------------------------------------
    python board/openmv/offline_stream_check.py

期望输出末行：`RESULT: PASS (n/n)`。

⚠️ **诚实边界（必读）**
  · 帧尺寸/字节数/帧率/内存**全部来自假模块**，不得引用为实测（`AGENTS.md` 铁律 1）；
  · 它**不能替代真机**：灰度在本固件下到底能不能出图、VCP 真能到几 fps，只能真机跑；
  · 它证明的是**脚本逻辑**：路线②确实不调编码器、三种模式的分支与载荷正确。
"""

import gc
import importlib.util
import os
import sys
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "board", "openmv", "openmv_stream.py")
LINK_PY = os.path.join(REPO, "board", "openmv", "vigilens_link.py")

# 与真机无关，只是让假 sensor 认得出帧尺寸（真机的值是传感器驱动里的枚举）
_SIZES = {"QQVGA": (160, 120), "QVGA": (320, 240), "VGA": (640, 480)}
_FS_IDS = {"QQVGA": 10, "QVGA": 11, "VGA": 12}
_FS_BY_ID = {v: k for k, v in _FS_IDS.items()}


class _Stop(Exception):
    """假传感器用它把 `stream()` 的 `while True` 打断 —— 真机上是 Ctrl-C / 拔线。"""


class _FakeCompressed(object):
    """`img.compress()` 的返回值：只需满足 `_to_bytes()` 的取字节协议。"""

    def __init__(self, n=1024):
        self._b = bytes([0xFF, 0xD8]) + bytes(n)

    def bytearray(self):
        return bytearray(self._b)


class _FakeStats(object):
    """`img.get_statistics()` 的返回值。

    ⚠️ 真机上 `r_mean()` / `g_mean()` / `b_mean()` / `mean()` 全是**方法**（不是属性）——
    本自检第一次跑就撞到这一点（假模块给成属性 → `TypeError: 'float' object is not callable`），
    所以这里如实照抄真机 API，别为了省事写成属性。
    """

    def r_mean(self):
        return 1.0

    def g_mean(self):
        return 2.0

    def b_mean(self):
        return 3.0

    def mean(self):
        return 2.0


class _FakeImg(object):
    def __init__(self, w, h, state):
        self._w, self._h, self._state = w, h, state
        # 逐像素内容与下标相关：这样 `pack_gray` 的"行序→列序"真被逐字节比对过，
        # 而不是"长度对了就算过"。
        self._buf = bytes((i * 7) & 0xFF for i in range(w * h))

    def width(self):
        return self._w

    def height(self):
        return self._h

    def bytearray(self):
        return bytearray(self._buf)

    def compress(self, quality=90):
        self._state["compress_quality"].append(quality)
        if self._state["fail_compress"]:
            raise OSError("Compression Failed!")     # 真机原话（docs/16 BUG-027）
        return _FakeCompressed()

    def get_statistics(self, roi=None):
        self._state["stats_roi"] = roi
        return _FakeStats()


def _make_env(state, sent):
    """造出 `sensor` / `image` / `pyb` 三个假模块（只实现脚本真正用到的那几个入口）。"""
    sensor = types.ModuleType("sensor")
    sensor.GRAYSCALE, sensor.RGB565, sensor.JPEG, sensor.BAYER = 0, 1, 2, 3
    for name, fs_id in _FS_IDS.items():
        setattr(sensor, name, fs_id)

    def _reset():
        state["pf"] = state["fs"] = None
        state["calls"].append("reset")

    def _set_pixformat(pf):
        state["pf"] = pf
        state["calls"].append("set_pixformat")

    def _set_framesize(fs):
        state["fs"] = fs
        state["calls"].append("set_framesize")

    def _snapshot(time=None):
        if time is not None:                 # warmup 用 snapshot(time=ms)
            return None
        state["snapshots"] += 1
        if state["snapshots"] > state["max_frames"]:
            raise _Stop("fake sensor: 已采集到本轮上限")
        w, h = _SIZES[_FS_BY_ID[state["fs"]]]
        return _FakeImg(w, h, state)

    sensor.reset = _reset
    sensor.set_pixformat = _set_pixformat
    sensor.set_framesize = _set_framesize
    sensor.skip_frames = lambda time=0: None
    sensor.snapshot = _snapshot

    image = types.ModuleType("image")        # noqa: F841  （脚本只 import 它，不调用）

    pyb = types.ModuleType("pyb")

    class _VCP(object):
        def send(self, data):
            sent.append(bytes(data))

    class _UART(object):
        def __init__(self, *a, **k):
            pass

        def write(self, data):
            sent.append(bytes(data))

    class _LED(object):
        def toggle(self):
            pass

    pyb.USB_VCP, pyb.UART, pyb.LED = _VCP, _UART, _LED
    return {"sensor": sensor, "image": image, "pyb": pyb}


def _patch_micropython_time():
    """补齐 MicroPython 专有的 time.ticks_*（CPython 上没有）。"""
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_us = lambda: int(time.monotonic() * 1e6)
    time.ticks_add = lambda t, d: t + d
    time.ticks_diff = lambda a, b: a - b
    time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)


def _load_link():
    """把仓库里**唯一那份**协议实现装进 sys.modules（脚本 `import vigilens_link` 就用它）。"""
    spec = importlib.util.spec_from_file_location("vigilens_link", LINK_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["vigilens_link"] = mod
    return mod


def _load_stream():
    """exec `openmv_stream.py`（去掉末尾那行裸 `main()`，由本文件控制节奏）。"""
    with open(SCRIPT, "r", encoding="utf-8") as f:
        src = f.read()
    lines = src.rstrip().split("\n")
    assert lines[-1].strip() == "main()", "脚本末尾不再是裸 main()，本自检需要同步"
    ns = {"__name__": "openmv_stream_under_test", "__file__": SCRIPT}
    exec(compile("\n".join(lines[:-1]), SCRIPT, "exec"), ns)
    return ns


def _run_stream(payload, max_frames=3, fail_compress=False):
    """跑一轮指定 payload 的 stream()，返回 (state, 已发出的原始帧, 逃出来的异常, 假模块)。"""
    state = {"calls": [], "compress_quality": [], "fail_compress": fail_compress,
             "pf": None, "fs": None, "snapshots": 0, "max_frames": max_frames,
             "stats_roi": None}
    sent = []
    fakes = _make_env(state, sent)
    for name, mod in fakes.items():
        sys.modules[name] = mod
    link = _load_link()
    ns = _load_stream()
    exc = None
    try:
        ns["stream"](None, payload=payload, use_usb=True)
    except BaseException as e:               # noqa: BLE001  （_Stop 是预期出口）
        exc = e
    return state, sent, exc, fakes, link


def _decode(sent, link):
    dec = link.Decoder()
    out = []
    for blob in sent:
        out.extend(dec.feed(blob))
    return out, dec


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if not os.path.isfile(SCRIPT):
        print("找不到 %s" % SCRIPT)
        return 1

    _patch_micropython_time()

    print("")
    print("=" * 70)
    print("OpenMV 推流脚本 · 离线自检（假模块，无硬件）")
    print("被测文件：%s" % SCRIPT)
    print("=" * 70)

    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # ---- T1/T2：路线②（灰度直发）------------------------------------------------
    st, sent, exc, fakes, link = _run_stream("gray")
    frames, dec = _decode(sent, link)
    sensor = fakes["sensor"]
    chk("T1a gray：循环跑到假传感器的上限（不是被异常打断）", isinstance(exc, _Stop),
        "exc=%r" % (exc,))
    chk("T1b gray：pixformat 设为 GRAYSCALE（路线②的硬件前提）", st["pf"] == sensor.GRAYSCALE,
        "pf=%r" % (st["pf"],))
    chk("T1c gray：帧尺寸用的是 GRAY_FRAMESIZE（默认 QQVGA = 160x120），不是 STREAM_FRAMESIZE",
        st["fs"] == _FS_IDS["QQVGA"], "fs=%r" % (st["fs"],))
    chk("T1d gray：3 帧全部解出且都是 TYPE_GRAY",
        len(frames) == 3 and all(f[0] == link.TYPE_GRAY for f in frames) and dec.crc_errors == 0,
        "frames=%d types=%s crc_err=%d" % (len(frames), [f[0] for f in frames], dec.crc_errors))
    if frames:
        w, h, pix = link.unpack_gray(frames[0][2])
        want = bytes((i * 7) & 0xFF for i in range(w * h))
        chk("T1e gray：载荷 == pack_gray(w, h, 像素)，逐字节一致",
            (w, h) == (160, 120) and len(pix) == 160 * 120 and pix == want,
            "w=%d h=%d len=%d" % (w, h, len(pix)))
        chk("T1f gray：frame_id 单调递增（契约 §1）",
            [f[1] for f in frames] == [1, 2, 3], "ids=%s" % ([f[1] for f in frames],))
    else:
        chk("T1e gray：载荷 == pack_gray(w, h, 像素)，逐字节一致", False, "没有帧")
        chk("T1f gray：frame_id 单调递增（契约 §1）", False, "没有帧")
    chk("T2  gray：**从不调用 img.compress()** —— 这就是路线②（BUG-027 的根治点）",
        st["compress_quality"] == [] and "compress" not in st["calls"],
        "compress 调用=%s" % (st["compress_quality"],))

    # ---- T3：JPEG 模式（对照，仍走压缩）------------------------------------------
    st, sent, exc, fakes, link = _run_stream("jpeg")
    frames, dec = _decode(sent, link)
    sensor = fakes["sensor"]
    chk("T3a jpeg：pixformat 设为 RGB565（JPEG 由软件压缩产生）", st["pf"] == sensor.RGB565,
        "pf=%r" % (st["pf"],))
    chk("T3b jpeg：3 帧都是 TYPE_JPEG，且确实每次都调了 compress(quality=90)",
        len(frames) == 3 and all(f[0] == link.TYPE_JPEG for f in frames)
        and st["compress_quality"] == [90, 90, 90],
        "frames=%d quality=%s" % (len(frames), st["compress_quality"]))
    if frames:
        chk("T3c jpeg：载荷 == compress() 返回的字节",
            frames[0][2][:2] == b"\xff\xd8", "head=%r" % (frames[0][2][:2],))
    else:
        chk("T3c jpeg：载荷 == compress() 返回的字节", False, "没有帧")

    # ---- T4：压缩失败不许再把整条流带走（BUG-027 的缓解，防回退）------------------
    st, sent, exc, fakes, link = _run_stream("jpeg", fail_compress=True)
    frames, _ = _decode(sent, link)
    chk("T4a jpeg+压缩失败：异常是假传感器的 _Stop，而**不是** OSError（流没被打断）",
        isinstance(exc, _Stop) and not isinstance(exc, OSError), "exc=%r" % (exc,))
    chk("T4b jpeg+压缩失败：坏帧一帧都不发（只丢该帧），且每帧都试过压缩",
        frames == [] and st["compress_quality"] == [90, 90, 90],
        "sent=%d compress=%s" % (len(frames), st["compress_quality"]))

    # ---- T5：stats 模式（路线④）--------------------------------------------------
    st, sent, exc, fakes, link = _run_stream("stats")
    frames, dec = _decode(sent, link)
    chk("T5a stats：3 帧都是 TYPE_STATS，载荷 44 字节（11 个 int32）",
        len(frames) == 3 and all(f[0] == link.TYPE_STATS for f in frames)
        and all(len(f[2]) == 44 for f in frames),
        "frames=%d lens=%s" % (len(frames), [len(f[2]) for f in frames]))
    chk("T5b stats：ROI 按半开区间折算成 (x, y, w, h) = (0, 0, 640, 480)",
        st["stats_roi"] == (0, 0, 640, 480), "roi=%r" % (st["stats_roi"],))
    chk("T5c stats：不产生任何图像字节（不调 compress）", st["compress_quality"] == [],
        "compress=%s" % (st["compress_quality"],))
    if frames:
        vals = link.unpack_stats(frames[0][2])
        chk("T5d stats：载荷字段可解且 frame_id 镜像 == 1", vals.get("frame_id") == 1,
            str(vals)[:80])
    else:
        chk("T5d stats：载荷字段可解且 frame_id 镜像 == 1", False, "没有帧")

    # ---- T6：payload 传错 → 立刻报错，不碰相机 -----------------------------------
    state = {"calls": [], "compress_quality": [], "fail_compress": False, "pf": None,
             "fs": None, "snapshots": 0, "max_frames": 1, "stats_roi": None}
    sent = []
    fakes = _make_env(state, sent)
    for name, mod in fakes.items():
        sys.modules[name] = mod
    _load_link()
    ns = _load_stream()
    raised = None
    try:
        ns["stream"](None, payload="jpeeg", use_usb=True)
    except ValueError as e:
        raised = e
    except BaseException as e:                # noqa: BLE001
        raised = e
    chk("T6 payload 传错时抛 ValueError，且完全没有碰相机（reset 都没调）",
        isinstance(raised, ValueError) and state["calls"] == [],
        "raised=%r calls=%s" % (raised, state["calls"]))

    failed = [c for c in checks if not c[1]]
    print("")
    for name, ok, detail in checks:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        if detail and not ok:
            print("         -> " + detail)
    print("-" * 70)
    print("⚠️ 本自检只证明**脚本逻辑**（分支/载荷/不调编码器）；帧率与内存一律以真机实测为准。")
    print("RESULT: %s (%d/%d)" % ("PASS" if not failed else "FAIL",
                                   len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
