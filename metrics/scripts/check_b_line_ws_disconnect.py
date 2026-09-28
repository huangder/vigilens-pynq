"""check_b_line_ws_disconnect.py —— B 线 `/ws` 断流兜底的回归检查。

为什么需要它（这个 bug 是"静默"的，只有跑起来才看得见）：
    `api.py` 的 `/ws` 原来用 `q.get_nowait()` + `continue`：只要 `HUB.latest` 非空，
    数据一断就永远走不到兜底分支，**服务端什么也不发**。本页面看不出来（`app.js` 自带
    客户端看门狗），但任何第三方消费者都会静默等不到 —— 契约 §2 第 5 态
    （`disconnected`，归属"B 线兜底"）在服务端侧是缺的。
    来源：`backend/A_LINE_DEV_STEPS.md` §9 第 8 条（A 线 2026-09-16 交接项）。

检查项（5 条，全过才算通过）：
    1. 真帧能推得进来（推 1 帧，WS 上收得到）
    2. 数据停了以后，`ws_disconnect_timeout_s` 内**确实收到** `disconnected`（回归点）
    3. 这帧 `disconnected` **能过契约校验**（`validate_frame` 无错）——
       不是 `hub.DISCONNECT_HINT` 那种半截提示字典，第三方消费者也解析得了
    4. 它的 `ts` / `frame_id` **不倒退**（契约 §1 要求单调递增；沿用上一帧序号表达
       "自第 N 帧起没有新测量"，而不是伪造一个不存在的帧号）
    5. 持续断流时会**重复下发**（心跳），不会只发一次就再次静默

用法（仓库根，需要先起得来 fastapi/uvicorn；会自己起一个临时 api.py，用完就杀）：
    python metrics/scripts/check_b_line_ws_disconnect.py
    python metrics/scripts/check_b_line_ws_disconnect.py --port 8899 --verbose
退出码：0 = 5 项全过；1 = 有失败；2 = 服务没起来。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# Windows 控制台默认 GBK，打印中文/符号会崩 —— 工具脚本必须自己能打印
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from contract import validate_frame  # noqa: E402
from mock import mock_frame  # noqa: E402


def _post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as resp:  # noqa: S310 —— 本机回环，非用户输入
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _wait_server(base: str, timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if _get_json(base + "/api/status").get("ok") is True:
                return True
        except (URLError, OSError, ValueError):
            pass
        time.sleep(0.3)
    return False


async def _run_checks(port: int, verbose: bool) -> int:
    import websockets

    base = f"http://127.0.0.1:{port}"
    uri = f"ws://127.0.0.1:{port}/ws"
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "backend/api.py", "--no-mock", "--port", str(port)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    try:
        if not _wait_server(base):
            print(f"[FAIL] api.py 没起来（{base}）—— 先确认 fastapi/uvicorn 已安装")
            return 2

        # 超时值以 config.yaml 为准（不在脚本里另写一个魔法数字）
        timeout = 3.0
        try:
            import config as _cfg

            timeout = float(_cfg.load_config()["ws_disconnect_timeout_s"])
        except Exception:  # noqa: BLE001
            pass

        sent = mock_frame(7, status="normal", seed=20260910)
        print(f"服务已就绪 {base}（ws_disconnect_timeout_s = {timeout} s）")

        async with websockets.connect(uri) as ws:
            # —— 1) 真帧能推得进来 ——
            _post_json(base + "/api/ingest", {"frame": sent})
            got_real = None
            deadline = time.time() + 5
            while time.time() < deadline and got_real is None:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if msg.get("status") != "disconnected":
                    got_real = msg
            if got_real is None:
                failures.append("第 1 项：推了一帧但 WS 上没收到（真帧通道不通）")
            else:
                print(f"  1) 真帧到达：status={got_real['status']} frame_id={got_real['frame_id']}")

            # —— 2/3/4) 断流后必须收到一帧契约合法的 disconnected，且序号不倒退 ——
            disc = None
            deadline = time.time() + timeout + 3
            while time.time() < deadline:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout + 3))
                except asyncio.TimeoutError:
                    break
                if msg.get("status") == "disconnected":
                    disc = msg
                    break

            if disc is None:
                failures.append(
                    f"第 2 项：数据停掉 {timeout + 3:.0f} s 内没收到 disconnected"
                    "（就是那个静默 bug：HUB.latest 非空后再也走不到兜底分支）"
                )
            else:
                print(f"  2) 断流兜底帧到达：status=disconnected frame_id={disc.get('frame_id')}")
                errs = validate_frame(disc)
                if errs:
                    failures.append(f"第 3 项：兜底帧不是合法契约帧 —— {errs}")
                else:
                    print("  3) 兜底帧通过契约校验：validate_frame → []（第三方消费者也解析得了）")
                if got_real is not None:
                    back = []
                    if disc.get("frame_id", -1) < got_real.get("frame_id", 0):
                        back.append(f"frame_id {disc.get('frame_id')} < {got_real.get('frame_id')}")
                    try:
                        if float(disc.get("ts")) < float(got_real.get("ts")):
                            back.append(f"ts {disc.get('ts')} < {got_real.get('ts')}")
                    except (TypeError, ValueError):
                        back.append(f"ts 不是数字：{disc.get('ts')!r}")
                    if back:
                        failures.append("第 4 项：兜底帧序号倒退 —— " + "；".join(back))
                    else:
                        print("  4) 序号不倒退："
                              f"frame_id={disc.get('frame_id')}（沿用上一帧）、ts={disc.get('ts')}")

            # —— 5) 持续断流会重复下发（心跳），不会只发一次 ——
            again = None
            deadline = time.time() + timeout + 3
            while time.time() < deadline:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout + 3))
                except asyncio.TimeoutError:
                    break
                if msg.get("status") == "disconnected":
                    again = msg
                    break
            if again is None:
                failures.append("第 5 项：持续断流时没有重复下发（只发一次就又静默了）")
            else:
                print("  5) 持续断流仍在心跳：再次收到 disconnected")

        if verbose:
            print("原始兜底帧：", json.dumps(disc, ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if failures:
        print("[FAIL] B 线 /ws 断流兜底检查未通过：")
        for f in failures:
            print("   -", f)
        return 1
    print("B 线 /ws 断流兜底检查：通过 —— 5/5")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B 线 /ws 断流兜底的回归检查")
    ap.add_argument("--port", type=int, default=8899, help="临时服务端口（默认 8899，避开常驻的 8000）")
    ap.add_argument("--verbose", action="store_true", help="打印原始兜底帧")
    args = ap.parse_args(argv)
    return asyncio.run(_run_checks(args.port, args.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
