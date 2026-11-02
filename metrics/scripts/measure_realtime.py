"""measure_realtime.py —— 按 **docs/15 §3.5 的实时性三档口径** 实测一条正在跑的链路。

背景（台账 BUG-015）：
    "实时性"以前是个没定义的词 —— 有人嫌网页刷新慢，但那其实是**契约冻结的 1 帧/秒**，
    不是延迟。所以先把词拆成三档、每档给一个**能算出来的数**，再拿这个脚本去测：

      R1 采集 fps    —— 帧源/管线**产出帧**的节奏（逻辑时间戳差）
      R2 处理 fps    —— 管线**算得动**的吞吐（`run_pipeline` 的 `throughput_fps`，与节流无关）
      R3 展示 Hz     —— WebSocket **推给网页**的节奏 + 每帧从 `ts` 到浏览器可见的**延迟**

    ⚠️ 三档**不能互相替代**：
       `throughput 54 fps` 只说明 R2 有货；`ws_push_hz 1.0` 说的是 R3 的**更新周期**（1 s）；
        而 R3 的**延迟**（每帧 `ts` → 收到）可以是几十毫秒 —— "1 Hz" ≠ "1 秒延迟"。

怎么读（一条命令，需要先起着 api.py + run_pipeline.py）：
    # 终端 1
    .venv\\Scripts\\python.exe backend/api.py --no-mock --port 8033
    # 终端 2（--wall-clock 才能算延迟；否则 ts 是逻辑时间，脚本会标 SKIP）
    .venv\\Scripts\\python.exe backend/run_pipeline.py --source data/raw/blink.mp4 \
        --wall-clock --realtime --seconds 25 --push-video \
        --post http://127.0.0.1:8033/api/ingest --summary metrics/logs/_e_summary.json
    # 终端 3（在管线跑起来之后、结束之前）
    .venv\\Scripts\\python.exe metrics/scripts/measure_realtime.py \
        --base http://127.0.0.1:8033 --seconds 15 --summary metrics/logs/_e_summary.json

判据（阈值全部来自 `config.yaml` 的 `realtime_*`，代码里不写魔法数字 —— docs/15 G6）：
    R1  fps ≥ fps_nominal × realtime_capture_ratio_min；帧间隔 p95 ≤ realtime_jitter_ratio_max / fps
    R2  throughput_fps ≥ fps_nominal × realtime_process_ratio_min
    R3  |展示 Hz − ws_push_hz| ≤ ws_push_hz × realtime_display_hz_tol；
        推送间隔 p95 ≤ realtime_jitter_ratio_max / ws_push_hz；
        展示延迟 p95 ≤ realtime_display_lag_max_ms；
        /api/status 的「新鲜度」（server_time − 最新帧 ts）≤ 1/ws_push_hz + 同一上限
        —— 它天生落在 [0, 1/ws_push_hz]，是**抽样周期**不是延迟，别混为一谈

退出码：0 = 所有**可测**项通过（SKIP 不算通过也不算失败）；1 = 有 FAIL 或全部不可测。
`--selftest`：不联网，只验本脚本的统计/判定纯函数（秒级）。

产物：stdout 表格；`--json <路径>` 落一份机器可读结果（默认不落盘，避免脏工作区）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
# 脚本在 metrics/scripts/ 下，`backend` 与根目录的 `config.yaml` 都要能从仓库根解析
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# config.yaml 缺键时的兜底（新克隆/旧 config 也能跑；值必须与 docs/15 §3.5 一致）
DEFAULTS = {
    "fps_nominal": 30.0,
    "ws_push_hz": 1.0,
    "realtime_capture_ratio_min": 0.95,
    "realtime_jitter_ratio_max": 1.5,
    "realtime_process_ratio_min": 1.0,
    "realtime_display_hz_tol": 0.10,
    "realtime_display_lag_max_ms": 500.0,
}


# --------------------------------------------------------------------------- #
# 纯函数（--selftest 直接测这些）
# --------------------------------------------------------------------------- #
def pct(values: list[float], q: float) -> float | None:
    """线性插值分位数（q∈[0,1]）；空列表 → None。"""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return float(xs[lo] * (1 - frac) + xs[hi] * frac)


def stats(values: list[float]) -> dict:
    """p50 / p95 / max / n，全 ms 或全 Hz（调用方自己保证单位）。"""
    return {
        "n": len(values),
        "p50": pct(values, 0.50),
        "p95": pct(values, 0.95),
        "max": max(values) if values else None,
        "min": min(values) if values else None,
    }


def is_wall_clock(ts: float, now: float, tolerance_s: float = 3600.0) -> bool:
    """`ts` 与当下差在 ±1 h 内 → 视为墙上时钟；否则是 `frame_id/fps` 逻辑时间。"""
    return abs(now - ts) <= tolerance_s


def logical_fps(frames: list[dict]) -> float | None:
    """用 (frame_id 差) / (ts 差) 反推**帧源产出**速率；不足两帧或 ts 差为 0 → None。"""
    if len(frames) < 2:
        return None
    f0, f1 = frames[0], frames[-1]
    d_ts = float(f1["ts"]) - float(f0["ts"])
    d_id = float(f1["frame_id"]) - float(f0["frame_id"])
    if d_ts <= 0 or d_id <= 0:
        return None
    return d_id / d_ts


def frame_gaps_ms(frames: list[dict]) -> list[float]:
    """**单帧**产出周期（ms）：相邻两次收到的 `ts` 差 ÷ `frame_id` 差。

    为什么要除 —— 网页收到的只是 1 Hz 的**抽样帧**，一次推送跨了 `fps/ws_push_hz` 帧；
    直接把 ts 差当帧间隔会得到 1000 ms 的假数（第一次实测就踩了这个坑）。
    """
    out = []
    for a, b in zip(frames, frames[1:]):
        d_ts = (float(b["ts"]) - float(a["ts"])) * 1000.0
        d_id = int(b["frame_id"]) - int(a["frame_id"])
        if d_ts > 0 and d_id > 0:
            out.append(d_ts / d_id)
    return out


def arrival_gaps_ms(samples: list[tuple[float, dict]]) -> list[float]:
    """相邻帧的**到达**间隔（ms）—— 反映 WebSocket 推送节奏（R3）。"""
    return [(b[0] - a[0]) * 1000.0 for a, b in zip(samples, samples[1:])]


def id_steps(samples: list[tuple[float, dict]]) -> list[int]:
    """相邻两次收到的 `frame_id` 差 —— 检测重复(=0)/跳帧(远大于 fps/ws_push_hz)。"""
    return [int(b[1]["frame_id"]) - int(a[1]["frame_id"]) for a, b in zip(samples, samples[1:])]


def verdict(name: str, value: float | None, ok: bool | None, criterion: str) -> dict:
    """ok=None → SKIP（不可测，不算通过也不算失败）。"""
    return {
        "item": name,
        "value": None if value is None else round(float(value), 3),
        "criterion": criterion,
        "status": "SKIP" if ok is None else ("PASS" if ok else "FAIL"),
    }


def judge_r1(fps: float | None, gaps: list[float], cfg: dict) -> list[dict]:
    nominal = float(cfg["fps_nominal"])
    cap_min = float(cfg["realtime_capture_ratio_min"])
    jitter_max = float(cfg["realtime_jitter_ratio_max"])
    g95 = pct(gaps, 0.95)
    return [
        verdict(
            "R1 采集/产出 fps",
            fps,
            None if fps is None else fps >= nominal * cap_min,
            f"≥ {nominal * cap_min:.2f} fps（{nominal:.0f}×{cap_min:.2f}）",
        ),
        verdict(
            "R1 帧间隔 p95",
            g95,
            None if g95 is None else g95 <= jitter_max * 1000.0 / nominal,
            f"≤ {jitter_max * 1000.0 / nominal:.0f} ms（{jitter_max:.1f}×{1000.0 / nominal:.1f} ms）",
        ),
    ]


def judge_r2(throughput: float | None, cfg: dict) -> list[dict]:
    nominal = float(cfg["fps_nominal"])
    ratio = float(cfg["realtime_process_ratio_min"])
    return [
        verdict(
            "R2 处理吞吐",
            throughput,
            None if throughput is None else throughput >= nominal * ratio,
            f"≥ {nominal * ratio:.1f} fps（吞吐，与节流无关）",
        )
    ]


def judge_r3(
    display_hz: float | None,
    arrival_p95_ms: float | None,
    lag_stats: dict,
    status_lag_stats: dict,
    steps: list[int],
    cfg: dict,
) -> list[dict]:
    push_hz = float(cfg["ws_push_hz"])
    tol = float(cfg["realtime_display_hz_tol"])
    jitter_max = float(cfg["realtime_jitter_ratio_max"])
    lag_max = float(cfg["realtime_display_lag_max_ms"])
    expected_step = float(cfg["fps_nominal"]) / push_hz
    rows = [
        verdict(
            "R3 展示 Hz",
            display_hz,
            None if display_hz is None else abs(display_hz - push_hz) <= push_hz * tol,
            f"{push_hz:.2f} ± {push_hz * tol:.2f} Hz（契约 ws_push_hz）",
        ),
        verdict(
            "R3 推送间隔 p95",
            arrival_p95_ms,
            None if arrival_p95_ms is None else arrival_p95_ms <= jitter_max * 1000.0 / push_hz,
            f"≤ {jitter_max * 1000.0 / push_hz:.0f} ms（{jitter_max:.1f}×{1000.0 / push_hz:.0f} ms）",
        ),
        verdict(
            "R3 展示延迟 p95",
            lag_stats.get("p95"),
            None if lag_stats.get("p95") is None else lag_stats["p95"] <= lag_max,
            f"≤ {lag_max:.0f} ms（帧 ts → 本机收到）",
        ),
        verdict(
            "R3 status 新鲜度 p95",
            status_lag_stats.get("p95"),
            None
            if status_lag_stats.get("p95") is None
            else status_lag_stats["p95"] <= 1000.0 / push_hz + lag_max,
            f"≤ {1000.0 / push_hz + lag_max:.0f} ms（1 个推送周期 {1000.0 / push_hz:.0f} ms + {lag_max:.0f}）",
        ),
    ]
    dup = sum(1 for s in steps if s == 0)
    skip = sum(1 for s in steps if s > expected_step * 3)
    rows.append(
        verdict(
            "R3 重复帧次数",
            float(dup),
            None if not steps else dup == 0,
            "= 0（同一帧不该推两次）",
        )
    )
    rows.append(
        verdict(
            "R3 疑似跳帧次数",
            float(skip),
            None if not steps else skip == 0,
            f"= 0（单步 > {expected_step * 3:.0f} 帧视为漏推）",
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# 采集：WS 帧 + /api/status 轮询
# --------------------------------------------------------------------------- #
def http_get_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def ws_collect(base: str, seconds: float, poll_every: float = 0.5) -> tuple[list, list, str | None]:
    """连 WS 收帧，期间每 `poll_every` 秒抓一次 /api/status。返回 (帧样本, status 样本, 错误)。"""
    try:
        from websockets.sync.client import connect  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return [], [], f"缺少 websockets 库（{exc}）；pip install websockets"

    ws_url = base.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/ws"
    samples: list[tuple[float, dict]] = []
    statuses: list[dict] = []
    last_poll = 0.0
    try:
        with connect(ws_url, open_timeout=5.0, max_size=4 * 1024 * 1024) as ws:
            t0 = time.time()
            while time.time() - t0 < seconds:
                try:
                    raw = ws.recv(timeout=0.1)
                except TimeoutError:
                    raw = None
                except Exception:  # noqa: BLE001  连接被对端关闭
                    break
                if raw is not None:
                    try:
                        frame = json.loads(raw)
                    except ValueError:
                        frame = None
                    if isinstance(frame, dict) and "frame_id" in frame:
                        samples.append((time.time(), frame))
                now = time.time()
                if now - last_poll >= poll_every:
                    last_poll = now
                    st = http_get_json(base.rstrip("/") + "/api/status")
                    if st is not None:
                        statuses.append(st)
    except Exception as exc:  # noqa: BLE001
        return samples, statuses, f"连不上 {ws_url}：{exc}"
    return samples, statuses, None


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按 docs/15 §3.5 三档口径实测实时性")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="api.py 的地址")
    ap.add_argument("--seconds", type=float, default=15.0, help="采集窗口（秒），建议 ≥ 3/ws_push_hz + 5")
    ap.add_argument("--summary", default="", help="run_pipeline 的 --summary JSON（取 R2 吞吐）")
    ap.add_argument("--json", default="", help="把结果写成 JSON（默认只打屏）")
    ap.add_argument("--selftest", action="store_true", help="只跑纯函数自检（不联网）")
    args = ap.parse_args(argv)

    from backend.config import load_config  # 延迟导入：--selftest 也走同一份阈值来源

    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in load_config().items() if k in DEFAULTS})

    if args.selftest:
        return selftest()

    if args.seconds < 2.0:
        print("--seconds 至少 2 秒（否则算不出间隔）")
        return 2

    print(f"窗口 {args.seconds:.0f} s  |  契约: fps_nominal={cfg['fps_nominal']:g}  ws_push_hz={cfg['ws_push_hz']:g}")
    samples, statuses, err = ws_collect(args.base, args.seconds)
    if err:
        print(f"❌ {err}")
        return 2
    if len(samples) < 2:
        print(f"❌ 窗口内只收到 {len(samples)} 帧 —— api.py 没在广播？先跑 run_pipeline --post")
        return 2

    frames = [s[1] for s in samples]
    now = time.time()
    wall = is_wall_clock(float(frames[0]["ts"]), now)
    print(f"时间基: {'墙上时钟（可算延迟）' if wall else '逻辑时间 frame_id/fps（延迟不可测 → SKIP）'}"
          f"  |  收到 {len(frames)} 帧  subscribers? {statuses[-1].get('subscribers') if statuses else '-'}")

    # R1：产出速率（ts 差）+ 帧间隔抖动
    fps = logical_fps(frames) if wall else None
    r1 = judge_r1(fps, frame_gaps_ms(frames), cfg)

    # R2：处理吞吐（只认 summary）
    throughput = None
    if args.summary:
        try:
            s = json.loads(Path(args.summary).read_text(encoding="utf-8"))
            throughput = s.get("throughput_fps")
        except (OSError, ValueError) as exc:
            print(f"⚠️ 读 summary 失败（R2 记 SKIP）：{exc}")
    r2 = judge_r2(throughput, cfg)

    # R3：到达节奏 + 延迟
    span = samples[-1][0] - samples[0][0]
    display_hz = (len(samples) - 1) / span if span > 0 else None
    agaps = arrival_gaps_ms(samples)
    lag = [(recv - float(f["ts"])) * 1000.0 for recv, f in samples] if wall else []
    st_lag = []
    for st in statuses:
        fr = st.get("frame") or {}
        if wall and isinstance(fr.get("ts"), (int, float)):
            st_lag.append((float(st["server_time"]) - float(fr["ts"])) * 1000.0)
    r3 = judge_r3(display_hz, pct(agaps, 0.95), stats(lag), stats(st_lag), id_steps(samples), cfg)

    rows = r1 + r2 + r3
    print()
    print(f"{'项':<26}{'实测':>12}  {'判据':<44}结论")
    for r in rows:
        val = "-" if r["value"] is None else f"{r['value']:.2f}"
        print(f"{r['item']:<26}{val:>12}  {r['criterion']:<44}{r['status']}")
    print()
    if agaps:
        a = stats(agaps)
        print(f"推送间隔 ms: n={a['n']} p50={a['p50']:.0f} p95={a['p95']:.0f} max={a['max']:.0f}")
    if lag:
        l = stats(lag)
        print(f"展示延迟 ms: n={l['n']} p50={l['p50']:.1f} p95={l['p95']:.1f} max={l['max']:.1f}")
    if st_lag:
        sl = stats(st_lag)
        print(f"status 新鲜度（最新 frame.ts 年龄）ms: n={sl['n']} p50={sl['p50']:.1f} p95={sl['p95']:.1f}")
    steps = id_steps(samples)
    if steps:
        print(f"frame_id 步长: p50={pct([float(x) for x in steps], 0.5):.0f} "
              f"max={max(steps)} 重复={sum(1 for x in steps if x == 0)}")

    fails = [r for r in rows if r["status"] == "FAIL"]
    skips = [r for r in rows if r["status"] == "SKIP"]
    passed = [r for r in rows if r["status"] == "PASS"]
    print(f"\n结论: PASS {len(passed)} / FAIL {len(fails)} / SKIP {len(skips)}"
          + ("  ⚠️ SKIP 项见上（多半是 --wall-clock 或 --summary 没给）" if skips else ""))

    if args.json:
        out = {
            "base": args.base,
            "seconds": args.seconds,
            "received_frames": len(frames),
            "wall_clock": wall,
            "config": cfg,
            "rows": rows,
            "arrival_ms": stats(agaps),
            "lag_ms": stats(lag),
            "status_lag_ms": stats(st_lag),
        }
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写 {args.json}")

    return 0 if not fails else 1


def selftest() -> int:
    cfg = dict(DEFAULTS)
    checks: list[tuple[str, bool]] = []
    checks.append(("pct 线性插值", pct([0, 10], 0.5) == 5.0))
    checks.append(("pct 单值", pct([7], 0.95) == 7.0))
    checks.append(("pct 空列表", pct([], 0.5) is None))
    checks.append(("stats p95", abs((stats([1, 2, 3, 4])["p95"] or 0) - 3.85) < 1e-9))
    checks.append(("墙上时钟判定", is_wall_clock(time.time() - 1, time.time())))
    checks.append(("逻辑时间判定", not is_wall_clock(3, time.time())))
    fr = [{"frame_id": 10, "ts": 100.0}, {"frame_id": 40, "ts": 101.0}]
    checks.append(("产出 fps = Δid/Δts", abs((logical_fps(fr) or 0) - 30.0) < 1e-9))
    checks.append(("单帧周期 = Δts/Δid", abs((frame_gaps_ms(fr)[0] if frame_gaps_ms(fr) else 0) - 1000.0 / 30) < 1e-9))
    checks.append(("重复 id 的周期被跳过", frame_gaps_ms([{"frame_id": 1, "ts": 1.0}, {"frame_id": 1, "ts": 2.0}]) == []))
    samples = [(0.0, {"frame_id": 1}), (0.98, {"frame_id": 31}), (1.99, {"frame_id": 61})]
    checks.append(("到达间隔 ms", [round(x) for x in arrival_gaps_ms(samples)] == [980, 1010]))
    checks.append(("步长", id_steps(samples) == [30, 30]))

    r1 = judge_r1(30.0, [33.0] * 10, cfg)
    checks.append(("R1 30fps 通过", r1[0]["status"] == "PASS"))
    checks.append(("R1 28fps 失败", judge_r1(28.0, [33.0], cfg)[0]["status"] == "FAIL"))
    checks.append(("R1 不可测→SKIP", judge_r1(None, [], cfg)[0]["status"] == "SKIP"))
    checks.append(("R2 30fps 通过", judge_r2(30.0, cfg)[0]["status"] == "PASS"))
    checks.append(("R2 29fps 失败", judge_r2(29.9, cfg)[0]["status"] == "FAIL"))

    good = judge_r3(1.0, 1000.0, {"p95": 20.0}, {"p95": 30.0}, [30] * 10, cfg)
    checks.append(("R3 正常 6/6 PASS", all(r["status"] == "PASS" for r in good)))
    dup = judge_r3(1.0, 1000.0, {"p95": 20.0}, {"p95": 30.0}, [0, 30], cfg)
    checks.append(("R3 重复帧被抓", dup[-2]["status"] == "FAIL"))
    jump = judge_r3(1.0, 1000.0, {"p95": 20.0}, {"p95": 30.0}, [120], cfg)
    checks.append(("R3 跳帧被抓", jump[-1]["status"] == "FAIL"))
    slow = judge_r3(0.5, 2000.0, {"p95": 900.0}, {"p95": 10.0}, [30], cfg)
    checks.append(("R3 Hz/间隔/延迟 都判失败",
                   [r["status"] for r in slow[:3]] == ["FAIL", "FAIL", "FAIL"]))
    stale = judge_r3(1.0, 1000.0, {"p95": 20.0}, {"p95": 1900.0}, [30], cfg)
    checks.append(("R3 status 过期被抓（>1 周期+余量）", stale[3]["status"] == "FAIL"))

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'OK ' if ok else 'BAD'}  {n}")
    print(f"\n工具自检: {len(checks) - len(bad)}/{len(checks)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
