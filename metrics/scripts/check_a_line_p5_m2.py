"""check_a_line_p5_m2.py —— P5 / M2 端到端检查：**A 线真实数据 → B 线服务 → 网页**。

为什么必须真的起两个进程：
    P5 的判据是"浏览器里看到的是 A 线跑出来的真实数字"。如果在同一个进程里
    import 一下、往 HUB 里塞个 dict，脚本也会绿 —— 但它根本没碰 M2 真正的那道缝：
    **跨进程 HTTP**，以及**两套契约实现**（A 的 `contract.py` 校验 vs B 的
    `/api/ingest` 里那份）。真实拓扑是：

        run_pipeline.py（进程 1，测量）--POST /api/ingest--> api.py（进程 2，服务）--WS--> 浏览器

    所以本脚本按真实拓扑跑：子进程 uvicorn（`api.py --no-mock`）+ 子进程管线
    （`run_pipeline.py --post`），然后**从 B 线那边回读**数字，与 A 线自己的流逐字段比对。

检查项（每项独立报 [PASS]/[FAIL]）：
    [1] B 线服务起得来（`--no-mock`，数据源确实是 ingest 而不是 mock）
    [2] 没人推数据时是 `disconnected` —— 第 5 种状态由 B 线兜底，不是 A 线伪造的
    [3] 4 段合成回放（blink/yawn/still/turn）经 `--post` 真实推给 B 线
    [4] B 线的 `/api/status`、`/api/metrics` 的数字与 A 线流里的帧**逐字段相同**
    [5] 六种 status 全部出现过（含 done 与 disconnected）
    [6] 坏帧被 `/api/ingest` 以 422 挡下，且不污染历史（契约门有效）
    [7] `/ws` 真推：连上 WebSocket 后推一帧，能原样收到（浏览器走的就是这条路）
    [8] `/` 托管的仪表盘页面可访问（同源，不用 file:// 也不用填地址）

用法（仓库根）：
    . .\\env.ps1
    python metrics/scripts/check_a_line_p5_m2.py
    python metrics/scripts/check_a_line_p5_m2.py --evidence    # 额外归档到 metrics/evidence/

退出码：0 = 全部通过；1 = 有失败项。缺 fastapi/uvicorn 时 [1] 直接 FAIL（这不是环境小问题，
因为 B 线服务起不来 M2 就是没接上）；缺 websockets 时只有 [7] 标 SKIP。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.contract import STATUS_VALUES  # noqa: E402

LOGS = REPO_ROOT / "metrics" / "logs"
EVIDENCE = REPO_ROOT / "metrics" / "evidence"
PATTERNS = ("blink", "yawn", "still", "turn")
SECONDS = 30            # 每段 30 秒：turn 的 adjust_posture 要 3~6 秒才出现，短了覆盖不全


def resolve_python() -> str:
    """解释器优先用项目 `.venv\\Scripts\\python.exe`（理由见 check_a_line_all.py）。"""
    venv_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv_py) if venv_py.exists() else sys.executable


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_json(url: str, *, payload: dict | None = None, timeout: float = 5.0) -> tuple[int, dict | str]:
    """GET（payload=None）或 POST 一个 JSON，返回 (状态码, 解析后的 body 或原文)。"""
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 本机回环地址
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), (_parse(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return int(e.code), _parse(body)
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def _parse(body: str):
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str) -> bool:
        self.results.append((name, ok, detail))
        print(f"    {'[PASS]' if ok else '[FAIL]'} {name} —— {detail}")
        return ok

    def skip(self, name: str, detail: str) -> None:
        self.results.append((name, True, f"SKIP：{detail}"))
        print(f"    [SKIP] {name} —— {detail}")


def wait_ready(base: str, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        code, body = http_json(f"{base}/api/status", timeout=2.0)
        if code == 200 and isinstance(body, dict):
            return True
        time.sleep(0.4)
    return False


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    py = resolve_python()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    ingest_url = f"{base}/api/ingest"
    ck = Checks()
    t0 = time.time()
    report: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "port": port, "steps": []}

    print("=== P5 / M2 端到端检查：A 线真实数据 → B 线服务 → 网页 ===")
    print(f"时间：{report['started_at']}   Python：{sys.version.split()[0]}")
    print(f"拓扑：run_pipeline.py --post → {ingest_url} → uvicorn(api.py --no-mock) → /ws → 浏览器")
    print()

    api_log = (LOGS / "_p5_api.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([py, "backend/api.py", "--no-mock", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=str(REPO_ROOT),
                            stdout=api_log, stderr=subprocess.STDOUT)
    try:
        # ---------- [1] B 线服务 ----------
        print("[1] B 线服务（api.py --no-mock）")
        if not wait_ready(base):
            ck.add("服务就绪", False, f"{base} 30 秒内没起来（日志：{api_log.name}）")
            return _finish(ck, report, t0)
        code, body = http_json(f"{base}/api/status")
        src = body.get("source") if isinstance(body, dict) else None
        ck.add("服务就绪", True, f"{base} 已监听")
        ck.add("数据源=ingest（不是 B 线 mock）", src == "ingest", f"/api/status.source = {src!r}")

        # ---------- [2] 无人推数据时的兜底 ----------
        print()
        print("[2] 兜底帧：没人推数据时 B 线必须自己说 disconnected")
        code, status_body = http_json(f"{base}/api/status")
        idle_status = (status_body.get("frame") or {}).get("status") if isinstance(status_body, dict) else None
        idle_published = status_body.get("published") if isinstance(status_body, dict) else None
        ck.add("空闲时 status=disconnected", idle_status == "disconnected",
               f"published={idle_published}, frame.status={idle_status!r}")

        # ---------- [3] A 线真实推送 ----------
        print()
        print(f"[3] A 线端到端推送（{'/'.join(PATTERNS)}，每段 {SECONDS}s）")
        runs: list[dict] = []
        for i, pat in enumerate(PATTERNS):
            stream = LOGS / f"_p5_{pat}.jsonl"
            summary = LOGS / f"_p5_{pat}_summary.json"
            r = subprocess.run(
                [py, "backend/run_pipeline.py", "--source", "synthetic", "--pattern", pat,
                 "--seconds", str(SECONDS), "--stub", "--quiet",
                 "--json", str(LOGS / f"_p5_{pat}_last.json"), "--jsonl", str(stream),
                 "--summary", str(summary), "--post", ingest_url,
                 # 连跑四段：后一段必须接着前一段的帧号/时间轴，否则 frame_id 回退
                 # （契约 §1 要求单调递增，B 线趋势曲线与断点重连都靠它）
                 "--frame-id-offset", str(i * 100_000)],
                cwd=str(REPO_ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300,
            )
            if r.returncode != 0:
                ck.add(f"回放 {pat}", False, f"退出码 {r.returncode}：{(r.stderr or '').strip()[:200]}")
                continue
            sm = json.loads(summary.read_text(encoding="utf-8"))
            frames = load_jsonl(stream)
            runs.append({"pattern": pat, "stream": frames, "summary": sm})
            ck.add(f"回放 {pat}", sm["post"]["posted"] > 0,
                   f"推 {sm['post']['posted']} 帧 / 节流跳过 {sm['post']['throttled']} 帧，"
                   f"frame_id 从 {sm['frame_id_offset']} 起，状态分布 {sm['status_counts']}，"
                   f"收尾帧 {sm['done_frame']['status']}")

        if not runs:
            return _finish(ck, report, t0)

        # ---------- [4] 回读比对：B 线手上的数字必须是 A 线跑出来的 ----------
        print()
        print("[4] 回读比对：B 线 /api/status、/api/metrics 与 A 线流逐字段相同")
        code, metrics = http_json(f"{base}/api/metrics?limit=600")
        hist = (metrics or {}).get("history") or []
        latest = (metrics or {}).get("latest") or {}
        ck.add("历史非空", len(hist) > 0, f"/api/metrics 收到 {len(hist)} 帧")
        ids = [h["frame_id"] for h in hist]
        ts_seq = [h["ts"] for h in hist]
        mono = all(b > a for a, b in zip(ids, ids[1:])) and all(b > a for a, b in zip(ts_seq, ts_seq[1:]))
        ck.add("frame_id / ts 单调递增（契约 §1）", mono,
               f"frame_id {ids[0]} → {ids[-1]}，ts {ts_seq[0]} → {ts_seq[-1]}"
               + ("" if mono else "，**出现回退**"))

        mine: dict[int, dict] = {}
        for run in runs:
            for f in run["stream"]:
                mine[f["frame_id"]] = f
        latest_ok = latest.get("frame_id") in mine and latest == mine[latest["frame_id"]]
        ck.add("最新一帧逐字段一致", latest_ok,
               f"frame_id={latest.get('frame_id')}（比对字段 {len(latest)} 个）")

        bad: list[str] = []
        # /api/metrics 的 history 只带这几个字段（B 线画趋势曲线用的就是它们）
        extract = {
            "ts": lambda f: f["ts"],
            "blink_rate_per_min": lambda f: f["behavior"]["blink_rate_per_min"],
            "perclos": lambda f: f["behavior"]["perclos"],
            "hr_bpm": lambda f: f["vital"]["hr_bpm"],
            "quality_overall": lambda f: f["quality"]["overall"],
            "status": lambda f: f["status"],
        }
        for h in hist:
            f = mine.get(h["frame_id"])
            if f is None:
                bad.append(f"frame_id={h['frame_id']} 不在 A 线流里")
                continue
            for key, fn in extract.items():
                want = fn(f)
                if h.get(key) != want:
                    bad.append(f"frame_id={h['frame_id']}.{key}: B={h.get(key)!r} != A={want!r}")
        ck.add("历史字段一致", not bad,
               f"逐帧比对 {len(hist)} 条 × {len(extract)} 个字段"
               + ("" if not bad else f"，{len(bad)} 处不一致：{bad[:3]}"))

        # ---------- [5] 六态覆盖 ----------
        print()
        print("[5] 六态覆盖（契约 §2 冻结的 6 个 status）")
        code, events = http_json(f"{base}/api/events?limit=200")
        seen = {f["status"] for run in runs for f in run["stream"]}
        seen |= {h["status"] for h in hist}
        seen |= {e.get("to") for e in ((events or {}).get("events") or []) if e.get("to")}
        seen |= {idle_status} if idle_status else set()
        missing = [s for s in STATUS_VALUES if s not in seen]
        ck.add("六态齐全", not missing, f"出现 {sorted(seen)}" + (f"，缺 {missing}" if missing else ""))
        ck.add("事件日志非空", bool((events or {}).get("events")),
               f"/api/events 记录 {len((events or {}).get('events') or [])} 次状态切换")

        # ---------- [6] 契约门 ----------
        print()
        print("[6] 契约门：坏帧必须被 /api/ingest 以 422 挡下，且不污染历史")
        bad_frame = json.loads(json.dumps(runs[-1]["stream"][-2]))   # 拿一帧真帧，改坏一个字段
        bad_frame["status"] = "definitely_not_a_status"
        code, resp = http_json(ingest_url, payload={"frame": bad_frame})
        errs = (resp or {}).get("errors") if isinstance(resp, dict) else resp
        ck.add("坏帧被 422 拒收", code == 422,
               f"HTTP {code}，errors={errs if isinstance(errs, str) else (errs or [])[:1]}")
        code, metrics2 = http_json(f"{base}/api/metrics?limit=600")
        ck.add("坏帧未进历史", len(((metrics2 or {}).get("history") or [])) == len(hist),
               f"历史条数 {len(hist)} → {len(((metrics2 or {}).get('history') or []))}")

        # ---------- [7] WebSocket ----------
        print()
        print("[7] WebSocket：浏览器走的那条路（/ws）")
        ws_ok, ws_detail = _check_ws(base, runs[-1]["stream"][-2])
        if ws_ok is None:
            ck.skip("WS 推送", ws_detail)
        else:
            ck.add("WS 推送原样到达", ws_ok, ws_detail)

        # ---------- [8] 页面同源可访问 ----------
        print()
        print("[8] 仪表盘页面由 B 线服务同源托管（不用 file://，也不用填地址）")
        try:
            with urllib.request.urlopen(f"{base}/", timeout=5) as resp:  # noqa: S310
                html = resp.read().decode("utf-8", errors="replace")
            ck.add("GET / 返回仪表盘", resp.status == 200 and "app.js" in html,
                   f"HTTP {resp.status}，{len(html)} 字节，引用 app.js={('app.js' in html)}")
        except Exception as e:  # noqa: BLE001
            ck.add("GET / 返回仪表盘", False, f"{e}")

        report["steps"] = [
            {"name": n, "ok": ok, "detail": d} for n, ok, d in ck.results
        ]
        report["statuses_seen"] = sorted(seen)
        report["runs"] = [
            {"pattern": r["pattern"], "frames": r["summary"]["frames_processed"],
             "posted": r["summary"]["post"]["posted"], "throttled": r["summary"]["post"]["throttled"],
             "status_counts": r["summary"]["status_counts"],
             "done_frame": r["summary"]["done_frame"],
             "landmark_source": r["summary"]["landmark_source"]}
            for r in runs
        ]
        return _finish(ck, report, t0)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        api_log.close()


def _check_ws(base: str, marker: dict) -> tuple[bool | None, str]:
    """连上 /ws，再推一帧 marker，看能不能原样收到（fail-open 成 SKIP 的唯一一项）。"""
    try:
        from websockets.sync.client import connect
    except ImportError:
        return None, "本机没有 websockets 库（pip install websockets 后此项才有意义）"

    uri = base.replace("http://", "ws://") + "/ws"
    try:
        with connect(uri, open_timeout=5) as ws:
            # subscribe() 会先补发 latest；把积压的旧帧排掉，再推我们的 marker
            for _ in range(5):
                try:
                    ws.recv(timeout=0.3)
                except TimeoutError:
                    break
                except Exception:  # noqa: BLE001
                    break
            marker = json.loads(json.dumps(marker))
            marker["frame_id"] = int(marker["frame_id"]) + 100_000
            marker["ts"] = float(marker["ts"]) + 100_000.0
            code, _ = http_json(base + "/api/ingest", payload={"frame": marker})
            if code != 200:
                return False, f"marker 推送失败：HTTP {code}"
            got = json.loads(ws.recv(timeout=5))
            if got != marker:
                return False, f"收到的帧与推的不一致（frame_id {got.get('frame_id')} vs {marker['frame_id']}）"
            return True, f"/ws 原样收到 frame_id={marker['frame_id']}（{len(marker)} 个字段）"
    except TimeoutError:
        return False, "5 秒内没收到 WebSocket 帧"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _finish(ck: Checks, report: dict, t0: float) -> int:
    failed = [(n, d) for n, ok, d in ck.results if not ok]
    elapsed = time.time() - t0
    report["steps"] = [{"name": n, "ok": ok, "detail": d} for n, ok, d in ck.results]
    report["elapsed_s"] = round(elapsed, 1)
    report["passed"] = len(ck.results) - len(failed)
    report["failed"] = len(failed)
    report["ok"] = not failed

    archive = "--evidence" in sys.argv
    out = LOGS / "_p5_m2_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print()
    print("=" * 62)
    if failed:
        print(f"P5 / M2 检查未通过：{len(failed)} 项失败（共 {len(ck.results)} 项，用时 {elapsed:.1f}s）")
        for n, d in failed:
            print(f"  {n}: {d}")
        return 1
    print(f"P5 / M2 检查通过：{len(ck.results)} 项全绿（用时 {elapsed:.1f}s）"
          f"，六态覆盖 {report.get('statuses_seen')}")
    if archive:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        ep = EVIDENCE / f"{time.strftime('%Y-%m-%d')}_a_line_p5_m2_bridge.json"
        ep.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"证据：{ep}（已归档，可提交）")
    else:
        print(f"详情：{out}（不入库；要归档证据请加 --evidence）")
    return 0


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    raise SystemExit(main())
