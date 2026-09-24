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
│ ⚠️ 本脚本**没有在真实 OpenMV 上运行过**（开发仓库的机器上没有硬件）。  │
│    它按 OpenMV 官方 API 编写；**所有帧率/内存数字都必须由你跑出来**，   │
│    不许把本文件里的任何"预期值"当成结论（AGENTS.md 铁律 1）。          │
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

import sensor
import pyb

# ===========================================================================
# ↓↓↓ 要改的配置都在这里 ↓↓↓
# ===========================================================================

MODE = "matrix"

# matrix 模式要尝试的组合：(pixformat 名, framesize 名, 帧缓冲数)
#   帧缓冲数 1 = 单缓冲；2 = 双缓冲（OpenMV 官方示例里 VGA JPEG 从 11.7 → 20 fps 的做法）
MATRIX = (
    ("RGB565",    "VGA",  1),
    ("RGB565",    "QVGA", 1),
    ("GRAYSCALE", "VGA",  1),
    ("GRAYSCALE", "QVGA", 1),
    ("JPEG",      "VGA",  1),
    ("JPEG",      "VGA",  2),   # ← 重点：双缓冲能不能把 VGA JPEG 拉到 20 fps
    ("JPEG",      "QVGA", 1),
)

# 每个组合测多久（秒）。太短会被启动瞬态污染；2 秒是"够稳又不太慢"的折中。
MATRIX_SECONDS = 2.0

# sustained / dump 用的参数（**以 matrix 的实测结果为准来填**）
CHOSEN_PIXFORMAT  = "JPEG"
CHOSEN_FRAMESIZE  = "VGA"
CHOSEN_FRAMEBUFFERS = 2

SUSTAINED_SECONDS = 10.0     # 长跑时长
DUMP_FRAMES       = 10       # 落盘帧数
JPEG_QUALITY      = 90       # 仅 JPEG 格式有效

# 落盘位置：SD 卡写大文件更合适；没有卡就退回内部 flash。
DUMP_DIR_CANDIDATES = ("/sd/vigilens_frames", "/flash/vigilens_frames")
REPORT_PATH_CANDIDATES = ("/sd/vigilens_report.json", "/flash/vigilens_report.json")

# 契约口径（docs/interface.md §0），仅用于「差距有多大」的对照展示，**不是实测值**
CONTRACT_W, CONTRACT_H = 640, 480
CONTRACT_BPP = 3             # RGB888

# ===========================================================================

_BPP = {"GRAYSCALE": 1, "RGB565": 2, "BAYER": 2, "JPEG": 0, "PNG": 0}


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


def _first_writable(cands, is_dir):
    """挑第一个能用的路径（SD 优先，退回 flash）。"""
    for c in cands:
        parent = c if is_dir else c.rsplit("/", 1)[0]
        if parent in ("", "/"):
            return c
        if _mkdir(parent):
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
    """设置帧缓冲数量。老固件可能没有这个 API，缺失时明确报告而不是静默忽略。"""
    fn = getattr(sensor, "set_framebuffers", None)
    if fn is None:
        return None
    try:
        fn(n)
        return True
    except Exception as e:
        return "err:%s" % e


def _apply(pf_name, fs_name, fb_count):
    """按给定组合配置传感器。返回 (ok, 说明)。"""
    pf = getattr(sensor, pf_name, None)
    fs = getattr(sensor, fs_name, None)
    if pf is None:
        return False, "本固件无 sensor.%s" % pf_name
    if fs is None:
        return False, "本固件无 sensor.%s" % fs_name

    sensor.reset()
    fbres = _set_framebuffers(fb_count)
    sensor.set_pixformat(pf)
    sensor.set_framesize(fs)
    sensor.skip_frames(time=800)
    note = ""
    if fb_count > 1:
        if fbres is None:
            note = "本固件无 set_framebuffers()，实际仍是单缓冲"
        elif isinstance(fbres, str):
            note = "set_framebuffers(%d) 失败：%s" % (fb_count, fbres)
        else:
            note = "双缓冲已生效"
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
        img = sensor.snapshot()
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


def run_matrix():
    log("=" * 78)
    log("OpenMV 图像采集能力矩阵 —— 结果请原样贴回项目记录，不要手改任何数字")
    log("=" * 78)
    log("固件: %s" % getattr(sys, "version", "?"))
    log("契约要求(docs/interface.md §0): %dx%d RGB888 @30fps = %d B/帧, %.3f MB/s" %
        (CONTRACT_W, CONTRACT_H, CONTRACT_W * CONTRACT_H * CONTRACT_BPP,
         CONTRACT_W * CONTRACT_H * CONTRACT_BPP * 30 / 1e6))
    gc.collect()
    log("启动时 gc.mem_free() = %d B（片上可用内存，用来判断帧缓冲装不装得下）" % gc.mem_free())
    log("")
    log("%-10s %-8s %2s %8s %8s %8s %10s %10s %9s  %s" %
        ("pixformat", "size", "fb", "fps均值", "fps中位", "最慢帧", "B/帧", "理论B", "mem可用", "备注"))
    log("-" * 118)

    results = []
    for pf_name, fs_name, fb in MATRIX:
        row = {"pixformat": pf_name, "framesize": fs_name, "framebuffers": fb}
        try:
            ok, note = _apply(pf_name, fs_name, fb)
            if not ok:
                row["ok"] = False
                row["error"] = note
                log("%-10s %-8s %2d %8s %8s %8s %10s %10s %9s  %s" %
                    (pf_name, fs_name, fb, "-", "-", "-", "-", "-", "-", note))
                results.append(row)
                continue
            st = _measure(MATRIX_SECONDS)
            row["ok"] = True
            row.update(st)
            row["note"] = note
            bpp = _BPP.get(pf_name, 0)
            theoretical = st["w"] * st["h"] * bpp if bpp else 0
            row["bytes_theoretical"] = theoretical
            log("%-10s %-8s %2d %8.2f %8.2f %8.2f %10d %10d %9d  %s" %
                (pf_name, fs_name, fb, st["fps_mean"], st["fps_median"],
                 st["fps_min_inst"], st["bytes_per_frame"], theoretical,
                 st["mem_free_after"], note))
            if pf_name in ("RGB565", "GRAYSCALE") and st["bytes_per_frame"] and theoretical and \
                    abs(st["bytes_per_frame"] - theoretical) > theoretical * 0.05:
                log("            ⚠️ 实得字节数与 w*h*bpp 不符（%d vs %d）—— 说明实际分辨率或格式与设定不同"
                    % (st["bytes_per_frame"], theoretical))
        except Exception as e:
            row["ok"] = False
            row["error"] = "%s: %s" % (type(e).__name__, e)
            log("%-10s %-8s %2d %8s %8s %8s %10s %10s %9s  %s" %
                (pf_name, fs_name, fb, "-", "-", "-", "-", "-", "-", row["error"]))
        results.append(row)
        pyb.LED(1).toggle()

    log("-" * 118)
    log("")
    log("怎么读这张表：")
    log("  · '最慢帧' 很重要 —— 它对应运动/眨眼检测里最坏情况的采样间隔。")
    log("  · '理论B' 是 w*h*每像素字节；若与 'B/帧' 差很多，说明实际生效的分辨率/格式与请求不同。")
    log("  · 契约要 640x480 RGB888 = 921600 B/帧；H7 只有 1MB SRAM，")
    log("    所以'能不能整帧装下'要看上面那列 gc.mem_free()，这是实测不是推算。")
    log("  · 帧率判据：契约要 30fps。低于 30 时注意 config.yaml 的 min_close_frames=3")
    log("    会让'最短可检出闭眼 = 3/fps'变大（10fps→300ms），会系统性漏掉短眨眼。")
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
    dump_dir = _first_writable(DUMP_DIR_CANDIDATES, True)
    if dump_dir is None:
        log("找不到可写目录（试过 %s）—— 请插 SD 卡" % (DUMP_DIR_CANDIDATES,))
        return None
    log("输出目录：%s" % dump_dir)

    ok, note = _apply(CHOSEN_PIXFORMAT, CHOSEN_FRAMESIZE, CHOSEN_FRAMEBUFFERS)
    if not ok:
        log("配置失败：%s" % note)
        return None
    if note:
        log("备注：%s" % note)

    frames = []
    t0 = time.ticks_ms()
    for i in range(DUMP_FRAMES):
        img = sensor.snapshot()
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
        pyb.LED(1).toggle()

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
            img = sensor.snapshot()
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
    log("")
    log("VigiLens / 知倦 —— OpenMV 图像采集测试，MODE=%s" % MODE)
    log("")

    report = {
        "tool": "board/openmv/openmv_capture_test.py",
        "mode": MODE,
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
    log("  · 若做了 dump → 把整个目录拷回 PC，跑：")
    log("      .venv\\Scripts\\python.exe board/openmv/raw_to_contract.py "
        "<目录> --out metrics/logs/openmv_frames.bin")
    log("    得到契约 §4.1 布局的 RGB888，可直接喂 A 线 / C 线。")


main()
