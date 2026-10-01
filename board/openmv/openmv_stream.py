# -*- coding: utf-8 -*-
"""
openmv_stream.py —— 在 **OpenMV Cam H7** 上运行的采集/链路程序（MicroPython）。

项目：知倦 / VigiLens —— 首次联调：OpenMV 采集链路
对应上位机工具：`board/openmv/host_capture_test.py`
协议实现        ：`board/openmv/vigilens_link.py`（**同一份**，不许在这里再抄一遍）

┌──────────────────────────────────────────────────────────────────────────┐
│ ⚠️ 本脚本**没有在真实 OpenMV 上运行过**（开发这个仓库的机器上没有硬件）。 │
│    它按 OpenMV 官方 API 文档编写，所有"实测数字"必须由你在板上跑出来。    │
│    跑之前先读 `board/openmv/README.md` 的"测试方案"。                     │
└──────────────────────────────────────────────────────────────────────────┘

安装（两步，别跳过第二步 —— 协议只有一份实现）：

  1. 用 OpenMV IDE 把本文件与 `vigilens_link.py` **一起**保存/拷贝到
     OpenMV 的 U 盘（就是插上摄像头后出现的那个盘符）根目录。
  2. 确认 OpenMV 盘里同时有 `openmv_stream.py` 和 `vigilens_link.py`。

改 `MODE` 常量选择模式，然后运行（OpenMV IDE 里点"运行"）：

  MODE = "probe"      ① 能力探测：把"这台摄像头到底能出什么分辨率/帧率"测出来。
                        这是**第一次上机最该跑的**，因为它把
                        OpenMV 官方页面自相矛盾的地方（描述里说 OV7725 能 640x480
                        RGB565@75fps，规格表却说 RGB565 上限 320x240）变成实测事实。
  MODE = "stats"      ② 只送统计量：像素留在 OpenMV，UART 只发几十字节/帧。
                        这是**唯一能同时拿到真实采集帧率**的低带宽路径。
  MODE = "uart_jpeg"  ③ 送 VGA JPEG 流（有损）。用来测"UART 到底能不能扛住送图"。
  MODE = "usb_jpeg"   ④ 送 VGA JPEG 流到 USB（虚拟串口），不经 UART。
  MODE = "usb_gray"   ⑤ 送**原始灰度**到 USB（**不压缩**）—— `docs/18` 路线②。
                        相机侧一次编码器都不调用，JPEG 由 PC 侧（omv_stream_bridge.ps1）生成。
                        这是 BUG-027 的根治方向：软件 `compress()` 需要一大块**连续**内存，
                        本板（H7 R2 + MT9M114）跑一阵必然失败；这条路把该需求从原理上删掉。
  MODE = "uart_gray"  ⑥ 同上，走 UART（带宽小得多，只适合 QQVGA 及以下）。
  MODE = "link"       ⑦ 纯链路握手：发 PING、等 PONG，验证接线与波特率。
                        需要上位机侧回应（host_capture_test.py --serial）。
  MODE = "echo"       ⑧ 字节回环：发什么收回什么（配合 PL 的 pl_uart_echo.v），
                        不需要上位机参与 —— 这是**第一次上板**最该跑的那条。

接线（UART 模式）：OpenMV 的 **P4=TX、P5=RX、GND** —— 详见 README 的接线表。
"""

import sys
import time
import gc

# ⚠️ 下面这几个模块**只存在于相机的 MicroPython 固件里**，PC 上没有，所以 Pylance 会报
#    `reportMissingImports`（"无法解析导入"）。这**不是缺依赖**（`pip install sensor` 这种包
#    不存在），**也不要**为了让告警消失就删掉它们 —— 删了相机上直接跑不起来。
#    本仓库的统一处理：加 `# type: ignore`（与 `host_capture_test.py` 对 cv2/serial 的做法一致）。
#
# ⚠️⚠️ `sensor` **不在这里 import** —— 它由下面的 v4/v5 兼容层决定取自哪里
#     （v5 走 `csi.CSI()` 的包装，v4 走 `import sensor`）。这里若再写一行
#     `import sensor`，在"只有 csi、没有 sensor"的固件上会直接 ImportError，
#     兼容层就白做了。**这不是漏写，是刻意**。
import image  # type: ignore  # noqa: F401  （部分固件需要显式 import 才有 img 方法）
import pyb  # type: ignore

# ---------------------------------------------------------------------------
# v4 / v5 相机 API 兼容层（BUG-005）
# ---------------------------------------------------------------------------
# 背景：OpenMV v5.0.0 有一条 **major** 破坏性变更 —— 官方说要用 `import csi` + `csi.CSI()`
# 取代 v4 的模块级 `sensor.*`。本文件原来**只写 v4 那一套**，于是"板上算、只传几十字节/帧"
# 的 `MODE="stats"`（docs/10 §6 推荐的短期方案）**从未在真机上跑过**。
#
# 2026-09-26 真机实测澄清了两件事：
#   ① 在 OpenMV v5.0.0 / MicroPython v1.28.0-49 上 **`import sensor` 仍然可用**
#      （官方那个 qstr "仍接线以兼容旧构建"），所以 v4 写法在 v5 上不是必然失败；
#   ② 但**不能依赖它** —— 它是兼容层，不是承诺。
#
# 本兼容层与 `openmv_capture_test.py` 的做法保持一致（那份已经真机验证过），
# 并按 `offline_check.py` 的验收要求**两条分支都能离线验**：
#   · 若 `csi` 可用 → 用 `csi.CSI()` 拿单例，并在本模块内提供与 v4 **同名**的模块级函数，
#     于是下面所有 `sensor.xxx(...)` 调用**一行都不用改**；
#   · 否则回落到原本的 `import sensor`（v4 行为，与改动前完全一致）。
#
# ⚠️ 为什么用 `sensor` 这个名字继续承载：本文件下游有 11 处 `sensor.*` 调用，
#    改成 `_CAM.xxx` 会动到所有模式分支、把真机回归面放大 —— 而"两套 API 都能跑"
#    这件事**只需要换绑一个名字**。故这里刻意保留 `sensor` 作为统一入口。
_CAM_API = None            # "csi" | "sensor"，会随报告一起打印，便于真机定位
_CSI_SINGLETON = [None]

try:                                    # v5+
    import csi as _csi_mod  # type: ignore
    _CSI_SINGLETON[0] = _csi_mod.CSI()
    _CAM_API = "csi"
except Exception:                       # v4.x（或 v5 上 csi 不可用）
    import sensor as sensor  # type: ignore
    _CAM_API = "sensor"


class _CsiAsSensor(object):
    """把 `csi.CSI()` 的单例**包装成 v4 的模块级函数名**，供本文件的 `sensor.*` 调用。

    只实现本文件真正用到的那些（reset / set_pixformat / set_framesize /
    set_framebuffers / skip_frames / snapshot），**不实现的一律显式报错**而不是静默返回 None
    —— 静默返回会让"其实没生效"看起来像"跑通了"（本项目已经栽过这类跟头）。
    """

    def __init__(self, csi_obj, csi_module):
        self._c = csi_obj
        self._m = csi_module

    # ---- 常量：从 csi 模块透传（GRAYSCALE / RGB565 / QVGA / VGA …）----
    def __getattr__(self, name):
        # 常量（GRAYSCALE 等）在 csi 模块上；方法在下游由本类显式转发。
        if hasattr(self._m, name):
            return getattr(self._m, name)
        if hasattr(self._c, name):
            return getattr(self._c, name)
        raise RuntimeError("本固件的 csi API 没有 %r —— 请在 IDE 里 dir(csi) 看可用名字" % name)

    # ---- v4 模块级函数 -> v5 单例方法 ----
    def reset(self):
        return self._c.reset()

    def set_pixformat(self, pf):
        return self._c.pixformat(pf)

    def set_framesize(self, fs):
        return self._c.framesize(fs)

    def set_framebuffers(self, n):
        fn = getattr(self._c, "set_framebuffers", None)
        if fn is None:
            raise RuntimeError("本固件的 csi.CSI 没有 set_framebuffers —— 无法固定帧缓冲数")
        return fn(n)

    def snapshot(self, **kw):
        return self._c.snapshot(**kw)

    def skip_frames(self, time=0):
        # v4 的 sensor.skip_frames(time=ms) 在 v5 由 CSI 上的同名/`skip_frames` 承担；
        # 缺失时退化成"抓几帧丢掉"，效果等价且不会静默。
        fn = getattr(self._c, "skip_frames", None)
        if fn is not None:
            return fn(time=time)
        n = 3
        for _ in range(n):
            self._c.snapshot()


if _CAM_API == "csi":
    sensor = _CsiAsSensor(_CSI_SINGLETON[0], _csi_mod)   # type: ignore  # noqa: F811

# ---------------------------------------------------------------------------
# ↓↓↓ 改这里 ↓↓↓
# ---------------------------------------------------------------------------
MODE = "probe"

UART_BAUD = 921600        # 先 921600 跑通，再逐步试 3000000 / 7500000
UART_ID = 3               # UART(3) → P4 = TX, P5 = RX（OpenMV H7 固定）
UART_TIMEOUT_CHAR = 200   # ms，写超时；太短会在高波特率下误报失败
FRAME_IDLE = 0            # >0 时每帧之间强制 sleep(ms)，用来压帧率做对照实验（0 = 不限速）

JPEG_QUALITY = 90         # 1..100；越高越清晰也越大。VGA 建议 70~90。**只对 *_jpeg 生效**。

# 送图模式的帧尺寸。**以 probe 模式的实测表为准**：
# OpenMV 官方对 H7 的说法自相矛盾（描述说 OV7725 可 VGA RGB565@75fps，
# 规格表又说 RGB565 上限 320x240），所以先用 probe 测，再回来填这里。
STREAM_FRAMESIZE = "QVGA"  # 可选 "VGA" / "QVGA"（`*_jpeg` / `stats` 用）

# 灰度直发（`*_gray`，docs/18 路线②）的帧尺寸。**为什么默认是 QQVGA**：
# 灰度单帧 = w*h 字节，而 VCP 实测吞吐约 258 KB/s（fpga/report/t8_*.md）——
# QQVGA 160x120 = 19200 B → 折算约 13 fps（够网页画面）；QVGA 76800 B → 只有约 3.4 fps。
# ⚠️ 这两个数都是**按吞吐折算的估计**（docs/18 §2 路线②的表），真机帧率以实测为准。
GRAY_FRAMESIZE = "QQVGA"

ROI = (0, 0, 640, 480)    # (x0, y0, x1, y1) **半开区间**，与契约 §0.1 第 2 条一致
# ---------------------------------------------------------------------------

FRAME_IDLE = 0


def log(*a):
    print(*a)


def _to_bytes(img):
    """把 image 对象变成 bytes。

    MicroPython 的 OpenMV 固件版本之间 API 有差异，这里逐个尝试，
    全都失败就抛出**带上下文**的错误，而不是让用户面对一个裸 AttributeError。
    """
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
            "请在 OpenMV IDE 里 print(dir(img)) 看你的固件提供哪个方法。原始错误: %s" % e
        )


# ---------------------------------------------------------------------------
# 模式 ①：能力探测
# ---------------------------------------------------------------------------

PROBE_CASES = (
    ("RGB565", "VGA"),
    ("RGB565", "QVGA"),
    ("GRAYSCALE", "VGA"),
    ("GRAYSCALE", "QVGA"),
    ("JPEG", "VGA"),
    ("JPEG", "QVGA"),
)


def _resolve(name):
    pf = getattr(sensor, name, None)
    if pf is None:
        raise RuntimeError("该固件没有 sensor.%s" % name)
    return pf


def probe():
    log("=" * 74)
    log("OpenMV 能力探测 —— 结果请原样贴回 A 线/项目记录，不要手改")
    log("=" * 74)
    log("固件版本: %s" % getattr(sys, "version", "?"))
    log("")
    log("%-10s %-8s %-8s %10s %12s %10s  %s" %
        ("pixformat", "framesize", "结果", "fps", "单帧字节", "MB/s", "备注"))
    log("-" * 74)

    results = []
    for pf_name, fs_name in PROBE_CASES:
        pf = _resolve(pf_name)
        fs = getattr(sensor, fs_name, None)
        row = {"pixformat": pf_name, "framesize": fs_name}
        try:
            sensor.reset()
            sensor.set_pixformat(pf)
            sensor.set_framesize(fs)
            sensor.skip_frames(time=1000)

            clock = time.clock()
            n = 0
            nbytes = 0
            t_end = time.ticks_add(time.ticks_ms(), 2000)
            while time.ticks_diff(t_end, time.ticks_ms()) > 0:
                clock.tick()
                img = sensor.snapshot()
                if n == 0:
                    # 单帧字节数：探测模式下一律取 JPEG 压缩后的真实尺寸，
                    # 未压缩格式取宽*高*每像素字节
                    try:
                        w = img.width()
                        h = img.height()
                        bpp = 1 if pf_name == "GRAYSCALE" else (2 if pf_name == "RGB565" else 0)
                        row["w"], row["h"] = w, h
                        nbytes += (w * h * bpp) if bpp else len(_to_bytes(img))
                    except Exception:
                        pass
                n += 1
            fps = n / 2.0
            row.update(ok=True, fps=round(fps, 2), bytes_per_frame=nbytes if nbytes else None)
            mv = (nbytes * fps / 1e6) if nbytes else 0.0
            row["MBps"] = round(mv, 3)
            log("%-10s %-8s %-8s %10.2f %12s %10.3f  %s" %
                (pf_name, fs_name, "OK", fps, nbytes or "?", mv, ""))
        except Exception as e:
            row.update(ok=False, error="%s: %s" % (type(e).__name__, e))
            log("%-10s %-8s %-8s %10s %12s %10s  %s" %
                (pf_name, fs_name, "失败", "-", "-", "-", row["error"]))
        results.append(row)

    log("-" * 74)
    log("")
    log("对照：UART 上限 7.5 Mb/s = 0.937 MB/s；USB-FS 上限 12 Mb/s = 1.5 MB/s")
    log("      契约 §0 要求 640x480 RGB888 @30fps = 27.648 MB/s")
    log("")
    log("把上面这张表原样贴回项目记录。这就是契约 §5.2 第 12 项")
    log("（'rPPG 端到端待真实视频 / 45Hz 切换'）缺的那份真实采集能力数据。")
    return results


# ---------------------------------------------------------------------------
# 模式 ⑤：纯链路握手
# ---------------------------------------------------------------------------


def link_test(uart):
    log("[link] 发 PING 等 PONG，最多 10 次 ...")
    dec = link.Decoder()
    ok = 0
    for i in range(1, 11):
        payload = b"vigilens-%d" % i
        uart.write(link.encode(link.TYPE_PING, i, payload))
        t_end = time.ticks_add(time.ticks_ms(), 500)
        got = False
        while (not got) and time.ticks_diff(t_end, time.ticks_ms()) > 0:
            chunk = uart.read() if uart.any() else None
            if not chunk:
                continue
            for mtype, fid, pl in dec.feed(chunk):
                if mtype == link.TYPE_PONG and fid == i and pl == payload:
                    ok += 1
                    got = True
                    log("[link] 第 %d 次：PONG 收到，payload 原样返回 ok" % i)
                    break
        if not got:
            log("[link] 第 %d 次：超时" % i)
    log("[link] 结果：%d/10 成功（CRC 错 %d，丢字节 %d）" % (ok, dec.crc_errors, dec.dropped_bytes))
    log("[link] 10/10 才算接线与波特率都对；0/10 先查 TX/RX 是否接反、GND 是否共地")
    return ok


# ---------------------------------------------------------------------------
# 模式 ②③④⑤⑥：送数据
# ---------------------------------------------------------------------------

# `stream()` 的 payload 三选一 —— 对应"相机侧越做越少"的三种做法。
# ⚠️ 不要在这三个之外再加值：本机没有硬件，任何新的编码路径都只能靠真机试错（AGENTS.md 铁律 2）。
PAYLOAD_KINDS = ("jpeg", "gray", "stats")


def stream(uart, payload, use_usb):
    """按 `payload` 指定的类型持续送帧，直到被 Ctrl-C 打断。

      "jpeg"  —— RGB565 + `img.compress()`（相机侧软件 JPEG）。**BUG-027 的来源**，留作对照。
      "gray"  —— 灰度**原样**送出，相机侧一次编码器都不调用（`docs/18` 路线②，根治方向）。
      "stats" —— 只送 ROI 统计量（几十字节/帧），相机侧**不产生任何图像**（`docs/18` 路线④）。
    """
    global FRAME_IDLE

    if payload not in PAYLOAD_KINDS:
        raise ValueError("未知 payload=%r，只能是 %s" % (payload, PAYLOAD_KINDS))
    framesize_name = GRAY_FRAMESIZE if payload == "gray" else STREAM_FRAMESIZE

    sensor.reset()
    if payload == "gray":
        # 路线②：**灰度直发，相机侧不压缩**。
        # 为什么这样能根治 BUG-027：失败点是"编码器要一块**连续**内存"，而本板可用堆
        # 只有 ~302 KB、QVGA 帧缓冲已占 153600 B（docs/18 §0）。这一路既不调 JPEG 编码器、
        # 也不要大缓冲，所以那个失败点在**原理上**不再存在。
        # 硬件底气：本板是 H7 R2（ON Semi MT9M114），**原生 8-bit 灰度**（docs/18 §0.1）。
        sensor.set_pixformat(sensor.GRAYSCALE)
    else:
        # ⚠️ 刻意**不**用 `sensor.set_pixformat(sensor.JPEG)`：本板传感器 MT9M114 是 raw Bayer，
        #    **物理上不输出 JPEG**，调用会直接抛 `RuntimeError: Sensor control failed.`
        #    （2026-09-26 真机实测；根因见 docs/16 BUG-017 与 docs/18 §0.1）。
        #    JPEG 只能由下面的 `img.compress(quality=...)` 生成 —— 而那条路就是 BUG-027。
        sensor.set_pixformat(sensor.RGB565)
    # 帧尺寸取上面两个常量之一 —— 到底哪个组合能成，以 probe 模式的实测表为准。
    sensor.set_framesize(getattr(sensor, framesize_name))
    sensor.skip_frames(time=1500)

    vcp = pyb.USB_VCP() if use_usb else None
    x0, y0, x1, y1 = ROI
    log("[stream] 开始：payload=%s usb=%s %s roi=%s" %
        (payload, use_usb, framesize_name, ROI))
    log("[stream] 每约 1 秒打一行进度。让上位机跑 host_capture_test.py 收。")

    fid = 0
    t_start = time.ticks_ms()
    last_report = t_start
    frames_at_report = 0
    sent_bytes = 0
    compress_fail = 0

    while True:
        img = sensor.snapshot()
        fid += 1
        t_frame0 = time.ticks_ms()

        if payload == "gray":
            # 路线②：灰度**原样**发。载荷 = 4B 宽 + 4B 高 + 逐行逐列 uint8（link.pack_gray）。
            # ⚠️ 这里**没有** `compress()`、也**不需要** `gc.collect()`：
            #    这条路径不分配任何大缓冲，所以既不会抛 Compression Failed，
            #    也没有"每帧回收一次"的必要（那本来是给 compress 的连续块让路的）。
            blob = link.pack_gray(img.width(), img.height(), _to_bytes(img))
            mtype = link.TYPE_GRAY
        elif payload == "jpeg":
            # ① 每帧先回收一次。`img.compress()` 要一块**连续**内存，堆碎片攒起来就会抛
            #    `OSError: Compression Failed!` —— 实测这个错会让整条流退出回 REPL
            #    （见 docs/16 BUG-027，症状是"相机活着但串口 0 字节"）。
            gc.collect()
            try:
                blob = _to_bytes(img.compress(quality=JPEG_QUALITY))
            except Exception as e:
                # ② 单帧压缩失败**不该**让整条流死掉：丢掉这一帧继续跑，并留下计数。
                compress_fail += 1
                if compress_fail <= 3 or compress_fail % 50 == 0:
                    log("[stream] 第 %d 帧压缩失败（累计 %d）：%s" % (fid, compress_fail, e))
                continue
            mtype = link.TYPE_JPEG
        else:
            # 只送统计量：ROI 是半开区间，OpenMV 的 get_statistics 要 (x, y, w, h)
            stats = img.get_statistics(roi=(x0, y0, x1 - x0, y1 - y0))
            count = (x1 - x0) * (y1 - y0)
            try:
                # sum_* 用"均值 × 像素数"还原，整数化后与契约 §3.2 的整型累加同量级。
                # ⚠️ 这是**近似**：OpenMV 的 mean 是浮点统计，与硬件整型累加不会逐位相等，
                #    因此这条路径只能做 A 线行为指标，**不能**当 C 线黄金参考。
                sr = int(stats.r_mean() * count)
                sg = int(stats.g_mean() * count)
                sb = int(stats.b_mean() * count)
                mg = int(stats.g_mean() * 256)
            except Exception:
                mg = int(stats.mean() * 256)
                sr = sg = sb = 0
                count = 0
            blob = link.pack_stats([fid, x0, y0, x1, y1, sr, sg, sb, count, mg,
                                    time.ticks_diff(time.ticks_ms(), t_frame0)])
            mtype = link.TYPE_STATS

        frame = link.encode(mtype, fid, blob)
        if use_usb:
            vcp.send(frame)
        else:
            uart.write(frame)
        sent_bytes += len(frame)

        if FRAME_IDLE:
            time.sleep_ms(FRAME_IDLE)

        now = time.ticks_ms()
        if time.ticks_diff(now, last_report) >= 1000:
            dt = time.ticks_diff(now, last_report) / 1000.0
            inst = (fid - frames_at_report) / dt
            log("[stream] fid=%d  瞬时 %.1f fps  已发 %.1f KB  累计 %.1f s" %
                (fid, inst, sent_bytes / 1024.0, time.ticks_diff(now, t_start) / 1000.0))
            last_report = now
            frames_at_report = fid
            pyb.LED(1).toggle()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def echo_test(uart, rounds=50):
    """把帧原样发出去、再原样收回来，逐字节比对。

    用来验证 `pl_uart_echo.v`（PL 字节回环）与整条物理通路：
    它同时覆盖「电气连通」「TX/RX 没接反」「波特率对」「GND 共地」「CRC 保护有效」。
    """
    log("[echo] 逐个发送并把字节原样收回，共 %d 轮 ..." % rounds)
    dec = link.Decoder()
    ok = 0
    total_sent = 0
    total_back = 0
    t_all = time.ticks_ms()

    for i in range(1, rounds + 1):
        payload = bytes(range(0, 16)) + b"vigilens"   # 固定可肉眼核对的内容
        blob = link.encode(link.TYPE_PING, i, payload)
        uart.write(blob)
        total_sent += len(blob)

        got = bytearray()
        t_end = time.ticks_add(time.ticks_ms(), 300)
        while (len(got) < len(blob)) and time.ticks_diff(t_end, time.ticks_ms()) > 0:
            chunk = uart.read() if uart.any() else None
            if chunk:
                got.extend(chunk)

        total_back += len(got)
        if bytes(got) == blob:
            ok += 1
        else:
            log("[echo] 第 %d 轮不一致：发 %d 字节，回 %d 字节%s"
                % (i, len(blob), len(got), "" if len(got) == len(blob) else "（长度就不对）"))

        # 顺便验证协议解码器能把这串字节解出来（而不是只会"字节相等"）
        for mtype, fid, pl in dec.feed(bytes(got)):
            if mtype != link.TYPE_PING or pl != payload:
                log("[echo] 第 %d 轮虽字节相等但解码语义不对" % i)

    dt = time.ticks_diff(time.ticks_ms(), t_all) / 1000.0
    log("[echo] 结果：%d/%d 轮逐字节一致  共发 %d 字节 / 共收 %d 字节  耗时 %.2fs"
        % (ok, rounds, total_sent, total_back, dt))
    log("[echo] 注意：pl_uart_echo.v 只有 1 字节暂存、没有 FIFO，")
    log("      连续满速发送时**预期会丢字节** —— 那是设计的已知取舍，不是接线问题。")
    log("      因此判据是「大部分轮次逐字节一致」，而不是 100%%。")
    return ok


def main():
    log("")
    log("VigiLens / 知倦 —— OpenMV 侧程序，MODE=%s" % MODE)
    log("")

    if MODE == "probe":
        probe()
        return

    if MODE == "usb_jpeg":
        stream(None, payload="jpeg", use_usb=True)
        return

    if MODE == "usb_gray":
        # docs/18 路线②：灰度直发，PC 侧编码。**这是本板推流画面的首选模式**。
        stream(None, payload="gray", use_usb=True)
        return

    uart = pyb.UART(UART_ID, UART_BAUD, timeout_char=UART_TIMEOUT_CHAR)
    log("[uart] UART(%d) @ %d baud  P4=TX P5=RX" % (UART_ID, UART_BAUD))

    if MODE == "link":
        link_test(uart)
    elif MODE == "echo":
        echo_test(uart)
    elif MODE == "stats":
        stream(uart, payload="stats", use_usb=False)
    elif MODE == "uart_jpeg":
        stream(uart, payload="jpeg", use_usb=False)
    elif MODE == "uart_gray":
        stream(uart, payload="gray", use_usb=False)
    else:
        log("未知 MODE=%r。可选：probe / link / echo / stats / uart_jpeg / usb_jpeg / "
            "uart_gray / usb_gray" % MODE)


try:
    import vigilens_link as link
except ImportError:
    print("=" * 74)
    print("找不到 vigilens_link.py —— 协议只有一份实现，不允许在这里内联复制。")
    print("请把 board/openmv/vigilens_link.py 一并拷到 OpenMV 的 U 盘根目录后重跑。")
    print("=" * 74)
    raise


main()
