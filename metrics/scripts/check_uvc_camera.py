"""check_uvc_camera.py —— USB(UVC) 相机验收工具（W1，见 docs/37 §5）。

为什么需要它：
    USB 路线的第一个问题是"这颗相机到底给不给 **1280x720 @ 60 fps MJPG**"，
    第二个问题是"PS 解 MJPEG 到底要多久"。这两件事都**不能靠猜**：
    前者要读设备真实模式表，后者要用真实 JPEG 帧量解码耗时。
    本脚本把这两件事变成可复现的命令与数字，并且**在没有相机的情况下也能自检**。

回答哪三个问题：
    1) 模式表：有没有 720p60？（Linux 走 V4L2 真表；Windows 走 OpenCV 协商，拿不到真表）
    2) 帧率与抖动：实测到达帧率、帧间隔 p50/p95/max、丢帧估算
    3) 解码预算：单核 / 多线程帧级并行 每帧耗时，与 16.7 ms 预算比较（R1 判据）

用法（仓库根）：
    python metrics/scripts/check_uvc_camera.py                      # 枚举设备 + 试 720p60
    python metrics/scripts/check_uvc_camera.py --capture 10         # 实测 10 s 帧率与抖动
    python metrics/scripts/check_uvc_camera.py --bench-decode       # 解码基准（用相机画面代理）
    python metrics/scripts/check_uvc_camera.py --bench-decode --mjpg-dump dump.mjpg   # 用真实 JPEG
    python metrics/scripts/check_uvc_camera.py --bench-encode-noise # R18：静止场景频谱
    python metrics/scripts/check_uvc_camera.py --selftest           # 无相机自检（进 check_all.py）

退出码：
    0 = 本次请求全部达成；1 = --selftest 有失败；2 = 有判据没过（缺 720p60 / 解码超预算）；
    3 = 没找到可用相机。

⚠️ 平台差异（**这是本脚本最容易被误读的地方**）：
    - Linux(V4L2)：能枚举真实 `ENUM_FMT x ENUM_FRAMEINTERVALS`，能读 `v4l2-ctl --list-ctrls`
      判断曝光/白平衡/增益是否可写。**板上才是这条路的判据环境**。
    - Windows(MSMF)：OpenCV 既不暴露 fourcc 也不暴露模式表，只能"请求一组模式、看协商回来什么"。
      所以 Windows 上的结论只能写成"协商到 1280x720@30"，**不能**写成"该相机支持/不支持 MJPG"。
    - 笔记本 CPU 的解码耗时**不能**外推到 A9：这里测的是工具链与帧尺寸的真实成本，
      A9 的判据必须在板上重测（docs/37 §6.1 已给出 20~37 ms 的【推测】区间）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:  # Windows 控制台默认 GBK，直接 print ⚠️/✅ 会 UnicodeEncodeError 崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]

# 判据常量（docs/37 §5 W1 / §9 M0）
BUDGET_MS_720P60 = 16.7          # 60 fps 每帧预算
TARGET_W, TARGET_H, TARGET_FPS = 1280, 720, 60

# 模式矩阵：先问 60 fps，再退 30 fps；分辨率从高到低
MODE_MATRIX = (
    (1280, 720, 60),
    (1280, 720, 30),
    (1920, 1080, 60),
    (1920, 1080, 30),
    (640, 480, 60),
    (640, 480, 30),
)

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


# ===========================================================================
# 1) 纯函数部分（自检就靠它们：不碰硬件）
# ===========================================================================
def split_mjpg_stream(blob: bytes) -> list[bytes]:
    """把一段 MJPEG 流切成逐帧 JPEG 字节。

    真实相机 dump 出来的流里常夹着垃圾（UVC 头、掉帧残片、半截帧），
    所以规则是：从每个 SOI 到**其后的第一个** EOI，且长度必须 > 4 字节；
    末尾没有 EOI 的半截帧**丢弃**（不猜）。
    """
    frames: list[bytes] = []
    pos = 0
    n = len(blob)
    while True:
        start = blob.find(JPEG_SOI, pos)
        if start < 0:
            break
        end = blob.find(JPEG_EOI, start + 2)
        if end < 0:
            break                      # 尾部半截帧：丢弃
        end += 2
        if end - start > 4:
            frames.append(blob[start:end])
        pos = end
    return frames


def interval_stats(dt_s: list[float]) -> dict:
    """帧间隔统计（秒 -> 毫秒）。空/单样本时返回 None 字段，不编数。"""
    if len(dt_s) < 2:
        return {"n": len(dt_s), "p50_ms": None, "p95_ms": None, "max_ms": None,
                "mean_ms": None, "fps": None}
    xs = sorted(dt_s)
    def pct(p: float) -> float:
        # 线性插值分位（样本少时比"最近秩"稳）
        if len(xs) == 1:
            return xs[0]
        k = (len(xs) - 1) * p / 100.0
        lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)
    total = sum(dt_s)
    return {
        "n": len(dt_s),
        "p50_ms": round(pct(50) * 1e3, 2),
        "p95_ms": round(pct(95) * 1e3, 2),
        "max_ms": round(max(dt_s) * 1e3, 2),
        "mean_ms": round(total / len(dt_s) * 1e3, 2),
        "fps": round((len(dt_s)) / total, 2),
    }


def parse_v4l2_formats(text: str) -> list[dict]:
    """解析 `v4l2-ctl --list-formats-ext` 的输出。

    v4l-utils 的输出长这样（缩进随版本略有差异）：
        [0]: 'MJPG' (Motion-JPEG, compressed)
                Size: Discrete 1280x720
                        Interval: Discrete 0.017s (60.000 fps)
    这里只认三样：fourcc/描述、Size、Interval(fps)，其余行忽略。
    """
    out: list[dict] = []
    cur: dict | None = None
    cur_size: tuple[int, int] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("[") and "]:" in stripped:
            head = stripped.split("]:", 1)[1].strip()
            fourcc = head.split(" ", 1)[0].strip("'\"")
            cur = {"fourcc": fourcc, "desc": head[len(fourcc):].strip(), "sizes": []}
            out.append(cur)
            cur_size = None
            continue
        if cur is None:
            continue
        if stripped.startswith("Size:"):
            token = stripped.split("Size:", 1)[1].strip()
            m = token.replace("Discrete", "").replace("Stepwise", "").strip()
            try:
                w, h = m.split("x")[:2]
                cur_size = (int(w), int(h))
                cur["sizes"].append({"w": cur_size[0], "h": cur_size[1], "fps": []})
            except Exception:  # noqa: BLE001
                cur_size = None
            continue
        if stripped.startswith("Interval:") and cur_size is not None and cur["sizes"]:
            token = stripped.split("Interval:", 1)[1]
            fps = None
            if "(" in token and "fps" in token:
                try:
                    fps = float(token.split("(", 1)[1].split("fps")[0].strip())
                except Exception:  # noqa: BLE001
                    fps = None
            if fps is not None:
                cur["sizes"][-1]["fps"].append(round(fps, 3))
    return out


def has_720p60(formats: list[dict]) -> bool:
    """真表里是否存在 >=1280x720 且 >=59.5 fps 的 MJPG 档。"""
    for fmt in formats:
        if fmt.get("fourcc", "").upper() not in ("MJPG", "JPEG"):
            continue
        for size in fmt.get("sizes", []):
            if size["w"] >= TARGET_W and size["h"] >= TARGET_H:
                if any(f >= TARGET_FPS - 0.5 for f in size.get("fps", [])):
                    return True
    return False


def spectrum_peak(values: list[float], fps: float, lo: float = 0.5, hi: float = 3.0) -> dict:
    """在一维亮度序列里找 [lo,hi] Hz 内的最大谱峰。

    R18 的用法：静止场景**不应**出现离散峰；有峰 = 编码器码控把亮度调制了。
    返回峰频、峰的相对幅度（对 0.5~3 Hz 段中位数）与中位谱。
    """
    import numpy as np

    x = np.asarray(values, dtype=np.float64)
    if x.size < 16 or fps <= 0:
        return {"n": int(x.size), "peak_hz": None, "peak_ratio": None, "note": "样本不足"}
    x = x - x.mean()
    win = np.hanning(x.size)
    spec = np.abs(np.fft.rfft(x * win)) / max(1.0, x.size)
    freqs = np.fft.rfftfreq(x.size, d=1.0 / fps)
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any():
        return {"n": int(x.size), "peak_hz": None, "peak_ratio": None, "note": "频带为空"}
    idx = int(np.argmax(spec[band]))
    peak = float(spec[band][idx])
    med = float(np.median(spec[band])) or 1e-12
    return {
        "n": int(x.size),
        "peak_hz": round(float(freqs[band][idx]), 3),
        "peak_ratio": round(peak / med, 2),
        "median": round(med, 6),
        "noise_floor_ratio_threshold": 8.0,
        "note": "" if peak / med >= 8.0 else "无明显离散峰",
    }


def jpeg_frames_from_video(path: Path, limit: int) -> list[bytes]:
    """从 .mjpg/.avi 里抠 JPEG 帧（找不到就返回空表，不猜）。"""
    blob = path.read_bytes()
    frames = split_mjpg_stream(blob)
    return frames[:limit]


# ===========================================================================
# 2) 设备部分（碰硬件，Windows/Linux 两条实现）
# ===========================================================================
@dataclass
class CamProbe:
    index: int
    backend: str = ""
    opened: bool = False
    negotiated: dict | None = None
    measured: dict | None = None
    error: str = ""


def _open_capture(index: int):
    import cv2

    if os.name == "nt":
        cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(index)
    return cap


def probe_mode(cap, w: int, h: int, fps: float, fourcc: str = "") -> dict:
    """请求一组模式，读回协商结果，并实测 15 帧的帧间隔（快速验证，不是 10 s 统计）。"""
    import cv2

    if fourcc:
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        except Exception:  # noqa: BLE001
            pass
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    for _ in range(3):                      # 冲掉缓存里的旧格式帧
        cap.read()
    got = {
        "req": [w, h, fps, fourcc or "-"],
        "w": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "h": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps_set": round(float(cap.get(cv2.CAP_PROP_FPS)), 2),
    }
    fourcc_raw = int(cap.get(cv2.CAP_PROP_FOURCC))
    got["fourcc_raw"] = "0x%08X" % fourcc_raw
    ts: list[float] = []
    shape = None
    t0 = time.perf_counter()
    while len(ts) < 15 and time.perf_counter() - t0 < 2.0:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        ts.append(time.perf_counter())
        shape = frame.shape
    got["shape"] = list(shape) if shape else None
    got["stats"] = interval_stats([b - a for a, b in zip(ts, ts[1:])])
    return got


def enumerate_cameras(max_index: int) -> list[CamProbe]:
    probes: list[CamProbe] = []
    for idx in range(max_index):
        cap = _open_capture(idx)
        p = CamProbe(index=idx)
        if not cap.isOpened():
            cap.release()
            continue
        p.opened = True
        try:
            p.backend = cap.getBackendName()
        except Exception:  # noqa: BLE001
            p.backend = "?"
        best: dict | None = None
        for (w, h, fps) in MODE_MATRIX:
            got = probe_mode(cap, w, h, fps)
            if got["stats"]["n"] < 5:
                continue
            # 选"分辨率最高、帧率最高"的那一档作为该相机的最优能力
            key = (got["w"] * got["h"], got["stats"]["fps"] or 0)
            if best is None or key > best["_key"]:
                got["_key"] = key
                best = got
        p.negotiated = {k: v for k, v in (best or {}).items() if k != "_key"} or None
        cap.release()
        probes.append(p)
    return probes


def v4l2_devices() -> list[str]:
    """Linux：/dev/video* 列表（Windows 返回空）。"""
    if os.name == "nt":
        return []
    return sorted(str(p) for p in Path("/dev").glob("video*"))


def v4l2_formats(dev: str) -> list[dict]:
    """Linux：优先 v4l2-ctl 真表；没有 v4l-utils 就返回空表（**不假装枚举过**）。"""
    try:
        out = subprocess.run(
            ["v4l2-ctl", "--list-formats-ext", "-d", dev],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError):
        return []
    if out.returncode != 0:
        return []
    return parse_v4l2_formats(out.stdout)


def v4l2_ctrls(dev: str) -> dict:
    """Linux：曝光/白平衡/增益是否**可写**（采购验收的硬条件之一）。"""
    try:
        out = subprocess.run(
            ["v4l2-ctl", "--list-ctrls", "-d", dev],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError):
        return {"available": False, "reason": "v4l2-ctl 不在 PATH（装 v4l-utils）"}
    if out.returncode != 0:
        return {"available": False, "reason": out.stderr.strip()[:200]}
    wanted = ("exposure", "white_balance", "gain", "brightness")
    rows = []
    for line in out.stdout.splitlines():
        s = line.strip()
        if not s or ":" not in s:
            continue
        name = s.split(":", 1)[0].strip()
        flags = s.split(":", 1)[1]
        if any(k in name.lower() for k in wanted):
            rows.append({
                "name": name,
                "writable": "inactive" not in flags,
                "raw": s[:160],
            })
    return {"available": True, "controls": rows}


# ===========================================================================
# 3) 采集统计 / 解码基准 / 编码噪声
# ===========================================================================
def capture_stats(index: int, seconds: float, w: int, h: int, fps: float,
                  fourcc: str = "") -> tuple[dict, list]:
    """实测 N 秒：到达帧率、帧间隔分布、丢帧估算。返回 (统计, 帧列表)。"""
    import cv2

    cap = _open_capture(index)
    if not cap.isOpened():
        cap.release()
        return {"opened": False}, []
    try:
        if fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, fps)
        for _ in range(5):
            cap.read()
        fps_set = round(float(cap.get(cv2.CAP_PROP_FPS)), 2)
        ts: list[float] = []
        frames = []
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            ts.append(time.perf_counter())
            frames.append(frame)
        stats = interval_stats([b - a for a, b in zip(ts, ts[1:])])
        elapsed = (ts[-1] - ts[0]) if len(ts) >= 2 else 0.0
        expected = fps_set * elapsed if fps_set > 0 else float("nan")
        return {
            "opened": True,
            "seconds_requested": seconds,
            "seconds_elapsed": round(elapsed, 2),
            "w": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "h": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps_set": fps_set,
            "frames": len(ts),
            "stats": stats,
            "dropped_estimate": (round(max(0.0, expected - len(ts)), 1)
                                 if len(ts) >= 2 and fps_set > 0 else None),
        }, frames
    finally:
        cap.release()


def _decode_bench_buffers(frames: list, quality: int) -> tuple[list[bytes], str]:
    """把采集到的画面编码成 JPEG，作为"真实尺寸"的解码输入。

    ⚠️ 这是**代理**：编码器是 cv2 自带的 libjpeg-turbo，不是相机里的那颗编码器，
    所以码率/VBR 行为与相机不同（R18 只能另测）。但帧尺寸与内容是真的，
    量的是"解 1280x720 这种图到底要多久"。
    """
    import cv2

    bufs: list[bytes] = []
    for f in frames:
        ok, enc = cv2.imencode(".jpg", f, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            bufs.append(enc.tobytes())
    return bufs, "proxy-cv2"


def bench_decode(bufs: list[bytes], threads: int, gray: bool = False) -> dict:
    """三档解码耗时：单线程 / 多线程帧级并行 / ROI-only（本机不可得则显式说明）。"""
    import cv2

    flag = cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR
    for b in bufs[:3]:                       # 预热（首次 decode 有初始化开销）
        cv2.imdecode(_as_u8(b), flag)
    t_single: list[float] = []
    for b in bufs:
        t0 = time.perf_counter()
        img = cv2.imdecode(_as_u8(b), flag)
        t_single.append(time.perf_counter() - t0)
        if img is None:
            raise RuntimeError("JPEG 解码失败：输入缓冲不是合法 JPEG")

    out = {
        "frames": len(bufs),
        "mode": "gray" if gray else "color",
        "bytes": {"total": sum(len(b) for b in bufs),
                  "mean_kb": round(sum(len(b) for b in bufs) / max(1, len(bufs)) / 1024, 1)},
        "tiers": {},
    }
    st = interval_stats(t_single)
    out["tiers"]["single_thread"] = {
        "ms_p50": round(sorted(t_single)[len(t_single) // 2] * 1e3, 2),
        "ms_p95": round(sorted(t_single)[int(len(t_single) * 0.95)] * 1e3, 2),
        "ms_mean": round(sum(t_single) / len(t_single) * 1e3, 2),
        "wall_ms_per_frame_same_as_mean": True,
        "_fps_stats": st,
    }
    if threads >= 2:
        from concurrent.futures import ThreadPoolExecutor

        t0 = time.perf_counter()

        def _one(b: bytes) -> None:
            cv2.imdecode(_as_u8(b), flag)

        with ThreadPoolExecutor(max_workers=threads) as pool:
            list(pool.map(_one, bufs))
        wall = time.perf_counter() - t0
        out["tiers"]["thread_parallel"] = {
            "threads": threads,
            "wall_ms_total": round(wall * 1e3, 1),
            "wall_ms_per_frame": round(wall / len(bufs) * 1e3, 2),
        }
    out["tiers"]["roi_only"] = {
        "ms_per_frame": None,
        "reason": "libjpeg 的 jpeg_skip_scanlines / crop 解码在 Python 绑定里没有暴露；"
                  "板上要用 C 或 libjpeg-turbo 的 tjDecompressWithROI 另测",
    }
    return out


def _as_u8(buf: bytes):
    import numpy as np

    return np.frombuffer(buf, dtype=np.uint8)


def bench_encode_noise(index: int, seconds: float) -> dict:
    """R18：静止场景下，ROI 均值序列在 0.5~3 Hz 是否出现离散峰（编码器在作弊）。"""
    import cv2
    import numpy as np

    cap = _open_capture(index)
    if not cap.isOpened():
        cap.release()
        return {"opened": False}
    try:
        means: list[float] = []
        sizes: list[int] = []
        ts: list[float] = []
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            ts.append(time.perf_counter())
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            h, w = g.shape
            roi = g[int(h * 0.30):int(h * 0.60), int(w * 0.30):int(w * 0.70)]
            means.append(float(roi.mean()))
            ok2, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            sizes.append(len(enc) if ok2 else 0)
        fps = 0.0
        if len(ts) >= 2:
            fps = (len(ts) - 1) / (ts[-1] - ts[0])
        spec = spectrum_peak(means, fps)
        return {
            "opened": True,
            "frames": len(means),
            "fps": round(fps, 2),
            "roi_mean": {"mean": round(float(np.mean(means)), 3) if means else None,
                         "std": round(float(np.std(means)), 3) if means else None},
            "jpeg_size_bytes": {"mean": int(np.mean(sizes)) if sizes else None,
                                "min": int(np.min(sizes)) if sizes else None,
                                "max": int(np.max(sizes)) if sizes else None},
            "spectrum": spec,
            "verdict": ("⚠️ 0.5~3 Hz 有离散峰，码控可能调制了亮度" if spec.get("peak_hz")
                        and (spec.get("peak_ratio") or 0) >= 8.0 else "✅ 无明显离散峰"),
            "note": "本机工具拿不到相机逐帧量化表；R18 的量化表判据需在板上用 libjpeg-turbo 读 Q 表",
        }
    finally:
        cap.release()


def _capture_best_frames(index: int, n: int) -> tuple[list, dict]:
    """按 720p 优先抓 n 帧，用作解码基准的输入（真实画面）。"""
    import cv2

    cap = _open_capture(index)
    if not cap.isOpened():
        cap.release()
        return [], {"opened": False}
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, TARGET_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, TARGET_H)
        cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
        for _ in range(5):
            cap.read()
        frames = []
        for _ in range(n):
            ok, f = cap.read()
            if not ok or f is None:
                break
            frames.append(f)
        return frames, {"opened": True,
                        "w": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        "h": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}
    finally:
        cap.release()


# ===========================================================================
# 4) 自检（无相机；假设备注入 + 跑真解析逻辑）
# ===========================================================================
V4L2_SAMPLE = """\
ioctl: VIDIOC_ENUM_FMT
\tType: Video Capture

\t[0]: 'MJPG' (Motion-JPEG, compressed)
\t\tSize: Discrete 1280x720
\t\t\tInterval: Discrete 0.017s (60.000 fps)
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t\tSize: Discrete 640x480
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t[1]: 'YUYV' (YUYV 4:2:2)
\t\tSize: Discrete 1280x720
\t\t\tInterval: Discrete 0.100s (10.000 fps)
"""


def selftest() -> int:
    import numpy as np

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        checks.append((name, bool(cond), detail))

    # ---- 1) MJPG 切帧：垃圾前缀、连续帧、尾部半截 ----
    a = b"\xff\xd8" + b"A" * 10 + b"\xff\xd9"
    b = b"\xff\xd8" + b"B" * 5 + b"\xff\xd9"
    tail = b"\xff\xd8" + b"C" * 3
    frames = split_mjpg_stream(b"garbage" + a + b"\x00\x01" + b + tail)
    check("切帧数量（丢弃尾部半截）", len(frames) == 2, f"got={len(frames)}")
    check("切帧内容与顺序", frames == [a, b])
    check("空输入不炸", split_mjpg_stream(b"") == [])

    # ---- 2) 帧间隔统计：已知序列必须算出已知分位 ----
    dts = [1 / 60.0] * 20
    st = interval_stats(dts)
    check("帧间隔 fps≈60", abs((st["fps"] or 0) - 60.0) < 1e-6, f"fps={st['fps']}")
    check("p50≈16.67ms", abs((st["p50_ms"] or 0) - 16.67) < 0.02, f"p50={st['p50_ms']}")
    st2 = interval_stats([0.01, 0.02, 0.03, 0.04])
    check("p95 落在最大值以内", 30.0 <= (st2["p95_ms"] or 0) <= 40.0, f"p95={st2['p95_ms']}")
    check("单样本不编数", interval_stats([0.02])["p50_ms"] is None)

    # ---- 3) v4l2-ctl 真表解析 + 720p60 判定 ----
    fmts = parse_v4l2_formats(V4L2_SAMPLE)
    check("解析出 2 个格式", len(fmts) == 2, f"got={len(fmts)}")
    check("fourcc 正确", [f["fourcc"] for f in fmts] == ["MJPG", "YUYV"])
    check("720p 有 60/30 两档", fmts[0]["sizes"][0]["fps"] == [60.0, 30.0],
          f"got={fmts[0]['sizes'][0]['fps']}")
    check("判定有 720p60", has_720p60(fmts) is True)
    check("YUYV 10fps 不算 720p60", has_720p60([fmts[1]]) is False)
    check("空表判定为 False", has_720p60([]) is False)

    # ---- 4) 频谱：1 Hz 正弦必须报 ~1 Hz；纯噪声无明显峰 ----
    fps = 30.0
    t = np.arange(0, 300) / fps
    sine = 100 + 0.5 * np.sin(2 * np.pi * 1.0 * t)
    sp = spectrum_peak(list(sine), fps)
    check("正弦峰频≈1Hz", abs((sp["peak_hz"] or 0) - 1.0) <= 0.11, f"peak={sp['peak_hz']}")
    rng = np.random.default_rng(20260910)
    noise = 100 + 0.01 * rng.standard_normal(300)
    spn = spectrum_peak(list(noise), fps)
    check("噪声无离散峰（比值<8）", (spn["peak_ratio"] or 0) < 8.0, f"ratio={spn['peak_ratio']}")

    # ---- 5) 解码基准的真逻辑：喂合成 JPEG 必须能解、且统计结构齐全 ----
    import cv2

    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    img[:, :640] = (30, 60, 90)
    img[:, 640:] = (200, 180, 160)
    cv2.putText(img, "VigiLens", (40, 360), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 6)
    ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    check("合成 JPEG 编码成功", ok)
    bufs = [enc.tobytes()] * 12
    bench = bench_decode(bufs, threads=2)
    check("解码三档键齐全",
          set(bench["tiers"]) == {"single_thread", "thread_parallel", "roi_only"})
    check("单线程 p50>0", (bench["tiers"]["single_thread"]["ms_p50"] or 0) > 0)
    check("并行档记账到 12 帧", bench["tiers"]["thread_parallel"]["wall_ms_total"] > 0)
    check("ROI 档显式给原因而非编数",
          bench["tiers"]["roi_only"]["ms_per_frame"] is None
          and "jpeg_skip_scanlines" in bench["tiers"]["roi_only"]["reason"])
    gray = bench_decode(bufs, threads=0, gray=True)
    check("灰度档可解码", (gray["tiers"]["single_thread"]["ms_p50"] or 0) > 0)

    n_pass = sum(1 for _, ok_, _ in checks if ok_)
    for name, ok_, detail in checks:
        mark = "PASS" if ok_ else "FAIL"
        extra = f"  ({detail})" if detail and not ok_ else ""
        print(f"  [{'OK' if ok_ else 'XX'}] {mark}  {name}{extra}")
    print(f"\nRESULT: {'PASS' if n_pass == len(checks) else 'FAIL'} "
          f"({n_pass}/{len(checks)})")
    return 0 if n_pass == len(checks) else 1


# ===========================================================================
# 5) 主流程
# ===========================================================================
def _print_probe_table(probes: list[CamProbe]) -> None:
    usable = [p for p in probes if p.opened]
    print(f"找到 {len(usable)} 个可用相机（探测索引 0~{len(probes) - 1 if probes else 0}）")
    for p in usable:
        neg = p.negotiated or {}
        st = (neg.get("stats") or {})
        print(f"  [idx {p.index}] backend={p.backend}  "
              f"协商={neg.get('w')}x{neg.get('h')}@{neg.get('fps_set')}  "
              f"fourcc={neg.get('fourcc_raw')}  "
              f"实测={st.get('fps')} fps  p50={st.get('p50_ms')} ms  p95={st.get('p95_ms')} ms")
    if os.name == "nt":
        print("  ⚠️ Windows/MSMF 不暴露 fourcc 与真实模式表：上面的 fourcc 只是属性读数，"
              "不代表相机格式；**MJPG 是否存在必须在 Linux/V4L2 上判定**。")


def _verdict_720p60(probes: list[CamProbe], formats_by_dev: dict) -> dict:
    """把"有没有 720p60"的两个证据源分开报，不合并成一个假结论。"""
    linux_devs = list(formats_by_dev)
    verdict = {"judged_on": None, "has_720p60": None, "detail": ""}
    if linux_devs:
        verdict["judged_on"] = "v4l2"
        hits = [d for d, fmts in formats_by_dev.items() if has_720p60(fmts)]
        verdict["has_720p60"] = bool(hits)
        verdict["detail"] = (f"V4L2 真表：{len(hits)}/{len(linux_devs)} 个设备存在 MJPG>=720p60"
                             if hits else "V4L2 真表：没有任何设备提供 MJPG 1280x720@60")
    else:
        best = None
        for p in probes:
            neg = p.negotiated or {}
            st = neg.get("stats") or {}
            if not st:
                continue
            key = (neg.get("w") or 0) * (neg.get("h") or 1)
            if best is None or key > best[0]:
                best = (key, neg, st)
        if best:
            neg, st = best[1], best[2]
            ok = (neg.get("w") == TARGET_W and neg.get("h") == TARGET_H
                  and (st.get("fps") or 0) >= TARGET_FPS - 1.0)
            verdict["judged_on"] = "opencv-negotiation (Windows：非真表，只作能力下界)"
            verdict["has_720p60"] = bool(ok)
            verdict["detail"] = (f"协商到 {neg.get('w')}x{neg.get('h')}，实测 {st.get('fps')} fps"
                                 + ("（达标）" if ok else "（**未达 720p60**，不能据此判定相机不支持）"))
    return verdict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="USB(UVC) 相机验收工具（W1）")
    ap.add_argument("--selftest", action="store_true", help="无相机自检（进 check_all.py）")
    ap.add_argument("--index-max", type=int, default=4, help="Windows 探测的相机索引上限")
    ap.add_argument("--device", type=int, default=0, help="要用哪个索引做实测/基准")
    ap.add_argument("--capture", type=float, default=0.0, metavar="SECONDS",
                    help="实测 N 秒帧率与抖动（0 = 不测）")
    ap.add_argument("--bench-decode", action="store_true", help="解码基准")
    ap.add_argument("--frames", type=int, default=200, help="解码基准用的帧数")
    ap.add_argument("--quality", type=int, default=85, help="代理编码的 JPEG 质量")
    ap.add_argument("--threads", type=int, default=min(2, os.cpu_count() or 1),
                    help="并行档线程数（0/1 = 跳过并行档）")
    ap.add_argument("--gray", action="store_true", help="解码基准按灰度解（更省，rPPG 常用）")
    ap.add_argument("--mjpg-dump", type=Path, default=None,
                    help="用真实相机 dump 的 MJPEG 流做基准（最可信）")
    ap.add_argument("--bench-encode-noise", action="store_true", help="R18 静止场景频谱")
    ap.add_argument("--report-seconds", type=float, default=10.0)
    ap.add_argument("--json", type=Path, default=None, help="把结果写成 JSON（证据）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    result: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "platform": os.name}

    # ---- Linux：先读真表（这是唯一能判定"有没有 720p60"的证据源）----
    formats_by_dev: dict = {}
    ctrls_by_dev: dict = {}
    for dev in v4l2_devices():
        fmts = v4l2_formats(dev)
        if fmts:
            formats_by_dev[dev] = fmts
        ctrls_by_dev[dev] = v4l2_ctrls(dev)
    if formats_by_dev:
        print("=== V4L2 真模式表 ===")
        for dev, fmts in formats_by_dev.items():
            print(f"  {dev}")
            for fmt in fmts:
                for size in fmt["sizes"]:
                    fps = ",".join(str(f) for f in size["fps"]) or "-"
                    print(f"    {fmt['fourcc']:<5} {size['w']}x{size['h']}  fps={fps}")
            if ctrls_by_dev.get(dev, {}).get("available"):
                rows = ctrls_by_dev[dev]["controls"]
                print(f"    可写控制项：{[r['name'] for r in rows if r['writable']][:8]}")
        result["v4l2"] = {"formats": formats_by_dev, "ctrls": ctrls_by_dev}

    probes = enumerate_cameras(max(1, args.index_max))
    print()
    _print_probe_table(probes)
    result["cameras"] = [
        {"index": p.index, "backend": p.backend, "opened": p.opened,
         "negotiated": p.negotiated} for p in probes
    ]

    verdict = _verdict_720p60(probes, formats_by_dev)
    result["verdict_720p60"] = verdict
    print(f"\n=== 720p60 判定（证据源：{verdict['judged_on']}）===")
    print(f"  {verdict['detail']}")

    if not any(p.opened for p in probes) and not formats_by_dev:
        print("\n❌ 没有找到可用相机：确认没被别的程序占用、相机没被禁用。")
        _maybe_write(args, result)
        return 3

    # ---- 10 s 实测 ----
    if args.capture > 0:
        print(f"\n=== 实测 {args.capture:g} s（720p 优先）===")
        stats, _frames = capture_stats(args.device, args.capture, TARGET_W, TARGET_H, TARGET_FPS)
        result["capture"] = stats
        if not stats.get("opened"):
            print("  ❌ 打不开指定相机")
        else:
            st = stats["stats"]
            print(f"  {stats['w']}x{stats['h']}@{stats['fps_set']}  抓到 {stats['frames']} 帧 / "
                  f"{stats['seconds_elapsed']} s")
            print(f"  到达帧率 {st['fps']} fps；间隔 p50 {st['p50_ms']} ms / p95 {st['p95_ms']} ms / "
                  f"max {st['max_ms']} ms；丢帧估算 {stats['dropped_estimate']}")

    # ---- 解码基准 ----
    if args.bench_decode:
        print(f"\n=== 解码基准（{args.frames} 帧）===")
        if args.mjpg_dump:
            bufs = jpeg_frames_from_video(args.mjpg_dump, args.frames)
            source = f"mjpg-dump:{args.mjpg_dump}"
            if not bufs:
                print(f"  ❌ 从 {args.mjpg_dump} 里没切出 JPEG 帧（文件不是 MJPEG 流？）")
        else:
            frames, meta = _capture_best_frames(args.device, args.frames)
            if not frames:
                print("  ❌ 抓不到画面，也没给 --mjpg-dump，无法做解码基准")
                bufs, source = [], "none"
            else:
                bufs, source = _decode_bench_buffers(frames, args.quality)
                print(f"  ⚠️ 输入是**代理** JPEG：{meta['w']}x{meta['h']}，"
                      f"q={args.quality}，编码器=cv2 自带（不是相机编码器）")
        if bufs:
            bench = bench_decode(bufs, threads=args.threads, gray=args.gray)
            bench["source"] = source
            result["bench_decode"] = bench
            t = bench["tiers"]
            print(f"  帧数 {bench['frames']}，平均 {bench['bytes']['mean_kb']} KB/帧，"
                  f"模式 {bench['mode']}")
            print(f"  单线程     p50 {t['single_thread']['ms_p50']} ms  "
                  f"p95 {t['single_thread']['ms_p95']} ms")
            if "thread_parallel" in t:
                print(f"  {t['thread_parallel']['threads']} 线程帧级并行  "
                      f"{t['thread_parallel']['wall_ms_per_frame']} ms/帧（墙钟摊薄）")
            print(f"  ROI-only   未测：{t['roi_only']['reason']}")
            best_ms = (t["thread_parallel"]["wall_ms_per_frame"]
                       if "thread_parallel" in t else t["single_thread"]["ms_p50"])
            passed = best_ms is not None and best_ms < BUDGET_MS_720P60
            print(f"  → 最快档 {best_ms} ms/帧 vs 预算 {BUDGET_MS_720P60} ms："
                  f"{'✅ 达标' if passed else '❌ 超预算'}"
                  f"（⚠️ 本机 CPU ≠ A9，判定必须在板上重测）")
            result["bench_decode"]["best_ms"] = best_ms
            result["bench_decode"]["budget_ms"] = BUDGET_MS_720P60
            result["bench_decode"]["passed"] = bool(passed)

    # ---- R18 编码噪声 ----
    if args.bench_encode_noise:
        print(f"\n=== R18 静止场景频谱（{args.report_seconds:g} s）===")
        noise = bench_encode_noise(args.device, args.report_seconds)
        result["encode_noise"] = noise
        if not noise.get("opened"):
            print("  ❌ 打不开相机")
        else:
            sp = noise["spectrum"]
            print(f"  {noise['frames']} 帧 @ {noise['fps']} fps；"
                  f"ROI 均值 {noise['roi_mean']['mean']} ± {noise['roi_mean']['std']}")
            print(f"  JPEG 大小 {noise['jpeg_size_bytes']['mean']} B "
                  f"(min {noise['jpeg_size_bytes']['min']} / max {noise['jpeg_size_bytes']['max']})")
            print(f"  0.5~3 Hz 最强峰 {sp['peak_hz']} Hz，相对中位谱 {sp['peak_ratio']}× "
                  f"→ {noise['verdict']}")
            print(f"  ⚠️ {noise['note']}")

    _maybe_write(args, result)
    # 判据未过 -> 退出码 2（check_all 只跑 --selftest，不受影响）
    failed = False
    if result.get("verdict_720p60", {}).get("has_720p60") is False:
        failed = True
    if result.get("bench_decode", {}).get("passed") is False:
        failed = True
    return 2 if failed else 0


def _maybe_write(args, result: dict) -> None:
    if not args.json:
        return
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n（结果已写入 {args.json}）")


if __name__ == "__main__":
    raise SystemExit(main())
