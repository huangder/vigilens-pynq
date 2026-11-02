#!/usr/bin/env python3
"""把一段视频（或摄像头）打包成开发板可接收的 RGB888 帧序列。

为什么需要这一层
----------------
Mizar-Z7020 上的 USB Host 控制器能起来（ci_hdrc root hub 可见），但 ULPI
viewport 永远等不到 run 完成（2026-10-26 实测，见
`metrics/evidence/2026-10-26_w7_usb_host_up_phy_silent.md`），所以板上**没有**
`/dev/video0`。摄像头改在主机侧采集，视频帧按本模块的格式送进板端 DDR。

文件格式（与 `fpga/sim/data_motion*` 的测试向量兼容）
----------------------------------------------------
    frames.bin  = frame0 的 W×H×3 字节 + frame1 ...（frame-major，RGB888）
    单帧内偏移  = 3*(y*W + x) + c，c = 0→R, 1→G, 2→B

注意两个容易踩的点：
  * OpenCV 读出来是 **BGR**，本模块统一转成 **RGB**；转错会让板端灰度
    整体偏色（不是崩溃，是静默错）。
  * 不要在契约帧里塞图像。这里产出的是**旁路文件**，不经过 `backend/contract.py`。

灰度参考模型（与 `fpga/src/rgb2gray.cpp` 逐字节一致）
----------------------------------------------------
    Y = (77*R + 150*G + 29*B + 128) >> 8
    只保留 (x % sample < keep) 且 (y % sample < keep) 的像素
    720p 档 sample=8/keep=3 → 480×270；640 档 sample=5/keep=3 → 384×288

`clip_digests()` 用同一式算每帧的 sum 与 FNV-1a 32 位哈希，板端 phase 10
回传同样的两个数，主机据此判定"板子真的算对了"，不需要把 1.3 MB 灰度图
经 JTAG 读回来。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "vigilens.video_clip/1"
LAYOUT = "frame*W*H*3 + (y*W+x)*3 + c"


@dataclass(frozen=True)
class Tier:
    """一个几何档位。bitstream / 固件 / 灰度尺寸必须三者同档。"""

    name: str
    width: int
    height: int
    gray_width: int
    gray_height: int
    sample: int
    keep: int
    fps: int
    bitstream: str
    firmware: str


TIERS: dict[str, Tier] = {
    "720p": Tier(
        name="720p",
        width=1280,
        height=720,
        gray_width=480,
        gray_height=270,
        sample=8,
        keep=3,
        fps=30,
        bitstream="board/bitstream/system_wrapper_720p60.bit",
        firmware="board/s4_bench/s4_bench_720.bin",
    ),
    "vga": Tier(
        name="vga",
        width=640,
        height=480,
        gray_width=384,
        gray_height=288,
        sample=5,
        keep=3,
        fps=30,
        bitstream="board/bitstream/system_wrapper.bit",
        firmware="board/s4_bench/s4_bench.bin",
    ),
}


class ClipError(ValueError):
    """帧序列不合法（尺寸/通道/为空）。"""


def get_tier(name: str) -> Tier:
    try:
        return TIERS[name]
    except KeyError:
        raise ClipError(f"未知档位 {name!r}；可选：{', '.join(TIERS)}") from None


def tier_for_size(width: int, height: int) -> Tier:
    for t in TIERS.values():
        if (t.width, t.height) == (width, height):
            return t
    raise ClipError(
        f"{width}×{height} 不对应任何档位；可选 "
        + "、".join(f"{t.name}={t.width}×{t.height}" for t in TIERS.values())
    )


def _to_rgb_u8(image: Any, *, index: int) -> np.ndarray:
    """把一帧转成 H×W×3 的 uint8 **RGB**。

    · OpenCV / 视频文件给的是 BGR → 这里翻通道；
    · 灰度图会复制成三通道；
    · `backend.capture.SyntheticImage` 没有像素，直接拒绝（合成帧源不能当视频传）。
    """
    if not hasattr(image, "shape") or not hasattr(image, "ndim"):
        raise ClipError(
            f"第 {index} 帧是合成帧（没有像素），不能打包成视频；"
            "请用真实摄像头/视频文件，或 make_clip.py --test-pattern"
        )
    arr = np.asarray(image)
    if arr.ndim == 2:
        arr = np.repeat(arr[:, :, None], 3, axis=2)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    elif arr.ndim != 3 or arr.shape[2] != 3:
        raise ClipError(f"第 {index} 帧形状不支持：{arr.shape}")
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    # OpenCV 的 BGR → RGB。即使上游已经是 RGB，本项目约定输入就是 BGR。
    rgb = np.ascontiguousarray(arr[:, :, ::-1])
    return rgb


def _resize_rgb(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    try:
        import cv2
    except ImportError:
        raise ClipError(
            f"需要缩放到 {width}×{height}，但当前环境没有 OpenCV；"
            "请换用同尺寸源，或安装 opencv-python"
        ) from None
    interp = cv2.INTER_AREA if (width * height < rgb.shape[0] * rgb.shape[1]) else cv2.INTER_LINEAR
    return cv2.resize(rgb, (width, height), interpolation=interp)


def write_clip(
    frames: Iterable[Any],
    out_dir: str | Path,
    *,
    tier: str | Tier = "720p",
    fps: int | None = None,
    source: str = "unknown",
    max_frames: int | None = None,
    allow_resize: bool = False,
) -> dict:
    """把帧序列写成 `frames.bin` + `manifest.json`，返回 manifest。

    参数
    ----
    frames       : 可迭代的帧；每帧按 **BGR** 解释（与 OpenCV 一致）
    out_dir      : 输出目录（会被创建；旧的 frames.bin/manifest.json 会被覆盖）
    tier         : "720p" 或 "vga"
    fps          : 覆盖档位默认帧率（只写进 manifest，不影响字节格式）
    max_frames   : 最多写多少帧；None = 全部（摄像头源必须给上限）
    allow_resize : 源尺寸与档位不一致时是否用 OpenCV 缩放（默认拒绝）
    """
    t = get_tier(tier) if isinstance(tier, str) else tier
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    part = out / "frames.bin.part"
    final = out / "frames.bin"

    digest = hashlib.sha256()
    n = 0
    total = 0
    try:
        with part.open("wb") as fh:
            for i, image in enumerate(frames):
                if max_frames is not None and n >= max_frames:
                    break
                rgb = _to_rgb_u8(image, index=i)
                if rgb.shape[:2] != (t.height, t.width):
                    if not allow_resize:
                        raise ClipError(
                            f"第 {i} 帧是 {rgb.shape[1]}×{rgb.shape[0]}，档位 {t.name} "
                            f"要求 {t.width}×{t.height}；加 --allow-resize 或换档"
                        )
                    rgb = _resize_rgb(rgb, t.width, t.height)
                raw = rgb.tobytes()
                fh.write(raw)
                digest.update(raw)
                n += 1
                total += len(raw)
        if n == 0:
            raise ClipError("一帧都没读到；检查源路径/摄像头序号/--frames")
        part.replace(final)
    except Exception:
        part.unlink(missing_ok=True)
        raise

    manifest = {
        "schema": SCHEMA,
        "tool": "board/video_link/make_clip.py",
        "source": source,
        "tier": t.name,
        "width": t.width,
        "height": t.height,
        "fps": int(fps if fps is not None else t.fps),
        "frames": n,
        "frame_bytes": t.width * t.height * 3,
        "bytes": total,
        "sha256": digest.hexdigest(),
        "color_order": "RGB",
        "layout": LAYOUT,
        "gray": {
            "width": t.gray_width,
            "height": t.gray_height,
            "sample": t.sample,
            "keep": t.keep,
            "formula": "Y=(77*R+150*G+29*B+128)>>8",
        },
        "bitstream": t.bitstream,
        "firmware": t.firmware,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def load_manifest(clip_dir: str | Path) -> dict:
    p = Path(clip_dir) / "manifest.json"
    if not p.exists():
        raise ClipError(f"没有 manifest.json：{p}")
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ClipError(f"manifest.json 不是合法 JSON：{e}") from None
    if m.get("schema") != SCHEMA:
        raise ClipError(f"manifest schema 不匹配：{m.get('schema')!r} != {SCHEMA!r}")
    for key in ("width", "height", "frames", "frame_bytes", "sha256", "tier"):
        if key not in m:
            raise ClipError(f"manifest 缺字段 {key!r}")
    return m


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_clip(clip_dir: str | Path) -> dict:
    """校验 manifest 与 frames.bin 是否一致（大小 + sha256）。"""
    clip = Path(clip_dir)
    m = load_manifest(clip)
    f = clip / "frames.bin"
    if not f.exists():
        return {"ok": False, "reasons": [f"缺 frames.bin：{f}"]}
    reasons: list[str] = []
    size = f.stat().st_size
    want = int(m["frames"]) * int(m["frame_bytes"])
    if size != want:
        reasons.append(f"frames.bin 大小 {size} != frames×frame_bytes {want}")
    if size != int(m.get("bytes", want)):
        reasons.append(f"frames.bin 大小 {size} != manifest.bytes {m.get('bytes')}")
    actual = sha256_file(f)
    if actual != m["sha256"]:
        reasons.append(f"sha256 不符：文件 {actual[:16]}… / manifest {str(m['sha256'])[:16]}…")
    return {"ok": not reasons, "reasons": reasons, "sha256": actual, "bytes": size}


def iter_clip_frames(clip_dir: str | Path, *, limit: int | None = None) -> Iterator[np.ndarray]:
    """按需迭代 clip 里的帧（memmap，不一次性占满内存）。"""
    clip = Path(clip_dir)
    m = load_manifest(clip)
    w, h = int(m["width"]), int(m["height"])
    n = int(m["frames"])
    if limit is not None:
        n = min(n, limit)
    arr = np.memmap(clip / "frames.bin", dtype=np.uint8, mode="r", shape=(n, h, w, 3))
    for i in range(n):
        yield np.asarray(arr[i])


def gray_from_rgb(rgb: np.ndarray, tier: str | Tier) -> np.ndarray:
    """按冻结口径把 RGB 帧转成灰度（含 3/5 或 3/8 相位点采样）。"""
    t = get_tier(tier) if isinstance(tier, str) else tier
    rgb = np.asarray(rgb)
    if rgb.shape[:2] != (t.height, t.width):
        raise ClipError(
            f"灰度参考要求 {t.width}×{t.height}，收到 {rgb.shape[1]}×{rgb.shape[0]}"
        )
    r = rgb[:, :, 0].astype(np.uint32)
    g = rgb[:, :, 1].astype(np.uint32)
    b = rgb[:, :, 2].astype(np.uint32)
    y = (77 * r + 150 * g + 29 * b + 128) >> 8
    ys = (np.arange(t.height) % t.sample) < t.keep
    xs = (np.arange(t.width) % t.sample) < t.keep
    out = y[np.ix_(ys, xs)]
    assert out.shape == (t.gray_height, t.gray_width), out.shape
    return out.astype(np.uint8)


def fnv1a32(data: bytes | np.ndarray) -> int:
    """32 位 FNV-1a；板端 C 用同一个常数，主机/板端逐帧对哈希。"""
    buf = data.tobytes() if isinstance(data, np.ndarray) else data
    h = 2166136261
    for b in buf:
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def clip_digests(clip_dir: str | Path) -> list[dict]:
    """每帧的 (sum, fnv1a32)；板端 phase 10 回传同样两个数。"""
    m = load_manifest(clip_dir)
    t = get_tier(str(m["tier"]))
    out: list[dict] = []
    for i, rgb in enumerate(iter_clip_frames(clip_dir)):
        gray = gray_from_rgb(rgb, t)
        raw = gray.tobytes()
        out.append(
            {
                "frame": i + 1,
                "sum": int(gray.sum(dtype=np.uint64)),
                "hash": fnv1a32(raw),
                "gray_bytes": len(raw),
            }
        )
    return out


def plan_transfer(clip_dir: str | Path, *, link_bytes_per_s: float = 300_000.0) -> dict:
    """估算 JTAG 传输时间。

    `link_bytes_per_s` 的默认 0.30 MB/s 来自 `board/s4_bench/s4_run.py` 的历史
    实测区间 0.26~0.35 MB/s（docs/36）。它**不是**本次运行实测值，只用于排期。
    """
    m = load_manifest(clip_dir)
    nbytes = int(m["bytes"])
    sec = nbytes / link_bytes_per_s if link_bytes_per_s > 0 else float("inf")
    return {
        "frames": int(m["frames"]),
        "bytes": nbytes,
        "link_bytes_per_s": link_bytes_per_s,
        "estimated_seconds": sec,
        "note": "按历史实测 0.26~0.35 MB/s 估；实际以 xsdb 日志为准",
    }
