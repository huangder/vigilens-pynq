# -*- coding: utf-8 -*-
"""
raw_to_contract.py —— 把 OpenMV 落盘的原始帧转成**契约 §4.1 布局的 RGB888**。

项目：知倦 / VigiLens —— OpenMV 图像采集测试的 PC 侧收尾工具

为什么转换放在 PC 而不是板上（这是刻意的设计决定）：
  1. **口径只能有一处实现**（AGENTS.md 的通行纪律）。板上转一份、PC 上转一份，
     迟早两边不一致。这里放一份，并且**能被 selftest 真跑到**。
  2. MicroPython 上逐像素 Python 循环极慢；PC 上 921,600 字节是瞬间的事。
  3. 板上写 SD 卡本来就慢，不该再叠加 CPU 转换。

产物：`bytes[frame*W*H*3 + 3*(y*W+x) + c]`，`c = 0→R, 1→G, 2→B`
      —— 与 `docs/interface.md` §4.1 的 `frames.bin` **逐字节同布局**，
      因此可以直接喂给 A 线，也可以给 C 线做 DMA 回放。

⚠️ RGB565 → RGB888 的**位扩展公式不猜**：用 `--calibrate` 读 OpenMV 落盘时
   记录的 `pixel_samples`（raw16 ↔ 驱动展开的 r,g,b），反推出唯一匹配的公式。
   没有对拍样本时**必须显式指定** `--expand replicate|shift`，否则本工具拒绝转换。

用法::

    # 0) 先看这份 dump 是什么格式、有哪些帧
    .venv\\Scripts\\python.exe board/openmv/raw_to_contract.py <dump目录> --info

    # 1) 用板上的对拍样本确定扩展公式（推荐，别跳过）
    .venv\\Scripts\\python.exe board/openmv/raw_to_contract.py <dump目录> --calibrate

    # 2) 转换（--expand 可用 calibrate 的结论；省略则自动用 calibrate 结果，否则报错）
    .venv\\Scripts\\python.exe board/openmv/raw_to_contract.py <dump目录> \\
        --out metrics/logs/openmv_frames.bin --report metrics/logs/openmv_convert.json

    # 离线自检（无硬件）
    .venv\\Scripts\\python.exe board/openmv/raw_to_contract.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from pathlib import Path

CONTRACT_W = 640
CONTRACT_H = 480
CONTRACT_BPP = 3


# ---------------------------------------------------------------------------
# RGB565 → RGB888 的两种位扩展
# ---------------------------------------------------------------------------

def _expand_replicate(v5_or_6, bits):
    """把 5(或 6) bit 扩展到 8 bit：高 3 位左移 + 用高位补齐低位，能取满 0~255。"""
    shift = 8 - bits
    return (v5_or_6 << shift) | (v5_or_6 >> (2 * bits - 8))


def _expand_shift(v5_or_6, bits):
    """只左移，低位置 0：5bit 最大 248，6bit 最大 252（**到不了 255**）。"""
    return v5_or_6 << (8 - bits)


EXPANDERS = {
    "replicate": _expand_replicate,
    "shift": _expand_shift,
}


def rgb565_to_rgb888(buf: bytes, w: int, h: int, stride: int | None = None,
                     expand: str = "replicate") -> bytes:
    """RGB565（小端，v = b0 | b1<<8，r5<<11 | g6<<5 | b5）→ RGB888。

    stride: 每行**字节数**。None = w*2（无填充）。有些帧缓冲会按行对齐补字节，
            这时 stride > w*2，必须按 stride 跳行，否则整幅图会斜切。
    """
    exp = EXPANDERS.get(expand)
    if exp is None:
        raise ValueError("未知的 expand=%r（可选 %s）" % (expand, sorted(EXPANDERS)))
    if stride is None:
        stride = w * 2
    need = stride * h - (stride - w * 2)   # 最后一行的尾部填充不计
    if len(buf) < need:
        raise ValueError("缓冲区太小：需要 %d B（stride=%d），实际 %d B" % (need, stride, len(buf)))

    out = bytearray(w * h * 3)
    o = 0
    for y in range(h):
        base = y * stride
        for x in range(w):
            i = base + x * 2
            v = buf[i] | (buf[i + 1] << 8)
            out[o] = exp((v >> 11) & 0x1F, 5)      # R
            out[o + 1] = exp((v >> 5) & 0x3F, 6)   # G
            out[o + 2] = exp(v & 0x1F, 5)          # B
            o += 3
    return bytes(out)


def gray_to_rgb888(buf: bytes, w: int, h: int, stride: int | None = None) -> bytes:
    """灰度 → RGB888（三通道复制同一灰度值）。这是**上采样**，不增加信息。"""
    if stride is None:
        stride = w
    out = bytearray(w * h * 3)
    o = 0
    for y in range(h):
        base = y * stride
        for x in range(w):
            g = buf[base + x]
            out[o] = out[o + 1] = out[o + 2] = g
            o += 3
    return bytes(out)


# ---------------------------------------------------------------------------
# stride 推断（帧缓冲有行填充时不推断就会整幅斜切）
# ---------------------------------------------------------------------------

def infer_stride(total_bytes: int, w: int, h: int, bpp: int) -> tuple[int, str]:
    """从实际字节数推断每行字节数。返回 (stride, 说明)。无法解释时抛异常。"""
    exact = w * bpp
    if total_bytes == exact * h:
        return exact, "无行填充（stride == w*bpp）"
    if h > 0 and total_bytes % h == 0:
        s = total_bytes // h
        if s >= exact:
            return s, "检测到行填充：stride=%d，每行多 %d B（推测为对齐）" % (s, s - exact)
    raise ValueError(
        "字节数无法解释：实际 %d B，w*h*bpp = %d，既不等于它也不能被 h=%d 整除。"
        "请把该帧的原始大小贴回项目记录再排查。" % (total_bytes, exact * h, h))


# ---------------------------------------------------------------------------
# 对拍：用板上的 pixel_samples 反推扩展公式
# ---------------------------------------------------------------------------

def calibrate(samples: list[dict]) -> dict:
    """给定 [{raw, rgb}]，判断哪个扩展公式能复现驱动给出的 r,g,b。"""
    usable = []
    for s in samples or []:
        raw, rgb = s.get("raw"), s.get("rgb")
        if raw is None or not rgb or len(rgb) < 3:
            continue
        usable.append((int(raw), (int(rgb[0]), int(rgb[1]), int(rgb[2]))))

    result = {"usable_samples": len(usable), "verdict": {}, "match": None,
              "note": "raw16 按 (r5<<11)|(g6<<5)|b5 解析"}
    if not usable:
        result["match"] = "no-samples"
        result["note"] += "；没有可用的对拍样本 → 必须用 --expand 显式指定公式"
        return result

    for name, exp in EXPANDERS.items():
        ok = 0
        worst = 0
        for raw, (r, g, b) in usable:
            er = exp((raw >> 11) & 0x1F, 5)
            eg = exp((raw >> 5) & 0x3F, 6)
            eb = exp(raw & 0x1F, 5)
            d = max(abs(er - r), abs(eg - g), abs(eb - b))
            worst = max(worst, d)
            if d == 0:
                ok += 1
        result["verdict"][name] = {"exact_matches": ok, "worst_abs_diff": worst,
                                   "of": len(usable)}

    exact = [n for n, v in result["verdict"].items() if v["exact_matches"] == len(usable)]
    if len(exact) == 1:
        result["match"] = exact[0]
    elif len(exact) > 1:
        result["match"] = "ambiguous:" + ",".join(sorted(exact))
        result["note"] += "；两种公式在本次样本上表现相同（样本区分度不够），请多采几个不同灰度的像素"
    else:
        best = min(result["verdict"].items(), key=lambda kv: kv[1]["worst_abs_diff"])
        result["match"] = "none"
        result["note"] += ("；两种公式都对不上驱动输出，最接近的是 %s（最大差 %d）。"
                           "说明本机固件的 RGB565 位序与假设不同，"
                           "请把 --info 与对拍样本原样贴回项目记录后再定。"
                           % (best[0], best[1]["worst_abs_diff"]))
    return result


# ---------------------------------------------------------------------------
# dump 目录读写
# ---------------------------------------------------------------------------

def load_dump(dump_dir: Path) -> tuple[dict, list[Path]]:
    meta_path = dump_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError("找不到 %s —— 这个目录不是 openmv_capture_test.py 的 dump 输出？"
                                % meta_path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    frames = sorted(p for p in dump_dir.glob("frame_*.raw"))
    if not frames:
        raise FileNotFoundError("目录里没有 frame_*.raw：%s" % dump_dir)
    return meta, frames


def convert(dump_dir: Path, out_path: Path | None, expand: str | None,
            report_path: Path | None) -> dict:
    meta, files = load_dump(dump_dir)
    w = meta.get("width")
    h = meta.get("height")
    pf = (meta.get("pixformat") or "").upper()
    bpp = meta.get("bytes_per_pixel") or 0
    if not w or not h:
        raise ValueError("meta.json 缺 width/height，无法转换")

    cal = calibrate(meta.get("pixel_samples") or [])
    chosen = expand
    if chosen is None:
        m = cal.get("match")
        if m in EXPANDERS:
            chosen = m
            print("[calibrate] 用板上对拍样本自动确定扩展公式：%s" % chosen)
        else:
            raise SystemExit(
                "拒绝转换：RGB565 的位扩展公式未确定（calibrate → %r）。\n"
                "  这是刻意的 —— 猜错会让每个像素系统性偏色，而且很难肉眼发现。\n"
                "  请二选一：① 重新跑 dump 模式拿到 pixel_samples 后用 --calibrate；\n"
                "            ② 明确指定 --expand replicate 或 --expand shift。\n"
                "  详细：%s" % (m, cal.get("note"))
            )

    report = {"dump_dir": str(dump_dir), "pixformat": pf, "width": w, "height": h,
              "frames_in": len(files), "expand": chosen, "calibration": cal,
              "contract_layout": "bytes[frame*W*H*3 + 3*(y*W+x) + c], c=0->R",
              "warnings": [], "frames": []}

    if pf == "JPEG":
        report["warnings"].append(
            "源格式是 JPEG（有损）。转出的 RGB888 **不能**作为 C 线黄金参考（契约 §4.3 要求容差 0）。"
            "只能用于 A 线的行为指标（眨眼/PERCLOS 等）。"
        )

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(out_path, "wb")
    else:
        fh = None

    try:
        for idx, f in enumerate(files):
            raw = f.read_bytes()
            # JPEG 的字节数每帧都不同，不能按 w*h*bpp 推 stride，直接原样另存并明确拒绝转换
            if pf == "JPEG":
                report["frames"].append({"file": f.name, "bytes": len(raw),
                                         "converted": False,
                                         "reason": "JPEG 需要解码器，本工具不做（会引入未声明的口径）"})
                report["warnings"].append("有 JPEG 帧未转换：%s" % f.name)
                continue
            if bpp not in (1, 2):
                raise ValueError("不支持的 bytes_per_pixel=%r（pixformat=%s）" % (bpp, pf))
            stride, how = infer_stride(len(raw), w, h, bpp)
            if bpp == 2:
                rgb = rgb565_to_rgb888(raw, w, h, stride, chosen)
            else:
                rgb = gray_to_rgb888(raw, w, h, stride)
            if fh is not None:
                fh.write(rgb)
            report["frames"].append({"file": f.name, "bytes_in": len(raw),
                                     "bytes_out": len(rgb), "stride": stride,
                                     "stride_note": how, "converted": True})
            if idx == 0:
                report["stride"] = stride
                report["stride_note"] = how
                # ⚠️ 注意："无行填充" 里也含 "填充" 二字，所以必须前缀匹配，
                #    不能用 `"填充" in how`（那样正常情况也会误报警告）。
                if how.startswith("检测到行填充"):
                    report["warnings"].append("检测到行填充，已按 stride=%d 逐行跳读" % stride)
    finally:
        if fh is not None:
            fh.close()

    converted = [f for f in report["frames"] if f.get("converted")]
    report["frames_out"] = len(converted)
    if out_path is not None:
        report["out_path"] = str(out_path)
        report["out_bytes"] = out_path.stat().st_size if out_path.exists() else 0
        expected = len(converted) * w * h * CONTRACT_BPP
        report["out_bytes_expected"] = expected
        if report["out_bytes"] != expected:
            report["warnings"].append(
                "输出字节数 %d != 期望 %d（%d 帧 × %d×%d×3）"
                % (report["out_bytes"], expected, len(converted), w, h))

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("转换报告：%s" % report_path)
    return report


# ---------------------------------------------------------------------------
# 自检（无硬件、无 dump 目录）
# ---------------------------------------------------------------------------

def _pack565(r5, g6, b5):
    return (r5 << 11) | (g6 << 5) | b5


def _selftest() -> bool:
    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # T1 两个扩展公式的端点行为（这是区分它们的根据）
    chk("T1a replicate(31,5)==255 且 replicate(63,6)==255",
        _expand_replicate(31, 5) == 255 and _expand_replicate(63, 6) == 255,
        "%d / %d" % (_expand_replicate(31, 5), _expand_replicate(63, 6)))
    chk("T1b shift(31,5)==248 且 shift(63,6)==252（到不了 255）",
        _expand_shift(31, 5) == 248 and _expand_shift(63, 6) == 252,
        "%d / %d" % (_expand_shift(31, 5), _expand_shift(63, 6)))
    chk("T1c 两者在 0 处都为 0",
        _expand_replicate(0, 5) == 0 and _expand_shift(0, 5) == 0)

    # T2 已知色的 RGB565 打包 → 转换结果必须落在正确的通道（**这是通道顺序的硬证据**）
    W, H = 2, 2
    # 纯红 (31,0,0)、纯绿 (0,63,0)、纯蓝 (0,0,31)、白 (31,63,31)
    vals = [_pack565(31, 0, 0), _pack565(0, 63, 0), _pack565(0, 0, 31), _pack565(31, 63, 31)]
    buf = b"".join(struct.pack("<H", v) for v in vals)
    out = rgb565_to_rgb888(buf, W, H, None, "replicate")
    px = [tuple(out[i * 3:i * 3 + 3]) for i in range(4)]
    chk("T2a 纯红 → (255,0,0)", px[0] == (255, 0, 0), str(px[0]))
    chk("T2b 纯绿 → (0,255,0)", px[1] == (0, 255, 0), str(px[1]))
    chk("T2c 纯蓝 → (0,0,255)", px[2] == (0, 0, 255), str(px[2]))
    chk("T2d 白   → (255,255,255)", px[3] == (255, 255, 255), str(px[3]))
    chk("T2e 布局 c=0→R（字节序不许颠倒）", out[0] == 255 and out[1] == 0 and out[2] == 0,
        "%d,%d,%d" % (out[0], out[1], out[2]))

    # T3 shift 变体在饱和值上确实不同（说明 T1 的区分是有意义的）
    out_s = rgb565_to_rgb888(struct.pack("<H", _pack565(31, 63, 31)), 1, 1, None, "shift")
    chk("T3 shift 变体：白 → (248,252,248) 而非 255",
        tuple(out_s) == (248, 252, 248), str(tuple(out_s)))

    # T4 stride 推断：无填充 / 有填充 / 无法解释
    s, _ = infer_stride(640 * 2 * 480, 640, 480, 2)
    chk("T4a 无填充时 stride == 1280", s == 1280, str(s))
    s2, note2 = infer_stride(1280 * 480 + 480 * 16, 640, 480, 2)
    chk("T4b 每行多 16 B 填充时推断出 stride == 1296", s2 == 1296, "%s %s" % (s2, note2))
    try:
        infer_stride(12345, 640, 480, 2)
        chk("T4c 无法解释的字节数应抛异常", False, "竟然没抛")
    except ValueError:
        chk("T4c 无法解释的字节数应抛异常", True)

    # T5 行填充时**不许斜切**：造一张每行填充的图，转换后每行内容必须与无填充版一致
    w2, h2 = 4, 3
    rows = []
    for y in range(h2):
        row = b"".join(struct.pack("<H", _pack565(y, y * 2, y)) for _ in range(w2))
        rows.append(row + b"\x00" * 6)          # 每行多 6 字节填充
    padded = b"".join(rows)
    st, nt = infer_stride(len(padded), w2, h2, 2)
    with_pad = rgb565_to_rgb888(padded, w2, h2, st, "replicate")
    no_pad = rgb565_to_rgb888(
        b"".join(b"".join(struct.pack("<H", _pack565(y, y * 2, y)) for _ in range(w2))
                 for y in range(h2)), w2, h2, None, "replicate")
    chk("T5 有行填充 vs 无行填充，结果逐字节相同（不斜切）",
        with_pad == no_pad and st == w2 * 2 + 6, "stride=%d note=%s" % (st, nt))

    # T6 灰度复制到三通道
    g = gray_to_rgb888(bytes([0, 127, 255, 10]), 2, 2, None)
    chk("T6 灰度 → RGB 三通道相等", g[0:3] == b"\x00\x00\x00" and g[3:6] == b"\x7f\x7f\x7f"
        and g[6:9] == b"\xff\xff\xff" and g[9:12] == b"\x0a\x0a\x0a",
        " ".join("%02x" % b for b in g))

    # T7 对拍逻辑：造一组"用的是 replicate"的样本，calibrate 必须判为 replicate
    samples = []
    for r5, g6, b5 in [(0, 0, 0), (31, 0, 0), (0, 63, 0), (0, 0, 31), (31, 63, 31), (17, 33, 9)]:
        samples.append({"raw": _pack565(r5, g6, b5),
                        "rgb": [_expand_replicate(r5, 5), _expand_replicate(g6, 6),
                                _expand_replicate(b5, 5)]})
    cal = calibrate(samples)
    chk("T7a 对拍能唯一判定 replicate", cal["match"] == "replicate", str(cal["match"]))

    samples_s = []
    for r5, g6, b5 in [(0, 0, 0), (31, 0, 0), (0, 63, 0), (0, 0, 31), (31, 63, 31), (17, 33, 9)]:
        samples_s.append({"raw": _pack565(r5, g6, b5),
                          "rgb": [_expand_shift(r5, 5), _expand_shift(g6, 6), _expand_shift(b5, 5)]})
    cal_s = calibrate(samples_s)
    chk("T7b 对拍能唯一判定 shift", cal_s["match"] == "shift", str(cal_s["match"]))

    cal_none = calibrate([])
    chk("T7c 无样本时不猜公式（必须返回 no-samples）",
        cal_none["match"] == "no-samples", str(cal_none["match"]))

    # T8 位序假设错误时必须**判为 none 而不是硬选一个**（防"悄悄偏色"）
    weird = [{"raw": _pack565(31, 0, 0), "rgb": [0, 0, 255]}]   # 假装 R/B 反了
    cal_w = calibrate(weird)
    chk("T8 公式对不上时判 none（不硬选）", cal_w["match"] == "none", str(cal_w["match"]))

    failed = [c for c in checks if not c[1]]
    print("=" * 76)
    print("raw_to_contract.py 自检（无硬件）")
    print("=" * 76)
    for name, ok, detail in checks:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
        if detail and not ok:
            print("         -> " + detail)
    print("-" * 76)
    print("RESULT: %s  (%d/%d)" % ("PASS" if not failed else "FAIL",
                                   len(checks) - len(failed), len(checks)))
    return not failed


# ---------------------------------------------------------------------------

def _print_info(dump_dir: Path) -> int:
    meta, files = load_dump(dump_dir)
    print("目录      : %s" % dump_dir)
    print("pixformat : %s   framesize: %s   fb: %s"
          % (meta.get("pixformat"), meta.get("framesize"), meta.get("framebuffers")))
    print("宽x高     : %sx%s   bytes_per_pixel: %s"
          % (meta.get("width"), meta.get("height"), meta.get("bytes_per_pixel")))
    print("帧数      : meta 记 %s，目录里找到 %s 个 frame_*.raw"
          % (meta.get("frame_count"), len(files)))
    print("回读失败  : %s" % meta.get("readback_failures"))
    print("对拍样本  : %d 个" % len(meta.get("pixel_samples") or []))
    sizes = [f.stat().st_size for f in files]
    if sizes:
        print("文件大小  : min %d / max %d B" % (min(sizes), max(sizes)))
        w, h, bpp = meta.get("width"), meta.get("height"), meta.get("bytes_per_pixel") or 0
        if w and h and bpp:
            try:
                st, note = infer_stride(sizes[0], w, h, bpp)
                print("stride    : %d  （%s）" % (st, note))
            except ValueError as e:
                print("stride    : 无法推断 —— %s" % e)
    print("\n契约口径: %dx%d RGB888 = %d B/帧"
          % (CONTRACT_W, CONTRACT_H, CONTRACT_W * CONTRACT_H * CONTRACT_BPP))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把 OpenMV 原始 dump 转成契约 §4.1 的 RGB888")
    ap.add_argument("dump_dir", nargs="?", help="openmv_capture_test.py 的 dump 目录")
    ap.add_argument("--info", action="store_true", help="只看这份 dump 的元信息")
    ap.add_argument("--calibrate", action="store_true", help="用板上的对拍样本判定扩展公式")
    ap.add_argument("--expand", choices=sorted(EXPANDERS), default=None,
                    help="显式指定位扩展公式；不给则用 calibrate 的结论")
    ap.add_argument("--out", default=None, help="输出的 frames.bin（契约 §4.1 布局）")
    ap.add_argument("--report", default=None, help="把转换报告写成 JSON")
    ap.add_argument("--selftest", action="store_true", help="只跑离线自检")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.selftest:
        return 0 if _selftest() else 1
    if not args.dump_dir:
        ap.error("要么给 dump 目录，要么 --selftest")

    d = Path(args.dump_dir)
    if args.info:
        return _print_info(d)

    if args.calibrate:
        meta, _ = load_dump(d)
        cal = calibrate(meta.get("pixel_samples") or [])
        print(json.dumps(cal, ensure_ascii=False, indent=2))
        if cal["match"] not in EXPANDERS:
            print("\n⚠️ 未能唯一确定公式 → 转换时必须显式 --expand（工具会拒绝自动转换）")
            return 2
        print("\n→ 转换时可用 --expand %s（或省略让工具自动采用）" % cal["match"])
        return 0

    rep = convert(d, Path(args.out) if args.out else None, args.expand,
                  Path(args.report) if args.report else None)
    print("帧数 in/out : %s / %s" % (rep["frames_in"], rep.get("frames_out")))
    print("扩展公式    : %s" % rep.get("expand"))
    if rep.get("stride"):
        print("stride      : %s（%s）" % (rep["stride"], rep.get("stride_note")))
    if rep.get("out_path"):
        print("输出        : %s（%d B，期望 %d B）"
              % (rep["out_path"], rep.get("out_bytes", 0), rep.get("out_bytes_expected", 0)))
    for wmsg in rep.get("warnings", []):
        print("⚠️  " + wmsg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
