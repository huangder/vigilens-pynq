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

# 横幅要判断"合成帧源下管线会不会关掉 --push-video"，那条判定只有 backend/capture.py 有。
# 读配置与判定共用同一份实现，横幅才不会和子进程各说一套（docs/16 BUG-001 / BUG-002）。
_BACKEND = REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from capture import is_video_file_source, will_be_synthetic  # noqa: E402
from config import load_config         # noqa: E402


def loop_flag(source: str, loop: bool) -> list[str]:
    """`--loop` 要不要传给子进程（纯函数，便于断言）。

    为什么不再"每轮重启 run_pipeline"（`docs/16` BUG-009 成因 A）：
    重启是**新进程**，`--frame-id-offset` 只能让 `frame_id` 继续涨，
    而 `blink_count` / `yawn_count` / `long_close_count` 与 PERCLOS 滑窗都从 0 重来，
    CSV 还会被 `open("w")` 截断 ⇒ "frame_id 连续、计数归零、CSV 只剩最后一轮"三者互相矛盾。
    现在改成把 `--loop` 交给**同一个管线进程**：文件读到尾就 seek 回 0，
    frame_id / ts / 计数器 / CSV 全都在一条时间轴上。

    只有视频文件源支持；摄像头 / MJPEG / 合成帧源给 `[]`（调用方负责提示）。
    """
    if not loop:
        return []
    return ["--loop"] if is_video_file_source(source) else []


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


def video_plan(*, source: str, no_video: bool, cfg: dict,
               video_hz: float | None = None,
               video_quality: int | None = None,
               video_max_width: int | None = None,
               synthetic: bool | None = None) -> dict:
    """把"旁路画面到底推不推、按什么参数推"算成一份**可断言**的状态。

    为什么要有这个纯函数（docs/16）：
      · BUG-001：横幅原来只看 `--no-video`，而"合成帧源下管线会自动关掉 `--push-video`"
        的决定发生在**子进程内部**（`run_pipeline.py` 的 `synthetic_src` 分支）⇒ 横幅必然说谎。
        现在这里先按**同一个判定**（`capture.will_be_synthetic`）算出来，再决定文案与命令行。
      · BUG-002：默认值原来是写死的 `'8'` / `80` / `640`；现在一律取 `cfg`（= `config.yaml`），
        与 `run_pipeline.py` 的取值顺序一致，CLI 覆盖优先。

    `state`：`on`（会推）/ `off_flag`（`--no-video`）/ `off_synthetic`（合成帧源无像素）。
    """
    if synthetic is None:
        synthetic = will_be_synthetic(source)
    hz = float(cfg["video_push_hz"] if video_hz is None else video_hz)
    quality = int(cfg["video_jpeg_quality"] if video_quality is None else video_quality)
    max_width = int(cfg["video_max_width"] if video_max_width is None else video_max_width)
    if no_video:
        state = "off_flag"
    elif synthetic:
        state = "off_synthetic"
    else:
        state = "on"
    return {
        "state": state,
        "synthetic": bool(synthetic),
        "source": str(source),
        "hz": hz,
        "quality": quality,
        "max_width": max_width,
    }


def banner_video_lines(plan: dict, base: str) -> list[str]:
    """开跑前横幅里"旁路画面"那几行（只描述**已经确定的**事实）。"""
    page = base.rstrip("/") + "/"
    if plan["state"] == "off_flag":
        return ["旁路画面: 关闭（--no-video；网页显示占位网格）"]
    if plan["state"] == "off_synthetic":
        return [
            "旁路画面: 关闭（合成帧源没有真实像素，管线会自动跳过 --push-video；网页显示占位网格）",
            "          不是配置错误 —— 要看真实画面请用 --source <视频文件> 或 --source 0",
        ]
    return [
        f"旁路画面: {page}video.mjpg",
        f"          实际参数: {plan['hz']:g} Hz / JPEG q{plan['quality']} / "
        f"最长边 {plan['max_width']}（默认来自 config.yaml，--video-* 可覆盖）",
    ]


def footer_video_lines(plan: dict) -> list[str]:
    """收尾横幅里"画面"那一行（同样按实际状态说）。"""
    if plan["state"] == "off_flag":
        return ["  · 画面：已关闭（--no-video；网页显示占位网格）"]
    if plan["state"] == "off_synthetic":
        return ["  · 画面：已关闭（合成帧源没有真实像素；网页显示占位网格）"]
    return [
        "  · 画面：来自旁路 /video.mjpg；测量结束后会判定为'无画面'并自动隐藏，",
        "          这是刻意的 —— 不显示冻结的旧画面。",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="VigiLens 一键演示：采集 → 分析 → 网页")
    ap.add_argument("--source", default="synthetic",
                    help="视频文件路径 / 摄像头序号 / synthetic（默认，不需要任何硬件）")
    ap.add_argument("--list", action="store_true", help="只列出可用摄像头后退出")
    ap.add_argument("--port", type=int, default=None, help="服务端口（默认自动挑空闲端口）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--seconds", type=float, default=None, help="每轮只跑 N 秒（默认跑完整个源）")
    ap.add_argument("--loop", action="store_true",
                    help="视频文件读到尾就从头继续（交给同一个管线进程：frame_id / ts 单调递增，"
                         "计数器与 CSV **不重置**；摄像头 / 合成帧源不支持）")
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

    # 先把"画面状态"算清楚，再打印、再拼命令行 —— 三者必须来自同一个 plan（docs/16 BUG-001/002）。
    cfg = load_config()
    plan = video_plan(source=args.source, no_video=args.no_video, cfg=cfg,
                      video_hz=args.video_hz, video_quality=args.video_quality,
                      video_max_width=args.video_max_width)

    print("=" * 78)
    print("VigiLens 一键演示：图像采集 → 数据分析 → 网页显示")
    print("=" * 78)
    print(f"帧源    : {args.source}")
    print(f"网页    : {page}")
    for line in banner_video_lines(plan, base):
        print(line)
    print(flush=True)   # 横幅必须**立即**落盘：重定向到日志时子进程先写，否则横幅会被排到后面

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
    if plan["state"] == "on":
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

    # --loop 交给**同一个管线进程**（文件尾 seek 回 0），不再"跑完一轮重启一个进程"：
    # 重启会让计数器归零、CSV 被截断（docs/16 BUG-009 成因 A）。
    extra = loop_flag(args.source, args.loop)
    if args.loop and not extra:
        print(f"[warn] --loop 只对**视频文件**源生效（当前 --source {args.source}），已忽略。",
              file=sys.stderr)
    cmd_base += extra

    last_code = 0
    try:
        r = subprocess.run(cmd_base, cwd=str(REPO_ROOT))
        last_code = r.returncode
        if last_code != 0:
            print(f"[warn] 本轮退出码 {last_code}"
                  + ("（指标推送失败，见上面的 [FAIL]）" if last_code == 3 else ""),
                  file=sys.stderr)

        print("\n" + "=" * 78)
        print(f"测量结束。网页仍在 {page}")
        print("  · 数字/曲线/状态：来自契约帧（POST /api/ingest → WebSocket）")
        for line in footer_video_lines(plan):
            print(line)
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
