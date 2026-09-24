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
  MODE = "link"       ⑤ 纯链路握手：发 PING、等 PONG，验证接线与波特率。
                        需要上位机侧回应（host_capture_test.py --serial）。
  MODE = "echo"       ⑥ 字节回环：发什么收回什么（配合 PL 的 pl_uart_echo.v），
                        不需要上位机参与 —— 这是**第一次上板**最该跑的那条。

接线（UART 模式）：OpenMV 的 **P4=TX、P5=RX、GND** —— 详见 README 的接线表。
"""

import sys
import time

# ⚠️ 下面这几个模块**只存在于相机的 MicroPython 固件里**，PC 上没有，所以 Pylance 会报
#    `reportMissingImports`（"无法解析导入"）。这**不是缺依赖**（`pip install sensor` 这种包
#    不存在），**也不要**为了让告警消失就删掉它们 —— 删了相机上直接跑不起来。
#    本仓库的统一处理：加 `# type: ignore`（与 `host_capture_test.py` 对 cv2/serial 的做法一致）。
import sensor  # type: ignore
import image  # type: ignore  # noqa: F401  （部分固件需要显式 import 才有 img 方法）
import pyb  # type: ignore

# ---------------------------------------------------------------------------
# ↓↓↓ 改这里 ↓↓↓
# ---------------------------------------------------------------------------
MODE = "probe"

UART_BAUD = 921600        # 先 921600 跑通，再逐步试 3000000 / 7500000
UART_ID = 3               # UART(3) → P4 = TX, P5 = RX（OpenMV H7 固定）
UART_TIMEOUT_CHAR = 200   # ms，写超时；太短会在高波特率下误报失败
FRAME_IDLE = 0            # >0 时每帧之间强制 sleep(ms)，用来压帧率做对照实验（0 = 不限速）

JPEG_QUALITY = 90         # 1..100；越高越清晰也越大。VGA 建议 70~90

# 送图模式的帧尺寸。**以 probe 模式的实测表为准**：
# OpenMV 官方对 H7 的说法自相矛盾（描述说 OV7725 可 VGA RGB565@75fps，
# 规格表又说 RGB565 上限 320x240），所以先用 probe 测，再回来填这里。
STREAM_FRAMESIZE = "QVGA"  # 可选 "VGA" / "QVGA"

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
# 模式 ②③④：送数据
# ---------------------------------------------------------------------------


def stream(uart, use_jpeg, use_usb):
    global FRAME_IDLE

    sensor.reset()
    if use_jpeg:
        sensor.set_pixformat(sensor.JPEG)
    else:
        sensor.set_pixformat(sensor.RGB565)
    # 帧尺寸取 STREAM_FRAMESIZE —— 到底哪个组合能成，以 probe 模式的实测表为准。
    sensor.set_framesize(getattr(sensor, STREAM_FRAMESIZE))
    sensor.skip_frames(time=1500)

    vcp = pyb.USB_VCP() if use_usb else None
    x0, y0, x1, y1 = ROI
    log("[stream] 开始：jpeg=%s usb=%s %s roi=%s" %
        (use_jpeg, use_usb, STREAM_FRAMESIZE, ROI))
    log("[stream] 每约 1 秒打一行进度。让上位机跑 host_capture_test.py 收。")

    fid = 0
    t_start = time.ticks_ms()
    last_report = t_start
    frames_at_report = 0
    sent_bytes = 0

    while True:
        img = sensor.snapshot()
        fid += 1
        t_frame0 = time.ticks_ms()

        if use_jpeg:
            blob = _to_bytes(img.compress(quality=JPEG_QUALITY))
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
        stream(None, use_jpeg=True, use_usb=True)
        return

    uart = pyb.UART(UART_ID, UART_BAUD, timeout_char=UART_TIMEOUT_CHAR)
    log("[uart] UART(%d) @ %d baud  P4=TX P5=RX" % (UART_ID, UART_BAUD))

    if MODE == "link":
        link_test(uart)
    elif MODE == "echo":
        echo_test(uart)
    elif MODE == "stats":
        stream(uart, use_jpeg=False, use_usb=False)
    elif MODE == "uart_jpeg":
        stream(uart, use_jpeg=True, use_usb=False)
    else:
        log("未知 MODE=%r。可选：probe / link / echo / stats / uart_jpeg / usb_jpeg" % MODE)


try:
    import vigilens_link as link
except ImportError:
    print("=" * 74)
    print("找不到 vigilens_link.py —— 协议只有一份实现，不允许在这里内联复制。")
    print("请把 board/openmv/vigilens_link.py 一并拷到 OpenMV 的 U 盘根目录后重跑。")
    print("=" * 74)
    raise


main()
