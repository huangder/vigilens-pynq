# -*- coding: utf-8 -*-
"""
inject_video.py —— 把**提前录好的影像**变成板卡/PC 两侧都能直接吃的测试向量

项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）

它解决什么问题
    M3 之前没有真实摄像头可用（相机侧只剩降规格采集能力，见 docs/10 §3），
    但 PL 的像素链（roi_statistic / rgb2gray / motion_quality）需要**真实内容的帧**才能算
    "功能测试"。所以这里把一段录好的视频转成三样东西：
      ① 契约布局的 RGB888 帧序列（喂板卡：PL 像素链 / DMA）
      ② **黄金参考 CSV**（ROI 求和、帧间运动量）—— 与 C 线 `fpga/sim/data*/` 的那套
         逐字节同口径，因此 `board/hw_sw_compare.py --data-root <本目录>` 可以直接用
      ③ 灰度向量（喂 `board/dma_test.py --golden-gray`）

产物树（`--out-dir` 下）——**目录结构就是 `hw_sw_compare.py --data-root` 要的样子**
    data/frames.bin              N 帧 RGB888 连排（契约布局：`3*(y*W+x)+c`，c: 0→R,1→G,2→B）
    data/golden_roi.csv          9 个 ROI 用例 × 前 5 帧（表头同 fpga/sim/gen_frames.py）
    data_motion/rgb_frames.bin   同上（同一份字节；hw_sw_compare 从这里读 10 帧）
    data_motion/gray.bin         N 帧灰度连排（3/5 相位抽取，先挑行再挑列）
    data_motion/golden_motion.csv 帧 1..N-1 的运动量（帧 0 刻意不给，见 docs/interface.md §3.3）
    data_motion/meta.txt         尺寸/字节数/阈值/口径（同 gen_motion_vectors.py 的键）
    frame0.rgb / frame0.gray     **单帧**，直接喂 `board/dma_test.py --frame` / `--golden-gray`
                                 ／`board/bringup_check.py --frame`
    manifest.txt                 源文件、档位、帧数、SHA256（**刻意不含时间戳**，同输入同产物）

用法
    # ① 转向量（录像 → 向量）
    python board/cam_detect/inject_video.py --source data/raw/blink.mp4 --out-dir _cam_build/vec_blink
    python board/cam_detect/inject_video.py --source data/raw/blink.mp4 --out-dir ... --frames 30 --start 15
    python board/cam_detect/inject_video.py --source data/raw/blink.mp4 --out-dir ... --tier 720p60

    # ② 板卡侧：把向量灌进 PL 像素链（两种入口，都不需要相机）
    python board/hw_sw_compare.py --bit system.bit --data-root _cam_build/vec_blink   # 离线/上板都用它
    python board/dma_test.py --bit system.bit --frame _cam_build/vec_blink/frame0.rgb \
           --golden-gray _cam_build/vec_blink/frame0.gray

    # ③ PC 侧：用同一段录像测"我写的程序"（A 线管线 + B 线网页）
    python backend/run_pipeline.py --source data/raw/blink.mp4 --json metrics/logs/last.json
    python metrics/scripts/run_demo.py --source data/raw/blink.mp4 --loop

    # ④ 离线自检（不需要板卡、不需要 cv2、不写临时文件）
    python board/cam_detect/inject_video.py --selftest

⚠️ 三条必须知道的口径（写错就会"黄金参考对不上、看起来像 PL 算错"）
    1. **通道顺序**：契约是 **RGB**；OpenCV 读进来是 BGR ⇒ 必须 `[..., ::-1]` 翻一次。
       本脚本只在 `extract_video()` 里翻，写文件时不碰顺序。
    2. **灰度**：`Y = (77R + 150G + 29B + 128) >> 8`；**缩放**：保留 `x % 5 < 3` 且 `y % 5 < 3`
       （先挑行再挑列）。这两个口径**直接调用 C 线生成器 `fpga/sim/gen_motion_vectors.py`
       的函数**，不重写公式 —— 重写就是制造第二个真相。
    3. **ROI 是半开区间** `[x0,x1)`，并由 `fpga/sim/gen_frames.py::roi_sum` 负责越界裁剪
       （空/反向 ROI 计 0）——同样直接复用。

⚠️ 与"录像自身帧率"无关：本脚本只挑帧、不解释时间。时间轴由消费方（
   `backend/run_pipeline.py` 的 `--fps` / 契约的 `frame_id`）决定。

退出码：0 = 成功；1 = 失败（含自检 FAIL）。
"""

import argparse
import hashlib
import os
import re
import sys

# ---------------------------------------------------------------------------
# 路径与"复用而非重写"的导入
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))          # board/cam_detect
BOARD = os.path.dirname(HERE)                              # board
REPO = os.path.dirname(BOARD)                              # 仓库根

for p in (os.path.join(REPO, "fpga", "sim"), BOARD):
    if p not in sys.path:
        sys.path.insert(0, p)

import regmap as R                      # 档位表（_TIERS）与 RGB_BYTES/GRAY_* —— 唯一来源
import gen_frames as GF                 # build_cases / roi_sum（ROI 黄金参考口径）
import gen_motion_vectors as GM         # gray_of / kept_indices / rgb_to_gray_decimated / motion_of

DEFAULT_MOTION_THRESH = 16              # config.yaml 的 motion_thresh_gray（读不到时的兜底）
ROI_FRAMES = 5                          # hw_sw_compare.compare_roi 读 5 帧
MOTION_FRAMES = 10                      # hw_sw_compare.compare_gray_and_motion 读 10 帧


def console_utf8():
    """Windows GBK 控制台下不因中文/⚠️ 崩掉（与 board/dma_test.py 同一做法）。

    ⚠️ 复盘过的坑：脚本打印完 `RESULT: PASS` 之后才 UnicodeEncodeError，退出码变成 1 ——
    人眼看着 PASS、机器记着 FAIL。`errors="replace"` 只把编不出的字符变 '?'，绝不崩。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        rec = getattr(stream, "reconfigure", None)
        if rec is None:
            continue
        try:
            rec(encoding="utf-8", errors="replace")
        except Exception:      # noqa: BLE001
            pass


def tier_of(name):
    """从 regmap 的档位表取 (w, h, num, den)。表是唯一来源，本脚本不复制它。"""
    tiers = getattr(R, "_TIERS", None)
    if not tiers:
        raise SystemExit("regmap.py 里没有 _TIERS 档位表：本脚本的档位取值必须以它为准")
    if name not in tiers:
        raise SystemExit("档位 %r 不在 regmap._TIERS 里（可选：%s）"
                         % (name, ", ".join(sorted(tiers))))
    t = tiers[name]
    return t["w"], t["h"], t["num"], t["den"]


def read_motion_thresh_from_config(default=DEFAULT_MOTION_THRESH):
    """从 config.yaml 读 `motion_thresh_gray`（阈值唯一来源）；读不到就返回 default。

    只做一次正则读取，**不 import backend**（这个脚本是 C 线工具，不引入 A 线依赖）。
    """
    path = os.path.join(REPO, "config.yaml")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return default, None
    m = re.search(r"^motion_thresh_gray:\s*(\d+)", text, re.M)
    if not m:
        return default, None
    return int(m.group(1)), path


# ---------------------------------------------------------------------------
# 1. 取帧：录像 → RGB888 字节
# ---------------------------------------------------------------------------
def extract_video(path, w, h, start, n):
    """用 OpenCV 读 n 帧（跳过 start 帧），返回 [rgb_bytes, ...]。

    ⚠️ `cv2` 读出来是 **BGR**，契约要 **RGB** ⇒ 这里翻一次，别在别处翻。
    ⚠️ 尺寸不符时用 `cv2.resize` 缩放到档位尺寸（双线性）并**打印提示**：
       缩小/放大都会改变像素统计，黄金参考随之改变 —— 这没问题，
       但要知道"板卡上跑的是缩放后的内容、不是原始录像"。
    """
    try:
        import cv2
    except ImportError as exc:                       # pragma: no cover
        raise SystemExit("需要 OpenCV 才能读视频（%s）；"
                         "或用 --selftest 只验向量生成路径" % exc)
    import numpy as np

    cap = cv2.VideoCapture(int(path) if str(path).isdigit() else str(path))
    if not cap.isOpened():
        raise SystemExit("打不开视频源：%r" % path)

    frames = []
    idx = 0
    resized_warned = False
    try:
        while len(frames) < n:
            ok, img = cap.read()
            if not ok:
                break
            if idx < start:
                idx += 1
                continue
            idx += 1
            fh_, fw_ = img.shape[0], img.shape[1]
            if (fw_, fh_) != (w, h):
                if not resized_warned:
                    print("  [info] 源尺寸 %dx%d ≠ 档位 %dx%d ⇒ 按档位缩放（黄金参考随之改变）"
                          % (fw_, fh_, w, h))
                    resized_warned = True
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
            rgb = np.ascontiguousarray(img[:, :, ::-1])       # BGR → RGB
            frames.append(rgb.tobytes())
    finally:
        cap.release()
    return frames


# ---------------------------------------------------------------------------
# 2. 写向量（纯函数：只吃 [rgb_bytes]，不碰 cv2）
# ---------------------------------------------------------------------------
def _gray_all(rgb_frames, w, h, num, den):
    """按档位的抽取比算灰度。返回 (grays, ow, oh)。

    ⚠️ `gen_motion_vectors.kept_indices` 读模块级的 DECIM_NUM/DECIM_DEN，
       所以这里**临时改写那两个全局量**再还原 —— 为的是复用它那套"先挑行再挑列"的实现，
       而不是在本脚本里重抄一遍公式（抄一遍 = 制造第二个真相）。
    """
    old = (GM.DECIM_NUM, GM.DECIM_DEN)
    GM.DECIM_NUM, GM.DECIM_DEN = num, den
    try:
        grays, ow, oh = [], None, None
        for rgb in rgb_frames:
            g, ow, oh = GM.rgb_to_gray_decimated(rgb, w, h)
            grays.append(g)
        return grays, ow, oh
    finally:
        GM.DECIM_NUM, GM.DECIM_DEN = old


def write_vectors(rgb_frames, w, h, num, den, out_dir, *,
                  thresh=DEFAULT_MOTION_THRESH, source_desc="<iterable>",
                  tier_name="640x480", roi_frames=ROI_FRAMES):
    """把 RGB 帧写成机器可吃的向量树。返回 dict（清单/摘要，供打印与自检断言）。"""
    n = len(rgb_frames)
    if n == 0:
        raise SystemExit("没有取到任何帧 —— 检查 --start/--frames 与源文件")
    frame_bytes = w * h * 3
    for i, f in enumerate(rgb_frames):
        if len(f) != frame_bytes:
            raise SystemExit("第 %d 帧长度 %d ≠ 期望 %d（档位 %s）"
                             % (i, len(f), frame_bytes, tier_name))
    want = max(roi_frames, MOTION_FRAMES)
    if n < want:
        print("  [warn] 只有 %d 帧，`hw_sw_compare.py` 比对需要 ≥%d 帧"
              "（ROI 读 %d、运动量读 %d）—— 会照常写，但板卡比对会缺行"
              % (n, want, roi_frames, MOTION_FRAMES))

    data_dir = os.path.join(out_dir, "data")
    motion_dir = os.path.join(out_dir, "data_motion")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(motion_dir, exist_ok=True)

    # ---- ① 帧序列（两处同一份字节；契约布局：3*(y*W+x)+c，c: 0→R,1→G,2→B）----
    blob = b"".join(rgb_frames)
    for p in (os.path.join(data_dir, "frames.bin"),
              os.path.join(motion_dir, "rgb_frames.bin")):
        with open(p, "wb") as fh:
            fh.write(blob)

    # ---- ② 灰度 ----
    grays, ow, oh = _gray_all(rgb_frames, w, h, num, den)
    gray_blob = b"".join(grays)
    with open(os.path.join(motion_dir, "gray.bin"), "wb") as fh:
        fh.write(gray_blob)

    # ---- ③ ROI 黄金参考（表头/注释与 fpga/sim/gen_frames.py 保持一致）----
    cases = GF.build_cases(w, h)
    roi_path = os.path.join(data_dir, "golden_roi.csv")
    with open(roi_path, "w", newline="\n") as fh:
        fh.write("# roi_statistic golden reference -- generated by "
                 "board/cam_detect/inject_video.py\n")
        fh.write("# DO NOT EDIT BY HAND; regenerate instead.\n")
        fh.write("# meta width=%d height=%d frames=%d source=%s bytes=%d "
                 "rgb888_interleaved_rgb\n" % (w, h, n, source_desc, frame_bytes))
        fh.write("case,frame,x0,y0,x1,y1,sum_r,sum_g,sum_b,count\n")
        for name, x0, y0, x1, y1 in cases:
            for fi in range(min(roi_frames, n)):
                sr, sg, sb, cnt = GF.roi_sum(rgb_frames[fi], w, x0, y0, x1, y1)
                fh.write("%s,%d,%d,%d,%d,%d,%d,%d,%d,%d\n"
                         % (name, fi, x0, y0, x1, y1, sr, sg, sb, cnt))

    # ---- ④ 运动量黄金参考（帧 0 刻意不给）----
    mot_path = os.path.join(motion_dir, "golden_motion.csv")
    with open(mot_path, "w", newline="\n") as fh:
        fh.write("# motion_quality golden reference -- generated by "
                 "board/cam_detect/inject_video.py\n")
        fh.write("# DO NOT EDIT BY HAND; regenerate instead.\n")
        fh.write("# NOTE: frame 0 is EXCLUDED -- prev-frame buffer is undefined at power-on,\n")
        fh.write("#       so a valid result starts from frame 1 (see docs/interface.md 3.3).\n")
        fh.write("frame,diff_total,motion_pixels,motion_ratio_q16,count\n")
        for i in range(1, n):
            dt, mp, rat, cnt = GM.motion_of(grays[i], grays[i - 1], ow * oh, thresh)
            fh.write("%d,%d,%d,%d,%d\n" % (i, dt, mp, rat, cnt))

    # ---- ⑤ meta.txt（键与 gen_motion_vectors.py 一致，外加 source/tier）----
    with open(os.path.join(motion_dir, "meta.txt"), "w", newline="\n") as fh:
        fh.write("in_width=%d\n" % w)
        fh.write("in_height=%d\n" % h)
        fh.write("out_width=%d\n" % ow)
        fh.write("out_height=%d\n" % oh)
        fh.write("frames=%d\n" % n)
        fh.write("rgb_bytes=%d\n" % frame_bytes)
        fh.write("gray_bytes=%d\n" % (ow * oh))
        fh.write("motion_thresh=%d\n" % thresh)
        fh.write("gray_formula=(77R+150G+29B+128)>>8\n")
        fh.write("decim=%d/%d keep_x%%%d<%d keep_y%%%d<%d\n" % (num, den, den, num, den, num))
        fh.write("tier=%s\n" % tier_name)
        fh.write("source=%s\n" % source_desc)

    # ---- ⑥ 单帧文件（喂 dma_test/bringup_check）----
    with open(os.path.join(out_dir, "frame0.rgb"), "wb") as fh:
        fh.write(rgb_frames[0])
    with open(os.path.join(out_dir, "frame0.gray"), "wb") as fh:
        fh.write(grays[0])

    # ---- ⑦ manifest（证据；刻意不含时间戳 ⇒ 同输入同产物）----
    def sha(b):
        return hashlib.sha256(b).hexdigest()

    manifest = os.path.join(out_dir, "manifest.txt")
    with open(manifest, "w", newline="\n") as fh:
        fh.write("inject_video.py manifest\n")
        fh.write("source=%s\n" % source_desc)
        fh.write("tier=%s\n" % tier_name)
        fh.write("in_width=%d in_height=%d\n" % (w, h))
        fh.write("out_width=%d out_height=%d\n" % (ow, oh))
        fh.write("frames=%d\n" % n)
        fh.write("roi_rows=%d motion_rows=%d\n"
                 % (len(cases) * min(roi_frames, n), n - 1))
        fh.write("motion_thresh=%d\n" % thresh)
        fh.write("frames_bin_sha256=%s\n" % sha(blob))
        fh.write("gray_bin_sha256=%s\n" % sha(gray_blob))
        fh.write("frame0_sha256=%s\n" % sha(rgb_frames[0]))

    return {
        "out_dir": out_dir,
        "frames": n,
        "frame_bytes": frame_bytes,
        "out_width": ow,
        "out_height": oh,
        "gray_bytes": ow * oh,
        "roi_rows": len(cases) * min(roi_frames, n),
        "motion_rows": n - 1,
        "frames_sha256": sha(blob),
        "gray_sha256": sha(gray_blob),
        "roi_path": roi_path,
        "motion_path": mot_path,
        "manifest": manifest,
    }


# ---------------------------------------------------------------------------
# 3. 自检（离线：不需要板卡、不需要 cv2、不写临时文件）
# ---------------------------------------------------------------------------
def selftest():
    """离线自检：用**已入库的黄金参考**反证本脚本的口径与格式没跑偏。

    为什么不拿视频做自检：视频解码依赖本机 OpenCV/编解码器（本项目已踩过
    "OpenCV 造不出 MJPG .avi" 的环境坑），而**口径对不对**与视频无关 ——
    所以自检直接喂 `gen_frames.build_frames` 的 LCG 帧（纯标准库、可复现），
    再与 `fpga/sim/data/golden_roi.csv`、`fpga/sim/data_motion/golden_motion.csv` 逐行比。
    """
    ok = fail = skip = 0
    lines = []

    def good(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            lines.append("[ ok ] %s%s" % (name, ("：" + detail) if detail else ""))
        else:
            fail += 1
            lines.append("[FAIL] %s%s" % (name, ("：" + detail) if detail else ""))

    # ---- T1 档位表与 regmap 一致 ----
    w, h, num, den = tier_of("640x480")
    good("T1 档位 640x480 与 regmap 一致",
         (w, h, num, den) == (R.FRAME_W, R.FRAME_H, 3, 5),
         "%dx%d decim=%d/%d" % (w, h, num, den))
    grays, ow, oh = _gray_all([b"\x00" * (w * h * 3)], w, h, num, den)
    good("T2 灰度尺寸 == regmap.GRAY_*", (ow, oh) == (R.GRAY_W, R.GRAY_H),
         "%dx%d = %d 字节（期望 %d）" % (ow, oh, len(grays[0]), R.GRAY_PIXELS))
    for tname, want_wh in (("720p60", (480, 270)), ("1080p45", (480, 270))):
        tw, th, tn, td = tier_of(tname)
        g2, ow2, oh2 = _gray_all([b"\x00" * (tw * th * 3)], tw, th, tn, td)
        good("T3 档位 %s 灰度尺寸" % tname, (ow2, oh2) == want_wh,
             "%dx%d（期望 %dx%d）" % (ow2, oh2, want_wh[0], want_wh[1]))

    # ---- T4 用 LCG 帧复算已入库的 ROI 黄金参考（容差 0）----
    frames = GF.build_frames(w, h, 20260910, 20260911)
    rgb_frames = [f[1] for f in frames]
    golden_roi = os.path.join(REPO, "fpga", "sim", "data", "golden_roi.csv")
    if not os.path.exists(golden_roi):
        skip += 1
        lines.append("[skip] T4 没有入库的 golden_roi.csv（%s）" % golden_roi)
    else:
        want = {}
        with open(golden_roi, encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("#") or ln.startswith("case,"):
                    continue
                p = ln.strip().split(",")
                if len(p) == 10:
                    # 列序：case,frame,x0,y0,x1,y1,sum_r,sum_g,sum_b,count
                    # ⇒ 只比后 4 个求和字段（前 4 个是 ROI 定义本身）
                    want[(p[0], int(p[1]))] = tuple(int(x) for x in p[6:10])
        diff = 0
        checked = 0
        for name, x0, y0, x1, y1 in GF.build_cases(w, h):
            for fi in range(5):
                got = GF.roi_sum(rgb_frames[fi], w, x0, y0, x1, y1)
                exp = want.get((name, fi))
                if exp is None:
                    continue
                checked += 1
                if tuple(got) != exp:
                    diff += 1
                    if diff <= 3:
                        lines.append("        %s 帧%d: got %s want %s"
                                     % (name, fi, got, exp))
        good("T4 ROI 黄金参考逐行复算（容差 0）", diff == 0 and checked == 45,
             "比了 %d 行，不一致 %d 行" % (checked, diff))

    # ---- T5 用 LCG 帧复算已入库的运动量黄金参考 ----
    golden_mot = os.path.join(REPO, "fpga", "sim", "data_motion", "golden_motion.csv")
    if not os.path.exists(golden_mot):
        skip += 1
        lines.append("[skip] T5 没有入库的 golden_motion.csv（%s）" % golden_mot)
    else:
        # ⚠️ 两个生成器的 build_*_frames 都返回 [(名字, bytes), ...]，要取 [1]
        mframes = [f[1] for f in GM.build_rgb_frames(w, h, 20260910, 20260911)]
        old = (GM.DECIM_NUM, GM.DECIM_DEN)
        GM.DECIM_NUM, GM.DECIM_DEN = num, den
        try:
            mg = [GM.rgb_to_gray_decimated(f, w, h)[0] for f in mframes]
        finally:
            GM.DECIM_NUM, GM.DECIM_DEN = old
        want = {}
        with open(golden_mot, encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("#") or ln.startswith("frame,"):
                    continue
                p = ln.strip().split(",")
                if len(p) == 5:
                    want[int(p[0])] = tuple(int(x) for x in p[1:])
        n_g = want and (max(want) if want else 0) or 0
        nn = min(len(mg) - 1, max(want) if want else 0)
        diff = 0
        for i in range(1, nn + 1):
            got = GM.motion_of(mg[i], mg[i - 1], len(mg[i]), 16)
            if tuple(got) != want[i]:
                diff += 1
                if diff <= 3:
                    lines.append("        帧%d: got %s want %s" % (i, got, want[i]))
        good("T5 运动量黄金参考逐行复算（容差 0）", diff == 0 and nn >= 9,
             "比了 %d 行，不一致 %d 行" % (nn, diff))

    # ---- T6 全链路写盘：LCG 10 帧 → 向量树，逐项验字节/行数/可复现 ----
    lcg10 = [f[1] for f in GM.build_rgb_frames(w, h, 20260910, 20260911)][:10]
    out1 = os.path.join(REPO, "_cam_build", "_inject_selftest_a")
    out2 = os.path.join(REPO, "_cam_build", "_inject_selftest_b")
    try:
        r1 = write_vectors(lcg10, w, h, num, den, out1, thresh=16,
                           source_desc="selftest:lcg10", tier_name="640x480")
        r2 = write_vectors(lcg10, w, h, num, den, out2, thresh=16,
                           source_desc="selftest:lcg10", tier_name="640x480")
        good("T6.1 frames.bin 字节数 == 10 × RGB_BYTES",
             os.path.getsize(os.path.join(out1, "data", "frames.bin")) == 10 * R.RGB_BYTES,
             "%d 字节" % os.path.getsize(os.path.join(out1, "data", "frames.bin")))
        good("T6.2 gray.bin 字节数 == 10 × GRAY_PIXELS",
             os.path.getsize(os.path.join(out1, "data_motion", "gray.bin"))
             == 10 * R.GRAY_PIXELS)
        good("T6.3 frame0.rgb/frame0.gray 尺寸配套",
             os.path.getsize(os.path.join(out1, "frame0.rgb")) == R.RGB_BYTES
             and os.path.getsize(os.path.join(out1, "frame0.gray")) == R.GRAY_PIXELS)
        good("T6.4 golden_roi.csv 行数 = 9 用例 × 5 帧",
             r1["roi_rows"] == 45, "%d 行" % r1["roi_rows"])
        good("T6.5 golden_motion.csv 行数 = 帧 1..9",
             r1["motion_rows"] == 9, "%d 行" % r1["motion_rows"])
        # 与入库的 ROI 黄金参考比"数据行"（忽略第 3 行 meta —— 那里写了源文件名）
        # ⚠️ 必须用 **gen_frames 的那 5 帧**：入库的 data/frames.bin 就是它生成的
        #    （gen_motion_vectors 的 10 帧是另一套内容，帧 2 起就不同 —— 第一版自检
        #     就是拿 10 帧去比 5 帧的黄金参考，逐行全错，教训记在这）。
        def data_lines(p):
            with open(p, encoding="utf-8") as fh:
                return [ln.strip() for ln in fh
                        if not ln.startswith("#") and not ln.startswith("case,")]
        out_roi = os.path.join(REPO, "_cam_build", "_inject_selftest_roi")
        gf5 = [f[1] for f in GF.build_frames(w, h, 20260910, 20260911)]
        write_vectors(gf5, w, h, num, den, out_roi, thresh=16,
                      source_desc="selftest:gf5", tier_name="640x480")
        good("T6.6 生成的 ROI 数据行 == 入库黄金参考（除 meta 注释行外逐行相同）",
             data_lines(os.path.join(out_roi, "data", "golden_roi.csv"))
             == data_lines(os.path.join(REPO, "fpga", "sim", "data", "golden_roi.csv")))
        good("T6.7 生成的运动量数据行 == 入库黄金参考",
             data_lines(os.path.join(out1, "data_motion", "golden_motion.csv"))
             == data_lines(os.path.join(REPO, "fpga", "sim", "data_motion",
                                        "golden_motion.csv")))
        good("T6.8 同输入两次生成逐字节一致（确定性）",
             r1["frames_sha256"] == r2["frames_sha256"]
             and r1["gray_sha256"] == r2["gray_sha256"],
             r1["frames_sha256"][:16] + "…")
        good("T6.9 manifest 里记了 SHA256 与帧数",
             "frames_bin_sha256=%s" % r1["frames_sha256"] in
             open(r1["manifest"], encoding="utf-8").read())
    except SystemExit as exc:
        good("T6 全链路写盘", False, str(exc))
    except OSError as exc:
        skip += 1
        lines.append("[skip] T6 写盘被拒（沙箱只读？）：%s" % exc)

    # ---- T7 坏输入必须给人话（不是抛栈）----
    try:
        write_vectors([b"\x00" * 10], 640, 480, 3, 5,
                      os.path.join(REPO, "_cam_build", "_inject_selftest_c"))
        good("T7 帧长度不符必须报错", False, "竟然没报错")
    except SystemExit as exc:
        good("T7 帧长度不符给人话", "期望" in str(exc), str(exc))
    try:
        tier_of("不存在的档位")
        good("T7b 未知档位必须报错", False, "竟然没报错")
    except SystemExit as exc:
        good("T7b 未知档位给人话", "不在" in str(exc))

    print("== inject_video.py --selftest ==")
    for ln in lines:
        print("  " + ln)
    tail = "，跳过 %d" % skip if skip else ""
    print("RESULT: %s (%d/%d%s)" % ("PASS" if fail == 0 else "FAIL", ok, ok + fail, tail))
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------------------
# 4. CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    console_utf8()
    ap = argparse.ArgumentParser(
        description="把录好的影像转成板卡/PC 都能吃的测试向量（RGB888 帧序列 + 黄金参考）")
    ap.add_argument("--source", help="视频文件路径，或摄像头序号（如 0）")
    ap.add_argument("--out-dir", help="产物目录（默认 _cam_build/vec_<源名>）")
    ap.add_argument("--tier", default=getattr(R, "TIER", "640x480"),
                    help="档位：640x480（默认） / 720p60 / 1080p45（取值来自 board/regmap.py）")
    ap.add_argument("--frames", type=int, default=MOTION_FRAMES,
                    help="取多少帧（hw_sw_compare 需要 ≥10；默认 %d）" % MOTION_FRAMES)
    ap.add_argument("--start", type=int, default=0, help="从第几帧开始取（跳过前 N 帧）")
    ap.add_argument("--motion-thresh", type=int, default=None,
                    help="运动像素阈值（默认读 config.yaml 的 motion_thresh_gray）")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不需要板卡/cv2）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.source:
        ap.error("要么给 --source，要么 --selftest")

    w, h, num, den = tier_of(args.tier)
    thresh = args.motion_thresh
    src_note = ""
    if thresh is None:
        thresh, cfg = read_motion_thresh_from_config()
        src_note = ("config.yaml 的 motion_thresh_gray" if cfg
                    else "兜底默认值（没读到 config.yaml）")

    out_dir = args.out_dir
    if not out_dir:
        stem = os.path.splitext(os.path.basename(str(args.source)))[0] or "cam"
        out_dir = os.path.join(REPO, "_cam_build", "vec_" + stem)
    out_dir = os.path.abspath(out_dir)

    print("== inject_video.py ==")
    print("  源：%s" % args.source)
    print("  档位：%s ⇒ %dx%d，抽取 %d/%d" % (args.tier, w, h, num, den))
    print("  取帧：start=%d frames=%d；运动阈值=%d（%s）"
          % (args.start, args.frames, thresh, src_note or "--motion-thresh"))
    print("  产物目录：%s" % out_dir)

    rgb_frames = extract_video(args.source, w, h, args.start, args.frames)
    print("  取到 %d 帧" % len(rgb_frames))

    res = write_vectors(rgb_frames, w, h, num, den, out_dir,
                        thresh=thresh, source_desc=str(args.source), tier_name=args.tier)

    print("  写出：")
    print("    data/frames.bin + data_motion/rgb_frames.bin  %d 帧 × %d 字节"
          % (res["frames"], res["frame_bytes"]))
    print("    data_motion/gray.bin                         %d 帧 × %d 字节（%dx%d）"
          % (res["frames"], res["gray_bytes"], res["out_width"], res["out_height"]))
    print("    data/golden_roi.csv                          %d 行" % res["roi_rows"])
    print("    data_motion/golden_motion.csv                %d 行" % res["motion_rows"])
    print("    frame0.rgb / frame0.gray / manifest.txt")
    print("  下一步（板卡）：python board/hw_sw_compare.py --bit system.bit --data-root %s"
          % out_dir)
    print("  下一步（PC）  ：python backend/run_pipeline.py --source %s" % args.source)
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
