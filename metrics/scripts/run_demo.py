"""run_demo.py —— 一条命令跑通「图像采集 → 数据分析 → 网页显示」。

项目：知倦 / VigiLens

它做的事（三件基本功能的完整闭环）：
    1. 起 B 线服务（`backend/api.py --no-mock`，同时托管 frontend/）
    2. 起 A 线管线（`backend/run_pipeline.py --post auto --push-video`）
         · 指标帧走契约通道  POST /api/ingest  → WebSocket → 网页数字/曲线/状态
         · 画面走旁路通道    POST /api/frame   → GET /video.mjpg → 网页真实画面
    3. 打开浏览器，并**保持服务运行**（Ctrl-C 退出）

为什么要有它：三个环节单独都有入口，但要人肉记住"先起谁、端口多少、两个地址别配错"。
而配错的典型症状是"画面没了但指标正常"，很难查。一条命令把地址从 `--post` 推导出来，
就不会配错（见 `publish.frame_url_from_ingest`）。

用法::

    # 合成帧源：不需要摄像头、不需要 OpenCV，先确认链路（画面会是占位）
    .venv\\Scripts\\python.exe metrics/scripts/run_demo.py

    # 真实摄像头（OpenMV 刷 uvc.bin 后就是标准 UVC 摄像头，用 --list 找序号）
    .venv\\Scripts\\python.exe metrics/scripts/run_demo.py --source 0
    .venv\\Scripts\\python.exe metrics/scripts/run_demo.py --list

    # 视频文件 + 循环，做长时间演示
    .venv\\Scripts\\python.exe metrics/scripts/run_demo.py --source data/raw/blink.mp4 --loop

    # 脱板演示：让网页里的画面更流畅/更大（不改 config.yaml —— 那是三人共用文件）
    .venv\\Scripts\\python.exe metrics/scripts/run_demo.py --source 0 --video-hz 25 --video-max-width 640

退出码：0 = 正常；2 = 参数/前置问题；3 = 指标推送到 B 线失败（与 run_pipeline 一致）。
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS = REPO_ROOT / "metrics" / "logs"


def resolve_python() -> str:
    venv_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv_py) if venv_py.exists() else sys.executable


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 —— 本机回环
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def wait_ready(base: str, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if http_json(f"{base}/api/status", timeout=2.0) is not None:
            return True
        time.sleep(0.3)
    return False


def list_cameras(max_index: int = 8) -> int:
    try:
        import cv2  # type: ignore
    except Exception:  # noqa: BLE001
        print("[FAIL] 需要 opencv-python 才能枚举摄像头：pip install -r requirements.txt",
              file=sys.stderr)
        return 2
    print("枚举摄像头（每个只读 1 帧；打不开的会跳过）...")
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                found.append((i, w, h))
        cap.release()
    if not found:
        print("  没找到任何可用摄像头。")
        print("  · OpenMV 走 USB-VCP 模式时**不会**出现——需先用 OpenMV IDE 刷 uvc.bin 固件；")
        print("  · 或者用视频文件：--source data/raw/xxx.mp4")
        return 0
    for i, w, h in found:
        print(f"  index {i} → 首帧 {w}x{h}")
    print(f"\n用法：--source {found[-1][0]}   （笔记本自带摄像头通常是 0）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="VigiLens 一键演示：采集 → 分析 → 网页")
    ap.add_argument("--source", default="synthetic",
                    help="视频文件路径 / 摄像头序号 / synthetic（默认，不需要任何硬件）")
    ap.add_argument("--list", action="store_true", help="只列出可用摄像头后退出")
    ap.add_argument("--port", type=int, default=None, help="服务端口（默认自动挑空闲端口）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--seconds", type=float, default=None, help="每轮只跑 N 秒（默认跑完整个源）")
    ap.add_argument("--loop", action="store_true",
                    help="跑完一轮再从头跑（用 --frame-id-offset 保证 frame_id 单调递增）")
    ap.add_argument("--no-video", action="store_true",
                    help="不推旁路画面（网页显示占位网格）——用来对照'画面没了但指标正常'")
    # 下面三个是 run_pipeline 的旁路画面参数，这里做**透传**：
    # 为什么需要：默认值来自 config.yaml（8 Hz / q80 / 最长边 640），而 config.yaml 是**三人共用文件**
    # （AGENTS.md §5），只为让演示画面流畅就去改它并不合适。用自己的开关临时覆盖即可。
    ap.add_argument("--video-hz", type=float, default=None,
                    help="旁路画面推送节奏（默认取 config.yaml 的 video_push_hz = 8）")
    ap.add_argument("--video-quality", type=int, default=None,
                    help="旁路 JPEG 质量 1..100（默认取 config.yaml 的 video_jpeg_quality = 80）")
    ap.add_argument("--video-max-width", type=int, default=None,
                    help="旁路画面最长边（等比缩放；默认取 config.yaml 的 video_max_width = 640）")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--exit-when-done", action="store_true",
                    help="测量结束后立即退出（不保持服务运行）。给自动化测试/CI 用 —— "
                         "默认会一直挂着等服务被 Ctrl-C，那是给人看的")
    ap.add_argument("--pattern", default="blink", choices=["blink", "yawn", "still", "turn"],
                    help="合成帧源的行为模式（真实视频/摄像头下被忽略）")
    ap.add_argument("--print-every", type=int, default=30, help="每 N 帧打印一行进度（0 = 不打印）")
    ap.add_argument("--json", dest="json_out", default=str(LOGS / "demo_last.json"))
    ap.add_argument("--csv", default=str(REPO_ROOT / "metrics" / "csv" / "demo_metrics.csv"))
    args = ap.parse_args()

    if args.list:
        return list_cameras()

    py = resolve_python()
    port = args.port or free_port()
    base = f"http://{args.host}:{port}"
    page = f"{base}/"

    print("=" * 78)
    print("VigiLens 一键演示：图像采集 → 数据分析 → 网页显示")
    print("=" * 78)
    print(f"帧源    : {args.source}")
    print(f"网页    : {page}")
    print(f"旁路画面: {'关闭（只显示占位网格）' if args.no_video else page + 'video.mjpg'}")
    if not args.no_video:
        print(f"          （节奏/质量/最长边未指定的取 config.yaml："
              f"{args.video_hz or '8'} Hz / q{args.video_quality or 80} / "
              f"最长边 {args.video_max_width or 640}）")
    print()

    LOGS.mkdir(parents=True, exist_ok=True)

    # ---- 1) 起 B 线服务 -------------------------------------------------------
    server = subprocess.Popen(
        [py, "backend/api.py", "--no-mock", "--host", args.host, "--port", str(port)],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    if not wait_ready(base):
        server.terminate()
        print("[FAIL] 后端服务没起来。常见原因：缺 fastapi/uvicorn（pip install -r requirements.txt）",
              file=sys.stderr)
        return 2
    print(f"[1/3] 服务已就绪  {base}")

    if not args.no_open:
        try:
            webbrowser.open(page)
            print("[2/3] 已尝试打开浏览器（--no-open 可关掉）")
        except Exception:  # noqa: BLE001
            print("[2/3] 打开浏览器失败，请手动访问上面的网址")
    else:
        print("[2/3] 跳过打开浏览器")

    cmd_base = [py, "backend/run_pipeline.py", "--source", str(args.source),
                "--pattern", args.pattern, "--post", "auto",
                "--json", args.json_out, "--csv", args.csv,
                "--print-every", str(args.print_every)]
    if args.seconds is not None:
        cmd_base += ["--seconds", str(args.seconds)]
    if not args.no_video:
        cmd_base += ["--push-video"]
        if args.video_hz is not None:
            cmd_base += ["--video-hz", str(args.video_hz)]
        if args.video_quality is not None:
            cmd_base += ["--video-quality", str(args.video_quality)]
        if args.video_max_width is not None:
            cmd_base += ["--video-max-width", str(args.video_max_width)]

    print("[3/3] 开始测量（Ctrl-C 可中断；服务会保持运行到退出）\n")
    print("[提示] 每条命令都带 --post auto 与固定端口，所以请把上面的网页地址当作唯一入口；")
    print("       如果你改了 --port，网页里的 WebSocket 地址也要改成 ws://...:%d/ws" % port)
    print()

    # run_pipeline 的 `--post auto` 固定指向 8000；本脚本随机挑端口，所以显式给地址。
    cmd_base[cmd_base.index("auto")] = f"{base}/api/ingest"

    offset = 0
    total_rounds = 0
    last_code = 0
    try:
        while True:
            total_rounds += 1
            cmd = list(cmd_base)
            if offset:
                cmd += ["--frame-id-offset", str(offset)]
            if total_rounds > 1:
                print(f"\n===== 第 {total_rounds} 轮（frame_id 从 {offset} 起）=====\n")
            r = subprocess.run(cmd, cwd=str(REPO_ROOT))
            last_code = r.returncode
            if last_code not in (0,):
                print(f"[warn] 本轮退出码 {last_code}"
                      + ("（指标推送失败，见上面的 [FAIL]）" if last_code == 3 else ""),
                      file=sys.stderr)
            if not args.loop:
                break
            # 契约要求 frame_id / ts 跨段单调递增；不偏移就会回退（AGENTS.md §6.2.1）
            s = Path(args.json_out)
            frames = 0
            if s.exists():
                try:
                    frames = int(json.loads(s.read_text(encoding="utf-8")).get("frame_id", 0)) + 1
                except Exception:  # noqa: BLE001
                    frames = 0
            offset = max(offset + 1, frames)
            print(f"\n[loop] 下一轮 frame_id 偏移 = {offset}（保证单调递增）")
            time.sleep(0.5)

        print("\n" + "=" * 78)
        print(f"测量结束。网页仍在 {page}")
        print("  · 数字/曲线/状态：来自契约帧（POST /api/ingest → WebSocket）")
        print("  · 画面：" + ("已关闭（--no-video）" if args.no_video else
                              "来自旁路 /video.mjpg；测量结束后会判定为'无画面'并自动隐藏，"))
        if not args.no_video:
            print("          这是刻意的 —— 不显示冻结的旧画面。")
        if args.exit_when_done:
            print("  · --exit-when-done：不保持服务，直接退出。")
        else:
            print("  · 服务保持运行，按 Ctrl-C 退出。")
        print("=" * 78)
        if args.exit_when_done:
            return last_code
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[退出] 收到 Ctrl-C，正在关闭服务 ...")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    return last_code


if __name__ == "__main__":
    raise SystemExit(main())
