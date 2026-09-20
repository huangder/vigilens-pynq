"""check_p4_readiness.py —— P4（视频到位后的标定与锁定）的**数据就绪检查**。

为什么需要它：
    P4 整体被真实视频阻塞，而"缺什么"很容易说不清（是没录？帧率不对？标注没写？）。
    本脚本把 P4 的入场券变成一张可勾选的清单：4 段标准视频 + 每段的标注 +
    规范符合性（分辨率 / 帧率 / 时长）+ 内容指纹（SHA256，黄金结果锁定要用）。

    它**现在就能跑**：视频没录时会把每一项都列成"缺"，这正是它该说的话。

用法（仓库根）：
    python metrics/scripts/check_p4_readiness.py
    python metrics/scripts/check_p4_readiness.py --raw data/raw --ann data/annotations
退出码：0 = 4 段视频与标注齐备且符合规范；1 = 有缺项（列出缺什么）。

⚠️ 本脚本只读，不改任何文件。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]

# 契约 §0：640×480 RGB888 @30 fps（v1.2 草案：45→30）；data/README.md：每段 20~30 s
EXPECT_W, EXPECT_H, EXPECT_FPS = 640, 480, 30.0
MIN_SECONDS, MAX_SECONDS = 18.0, 40.0      # 给转码留一点余量，但明显不对就报出来
VIDEOS = ["still", "blink", "yawn", "turn"]
EVENTS = {"blink", "long_close", "yawn", "turn", "occluded"}


def find_ffprobe() -> str | None:
    """优先 PATH，其次项目自带的 .tools/ffmpeg（本机就是后者）。"""
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    local = REPO_ROOT / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"
    return str(local) if local.exists() else None


def probe(path: Path, ffprobe: str) -> dict | None:
    """读视频的宽/高/帧率/时长；读不出来返回 None。

    ⚠️ 时长要**两段都查**：mp4 的时长在 stream 段，而 webm/mkv 常常只在 format 段
    （stream 段直接给 `N/A`）。只查 stream 会把合法视频误判成"时长 0 秒"。
    """
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,r_frame_rate,duration",
           "-of", "default=noprint_wrappers=1", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    info: dict[str, float] = {}
    for line in r.stdout.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k in ("width", "height"):
            info[k] = int(v)
        elif k == "r_frame_rate" and "/" in v:
            num, _, den = v.partition("/")
            info["fps"] = round(float(num) / float(den), 3) if float(den) else 0.0
        elif k == "duration":
            try:
                info["duration"] = float(v)
            except ValueError:
                pass            # "N/A"：交给下面的 format 兜底

    if "duration" not in info:
        r2 = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        for line in (r2.stdout or "").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "duration":
                try:
                    info["duration"] = float(v.strip())
                except ValueError:
                    pass
    return info


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_annotations(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    """解析标注 CSV：返回 (文件头里的 meta, 事件行)。"""
    meta: dict[str, str] = {}
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            for tok in body.split():
                if "=" in tok:
                    k, _, v = tok.partition("=")
                    meta[k.strip()] = v.strip()
            continue
        parts = [p.strip() for p in line.split(",")]
        if header is None:
            header = parts
            continue
        rows.append(dict(zip(header, parts)))
    return meta, rows


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="P4 数据就绪检查（只读）")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--ann", default="data/annotations")
    ap.add_argument("--golden", default="data/golden")
    args = ap.parse_args()

    raw = REPO_ROOT / args.raw
    ann = REPO_ROOT / args.ann
    golden = REPO_ROOT / args.golden
    ffprobe = find_ffprobe()

    print("=== P4 数据就绪检查（视频到位后的标定与锁定）===")
    print(f"视频目录: {raw.relative_to(REPO_ROOT)}")
    print(f"标注目录: {ann.relative_to(REPO_ROOT)}")
    print(f"ffprobe : {ffprobe or '未找到（无法校验分辨率/帧率/时长）'}")
    print()

    problems: list[str] = []
    ready = 0
    for name in VIDEOS:
        vpath = raw / f"{name}.mp4"
        apath = ann / f"{name}.csv"
        marks: list[str] = []

        if not vpath.exists():
            problems.append(f"{name}.mp4 不存在（放 {raw.relative_to(REPO_ROOT)}/，规范见 data/README.md）")
            marks.append("视频 ❌")
        else:
            size_mb = vpath.stat().st_size / 1048576
            digest = sha256(vpath)
            info = probe(vpath, ffprobe) if ffprobe else None
            if info is None:
                marks.append(f"视频 ⚠️ {size_mb:.1f} MB（未能解析参数）")
                problems.append(f"{name}.mp4 无法解析（文件损坏？或 ffprobe 不可用）")
            else:
                fps = info.get("fps", 0.0)
                dur = info.get("duration", 0.0)
                ok_wh = (info.get("width") == EXPECT_W and info.get("height") == EXPECT_H)
                ok_fps = abs(fps - EXPECT_FPS) < 0.5
                ok_dur = MIN_SECONDS <= dur <= MAX_SECONDS
                # 标记要如实反映"是否符合规范"，不能一边报问题一边打勾
                marks.append(f"视频 {'✅' if (ok_wh and ok_fps and ok_dur) else '⚠️'} "
                             f"{info.get('width')}×{info.get('height')} @{fps:g}fps "
                             f"{dur:.1f}s {size_mb:.1f}MB")
                if not ok_wh:
                    problems.append(f"{name}.mp4 分辨率 {info.get('width')}×{info.get('height')} "
                                    f"≠ 契约的 {EXPECT_W}×{EXPECT_H}")
                if not ok_fps:
                    problems.append(f"{name}.mp4 帧率 {fps:g} ≠ 契约的 {EXPECT_FPS:g}（须重新转码）")
                if not ok_dur:
                    problems.append(f"{name}.mp4 时长 {dur:.1f}s 不在 {MIN_SECONDS:g}~{MAX_SECONDS:g}s 内")
                if ok_wh and ok_fps and ok_dur:
                    ready += 1
            marks.append(f"sha256={digest[:12]}…")

        if not apath.exists():
            marks.append("标注 ❌")
            problems.append(f"{name}.csv 标注不存在（放 {ann.relative_to(REPO_ROOT)}/，格式见 data/README.md）")
        else:
            meta, rows = read_annotations(apath)
            bad = [r for r in rows if r.get("event") not in EVENTS]
            marks.append(f"标注 ✅ {len(rows)} 条事件"
                         + (f"，其中 {len(bad)} 条 event 非法" if bad else ""))
            if bad:
                problems.append(f"{name}.csv 有 {len(bad)} 条事件的 event 不在 {sorted(EVENTS)} 内")
            if not rows:
                problems.append(f"{name}.csv 没有任何事件行")

        print(f"  {name:<6} " + "  ".join(marks))

    print()
    existing_golden = sorted(p.name for p in golden.glob("*") if p.is_file() and p.name != ".gitkeep")
    print(f"data/golden/ 现有 {len(existing_golden)} 个文件"
          + (f"：{', '.join(existing_golden[:6])}" if existing_golden else "（空，A10 还没跑）"))
    print()

    if problems:
        print(f"[未就绪] P4 还缺以下 {len(problems)} 项：")
        for p in problems:
            print(f"  · {p}")
        print()
        print("→ 补齐后重跑本脚本；再按 backend/P4_CALIBRATION_GUIDE.md 逐项标定。")
        return 1

    print(f"[就绪] 4 段视频与标注齐备（{ready}/4 段通过规范校验）。")
    print("→ 下一步：python metrics/scripts/make_golden.py   （锁定 A10 黄金结果）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
