"""test_publish.py —— M2（P5）交接面的测试：A 线 → B 线 `/api/ingest`。

为什么单独测这一层：
    P5 是新加的**跨进程**通道，出问题的方式和算法不一样 ——
    它不会算错数，它会**安静地少发几帧**。"网页不动、日志无损"是最难查的现场故障，
    所以这里专门覆盖四件事：
      1) 按逻辑时间节流（回放比实时快几十倍，不节流会灌爆对面；按墙上时钟节流又会几乎不发）；
      2) 发出去的就是契约帧本身（不多字段、不少字段、不带内部 `_triggers`）；
      3) 发之前本地先校验，坏帧不许上路；
      4) 连不上 / 对面 422 必须**抛错**，由 run_pipeline 以退出码 3 收场 —— 不许静默丢帧。

接的是**真的 HTTP**（本机 http.server），不是 mock 掉 urllib：
    这一层的价值全在"两个进程之间真的通了"，把传输打桩等于没测。
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from backend.config import load_config
from backend.contract import validate_frame
from backend.publish import FramePoster, PostError
from backend.run_pipeline import main as run_pipeline_main
from backend.storage import load_jsonl


def _free_port() -> int:
    """拿一个（当前）没人监听的端口：用来测"连不上"。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    """最小可用的 ingest 假服务：记录收到的 payload，可按需返回 422。"""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.mode = "ok"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 —— BaseHTTPRequestHandler 的命名
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                try:
                    outer.received.append(json.loads(raw.decode("utf-8")))
                except json.JSONDecodeError:
                    outer.received.append({"_raw": raw.decode("utf-8", "replace")})

                if outer.mode == "422":
                    status, body = 422, {"ok": False, "errors": ["status 不在契约枚举内（假服务）"]}
                else:
                    status, body = 200, {"ok": True, "published": len(outer.received)}
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a: Any) -> None:  # 静音
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/api/ingest"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "_Server":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @property
    def frames(self) -> list[dict]:
        return [p["frame"] for p in self.received if isinstance(p.get("frame"), dict)]


@pytest.fixture
def ingest() -> Iterator[_Server]:
    with _Server() as srv:
        yield srv


def _good_frame(frame_id: int = 0, ts: float = 0.0) -> dict:
    from backend.mock import mock_frame

    return mock_frame(frame_id, ts=ts, status="normal")


# ---------------------------------------------------------------------------
# 1) 节流：按**逻辑时间**，不按墙上时钟
# ---------------------------------------------------------------------------

def test_due_uses_logical_timestamps() -> None:
    p = FramePoster("http://127.0.0.1:1/api/ingest", hz=1.0, backoff=0.0)
    assert p.due(0.0) is True, "第一帧必须发（否则对面等不到任何数据）"
    p._last_ts = 0.0
    assert p.due(0.5) is False
    assert p.due(0.999) is False
    assert p.due(1.0) is True
    p._last_ts = 1.0
    assert p.due(2.0) is True, "回放快于实时也必须按 ts 判断，不能按墙钟"


def test_maybe_post_throttles_and_counts(ingest: _Server) -> None:
    p = FramePoster(ingest.url, hz=1.0, backoff=0.0)
    # 45 fps 跑 3 秒 = 136 帧（ts 0.000~3.000），1 Hz 推送应当只发 4 帧（ts=0/1/2/3；done 另算）
    sent = sum(p.maybe_post(_good_frame(i, round(i / 45, 3))) for i in range(136))
    assert sent == 4, f"1 Hz × 3 秒应发 4 帧，实际 {sent}"
    assert p.throttled == 132
    assert len(ingest.frames) == 4
    assert [f["ts"] for f in ingest.frames] == [0.0, 1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# 2) 发出去的就是契约帧本身
# ---------------------------------------------------------------------------

def test_payload_is_frame_wrapper_and_passes_contract(ingest: _Server) -> None:
    p = FramePoster(ingest.url, backoff=0.0)
    resp = p.post(_good_frame(7, 1.5))
    assert resp.get("ok") is True, "对面 200 的响应要能读回来（不吞）"
    assert len(ingest.received) == 1
    payload = ingest.received[0]
    assert set(payload) == {"frame"}, "包法固定 {\"frame\": ...}（api.py 两种都兼容，这里锁一种）"
    assert validate_frame(payload["frame"]) == []
    assert payload["frame"]["frame_id"] == 7


def test_internal_fields_are_stripped(ingest: _Server) -> None:
    fr = _good_frame(1, 0.0)
    fr["_triggers"] = [{"rule": "perclos"}]      # run_pipeline 会挂在内存里
    FramePoster(ingest.url, backoff=0.0).post(fr)
    sent = ingest.frames[0]
    assert "_triggers" not in sent
    assert validate_frame(sent) == [], "内部字段漏出去会让对面 422"


def test_invalid_frame_is_rejected_before_sending(ingest: _Server) -> None:
    fr = _good_frame(2, 0.0)
    fr["status"] = "nonsense"                     # 契约只允许 6 个枚举值
    p = FramePoster(ingest.url, backoff=0.0)
    with pytest.raises(PostError) as ei:
        p.post(fr)
    assert ei.value.contract_mismatch is True
    assert ei.value.errors, "要带上是哪个字段坏了"
    assert ingest.received == [], "本地就该拦下，不该浪费一次 HTTP"
    assert p.attempts == 0


# ---------------------------------------------------------------------------
# 3) 失败必须出声
# ---------------------------------------------------------------------------

def test_422_is_reported_as_contract_mismatch_without_retry(ingest: _Server) -> None:
    ingest.mode = "422"
    p = FramePoster(ingest.url, retries=2, backoff=0.0)
    with pytest.raises(PostError) as ei:
        p.post(_good_frame(3, 0.0))
    assert ei.value.status == 422
    assert ei.value.contract_mismatch is True
    assert ei.value.errors == ["status 不在契约枚举内（假服务）"]
    assert p.attempts == 1, "契约不匹配重试没有意义（重试一百次对面还是拒收）"
    assert len(ingest.received) == 1


def test_connection_failure_retries_then_raises() -> None:
    url = f"http://127.0.0.1:{_free_port()}/api/ingest"
    p = FramePoster(url, retries=2, backoff=0.0, timeout=0.5)
    with pytest.raises(PostError) as ei:
        p.post(_good_frame(4, 0.0))
    assert p.attempts == 3, "首次 + 2 次重试"
    assert "连续" in str(ei.value) or "失败" in str(ei.value)
    assert p.posted == 0 and p.errors


# ---------------------------------------------------------------------------
# 4) 端到端：run_pipeline --post
# ---------------------------------------------------------------------------

def test_run_pipeline_posts_real_frames_and_done(ingest: _Server, workdir: Path) -> None:
    """网页上要看到的就是这次运行的真实数字 —— 逐字段比对 jsonl 流与对面收到的帧。"""
    stream = workdir / "stream.jsonl"
    rc = run_pipeline_main([
        "--source", "synthetic", "--pattern", "blink", "--seconds", "3", "--stub", "--quiet",
        "--json", str(workdir / "last.json"), "--jsonl", str(stream),
        "--summary", str(workdir / "summary.json"),
        "--post", ingest.url, "--post-hz", "1.0",
    ])
    assert rc == 0

    received = ingest.frames
    assert received, "对面一帧都没收到 = M2 没接上"
    assert [f["status"] for f in received][-1] == "done", "收尾帧要推过去（契约六态之一）"
    for f in received:
        assert validate_frame(f) == [], "对面收到的每一帧都必须合法"

    stream_frames = load_jsonl(stream)
    received_ids = [f["frame_id"] for f in received]
    expected_ids = [f["frame_id"] for f in stream_frames
                    if f["frame_id"] in set(received_ids)]
    assert received_ids == expected_ids, "推出去的必须是流里的帧，不许改写数值"
    by_id = {f["frame_id"]: f for f in stream_frames}
    for f in received:
        assert f == by_id[f["frame_id"]], f"frame {f['frame_id']} 推送内容与流不一致"

    summary = json.loads((workdir / "summary.json").read_text(encoding="utf-8"))
    assert summary["post"]["posted"] == len(received)
    assert summary["post"]["throttled"] == summary["frames_processed"] - (len(received) - 1)
    assert summary["post"]["errors"] == []
    assert summary["done_frame"]["status"] == "done"
    assert summary["done_frame"]["written_to"] == "jsonl+post"


def test_done_frame_goes_to_stream_but_not_csv(workdir: Path) -> None:
    """CSV 是逐帧测量表（P4 标定按行数算），收尾帧只进时间流。"""
    import csv

    stream, csvp = workdir / "s.jsonl", workdir / "m.csv"
    rc = run_pipeline_main(["--source", "synthetic", "--seconds", "2", "--stub", "--quiet",
                            "--json", str(workdir / "l.json"), "--jsonl", str(stream),
                            "--csv", str(csvp), "--summary", str(workdir / "sum.json")])
    assert rc == 0
    fps = float(load_config()["fps_nominal"])
    frames = load_jsonl(stream)
    assert frames[-1]["status"] == "done"
    assert len(frames) == round(2 * fps) + 1, "流 = 测量帧 + 1 个收尾帧"
    assert frames[-1]["frame_id"] == frames[-2]["frame_id"] + 1, "frame_id 必须单调递增"
    assert frames[-1]["ts"] > frames[-2]["ts"], "ts 必须单调递增"

    with csvp.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == round(2 * fps), "CSV 不许出现 done 行"
    assert all(r["status"] != "done" for r in rows)

    snap = json.loads((workdir / "l.json").read_text(encoding="utf-8"))
    assert snap["status"] != "done", "last.json 是「最新一次测量」快照，不被收尾帧覆盖"


def test_post_failure_keeps_measurement_and_exits_3(workdir: Path) -> None:
    """B 线没起 / 端口写错：测量必须**完整落盘**，结局必须**响亮**（退出码 3）。

    这两条缺一不可（实测踩到的反例）：
      - 只在失败时立刻 return 3：测量产物被截断成 1 行 CSV，看起来像"跑过了"，
        又像"没跑过"，是最坏的一种状态（数据真实性风险）；
      - 只继续跑完不出声：网页一直不动，没人知道 B 线根本没收到数据。
    """
    import csv

    url = f"http://127.0.0.1:{_free_port()}/api/ingest"
    stream, csvp = workdir / "s.jsonl", workdir / "m.csv"
    rc = run_pipeline_main(["--source", "synthetic", "--seconds", "2", "--stub", "--quiet",
                            "--json", str(workdir / "l.json"), "--jsonl", str(stream),
                            "--csv", str(csvp), "--summary", str(workdir / "sum.json"),
                            "--post", url, "--post-retries", "0"])
    assert rc == 3

    expected = round(2 * float(load_config()["fps_nominal"]))
    with csvp.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == expected, "推送失败不该让测量产物截断"
    frames = load_jsonl(stream)
    assert frames[-1]["status"] == "done", "收尾帧照常进流"

    sm = json.loads((workdir / "sum.json").read_text(encoding="utf-8"))
    assert sm["post"]["ok"] is False
    assert sm["post"]["stopped_after_failure"] is True
    assert sm["post"]["posted"] == 0
    assert sm["post"]["http_attempts"] == 1, "第一次彻底失败后就停推，不许逐帧重试刷日志"
    assert sm["post"]["errors"], "失败原文要留在摘要里，便于排查"
    snap = json.loads((workdir / "l.json").read_text(encoding="utf-8"))
    assert snap["status"] != "done", "last.json 仍是最新一次测量"


def test_no_done_flag_suppresses_closing_frame(workdir: Path) -> None:
    stream = workdir / "s.jsonl"
    rc = run_pipeline_main(["--source", "synthetic", "--seconds", "1", "--stub", "--quiet",
                            "--json", str(workdir / "l.json"), "--jsonl", str(stream),
                            "--summary", str(workdir / "sum.json"), "--no-done"])
    assert rc == 0
    assert all(f["status"] != "done" for f in load_jsonl(stream))
    assert json.loads((workdir / "sum.json").read_text(encoding="utf-8"))["done_frame"] is None


def test_frame_id_offset_continues_the_timeline(workdir: Path) -> None:
    """M2 连跑多段回放：第二段必须接着第一段的 frame_id/ts，否则契约 §1 的单调性就断了。

    这条是**真实踩到过的**：四段合成回放（blink/yawn/still/turn）各自从 frame_id=0 开始，
    B 线历史里出现 frame_id 回退与重号，趋势曲线的横轴与断点重连都会错位。
    """
    fps = float(load_config()["fps_nominal"])
    out = {}
    for tag, offset in (("a", 0), ("b", 100_000)):
        stream = workdir / f"s_{tag}.jsonl"
        rc = run_pipeline_main(["--source", "synthetic", "--seconds", "2", "--stub", "--quiet",
                                "--json", str(workdir / f"l_{tag}.json"),
                                "--jsonl", str(stream), "--frame-id-offset", str(offset),
                                "--summary", str(workdir / f"sum_{tag}.json")])
        assert rc == 0
        out[tag] = load_jsonl(stream)
        assert out[tag][0]["frame_id"] == offset
        assert out[tag][0]["ts"] == pytest.approx(offset / fps), "ts 必须跟着 frame_id 走"
        assert out[tag][-1]["frame_id"] > out[tag][-2]["frame_id"], "段内也要单调递增"
    assert out["b"][0]["frame_id"] > out["a"][-1]["frame_id"], "跨段不许回退"
    assert out["b"][0]["ts"] > out["a"][-1]["ts"]


def test_posting_is_byte_reproducible(ingest: _Server, workdir: Path) -> None:
    """同样的命令跑两次，**推给 B 线的帧集合逐字节相同**。

    为什么单独测这条：节流是"按逻辑时间"的，一旦有人把它改成按墙上时钟
    （看着更"实时"），同一段回放两次就会推出不同的帧集合 —— 现场演示对不上、
    事后也复现不了。回归用这一条钉住。
    """
    args = ["--source", "synthetic", "--pattern", "turn", "--seconds", "6", "--stub",
            "--quiet", "--json", str(workdir / "l.json"), "--post", ingest.url]
    runs = []
    for tag in ("a", "b"):
        rc = run_pipeline_main([*args, "--summary", str(workdir / f"sum_{tag}.json")])
        assert rc == 0
        runs.append(list(ingest.frames))
    first, second = runs[0], ingest.frames[len(runs[0]):]
    assert first == second, "两次运行推送的帧必须逐字节相同（不许有墙钟/随机来源）"
    assert first and first[-1]["status"] == "done"
    assert [f["ts"] for f in first[:-1]] == [float(i) for i in range(len(first) - 1)], \
        "1 Hz 逻辑时间节流：ts 应恰好落在整秒上"
