# -*- coding: utf-8 -*-
"""
openmv_capture_test.py —— 在 **OpenMV Cam H7** 上运行的「图像采集测试」程序。

项目：知倦 / VigiLens —— 首次联调：OpenMV 图像采集能力实测

它回答四个必须用**实测**才能定论的问题（这也是它存在的理由）：

  Q1  这台 OpenMV 到底能出哪些「像素格式 × 分辨率」组合？
      （官方产品页自相矛盾：描述段说 OV7725 可 640×480 RGB565 @75fps，
        规格表却说 RGB565 上限 320×240 —— 这里把它测成事实。）
  Q2  每种组合的**真实帧率**是多少？稳定性如何（抖动、最慢帧）？
  Q3  片内内存到底装得下多大的帧？（契约要 640×480**RGB888** = 921,600 B，
      而 H7 只有 1 MB SRAM —— 这条要拿 gc.mem_free() 实测，不是推算。）
  Q4  能不能把采到的帧**无损**落盘、供 A/C 线后续使用？

┌────────────────────────────────────────────────────────────────────────┐
│ 真机状态（2026-09-24，OpenMV Cam H7，固件走 `csi` 类 API）：            │
│   ✅ 已跑过一次 matrix —— **但只拿回前两组**（原因见下面的排序说明）：   │
│      · RGB565 / VGA  → `RuntimeError: Frame buffer overflow`（预期内）  │
│      · RGB565 / QVGA → 均值 39.76 fps；153600 B/帧；gc.mem_free 308944 B│
│   ⚠️ 其余组合**一行都没打印**，尚分不清是"没跑到"还是"把相机搞死了"——   │
│      所以本文件把组合重排成"先便宜后危险"，并加了分段开关 MATRIX_STAGE。 │
│   ⚠️ 除上面两行外，本文件里任何"预期值"都**不是**结论（`AGENTS.md` 铁律 1）│
└────────────────────────────────────────────────────────────────────────┘

安装与运行
----------
1. 本文件**自包含**（只 import sensor/pyb/gc/os/sys/time），**不需要** `vigilens_link.py`。
   用 OpenMV IDE 或 VSCode 的 OpenMV 扩展打开它、改下面的 `MODE`、点运行即可。
   （只有 `openmv_stream.py` 才需要把 `vigilens_link.py` 一并放到 OpenMV 的盘上。）
2. 改下面 `MODE`，在 OpenMV IDE 里点"运行"。
3. 跑完把打印的报告整段贴回项目记录；`dump` 模式另外会写 `report.json` 和原始帧。

MODE 取值
---------
  "matrix"     能力矩阵：逐个试「像素格式 × 分辨率 × 帧缓冲数」，测帧率与内存。**先跑这个。**
               组合按「信息量/风险」排序（小的、要 JPEG 字节数的在前，已知会 overflow 的在后）；
               可用 `MATRIX_STAGE` 只跑 "small"/"mid"/"big" 其中一组 —— 分段跑更抗"相机卡死"。
  "sustained"  长跑：在 `CHOSEN_*` 指定的一组参数上连续跑 N 秒，给帧率统计（含抖动/最慢帧）。
  "dump"       落盘：按 `CHOSEN_*` 采 N 帧，**原样**写入 SD，并写 `meta.json` 说明布局。
               （RGB565→RGB888 的转换放在上位机做：`raw_to_contract.py`，
                 这样转换口径只有一处实现，而且能在 PC 上离线自检。）
  "all"        先 matrix，再 dump。

落盘格式（刻意选"原样字节 + sidecar"，不做板上转换）
---------------------------------------------------
  <DUMP_DIR>/frame_0000.raw ...   每个文件 = `img.bytearray()` 的原始字节，长度应为 w*h*bpp
  <DUMP_DIR>/meta.json            说明 pixformat / 宽高 / 每像素字节 / 字节序 / 帧数 / 校验和
  <REPORT_PATH>                   本次测试的完整机器可读报告（给项目留档）
"""

import gc
import os
import sys
import time

# ===========================================================================
# 兼容层：同时支持 v4.x（`sensor` 模块 API）与 v5.x（`csi.CSI` 类 API）
# ---------------------------------------------------------------------------
# 为什么必须有这一层：**v5.0.0 有一条标记为 major 的破坏性变更** ——
#   "sensor replaced by csi (major): Every official example was rewritten to drop
#    `import sensor` in favor of `import csi`. The legacy module-level functional API
#    (sensor.reset(), sensor.set_pixformat(), …) is superseded by the class-based
#    csi.CSI API."
#   官方同时说明 `sensor` 这个 qstr "is still wired up ... for backwards-compatible
#   firmware builds" —— 也就是说 v5 上 `import sensor` **可能还能用，但不保证**。
#   所以这里**不赌**：优先用 v5 的 csi，退回 v4 的 sensor，并把实际走的那条打出来。
#
# ⚠️ 另一个 v5 的坑（与 API 无关，但更隐蔽）：**SD 卡的挂载点从 `/sd` 变成了 `/sdcard`**。
#    v5 的 H7 文档原文："When a card is inserted it is mounted automatically at `/sdcard`"。
#    两个路径都试，见下面的 DUMP_DIR_CANDIDATES。
# ===========================================================================

_CAM = None          # v5: CSI 实例；v4: sensor 模块本身
_CAM_MOD = None      # 取常量用的模块（csi 或 sensor）
_CAM_API = None      # "csi" | "sensor"
_LED = None          # 惰性初始化的 LED 句柄列表，拿不到就是 None（不影响测试）


def _open_camera():
    """探测并打开相机。只做一次。返回 None；失败抛 RuntimeError（带明确原因）。"""
    global _CAM, _CAM_MOD, _CAM_API
    if _CAM is not None:
        return
    try:
        import csi as m                    # v5+
        _CAM_API, _CAM_MOD, _CAM = "csi", m, m.CSI()
        return
    except ImportError:
        pass
    except Exception:
        raise
    try:
        import sensor as m                 # v4.x
        _CAM_API, _CAM_MOD, _CAM = "sensor", m, m
        return
    except ImportError:
        pass
    raise RuntimeError("固件里既没有 `csi`（v5+）也没有 `sensor`（v4）模块 —— 无法识别相机 API")


def _cam_version():
    """尽量拿到固件/板子的可读标识，写进报告用。

    ⚠️ **命名踩过的坑（2026-09-24 实测）**：`os.uname().release` 是 **MicroPython 的版本**，
    不是 OpenMV 固件的版本；而 `sys.version` 的首段（如 `3.4.0`）是 **MicroPython 声称的
    Python 语言级别**，也不是任何"MicroPython 版本"。第一次真机运行把两者拼成了
    `OPENMV4 with STM32H743 1.28.0 | MicroPython 3.4.0` —— **看着像固件版本，其实都不是**。
    所以这里把标签写准；真正的固件版本交给 `_probe_env()` 去问 `omv` 模块。
    """
    parts = []
    try:
        u = os.uname()
        parts.append("%s %s" % (getattr(u, "machine", "?"), getattr(u, "release", "?")))
    except Exception:
        pass
    try:
        parts.append("Python 语言级别 %s（MicroPython 声称）" % getattr(sys, "version", "?").split()[0])
    except Exception:
        pass
    return " | ".join(parts) if parts else "未知"


# 传感器 ID → 型号。**参考表，不是事实来源**：事实是 `get_id()` 打出来的那个十六进制值。
# 为什么关心这一条：**OV5640 自带 JPEG 输出，OV7725 没有** —— 这决定 VGA JPEG 那几行是
# "传感器直出压缩流"还是"固件先整帧再压缩"（后者在 H7 上必然内存不够）。
_SENSOR_ID_REF = {0x7725: "OV7725", 0x5640: "OV5640", 0x2640: "OV2640", 0x2145: "GC2145"}


def _probe_env():
    """把"这台相机到底是什么"能问到的全问一遍，返回 dict。任何人答不上来都只是 "?"。

    为什么值得多打这么多行：矩阵的每一行只有在**知道板型 / 固件 / 传感器型号**时才有意义，
    而且报告要能追溯到具体固件（v5.0.0 把 `sensor` 换成了 `csi`，同一份脚本走的是不同分支）。
    **拿不到的一律记 "?"，绝不猜、也绝不影响测试**（每条都 try/except 兜住）。
    """
    facts = {}
    try:
        u = os.uname()
        for k in ("sysname", "nodename", "release", "version", "machine"):
            facts["uname." + k] = getattr(u, k, None)
    except Exception as e:
        facts["uname"] = "拿不到（%s）" % e
    for k in ("platform", "maxsize", "byteorder"):
        try:
            facts["sys." + k] = getattr(sys, k, None)
        except Exception:
            pass
    try:
        facts["sys.version"] = getattr(sys, "version", None)
    except Exception:
        pass
    try:
        impl = sys.implementation
        facts["sys.implementation"] = "%s %s" % (
            getattr(impl, "name", "?"),
            ".".join(str(x) for x in getattr(impl, "version", ()) or ()))
    except Exception:
        pass
    # 真正的 OpenMV 固件版本 / 板型 / 架构：优先问 `omv` 模块（新版固件才有）
    try:
        import omv as _omv
        for name in ("version", "board_type", "board_id", "arch"):
            fn = getattr(_omv, name, None)
            if fn is None:
                continue
            try:
                facts["omv." + name] = fn() if callable(fn) else fn
            except Exception as e:
                facts["omv." + name] = "拿不到（%s）" % e
    except Exception as e:
        facts["omv"] = "不可用（%s）" % e
    # 相机模块自己的版本属性（v4/v5 都可能有，命名不一定）
    try:
        _open_camera()
        for name in ("version", "__version__"):
            v = getattr(_CAM_MOD, name, None)
            if v is not None:
                facts["%s.%s" % (_CAM_API, name)] = v
    except Exception:
        pass
    # 传感器 ID：确认到底是哪颗 sensor（见 _SENSOR_ID_REF 的说明）
    try:
        _open_camera()
        fn = getattr(_CAM, "get_id", None) or getattr(_CAM_MOD, "get_id", None)
        if fn is None:
            facts["sensor_id"] = "本固件没有 get_id()"
        else:
            sid = fn()
            facts["sensor_id"] = "0x%04X" % sid
            facts["sensor_id_解读"] = "%s（参考表，以左侧十六进制值为准）" % \
                _SENSOR_ID_REF.get(sid, "参考表里没有这个值 —— 请按数值查官方资料")
    except Exception as e:
        facts["sensor_id"] = "拿不到（%s）" % e
    return facts


def _cam_reset():
    _open_camera()
    _CAM.reset()


def _cam_set_pixformat(name):
    _open_camera()
    val = getattr(_CAM_MOD, name)
    if _CAM_API == "csi":
        _CAM.pixformat(val)
    else:
        _CAM.set_pixformat(val)


def _cam_set_framesize(name):
    _open_camera()
    val = getattr(_CAM_MOD, name)
    if _CAM_API == "csi":
        _CAM.framesize(val)
    else:
        _CAM.set_framesize(val)


def _cam_warmup(ms):
    """等自动曝光稳定。v5 用 snapshot(time=)；v4 用 skip_frames(time=)。"""
    _open_camera()
    if _CAM_API == "csi":
        _CAM.snapshot(time=ms)
    else:
        _CAM.skip_frames(time=ms)


def _cam_snapshot():
    _open_camera()
    return _CAM.snapshot()


def _cam_has_attr(name):
    _open_camera()
    return getattr(_CAM, name, None) is not None or getattr(_CAM_MOD, name, None) is not None


def _blink():
    """让用户 LED 闪一下表示进度。**纯装饰**：任何失败都静默忽略，绝不影响测试。"""
    global _LED
    try:
        if _LED is None:
            try:
                from machine import LED
                _LED = [LED("LED_RED"), LED("LED_GREEN"), LED("LED_BLUE")]
            except Exception:
                try:
                    import pyb
                    _LED = [pyb.LED(1), pyb.LED(2), pyb.LED(3)]
                except Exception:
                    _LED = []
        if _LED:
            led = _LED[int(time.ticks_ms() / 100) % len(_LED)]
            led.toggle()
    except Exception:
        pass


# ===========================================================================
# ↓↓↓ 要改的配置都在这里 ↓↓↓
# ===========================================================================

MODE = "matrix"

# matrix 模式要尝试的组合：(pixformat 名, framesize 名, 帧缓冲数, 分组)
#   帧缓冲数 1 = 单缓冲；2 = 双缓冲（OpenMV 官方示例里 VGA JPEG 从 11.7 → 20 fps 的做法）
#
# ⚠️ **顺序是刻意的，不要随手重排**（2026-09-24 第一次真机运行的教训）：
#   实测已证明「RGB565 / VGA」会抛 `RuntimeError: Frame buffer overflow`
#   （需 614400 B > 片上可用 308944 B）。**内存耗尽的极端情况下相机会卡死/重启，
#   排在它后面的行会全部丢失** —— 第一次真机运行正是只拿回了前两组（RGB565 VGA 失败行 +
#   RGB565 QVGA 成功行），后面 5 组一行都没打印出来，分不清是"没跑到"还是"跑崩了"。
#   所以排序原则改成「**先拿信息量最大、最便宜的，把已知会炸的放最后**」：
#     组 small：小图 + **JPEG 的真实压缩后字节数**（A 线旁路画面链路要按它做带宽预算）
#     组 mid  ：中等图，含已知能成的 RGB565/QVGA（复测一遍，确认可复现）
#     组 big  ：大图 / 已知会抛 overflow 的组合（**故意排最后**，炸了也只剩它自己没跑完）
#   `MATRIX_STAGE` 可以只跑其中一组 —— 万一某组让相机卡死，分段跑不会连带丢掉别的组。
MATRIX = (
    ("JPEG",      "QVGA", 1, "small"),
    ("JPEG",      "QVGA", 2, "small"),
    ("GRAYSCALE", "QQVGA", 1, "small"),
    ("RGB565",    "QQVGA", 1, "small"),
    ("RGB565",    "QVGA", 1, "mid"),      # ← 2026-09-24 实测：均值 39.76 fps
    ("GRAYSCALE", "QVGA", 1, "mid"),
    ("GRAYSCALE", "VGA",  1, "big"),      # ← 边界：307200 B vs 启动时可用 308944 B（只差 1744 B）
    ("JPEG",      "VGA",  1, "big"),
    ("JPEG",      "VGA",  2, "big"),      # ← 重点：双缓冲能不能把 VGA JPEG 拉到 20 fps
    ("RGB565",    "VGA",  1, "big"),      # ← 已知会失败（留作证据，不要删）
)

# "auto" = 全表（按上面的顺序）；"small" / "mid" / "big" = 只跑那一组
MATRIX_STAGE = "auto"

# 每个组合测多久（秒）。太短会被启动瞬态污染；2 秒是"够稳又不太慢"的折中。
MATRIX_SECONDS = 2.0

# sustained / dump 用的参数（**以 matrix 的实测结果为准来填**）
CHOSEN_PIXFORMAT  = "JPEG"
CHOSEN_FRAMESIZE  = "VGA"
CHOSEN_FRAMEBUFFERS = 2

SUSTAINED_SECONDS = 10.0     # 长跑时长
DUMP_FRAMES       = 10       # 落盘帧数
JPEG_QUALITY      = 90       # 仅 JPEG 格式有效

# 落盘位置：**只认 SD 卡**。
# ⚠️ **挂载点在 v5 变了**：v5 的 H7 文档写 "mounted automatically at `/sdcard`"，
#    而 v4.x 用的是 `/sd`。两个都列，谁先能用用谁。
# ⚠️ 为什么把 /flash 也列进来却几乎必然被跳过：OpenMV Cam H7 的板载 FAT 盘极小
#    （官方开发者："the onboard flash on the H7 is extremely small"；社区实测 main.py
#    涨到 43 KB 就报 Not enough disk space），而一帧 320x240 RGB565 就有 153,600 B。
#    列出来是为了让 `_first_writable` 明确打印"跳过了它、为什么"，而不是悄悄失败。
DUMP_DIR_CANDIDATES = ("/sdcard/vigilens_frames", "/sd/vigilens_frames",
                       "/flash/vigilens_frames")
# 报告只有几 KB，可以放 /flash（但仍会先查容量）。
REPORT_PATH_CANDIDATES = ("/sdcard/vigilens_report.json", "/sd/vigilens_report.json",
                          "/flash/vigilens_report.json")

# 契约口径（docs/interface.md §0），仅用于「差距有多大」的对照展示，**不是实测值**
CONTRACT_W, CONTRACT_H = 640, 480
CONTRACT_BPP = 3             # RGB888

# 仅用于把实测帧率折算成"最短可检出闭眼"给人看，**脚本不做任何判断**。
# 来源：config.yaml 的 `min_close_frames: 3`（连续 3 帧低于阈值才记一次闭眼）。
# ⚠️ 相机上读不到 config.yaml，所以这里是**引用值**；真要改阈值请改 config.yaml（三人共用文件）。
MIN_CLOSE_FRAMES_FOR_DISPLAY = 3

# ===========================================================================

_BPP = {"GRAYSCALE": 1, "RGB565": 2, "BAYER": 2, "JPEG": 0, "PNG": 0}

# JPEG 的"名义字节数"除数 —— **只用于给组合排序**（VGA ≈ 51 KB），
# 不是实测值、更不是承诺。**真实压缩后字节数由 matrix 的 `B/帧` 列实测给出。**
_JPEG_NOMINAL_DIV = 6

# 分辨率名 → (w, h)。只列本脚本用到的；不认识的返回 (0,0)，**不猜**。
_SIZE_WH = {"QQVGA": (160, 120), "QVGA": (320, 240), "VGA": (640, 480)}


def _est_bytes(pf_name, fs_name):
    """估算一组组合的单帧占用，仅用于「排序」和「装不装得下」的预判。"""
    w, h = _SIZE_WH.get(fs_name, (0, 0))
    if not w:
        return 0
    bpp = _BPP.get(pf_name, 0)
    if bpp:
        return w * h * bpp
    if pf_name == "JPEG":
        return w * h // _JPEG_NOMINAL_DIV
    return 0


def _fmt_theoretical(pf_name, val):
    """`理论B` 列的显示：压缩格式（JPEG）的值前面加 `~`，提示它是名义值不是实测值。"""
    if not val:
        return "-"
    return ("~%d" % val) if _BPP.get(pf_name, 0) == 0 else str(val)


def log(*a):
    print(*a)


def _mem_free():
    gc.collect()
    return gc.mem_free()


def _mkdir(path):
    try:
        os.mkdir(path)
        return True
    except OSError:
        # 已存在也算成功
        try:
            os.stat(path)
            return True
        except OSError:
            return False


def _free_bytes(path):
    """该挂载点的可用字节数；拿不到就返回 None（**不猜** 0、也不猜很大）。

    为什么必须查这个：**OpenMV Cam H7 的板载 FAT 盘极小** —— 官方开发者原话是
    "the onboard flash on the H7 is extremely small"，社区实测 **main.py 涨到 43 KB
    就报 `Not enough disk space`**。而一帧 320x240 RGB565 就有 153,600 B ——
    往 /flash 写帧必然是"内存不足"。所以落盘前必须先问容量。
    """
    try:
        st = os.statvfs(path)
        return st[0] * st[3]          # f_bsize * f_bfree
    except Exception:
        return None


def _first_writable(cands, is_dir, need_bytes=0):
    """挑第一个**确实装得下**的路径。

    与旧版的区别：旧版只要 mkdir 成功就返回，于是会在 H7 那个 43 KB 的 /flash 上
    "成功创建目录"然后每帧写失败。现在：
      · 拿得到容量 → 可用字节 < need_bytes 就跳过（并说明）
      · 拿不到容量 → **对 /flash 直接跳过**（极小且拿不到容量时不能赌）
    """
    for c in cands:
        parent = c if is_dir else c.rsplit("/", 1)[0]
        if parent in ("", "/"):
            return c
        if not _mkdir(parent):
            continue
        free = _free_bytes(parent)
        if free is None:
            if parent.startswith("/flash"):
                log("  跳过 %s：拿不到可用容量，且 /flash 在 H7 上极小（约 43 KB 级），不赌"
                    % parent)
                continue
            return c
        if need_bytes and free < need_bytes:
            log("  跳过 %s：可用 %d B < 需要 %d B" % (parent, free, need_bytes))
            continue
        return c
    return None


def _to_bytes(img):
    """把 image 对象变成 bytes。OpenMV 固件版本间 API 有差异，逐个试。"""
    for name in ("bytearray", "to_bytes"):
        fn = getattr(img, name, None)
        if fn is not None:
            try:
                return bytes(fn())
            except Exception:
                pass
    try:
        return bytes(img)
    except Exception as e:
        raise RuntimeError(
            "拿不到图像字节（试过 bytearray()/to_bytes()/bytes()）。"
            "请在 IDE 里 print(dir(img)) 看本固件提供哪个方法。原始错误: %s" % e)


def _set_framebuffers(n):
    """设置帧缓冲数量。老固件可能没有这个 API，缺失时明确报告而不是静默忽略。

    v5 的 `csi.CSI` 与 v4 的 `sensor` 模块都可能有这个方法，两个都探。
    """
    _open_camera()
    fn = getattr(_CAM, "set_framebuffers", None) or getattr(_CAM_MOD, "set_framebuffers", None)
    if fn is None:
        return None
    try:
        fn(n)
        return True
    except Exception as e:
        return "err:%s" % e


def _apply(pf_name, fs_name, fb_count):
    """按给定组合配置传感器。返回 (ok, 说明)。**v4/v5 都走这里。**"""
    _open_camera()
    pf = getattr(_CAM_MOD, pf_name, None)
    fs = getattr(_CAM_MOD, fs_name, None)
    if pf is None:
        return False, "本固件（%s API）无 %s 常量" % (_CAM_API, pf_name)
    if fs is None:
        return False, "本固件（%s API）无 %s 常量" % (_CAM_API, fs_name)

    _cam_reset()
    fbres = _set_framebuffers(fb_count)
    _cam_set_pixformat(pf_name)
    _cam_set_framesize(fs_name)
    _cam_warmup(800)
    note = "%s API" % _CAM_API
    if fb_count > 1:
        if fbres is None:
            note += "；本固件无 set_framebuffers()，实际仍是单缓冲"
        elif isinstance(fbres, str):
            note += "；set_framebuffers(%d) 失败：%s" % (fb_count, fbres)
        else:
            note += "；双缓冲已生效"
    return True, note


# ---------------------------------------------------------------------------
# 帧率测量（用 ticks_us 逐帧打点，能给出抖动而不只是一个平均值）
# ---------------------------------------------------------------------------


def _stats(deltas_us):
    """从逐帧间隔（微秒）算帧率统计。返回 dict；样本不足时给 None。"""
    n = len(deltas_us)
    if n == 0:
        return {"samples": 0}
    xs = sorted(deltas_us)
    total_us = sum(xs)
    mean_us = total_us / n
    # 中位数
    mid = n // 2
    med_us = xs[mid] if (n % 2) else (xs[mid - 1] + xs[mid]) / 2.0
    # p95 间隔（= 最慢的 5%）→ 折算成"最低瞬时帧率"
    p95_us = xs[min(n - 1, int(n * 0.95))]
    return {
        "samples": n,
        "elapsed_s": round(total_us / 1e6, 3),
        "fps_mean": round(1e6 / mean_us, 2),
        "fps_median": round(1e6 / med_us, 2),
        "fps_min_inst": round(1e6 / xs[-1], 2),      # 单帧最慢
        "fps_p95_inst": round(1e6 / p95_us, 2),      # 95 分位间隔对应的帧率
        "fps_max_inst": round(1e6 / xs[0], 2),       # 单帧最快
        "interval_us_mean": int(mean_us),
        "interval_us_min": xs[0],
        "interval_us_max": xs[-1],
        "jitter_us_std": int(_std(xs, mean_us)),
    }


def _std(xs, mean):
    if len(xs) < 2:
        return 0.0
    acc = 0.0
    for x in xs:
        d = x - mean
        acc += d * d
    return (acc / (len(xs) - 1)) ** 0.5


def _measure(seconds):
    """在当前传感器配置下连续 snapshot，测帧率 + 单帧字节 + 内存占用。"""
    mem_before = _mem_free()
    t_end = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
    deltas = []
    n = 0
    first_bytes = 0
    w = h = 0
    last_t = time.ticks_us()
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        img = _cam_snapshot()
        now = time.ticks_us()
        if n > 0 or True:
            dt = time.ticks_diff(now, last_t)
            if dt > 0:
                deltas.append(dt)
        last_t = now
        if n == 0:
            try:
                w, h = img.width(), img.height()
            except Exception:
                pass
            try:
                first_bytes = len(_to_bytes(img))
            except Exception:
                first_bytes = 0
        n += 1
    mem_after = _mem_free()
    st = _stats(deltas)
    st["frames"] = n
    st["w"] = w
    st["h"] = h
    st["bytes_per_frame"] = first_bytes
    st["mem_free_before"] = mem_before
    st["mem_free_after"] = mem_after
    return st


# ---------------------------------------------------------------------------
# 模式 1：能力矩阵
# ---------------------------------------------------------------------------


def _matrix_rows():
    """按 `MATRIX_STAGE` 过滤出本轮要跑的组合（**保持 MATRIX 里的顺序**）。"""
    if MATRIX_STAGE == "auto":
        return list(MATRIX)
    return [r for r in MATRIX if r[3] == MATRIX_STAGE]


def _print_verdict(results, mem_start):
    """把上面那张表**自动**折算成「这对契约意味着什么」。

    只用表里的实测数字做算术，**不引入任何新假设、不引用任何预期值**。
    这一段的存在理由：第一次真机运行拿回表以后，人还是得自己算"这够不够"——
    而算错/算漏正是《05》铁律 1 想防的事情。让脚本自己算，人只负责核对。
    """
    contract_bytes = CONTRACT_W * CONTRACT_H * CONTRACT_BPP
    ok_rows = [r for r in results if r.get("ok")]
    bad_rows = [r for r in results if not r.get("ok")]
    log("")
    log("=" * 78)
    log("契约可行性小结（下面每一个数字都来自上面那张表的实测行）")
    log("=" * 78)
    log("  · 契约帧 = %dx%d RGB888 = %d B/帧；启动时 gc.mem_free() 实测 = %d B（相差 %.1f 倍）"
        % (CONTRACT_W, CONTRACT_H, contract_bytes, mem_start,
           contract_bytes / float(mem_start) if mem_start else 0.0))
    log("  · 本轮成功 %d 组 / 失败 %d 组" % (len(ok_rows), len(bad_rows)))
    if not ok_rows:
        log("  · **一组都没成功** —— 先看上面的报错原文，别急着改脚本。")
        return
    biggest = max(ok_rows, key=lambda r: r.get("bytes_per_frame") or 0)
    log("  · 单帧实测字节数最大的成功组合：%s / %s / fb=%d → %d B（= 契约帧的 %.1f%%）"
        % (biggest["pixformat"], biggest["framesize"], biggest["framebuffers"],
           biggest.get("bytes_per_frame") or 0,
           100.0 * (biggest.get("bytes_per_frame") or 0) / contract_bytes))
    # 「整帧装得下契约帧吗」只对**未压缩**格式可算；JPEG 是压缩流，字节数不可比，故排除。
    fits = [r for r in ok_rows
            if _BPP.get(r["pixformat"], 0) and (r.get("bytes_theoretical") or 0) >= contract_bytes]
    log("  · 「整帧装得下契约帧（≥%d B）」的成功组合：%s"
        % (contract_bytes,
           "、".join("%s/%s" % (r["pixformat"], r["framesize"]) for r in fits) if fits
           else "**无**"))
    fast = [r for r in ok_rows if (r.get("fps_mean") or 0) >= 30.0]
    log("  · 「实测均值帧率 ≥ 30 fps」的成功组合：%s"
        % ("、".join("%s/%s=%.2f fps" % (r["pixformat"], r["framesize"], r["fps_mean"])
                     for r in fast) if fast else "**无**"))
    slow = min(ok_rows, key=lambda r: r.get("fps_min_inst") or 1e9)
    worst = slow.get("fps_min_inst") or 0
    log("  · 最坏单帧（决定「最坏采样间隔」）：%s / %s → %.2f fps（约 %.1f ms/帧）"
        % (slow["pixformat"], slow["framesize"], worst, (1000.0 / worst) if worst else 0.0))
    for r in [x for x in ok_rows if x.get("fps_mean")]:
        f = r["fps_mean"]
        log("      · 按 config.yaml 的 min_close_frames=%d 折算 %s/%s/fb=%d：最短可检出闭眼 ≈ %.0f ms"
            % (MIN_CLOSE_FRAMES_FOR_DISPLAY, r["pixformat"], r["framesize"], r["framebuffers"],
               MIN_CLOSE_FRAMES_FOR_DISPLAY / f * 1000.0))
    log("      （正常眨眼 100~400 ms —— 短于左侧值的眨眼会**整段漏掉**，这是系统性低估疲劳）")
    if not fits:
        log("  · **算术推论（前提就是上面那列 mem_free 实测值）**：H7 留给帧缓冲的可用 RAM")
        log("    装不下契约帧，而且**这与接哪个口无关** —— 是**片上内存**限制，不是链路带宽限制。")
        log("    所以：OpenMV Cam H7 **不可能**作为契约 §0（640x480 RGB888 @30fps）的像素源；")
        log("    它在本项目里的位置是**降规格采集源**（QVGA 级）与「人脸检测/追踪目标」，见 docs/10 §3。")
        log("    要满足契约只能换 MIPI CSI 摄像头（Mizar-Z7020 自带该口），见 docs/12 §12。")


def run_matrix():
    log("=" * 78)
    log("OpenMV 图像采集能力矩阵 —— 结果请原样贴回项目记录，不要手改任何数字")
    log("=" * 78)
    log("固件: %s" % getattr(sys, "version", "?"))
    log("契约要求(docs/interface.md §0): %dx%d RGB888 @30fps = %d B/帧, %.3f MB/s" %
        (CONTRACT_W, CONTRACT_H, CONTRACT_W * CONTRACT_H * CONTRACT_BPP,
         CONTRACT_W * CONTRACT_H * CONTRACT_BPP * 30 / 1e6))
    gc.collect()
    mem_start = gc.mem_free()
    log("启动时 gc.mem_free() = %d B（片上可用内存，用来判断帧缓冲装不装得下）" % mem_start)
    rows = _matrix_rows()
    log("本轮 MATRIX_STAGE=%s → %d 组。**下面的顺序就是执行顺序**：" % (MATRIX_STAGE, len(rows)))
    for i, (pf_name, fs_name, fb, stage) in enumerate(rows):
        est = _est_bytes(pf_name, fs_name)
        log("   %2d. %-10s %-8s fb=%d [%-5s] 预计整帧 %s B%s"
            % (i + 1, pf_name, fs_name, fb, stage, _fmt_theoretical(pf_name, est),
               "   ← 比可用内存大，**预期失败**" if est and est > mem_start else ""))
    log("")
    log("%-10s %-8s %2s %8s %8s %8s %10s %10s %9s  %s" %
        ("pixformat", "size", "fb", "fps均值", "fps中位", "最慢帧fps", "B/帧", "理论B",
         "mem可用", "备注"))
    log("-" * 118)

    results = []
    for idx, (pf_name, fs_name, fb, stage) in enumerate(rows):
        row = {"pixformat": pf_name, "framesize": fs_name, "framebuffers": fb,
               "stage": stage}
        est = _est_bytes(pf_name, fs_name)
        row["bytes_theoretical"] = est
        log("")
        log("---- [%d/%d] %s / %s / fb=%d 开始（此刻 gc.mem_free() = %d B）"
            % (idx + 1, len(rows), pf_name, fs_name, fb, _mem_free()))
        log("     提示：**如果这行之后就没有输出了，是这一组把相机搞死了**（内存耗尽/卡死），")
        log("           不是脚本逻辑问题 —— 请把最后一行原样报回，并用 MATRIX_STAGE 分段排查。")
        try:
            ok, note = _apply(pf_name, fs_name, fb)
            if not ok:
                row["ok"] = False
                row["error"] = note
                row["mem_free_after"] = _mem_free()
                log("%-10s %-8s %2d %8s %8s %8s %10s %10s %9d  %s" %
                    (pf_name, fs_name, fb, "-", "-", "-", "-",
                     _fmt_theoretical(pf_name, est), row["mem_free_after"], note))
                results.append(row)
                continue
            st = _measure(MATRIX_SECONDS)
            row["ok"] = True
            row.update(st)
            row["note"] = note
            bpp = _BPP.get(pf_name, 0)
            if bpp and st["w"] and st["h"]:
                theoretical = st["w"] * st["h"] * bpp
            else:
                theoretical = est          # 压缩格式：保持**名义值**（显示时前面带 ~）
            row["bytes_theoretical"] = theoretical
            log("%-10s %-8s %2d %8.2f %8.2f %8.2f %10d %10s %9d  %s" %
                (pf_name, fs_name, fb, st["fps_mean"], st["fps_median"],
                 st["fps_min_inst"], st["bytes_per_frame"],
                 _fmt_theoretical(pf_name, theoretical),
                 st["mem_free_after"], note))
            if bpp and st["bytes_per_frame"] and theoretical and \
                    abs(st["bytes_per_frame"] - theoretical) > theoretical * 0.05:
                log("            ⚠️ 实得字节数与 w*h*bpp 不符（%d vs %d）—— 说明实际分辨率或格式与设定不同"
                    % (st["bytes_per_frame"], theoretical))
        except Exception as e:
            row["ok"] = False
            row["error"] = "%s: %s" % (type(e).__name__, e)
            row["mem_free_after"] = _mem_free()
            log("%-10s %-8s %2d %8s %8s %8s %10s %10s %9d  %s" %
                (pf_name, fs_name, fb, "-", "-", "-", "-",
                 _fmt_theoretical(pf_name, est), row["mem_free_after"], row["error"]))
        results.append(row)
        _blink()

    log("-" * 118)
    log("")
    log("怎么读这张表：")
    log("  · '最慢帧fps' 很重要 —— 它的倒数就是运动/眨眼检测里**最坏情况的采样间隔**。")
    log("    第一行失败、第二行成功时容易看错：**'最慢帧fps' 是帧率不是毫秒**（16.08 = 62.2 ms/帧）。")
    log("  · '理论B' 是 w*h*每像素字节；**前面带 `~` 的是压缩格式（JPEG）的名义值，不是实测值**，")
    log("    那种格式只看 'B/帧' 列。未压缩格式若两者差很多，说明实际生效的分辨率/格式与请求不同。")
    log("  · 契约要 640x480 RGB888 = 921600 B/帧。H7 的 STM32H743 标称 1MB SRAM，")
    log("    但**留给帧缓冲/图片处理的可用 RAM 小得多**（2026-09-24 实测 gc.mem_free() = 308944 B）——")
    log("    这正好解释了官方规格表为什么写 'RGB565 上限 320x240'（VGA RGB565 = 614400 B，装不下）。")
    log("    所以'能不能整帧装下'要看上面那列 mem_free，**这是实测不是推算**。")
    log("  · 帧率判据：契约要 30fps。低于 30 时注意 config.yaml 的 min_close_frames=3")
    log("    会让'最短可检出闭眼 = 3/fps'变大（10fps→300ms），会系统性漏掉短眨眼。")
    _print_verdict(results, mem_start)
    return results


# ---------------------------------------------------------------------------
# 模式 2：长跑
# ---------------------------------------------------------------------------


def run_sustained():
    log("=" * 78)
    log("长跑 %.1fs：%s / %s / fb=%d" % (SUSTAINED_SECONDS, CHOSEN_PIXFORMAT,
                                       CHOSEN_FRAMESIZE, CHOSEN_FRAMEBUFFERS))
    log("=" * 78)
    ok, note = _apply(CHOSEN_PIXFORMAT, CHOSEN_FRAMESIZE, CHOSEN_FRAMEBUFFERS)
    if not ok:
        log("配置失败：%s" % note)
        return None
    st = _measure(SUSTAINED_SECONDS)
    res = {"pixformat": CHOSEN_PIXFORMAT, "framesize": CHOSEN_FRAMESIZE,
           "framebuffers": CHOSEN_FRAMEBUFFERS, "ok": True, "note": note}
    res.update(st)
    log("")
    log("帧数        : %d" % st["frames"])
    log("帧率 均值   : %.2f fps   中位: %.2f fps" % (st["fps_mean"], st["fps_median"]))
    log("瞬时 最低/最高: %.2f / %.2f fps" % (st["fps_min_inst"], st["fps_max_inst"]))
    log("帧间隔      : 均值 %d us / 最短 %d / 最长 %d us（抖动 std %d us）" %
        (st["interval_us_mean"], st["interval_us_min"], st["interval_us_max"], st["jitter_us_std"]))
    log("每帧字节    : %d" % st["bytes_per_frame"])
    log("实际吞吐    : %.3f MB/s" % (st["bytes_per_frame"] * st["fps_mean"] / 1e6))
    log("gc.mem_free : 前 %d → 后 %d" % (st["mem_free_before"], st["mem_free_after"]))
    return res


# ---------------------------------------------------------------------------
# 模式 3：无损落盘（原样字节 + sidecar）
# ---------------------------------------------------------------------------


def run_dump():
    log("=" * 78)
    log("落盘 %d 帧：%s / %s / fb=%d" % (DUMP_FRAMES, CHOSEN_PIXFORMAT,
                                       CHOSEN_FRAMESIZE, CHOSEN_FRAMEBUFFERS))
    log("=" * 78)

    # 先按目标配置估一帧要多大，再决定往哪写。
    # 保守放大 1.25 倍：JPEG 大小不定，未压缩格式还要留元数据空间。
    bpp = _BPP.get(CHOSEN_PIXFORMAT, 0)
    est_per_frame = 1
    for _name, _w, _h in (("VGA", 640, 480), ("QVGA", 320, 240)):
        if _name == CHOSEN_FRAMESIZE:
            est_per_frame = max(1, int(_w * _h * (bpp or 2) * 1.25))
    need = est_per_frame * DUMP_FRAMES

    dump_dir = _first_writable(DUMP_DIR_CANDIDATES, True, need_bytes=need)
    if dump_dir is None:
        log("")
        log("**落盘终止：找不到能装下 %d 帧（约 %d B）的可写目录。**" % (DUMP_FRAMES, need))
        log("  试过的候选：%s" % (DUMP_DIR_CANDIDATES,))
        log("")
        log("  为什么不能在 /flash 上写：**OpenMV Cam H7 的板载 FAT 盘极小**。")
        log("  官方开发者的原话是 'the onboard flash on the H7 is extremely small'；")
        log("  社区实测 main.py 涨到 **43 KB** 就报 `Not enough disk space`。")
        log("  而一帧 320x240 RGB565 = 153,600 B —— 一帧都放不下。")
        log("")
        log("  怎么办：**插一张 micro SD 卡**，然后重跑 dump 模式（数据写到 /sd）。")
        log("  没有 SD 卡也能拿到同样的信息：先用 matrix 模式（纯内存，不落盘）。")
        return None
    log("输出目录：%s（计划写入约 %d B）" % (dump_dir, need))
    free = _free_bytes(dump_dir)
    if free is not None:
        log("该挂载点可用：%d B" % free)

    ok, note = _apply(CHOSEN_PIXFORMAT, CHOSEN_FRAMESIZE, CHOSEN_FRAMEBUFFERS)
    if not ok:
        log("配置失败：%s" % note)
        return None
    if note:
        log("备注：%s" % note)

    frames = []
    t0 = time.ticks_ms()
    for i in range(DUMP_FRAMES):
        img = _cam_snapshot()
        if CHOSEN_PIXFORMAT == "JPEG":
            blob = _to_bytes(img.compress(quality=JPEG_QUALITY))
        else:
            blob = _to_bytes(img)
        path = "%s/frame_%04d.raw" % (dump_dir, i)
        with open(path, "wb") as f:
            f.write(blob)
        # 立刻回读校验 —— SD 卡写入失败/截断很常见，不校验就是"看起来成功"
        try:
            with open(path, "rb") as f:
                back = f.read()
        except Exception as e:
            back = b""
            log("  帧 %d 回读失败：%s" % (i, e))
        frames.append({
            "index": i,
            "file": path.rsplit("/", 1)[-1],
            "bytes_written": len(blob),
            "bytes_readback": len(back),
            "ok": len(back) == len(blob),
            "w": img.width(),
            "h": img.height(),
        })
        log("  帧 %d: 写入 %d B / 回读 %d B  %s" %
            (i, len(blob), len(back), "OK" if len(back) == len(blob) else "!! 不一致"))
        _blink()

    bad = [f for f in frames if not f["ok"]]

    # ---- 像素格式对拍样本 ------------------------------------------------
    # 为什么需要这个：RGB565 → RGB888 的**位扩展方式有两种常见变体**
    #   replicate: r8 = (r5<<3)|(r5>>2)   （能到 255）
    #   shift    : r8 = r5<<3             （最大只到 248）
    # 到底 OpenMV 用哪种，**不能猜**。OpenMV 的 get_pixel 支持 rgbtuple 参数：
    #   raw16 = img.get_pixel(x, y)             → RGB565 的 16 bit 原值
    #   (r,g,b) = img.get_pixel(x, y, rgbtuple=True) → 驱动自己展开的 8 bit
    # 把两者都记下来，PC 侧就能**反推**出唯一正确的公式（raw_to_contract.py --calibrate）。
    samples = []
    if CHOSEN_PIXFORMAT in ("RGB565", "GRAYSCALE", "BAYER"):
        try:
            img = _cam_snapshot()
            w, h = img.width(), img.height()
            # 取若干固定位置：四角 + 中心 + 三分点，尽量覆盖不同灰度
            pts = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                   (w // 2, h // 2), (w // 3, h // 3), (2 * w // 3, 2 * h // 3)]
            for (x, y) in pts:
                try:
                    raw = img.get_pixel(x, y)
                except Exception:
                    raw = None
                try:
                    trip = img.get_pixel(x, y, rgbtuple=True)
                except Exception:
                    trip = None
                samples.append({"x": x, "y": y, "raw": raw,
                                "rgb": list(trip) if trip is not None else None})
            log("")
            log("已记录 %d 个像素对拍样本（raw16 ↔ 驱动的 r,g,b），供 PC 侧反推扩展公式："
                % len(samples))
            for s in samples:
                log("   (%4d,%4d) raw=%s rgb=%s" % (s["x"], s["y"], s["raw"], s["rgb"]))
        except Exception as e:
            log("⚠️ 取像素对拍样本失败：%s" % e)

    meta = {
        "source": "OpenMV Cam H7 (openmv_capture_test.py)",
        "pixformat": CHOSEN_PIXFORMAT,
        "framesize": CHOSEN_FRAMESIZE,
        "framebuffers": CHOSEN_FRAMEBUFFERS,
        "jpeg_quality": JPEG_QUALITY if CHOSEN_PIXFORMAT == "JPEG" else None,
        "width": frames[0]["w"] if frames else None,
        "height": frames[0]["h"] if frames else None,
        "bytes_per_pixel": _BPP.get(CHOSEN_PIXFORMAT, 0),
        "frame_count": len(frames),
        "elapsed_ms": time.ticks_diff(time.ticks_ms(), t0),
        "frames": frames,
        "readback_failures": len(bad),
        "pixel_samples": samples,
        # 布局说明：本文件是 img.bytearray() 的**原样字节**，没有做任何转换。
        # 转换必须在 PC 上用 raw_to_contract.py 做，保证"口径只有一处实现"。
        # 若本格式是 JPEG，则是有损压缩，**不能**当 C 线黄金参考。
        "layout": "raw img.bytearray(); row-major, no channel conversion",
    }
    log("")
    log("落盘完成：%d 帧，回读失败 %d 帧" % (len(frames), len(bad)))
    return meta, dump_dir


# ---------------------------------------------------------------------------
# 报告落盘
# ---------------------------------------------------------------------------


def write_report(report):
    path = _first_writable(REPORT_PATH_CANDIDATES, False)
    if path is None:
        log("⚠️ 找不到可写路径写 report.json（试过 %s）" % (REPORT_PATH_CANDIDATES,))
        return None
    try:
        import json
        text = json.dumps(report, ensure_ascii=False, indent=1)
    except Exception as e:
        log("⚠️ json 不可用（%s），改为写 Python repr" % e)
        text = repr(report)
    try:
        with open(path, "w") as f:
            f.write(text)
        log("报告已写入：%s（%d B）" % (path, len(text)))
        return path
    except Exception as e:
        log("⚠️ 写报告失败：%s" % e)
        return None


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main():
    # ⚠️ **必须声明 global**：下面的 `except` 分支会给 `_CAM_API` 赋值，若不声明，
    #    Python 就会把 `_CAM_API` 当成 main() 的**局部变量** —— 于是**成功路径**读它时抛
    #    `UnboundLocalError: cannot access local variable '_CAM_API'`，又被紧接着的
    #    `except Exception` 抓住，最终打印出**"相机 API : 探测失败"**这种假故障，
    #    并让 report.json 里的 `camera_api` 变成 null。
    #    2026-09-24 由 `metrics/logs/_openmv_fake_run.py`（本机假模块自检）抓到 ——
    #    真机上它同样会发生，只是那行"探测失败"很容易被当成"相机没连上"而放过。
    global _CAM_API
    log("")
    log("VigiLens / 知倦 —— OpenMV 图像采集测试，MODE=%s" % MODE)
    log("")

    # 先把"这是哪台相机、哪版固件、走哪套 API"打出来并写进报告。
    # 为什么重要：v5.0.0 把 `sensor` 换成了 `csi`（major 破坏性变更），
    # 同一份脚本在不同固件上走的是不同分支 —— 不记下来的数据没法追溯。
    ver = _cam_version()
    log("板子/版本线索 : %s" % ver)
    log("   ⚠️ 读法：`uname.release` 是 **MicroPython 的版本**，`sys.version` 首段（如 3.4.0）")
    log("      是 **MicroPython 声称的 Python 语言级别** —— 两个**都不是 OpenMV 固件版本**。")
    log("      真正的固件版本看下面的 `omv.*` 行；那行若拿不到，就说明本固件没有 `omv` 模块。")
    try:
        _open_camera()
        log("相机 API  : %s（%s）" % (_CAM_API, "csi.CSI 类 API（v5+）" if _CAM_API == "csi"
                                     else "sensor 模块 API（v4.x）"))
    except Exception as e:
        log("相机 API  : **探测失败** —— %s" % e)
        log("            （matrix 模式会逐个组合报错；请把下面的报错原文贴回项目记录）")
        _CAM_API = None

    env = _probe_env()
    log("")
    log("环境事实（逐项尽力获取，拿不到就是带说明的 '?'；**任何一项缺失都不影响测试**）:")
    for k in sorted(env.keys()):
        log("  %-26s : %s" % (k, env[k]))

    report = {
        "tool": "board/openmv/openmv_capture_test.py",
        "mode": MODE,
        "firmware": ver,
        "camera_api": _CAM_API,
        "env": env,
        "micropython": getattr(sys, "version", "?"),
        "platform": getattr(sys, "platform", "?"),
        "contract": {"width": CONTRACT_W, "height": CONTRACT_H, "bpp": CONTRACT_BPP,
                     "note": "docs/interface.md §0；本文件不含契约的实测结论"},
    }

    if MODE in ("matrix", "all"):
        report["matrix"] = run_matrix()
    if MODE in ("sustained", "all"):
        report["sustained"] = run_sustained()
    if MODE in ("dump", "all"):
        d = run_dump()
        if d:
            report["dump"] = d[0]
            report["dump_dir"] = d[1]

    log("")
    write_report(report)
    log("")
    log("下一步：")
    log("  · matrix 的表 → 贴回项目记录，并据此填 openmv_stream.py 的 STREAM_FRAMESIZE。")
    log("  · 若做了 dump → 把整个目录拷回 PC，跑（`metrics\\logs\\openmv_dump` 换成你的实际目录）：")
    log("      .venv\\Scripts\\python.exe board/openmv/raw_to_contract.py "
        "metrics\\logs\\openmv_dump --out metrics/logs/openmv_frames.bin")
    log("    （⚠️ 别把 <尖括号> 当命令照抄：PowerShell 里 < > 是保留运算符，无法执行）")
    log("    得到契约 §4.1 布局的 RGB888，可直接喂 A 线 / C 线。")


main()
