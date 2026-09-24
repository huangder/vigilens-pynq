# -*- coding: utf-8 -*-
"""host_capture_test.py —— 笔记本（上位机）侧的 OpenMV 采集链路测试工具。

项目：知倦 / VigiLens —— 首次联调「OpenMV 采集链路 + A 线指标跑通」

它回答的问题（都必须是**真实运行测出来的**，不允许拍脑袋）：
  1. OpenMV 作为视频源，**实际**能给出什么分辨率 / 什么帧率？
  2. 离契约 `docs/interface.md` §0 的 **640×480 RGB888 @ 30 fps** 差多少？
  3. 用 A 线 `backend/run_pipeline.py` 消费它，链路通不通？（本工具负责产出可喂的原始帧）
  4. 这条链路的**真实带宽**是多少 → 直接决定"能不能送进 PL"（见 README 带宽算术）。

四种输入模式：
  --list                        列出本机 OpenCV 能打开的摄像头（找 OpenMV 的序号）
  --device 0                    用 OpenCV 打开 UVC 摄像头（OpenMV 刷 uvc.bin 后走这条）
  --device data/raw/xx.mp4      用视频文件（回归对比用，可无限重复）
  --serial COM5 --baud 921600   从 OpenMV 的 UART 读自定义帧（协议见 vigilens_link.py）

产物（默认写 `metrics/logs/`，**不是** `metrics/evidence/`）：
  - `openmv_capture_<时间戳>.json`   本次全部测量值 + 契约判定
  - `openmv_capture_<时间戳>.csv`    每个配置一行，便于画图
  - `--dump-rgb-bin out.bin`         按契约 §4.1 冻结布局导出真实 RGB888 帧
                                     （给 C 线上板做 DMA 回放 / 黄金参考用）

用法::

    .venv\\Scripts\\python.exe board/openmv/host_capture_test.py --list
    .venv\\Scripts\\python.exe board/openmv/host_capture_test.py --device 0 --seconds 5 \\
        --probe 640x480,320x240,160x120 --dump-rgb-bin metrics/logs/openmv_vga.bin --dump-frames 5
    .venv\\Scripts\\python.exe board/openmv/host_capture_test.py --serial COM5 --baud 921600 --seconds 5
    .venv\\Scripts\\python.exe board/openmv/host_capture_test.py --selftest

⚠️ 本脚本**没有在真实 OpenMV 上跑过**（我手上没有硬件）。它自己的判定逻辑与协议解析
   由 `--selftest` 覆盖（不依赖摄像头）；硬件相关的每一个数字必须由你跑出来。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import vigilens_link as link  # noqa: E402

REPO_ROOT = _HERE.parent.parent
DEFAULT_OUTDIR = REPO_ROOT / "metrics" / "logs"
# CSV 单独放 metrics/csv/ —— 这是 .gitignore 的既有约定
# （它忽略 metrics/csv/*.csv 与 metrics/logs/*.json，但不忽略 metrics/logs/*.csv）。
# 把 CSV 写进 metrics/logs/ 会让工作区多出未跟踪文件，破坏"只应出现你本线的改动"。
DEFAULT_CSVDIR = REPO_ROOT / "metrics" / "csv"


def enable_utf8_console() -> None:
    """Windows 控制台默认 GBK，中文会乱码（与 backend/console.py 同做法）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 契约：唯一来源是 config.yaml，本文件不允许出现魔法数字
# ---------------------------------------------------------------------------


def load_contract() -> dict:
    """从 config.yaml 读契约口径（§0 图像尺寸/格式/帧率）。

    优先用 PyYAML；没有 PyYAML 时用极简解析兜底（与 backend/config.py 的降级思路一致），
    只挑我们真正需要的几个键，不试图复刻一个完整 YAML 解析器。
    """
    path = REPO_ROOT / "config.yaml"
    fps = width = height = None
    pixel_format = "RGB888"
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        fps = data.get("fps_nominal")
        fpga = data.get("fpga") or {}
        width = fpga.get("img_width")
        height = fpga.get("img_height")
        pixel_format = fpga.get("pixel_format", pixel_format)
    except Exception:
        text = path.read_text(encoding="utf-8")
        in_fpga = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            if not line.startswith((" ", "\t")):
                in_fpga = line.strip().startswith("fpga:")
                if line.strip().startswith("fps_nominal:"):
                    fps = _num(line.split(":", 1)[1])
                continue
            if in_fpga:
                if line.strip().startswith("img_width:"):
                    width = _num(line.split(":", 1)[1])
                elif line.strip().startswith("img_height:"):
                    height = _num(line.split(":", 1)[1])
                elif line.strip().startswith("pixel_format:"):
                    pixel_format = line.split(":", 1)[1].strip()

    if fps is None or width is None or height is None:
        raise RuntimeError(
            "无法从 config.yaml 读出 fps_nominal / fpga.img_width / fpga.img_height；"
            "契约是唯一来源，不要在本脚本里写死这些数字。"
        )
    w, h, f = int(width), int(height), float(fps)
    return {
        "width": w,
        "height": h,
        "fps": f,
        "pixel_format": str(pixel_format),
        "bytes_per_frame": w * h * 3,
        "required_MBps": w * h * 3 * f / 1e6,
        "required_Mbps": w * h * 3 * 8 * f / 1e6,
        "source": str(path.relative_to(REPO_ROOT)),
    }


def _num(s: str):
    s = s.strip()
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


def judge(width, height, fps, contract: dict, source_kind: str = "camera") -> dict:
    """把一次测量结果与契约 §0 对比。返回 dict（problems 为空即完全达标）。

    ⚠️ `source_kind == "file"` 时**不做帧率判定**：视频文件回放是"能解多快解多快"，
    在 PC 上轻松几百 fps，这个数字跟摄像头能力毫无关系。拿它去判"达标"就是自欺。
    文件回放的用途是**回归复现**（同一条输入跑两次结果必须一致），不是性能证据。
    """
    problems: list[str] = []
    notes: list[str] = []

    if source_kind == "file":
        notes.append(
            "这是视频文件回放：解码帧率（%s fps）**不能**当作摄像头能力，因此本配置不做契约判定。"
            % ("%.1f" % fps if fps else "?")
        )
        return {
            "ok": None,
            "problems": [],
            "notes": notes,
            "applicable": False,
            "achieved_MBps": None,
            "required_MBps": round(contract["required_MBps"], 3),
            "bandwidth_ratio": None,
        }

    if (width, height) != (contract["width"], contract["height"]):
        problems.append(
            "分辨率不符：实测 %sx%s，契约 §0 冻结 %sx%s"
            % (width, height, contract["width"], contract["height"])
        )
    if fps is None:
        problems.append("帧率未测到")
    elif fps < contract["fps"] * 0.9:
        problems.append("帧率不足：实测 %.2f fps < 契约 %.1f fps 的 90%%" % (fps, contract["fps"]))
    elif fps < contract["fps"]:
        notes.append("帧率略低：实测 %.2f fps（契约 %.1f，落在 90%% 容差内）" % (fps, contract["fps"]))

    got_MBps = (width * height * 3 * fps / 1e6) if fps else 0.0
    return {
        "ok": not problems,
        "applicable": True,
        "problems": problems,
        "notes": notes,
        "achieved_MBps": round(got_MBps, 3),
        "required_MBps": round(contract["required_MBps"], 3),
        "bandwidth_ratio": round(got_MBps / contract["required_MBps"], 3) if contract["required_MBps"] else None,
    }


# ---------------------------------------------------------------------------
# UVC / 视频文件测量
# ---------------------------------------------------------------------------


def _open_capture(device: str, fourcc: str | None):
    import cv2  # type: ignore

    src = int(device) if device.isdigit() else str(device)
    cap = cv2.VideoCapture(src)
    if fourcc and device.isdigit():
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    return cap


def measure_stream(device: str, width: int, height: int, seconds: float,
                   fourcc: str | None, dump_path: Path | None, dump_frames: int) -> dict:
    """在指定分辨率下真实抓 `seconds` 秒，返回实测值。"""
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    import hashlib

    cap = _open_capture(device, fourcc)
    if not cap.isOpened():
        return {"error": "OpenCV 打不开 %r（序号不对 / 被占用 / 需要刷 uvc.bin）" % device}

    is_file = not str(device).isdigit()
    nominal_fps = None
    total_in_file = None
    try:
        if is_file:
            # 文件的"标称帧率"来自容器元数据，是它唯一有意义的帧率；
            # 解码墙钟速率只反映 CPU 有多快，不能当摄像头性能。
            nominal_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or None
            total_in_file = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) or None
        if width and height and not is_file:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not is_file:
            # 摄像头：先丢几帧，把驱动/传感器的陈旧缓冲冲掉，否则测出来的 fps 会虚高。
            # 文件：**不能**丢帧 —— 回放要能从第 0 帧确定性开始（黄金向量靠这个）。
            for _ in range(5):
                cap.read()

        t0 = time.perf_counter()
        ok, frame = cap.read()
        first_latency = time.perf_counter() - t0
        if not ok or frame is None:
            return {"error": "首帧读取失败（分辨率 %dx%d 可能不被支持）" % (width, height)}

        actual_h, actual_w = frame.shape[:2]
        hashes: set[bytes] = set()
        n = 0
        dumped = 0
        if dump_path is not None:
            dump_path.parent.mkdir(parents=True, exist_ok=True)
            dump_fh = open(dump_path, "wb")
        else:
            dump_fh = None

        try:
            t_start = time.perf_counter()
            while True:
                if frame is not None:
                    n += 1
                    h = hashlib.blake2b(np.ascontiguousarray(frame).tobytes(), digest_size=8).digest()
                    hashes.add(h)
                    if dump_fh is not None and dumped < dump_frames:
                        # 契约 §4.1 冻结布局：c=0→R。OpenCV 给的是 BGR，必须翻通道。
                        rgb = np.ascontiguousarray(frame[:, :, ::-1])
                        if rgb.shape[1] != width or rgb.shape[0] != height:
                            rgb = cv2.resize(frame, (width, height))[:, :, ::-1]
                        dump_fh.write(np.ascontiguousarray(rgb).tobytes())
                        dumped += 1
                elapsed = time.perf_counter() - t_start
                if elapsed >= seconds:
                    break
                ok, frame = cap.read()
                if not ok:
                    break
            elapsed = time.perf_counter() - t_start
        finally:
            if dump_fh is not None:
                dump_fh.close()
    finally:
        cap.release()

    fps = n / elapsed if elapsed > 0 else 0.0
    return {
        "device": device,
        "source_kind": "file" if is_file else "camera",
        "nominal_fps": round(nominal_fps, 2) if nominal_fps else None,
        "file_total_frames": total_in_file,
        "requested": "%dx%d" % (width, height),
        "actual_width": actual_w,
        "actual_height": actual_h,
        "frames": n,
        "seconds": round(elapsed, 3),
        "fps": round(fps, 2),
        "first_frame_latency_ms": round(first_latency * 1000, 1),
        "distinct_frames": len(hashes),
        "frozen_source": len(hashes) <= 1 and n > 1,
        "MBps_gray_or_color_raw": round(actual_w * actual_h * 3 * fps / 1e6, 3),
        "dumped_frames": dumped,
        "dump_path": str(dump_path) if dump_fh is not None and dumped else None,
    }


def list_cameras(max_index: int = 8, seconds: float = 1.0) -> list[dict]:
    """逐个试摄像头序号。很多 UVC 源打不开会卡很久，所以每个只试 1 秒。"""
    import cv2  # type: ignore

    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        if ok and frame is not None:
            h, w = frame.shape[:2]
            found.append({"index": i, "width": w, "height": h})
        cap.release()
    return found


# ---------------------------------------------------------------------------
# UART / 串口测量（OpenMV 自定义帧协议）
# ---------------------------------------------------------------------------


def measure_serial(port: str, baud: int, seconds: float) -> dict:
    try:
        import serial  # type: ignore
    except ImportError:
        return {
            "error": "未安装 pyserial。装：.venv\\Scripts\\python.exe -m pip install pyserial",
            "hint": "也可以改用 --device 走 UVC 路线，那条路不需要 pyserial。",
        }

    dec = link.Decoder()
    n = 0
    first_id = last_id = None
    gaps = 0
    bytes_total = 0
    per_type: dict[str, int] = {}
    payload_bytes = 0
    ms_list: list[float] = []

    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except Exception as e:  # 端口不存在 / 被占用 / 权限
        return {"error": "打开串口 %s 失败：%s" % (port, e)}

    try:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            chunk = ser.read(4096)
            if not chunk:
                continue
            bytes_total += len(chunk)
            for mtype, fid, payload in dec.feed(chunk):
                n += 1
                payload_bytes += len(payload)
                per_type[link.type_name(mtype)] = per_type.get(link.type_name(mtype), 0) + 1
                if first_id is None:
                    first_id = fid
                elif last_id is not None and fid != last_id + 1:
                    gaps += fid - last_id - 1
                last_id = fid
                if mtype == link.TYPE_STATS and len(payload) >= 4 * 11:
                    ms_list.append(link.unpack_stats(payload).get("ms_elapsed", 0))
        elapsed = time.perf_counter() - t0
    finally:
        ser.close()

    expected = (gaps + n) if (gaps + n) else 0
    return {
        "port": port,
        "baud": baud,
        "seconds": round(elapsed, 3),
        "frames_decoded": n,
        "bytes_total": bytes_total,
        "payload_bytes": payload_bytes,
        "goodput_KBps": round(payload_bytes / elapsed / 1e3, 2),
        "wire_KBps": round(bytes_total / elapsed / 1e3, 2),
        "wire_Mbps": round(bytes_total * 8 / elapsed / 1e6, 3),
        "crc_errors": dec.crc_errors,
        "dropped_bytes": dec.dropped_bytes,
        "oversize_rejected": dec.oversize_rejected,
        "first_frame_id": first_id,
        "last_frame_id": last_id,
        "lost_frames": gaps,
        "loss_rate": round(gaps / expected, 4) if expected else None,
        "frames_per_sec": round(n / elapsed, 2) if elapsed else 0,
        "per_type": per_type,
        "openmv_ms_elapsed_median": round(statistics.median(ms_list), 2) if ms_list else None,
    }


# ---------------------------------------------------------------------------
# 自检（不依赖摄像头 / 串口）
# ---------------------------------------------------------------------------


def _selftest() -> bool:
    import numpy as np  # type: ignore

    checks: list[tuple[str, bool, str]] = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # T1 契约真的从 config.yaml 读出来了，且与仓库冻结口径一致
    c = load_contract()
    chk("T1 契约取自 config.yaml", c["source"] == os.path.join("config", "yaml") or c["source"].endswith("config.yaml"),
        str(c["source"]))
    chk("T1b 契约 = 640x480 / 30fps / 921600 B/帧",
        (c["width"], c["height"], c["fps"], c["bytes_per_frame"]) == (640, 480, 30.0, 921600),
        "got %sx%s @%s %s B" % (c["width"], c["height"], c["fps"], c["bytes_per_frame"]))
    chk("T1c 契约带宽 27.648 MB/s", abs(c["required_MBps"] - 27.648) < 1e-6, "%.4f" % c["required_MBps"])

    # T2 判定逻辑：达标 / 分辨率不符 / 帧率不足 三条分支都要走对
    j = judge(640, 480, 30.0, c)
    chk("T2a 640x480@30 → 达标", j["ok"] and not j["problems"], str(j["problems"]))
    j = judge(320, 240, 30.0, c)
    chk("T2b 320x240@30 → 分辨率不符（且列出问题）", (not j["ok"]) and len(j["problems"]) == 1, str(j["problems"]))
    j = judge(640, 480, 12.0, c)
    chk("T2c 640x480@12 → 帧率不足", (not j["ok"]) and "帧率不足" in j["problems"][0], str(j["problems"]))
    j = judge(640, 480, 28.0, c)
    chk("T2d 640x480@28 → 容差内给出提醒但不判失败", j["ok"] and j["notes"], str(j["notes"]))

    # T3 契约 §4.1 的 RGB 布局必须逐字节对得上（这是 C 线上板回放的前提）
    W, H = 4, 3
    bgr = np.zeros((H, W, 3), dtype=np.uint8)
    bgr[0, 0] = (10, 20, 30)  # B,G,R
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    raw = np.ascontiguousarray(rgb).tobytes()
    # 契约：bytes[3*(y*W+x)+c]，c=0→R
    chk("T3 BGR→契约 RGB888 布局逐字节正确",
        raw[0] == 30 and raw[1] == 20 and raw[2] == 10,
        "got R=%d G=%d B=%d" % (raw[0], raw[1], raw[2]))
    chk("T3b 布局长度 == W*H*3", len(raw) == W * H * 3, "len=%d" % len(raw))

    # T4 UART 模式在没装 pyserial 时必须给可读的提示，而不是崩掉
    r = measure_serial("COM_DOES_NOT_EXIST", 921600, 0.05)
    chk("T4 缺 pyserial 时优雅返回 error", "error" in r, str(r)[:100])

    failed = [x for x in checks if not x[1]]
    print("=" * 74)
    print("host_capture_test.py 自检（不依赖摄像头/串口）")
    print("=" * 74)
    for name, ok, detail in checks:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        if detail and not ok:
            print("         → " + detail)
    print("-" * 74)
    print("RESULT: %s  (%d/%d)" % ("PASS" if not failed else "FAIL",
                                   len(checks) - len(failed), len(checks)))
    return not failed


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VigiLens：OpenMV 采集链路测试（上位机侧）")
    ap.add_argument("--list", action="store_true", help="列出可用的摄像头序号后退出")
    ap.add_argument("--device", default=None, help="OpenCV 视频源：摄像头序号（如 0）或视频文件路径")
    ap.add_argument("--serial", default=None, help="串口设备（如 COM5 / /dev/ttyUSB0），走自定义帧协议")
    ap.add_argument("--baud", type=int, default=921600, help="串口波特率（默认 921600）")
    ap.add_argument("--seconds", type=float, default=5.0, help="每个配置的采集时长（默认 5s）")
    ap.add_argument("--probe", default=None,
                    help="逗号分隔的分辨率清单，如 640x480,320x240；默认用契约分辨率")
    ap.add_argument("--fourcc", default="MJPG",
                    help="UVC 像素格式（默认 MJPG；很多摄像头只有 MJPG 才能到 30fps）。填 none 表示不设置")
    ap.add_argument("--dump-rgb-bin", default=None, help="按契约 §4.1 布局导出真实 RGB888 帧的保存路径")
    ap.add_argument("--dump-frames", type=int, default=5, help="导出多少帧（默认 5）")
    ap.add_argument("--outdir", default=str(DEFAULT_OUTDIR), help="JSON 产物目录（默认 metrics/logs/）")
    ap.add_argument("--csvdir", default=str(DEFAULT_CSVDIR),
                    help="CSV 产物目录（默认 metrics/csv/，该目录已被 .gitignore 忽略）")
    ap.add_argument("--selftest", action="store_true", help="只跑离线自检")
    args = ap.parse_args(argv)

    enable_utf8_console()

    if args.selftest:
        return 0 if _selftest() else 1

    contract = load_contract()
    print("契约（来源 %s）：%dx%d %s @ %.0f fps → 单帧 %d B，需要 %.2f MB/s（%.1f Mbps）"
          % (contract["source"], contract["width"], contract["height"], contract["pixel_format"],
             contract["fps"], contract["bytes_per_frame"], contract["required_MBps"],
             contract["required_Mbps"]))
    print()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.list:
        cams = list_cameras()
        if not cams:
            print("没找到任何可用摄像头。")
            print("  · OpenMV 走 USB-VCP 模式时**不会**出现在这里 —— 需要先刷 uvc.bin 固件。")
            print("  · 想走串口请用 --serial COMx。")
        else:
            print("可用摄像头：")
            for cam in cams:
                print("  index %d → 首帧 %dx%d" % (cam["index"], cam["width"], cam["height"]))
            print("\n提示：OpenMV 通常在序号最大的那个（笔记本自带摄像头是 0）。")
        return 0

    if not args.device and not args.serial:
        ap.error("要么 --device，要么 --serial（或 --list / --selftest）")

    record: dict = {
        "tool": "board/openmv/host_capture_test.py",
        "when": datetime.now().isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": sys.version.split()[0],
                 "machine": platform.machine()},
        "contract": contract,
        "results": [],
    }
    if args.dump_rgb_bin:
        record["dump_path"] = args.dump_rgb_bin

    csv_rows: list[dict] = []

    if args.serial:
        print("── 串口模式 %s @ %d baud，%.1fs ──" % (args.serial, args.baud, args.seconds))
        r = measure_serial(args.serial, args.baud, args.seconds)
        record["results"].append({"mode": "serial", **r})
        if "error" in r:
            print("  [FAIL] " + r["error"])
        else:
            print("  解出帧数        : %d（%.2f 帧/s）" % (r["frames_decoded"], r["frames_per_sec"]))
            print("  frame_id 区间   : %s → %s，丢帧 %s（丢帧率 %s）"
                  % (r["first_frame_id"], r["last_frame_id"], r["lost_frames"], r["loss_rate"]))
            print("  线速            : %.3f Mbps（有效载荷 %.2f KB/s）" % (r["wire_Mbps"], r["goodput_KBps"]))
            print("  链路错误        : CRC %d / 丢字节 %d / 超长拒绝 %d"
                  % (r["crc_errors"], r["dropped_bytes"], r["oversize_rejected"]))
            print("  帧类型          : %s" % r["per_type"])
            csv_rows.append({"mode": "serial", "port": args.serial, "baud": args.baud,
                             "fps": r["frames_per_sec"], "loss_rate": r["loss_rate"],
                             "wire_Mbps": r["wire_Mbps"], "crc_errors": r["crc_errors"]})
        json_path = outdir / ("openmv_serial_%s.json" % stamp)
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n产物: %s" % json_path)
        return 0

    # 视频源模式：逐个探测分辨率
    if args.probe:
        targets = []
        for item in args.probe.split(","):
            w, h = item.strip().lower().split("x")
            targets.append((int(w), int(h)))
    else:
        targets = [(contract["width"], contract["height"])]

    print("── 视频源 %r，探测 %d 个配置 × %.1fs ──" % (args.device, len(targets), args.seconds))
    is_file_source = not str(args.device).isdigit()
    if is_file_source and len(targets) > 1:
        print("  ⚠️ 视频文件是固定尺寸，--probe 对它无效（分辨率改不了）；只按第一个配置测。")
        targets = targets[:1]
    dump_path = Path(args.dump_rgb_bin) if args.dump_rgb_bin else None
    verdicts = []
    for i, (w, h) in enumerate(targets):
        r = measure_stream(args.device, w, h, args.seconds, 
                           None if args.fourcc.lower() == "none" else args.fourcc,
                           dump_path if i == 0 else None, args.dump_frames)
        if "error" in r:
            print("  [FAIL] %s: %s" % (r["requested"], r["error"]))
            record["results"].append({"mode": "video", "requested": r["requested"], **r})
            continue
        v = judge(r["actual_width"], r["actual_height"], r["fps"], contract, r["source_kind"])
        if v.get("applicable"):
            verdicts.append(v)
        if r["source_kind"] == "file":
            flag = "回放"
            extra = "  标称 %s fps / 文件共 %s 帧" % (r["nominal_fps"], r["file_total_frames"])
        else:
            flag = "OK  " if v["ok"] else "差  "
            extra = ""
        print("  [%s] 请求 %-9s → 实得 %dx%d  解码 %6.2f fps  首帧 %.0f ms  独立帧 %d/%d%s%s"
              % (flag, r["requested"], r["actual_width"], r["actual_height"], r["fps"],
                 r["first_frame_latency_ms"], r["distinct_frames"], r["frames"],
                 "  ⚠️画面疑似冻结" if r["frozen_source"] else "", extra))
        for p in v["problems"]:
            print("         → %s" % p)
        for nt in v["notes"]:
            print("         · %s" % nt)
        record["results"].append({"mode": "video", **r, "verdict": v})
        csv_rows.append({
            "mode": "video", "device": args.device, "requested": r["requested"],
            "actual": "%dx%d" % (r["actual_width"], r["actual_height"]), "fps": r["fps"],
            "nominal_fps": r["nominal_fps"], "source_kind": r["source_kind"],
            "first_frame_latency_ms": r["first_frame_latency_ms"],
            "distinct_frames": r["distinct_frames"], "frames": r["frames"],
            "achieved_MBps": v["achieved_MBps"], "bandwidth_ratio": v["bandwidth_ratio"],
            "ok": v["ok"],
        })

    best = None
    for v in verdicts:
        if v["ok"]:
            best = v
            break
    record["summary"] = {
        "any_config_meets_contract": best is not None,
        "configs_ok": sum(1 for v in verdicts if v["ok"]),
        "configs_tested": len(verdicts),
        "source_kind": "file" if is_file_source else "camera",
        "conclusion": (
            "本次是**视频文件回放**：只用于回归复现，不能据此判断摄像头能力。"
            "要判断 OpenMV 是否达标，请用 --device <摄像头序号> 或 --serial。"
            if is_file_source else
            ("有配置达标：可作为 A 线采集源"
             if best else
             "没有任何配置达到契约 §0（640×480 RGB888 @ %.0f fps）。"
             "OpenMV Cam H7 的物理上限见 board/openmv/README.md 的带宽算术；"
             "A 线可降级使用（眨眼/PERCLOS 对分辨率不敏感），但 C 线逐点比对不可用。" % contract["fps"])
        ),
    }

    json_path = outdir / ("openmv_capture_%s.json" % stamp)
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = None
    if csv_rows:
        csvdir = Path(args.csvdir)
        csvdir.mkdir(parents=True, exist_ok=True)
        csv_path = csvdir / ("openmv_capture_%s.csv" % stamp)
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
            wr.writeheader()
            wr.writerows(csv_rows)

    print("\n── 结论 ──")
    print("  " + record["summary"]["conclusion"])
    print("  JSON 产物: %s" % json_path)
    if csv_path:
        print("  CSV  产物: %s" % csv_path)
    if args.dump_rgb_bin:
        print("  RGB888 导出: %s（契约 §4.1 布局，可直接喂 C 线 DMA 回放）" % args.dump_rgb_bin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
