"""Local-only human acceptance capture/replay. Cues are NOT ground-truth labels.

record: serve a local consent/start and manual review page; retain the original
board packets (including timestamps/PL metadata) in ignored metrics/logs only.
replay: feed those identical packets to the production pipeline over loopback.
No face image, annotation or algorithm threshold is sent to an external service.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import board_source as bs
from backend.config import load_config

# Durations specify test stimuli, not detector thresholds. A human must label
# what actually happened; these planned ranges never become annotations.
PLAN = [
    (10, "准备：坐正，整张脸入镜。接下来按屏幕和语音提示动作。"),
    (35, "基线：正视相机，自然睁眼，保持安静。可以自然眨眼。"),
    (5, "眨眼一次，然后自然睁眼。"),
    (5, "再眨眼一次，然后自然睁眼。"),
    (5, "再眨眼一次，然后自然睁眼。"),
    (5, "再眨眼一次，然后自然睁眼。"),
    (2, "现在闭眼，听到下一条提示再睁开。"),
    (8, "睁开眼睛，保持正视相机。"),
    (15, "正常说话：介绍今天的天气和正在做的事情。不要刻意张大嘴。"),
    (5, "模拟一次明显哈欠：缓慢张大嘴，维持约两秒后闭上。"),
    (5, "闭嘴休息，正常睁眼。"),
    (5, "再模拟一次明显哈欠，然后闭上嘴。"),
    (5, "闭嘴休息，正常睁眼。"),
    (5, "第三次模拟明显哈欠，然后闭上嘴。"),
    (5, "恢复正视相机，闭嘴休息。"),
    (5, "向左转头，保持片刻。"),
    (5, "转回正面。"),
    (5, "向右转头，保持片刻。"),
    (5, "转回正面。"),
    (5, "低头，保持片刻。"),
    (5, "抬头恢复正面。"),
    (5, "用手遮挡自己的脸。"),
    (5, "放下手，恢复正面。"),
    (5, "暂时移出相机画面。"),
    (5, "回到画面中，恢复正面。"),
    (8, "保持正面，正常光照。"),
    (8, "如果方便，降低脸部照明；不方便就保持原样，之后标注未执行。"),
    (8, "恢复正常照明和正面姿态。"),
    (8, "轻轻左右晃动身体，保持脸部在画面内。"),
    (8, "停止晃动，保持正面。本次动作录制即将结束。"),
]
EVENTS = {"blink", "long_close", "yawn", "turn", "occluded", "normal",
          "talking", "dark", "moving", "ignore"}
QUICK_SECTIONS = [(0, 45, "准备和正常正脸"), (45, 75, "眨眼和长闭眼"),
                  (75, 90, "正常说话"), (90, 120, "模拟哈欠"),
                  (120, 150, "转头和低头"), (150, 170, "遮挡和出框"),
                  (170, 178, "正常光照"), (178, 186, "尝试变暗"),
                  (186, 194, "恢复光照"), (194, 202, "晃动"),
                  (202, None, "恢复正面和录制收尾")]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def phase_at(seconds: float) -> dict:
    start = 0
    for i, (duration, text) in enumerate(PLAN):
        if seconds < start + duration:
            return {"id": i, "text": text, "remaining": start + duration - seconds,
                    "next": PLAN[i + 1][1] if i + 1 < len(PLAN) else "完成"}
        start += duration
    return {"id": len(PLAN), "text": "动作已完成，保持正面，等待录制自动收尾。",
            "remaining": 0, "next": "逐帧复核并填写标注"}


class PacketSocket:
    """Capture exactly the bytes consumed by the existing protocol validator."""
    def __init__(self, sock):
        self.sock = sock
        self.packet = bytearray()

    def settimeout(self, timeout):
        self.sock.settimeout(timeout)

    def recv(self, count):
        block = self.sock.recv(count)
        self.packet.extend(block)
        return block


def jpeg_from_packet(packet: bytes) -> bytes:
    magic, meta_size, payload_size = struct.unpack("!4sII", packet[:12])
    if magic != b"VLP2" or len(packet) != 16 + meta_size + payload_size:
        raise ValueError("review requires an intact VLP2 MJPEG packet")
    meta = json.loads(packet[16:16 + meta_size])
    if meta.get("codec") != "jpeg-mjpeg":
        raise ValueError("review requires jpeg-mjpeg")
    return packet[16 + meta_size:]


def validate_labels(rows: list, count: int) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("annotations must be a list")
    clean = []
    for row in rows:
        start, end = row.get("start_frame"), row.get("end_frame")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end < count or row.get("event") not in EVENTS):
            raise ValueError("invalid annotation event/frame interval")
        clean.append({"start_frame": start, "end_frame": end,
                      "event": row["event"], "note": str(row.get("note", ""))})
    return sorted(clean, key=lambda r: (r["start_frame"], r["end_frame"], r["event"]))


class Session:
    def __init__(self, directory: Path, board: str, prepared: Path | None):
        self.directory, self.board, self.prepared = directory, board, prepared
        self.lock = threading.RLock()
        self.state = {"status": "ready", "frames": 0, "seconds": 0.0,
                      "error": None, "duration": sum(p[0] for p in PLAN)}
        self.index: list[dict] = []
        self.latest = b""
        self.token = secrets.token_urlsafe(24)
        self.worker = None

    def quick_sections(self):
        sections = []
        for i, (start, end, title) in enumerate(QUICK_SECTIONS):
            ids = [n for n, x in enumerate(self.index) if x["ts"] >= start and (end is None or x["ts"] < end)]
            if ids:
                sections.append({"id": i, "title": title, "start_frame": ids[0], "end_frame": ids[-1],
                                 "start_s": self.index[ids[0]]["ts"], "end_s": self.index[ids[-1]]["ts"]})
        return sections

    def save_quick_review(self, request):
        with self.lock:
            if self.state["status"] not in ("review", "reviewed"):
                raise ValueError("capture must finish successfully before review")
            if not str(request.get("reviewer", "")).strip() or request.get("reviewed_all") is not True:
                raise ValueError("请填写复核者代号并确认已经看过各段回放")
            sections = self.quick_sections()
            rows = request.get("rows")
            if not isinstance(rows, list) or len(rows) != len(sections):
                raise ValueError("请逐段确认；无法判断的片段选择不确定，不要猜次数")
            clean = []
            for section, row in zip(sections, rows):
                if row.get("id") != section["id"] or row.get("confirmed") is not True:
                    raise ValueError("还有片段没有人工确认")
                execution = row.get("execution")
                if execution not in ("complete", "partial", "not_done", "uncertain"):
                    raise ValueError("请选择该段动作实际完成情况")
                counts = row.get("counts", {})
                if set(counts) != {"blink", "long_close", "yawn"}:
                    raise ValueError("三种事件次数字段必须齐全")
                if any(v is not None and (type(v) is not int or v < 0) for v in counts.values()):
                    raise ValueError("实际次数须为非负整数；无法确定请留空")
                clean.append({**section, "counts": counts, "execution": execution,
                              "confirmed": True, "note": str(row.get("note", ""))})
            dump(self.directory / "quick_counts.json", clean)
            dump(self.directory / "quick_human_review.json", {
                "reviewer": str(request["reviewer"]).strip(), "reviewed_all": True,
                "annotation_source": "human_segment_counts", "frame_level_ground_truth": False,
                "wire_sha256": sha256(self.directory / "private.vlp"),
                "counts_sha256": sha256(self.directory / "quick_counts.json"),
                "count_semantics": "Count eye events at reopening, yawns once per episode; uncertain counts stay null.",
                "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            self.state["status"] = "reviewed"

    def start(self):
        with self.lock:
            if self.state["status"] != "ready":
                raise ValueError("a session can only be started once")
            self.state["status"] = "starting"
            self.worker = threading.Thread(target=self.capture, daemon=True)
            self.worker.start()

    def console(self, script: str, timeout: int):
        result = subprocess.run([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(ROOT / "board/w7_buildroot/run_console.ps1"), "-Port", "COM11",
            "-CommandFile", str(self.prepared / script), "-TimeoutSeconds", str(timeout),
            "-Out", str(self.directory / (script + ".serial.log"))],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout + 25)
        if result.returncode:
            raise ValueError("serial helper failed: " + result.stdout[-1000:] + result.stderr[-1000:])
        return (self.directory / (script + ".serial.log")).read_text(encoding="utf-8-sig")

    def capture(self):
        cfg = load_config()
        previous = None
        complete = False
        try:
            if self.prepared:
                launch = self.console("launch.sh", 45)
                if not re.search(r"(?m)^VIGI_NETWORK_READY\s*$", launch) or re.search(r"(?m)^VIGI_NETWORK_FAIL_", launch):
                    raise ValueError("board did not become ready; inspect launch.sh.serial.log")
            address = bs.parse_address(self.board)
            with socket.create_connection(address, timeout=bs.PACKET_TIMEOUT) as sock, (
                    self.directory / "private.vlp").open("xb") as raw, (
                    self.directory / "index.jsonl").open("x", encoding="utf-8") as index:
                tee = PacketSocket(sock)
                while True:
                    tee.packet.clear()
                    packet = bs.read_packet(tee, int(cfg["motion_thresh_gray"]))
                    if packet is None:
                        raise ValueError("stream ended without VLE1")
                    offset = raw.tell()
                    raw.write(tee.packet)
                    if isinstance(packet, bs.BoardStreamEnd):
                        if not self.index or packet.compared_frames != len(self.index):
                            raise ValueError("VLE1 count mismatch")
                        complete = True
                        break
                    meta, rgb = packet
                    if previous:
                        if any(meta[k] <= previous[k] for k in (
                                "source_sequence", "source_ts_us", "pl_frame_id")):
                            raise ValueError("nonmonotonic board metadata")
                        if not meta["motion_valid"]:
                            raise ValueError("motion invalid after first frame")
                    elif meta["motion_valid"]:
                        raise ValueError("first motion must be invalid")
                    import numpy as np
                    image = np.frombuffer(rgb, dtype=np.uint8).reshape(bs.HEIGHT, bs.WIDTH, 3)
                    if not bs._channel_sums_ok(bs._channel_sums(image), meta):
                        raise ValueError("same-packet PL channel sums mismatch")
                    jpg = jpeg_from_packet(tee.packet)
                    first_ts = self.index[0]["source_ts_us"] if self.index else meta["source_ts_us"]
                    item = {"frame_id": len(self.index), "offset": offset,
                            "size": len(tee.packet), "source_ts_us": meta["source_ts_us"],
                            "ts": (meta["source_ts_us"] - first_ts) / 1e6}
                    index.write(json.dumps(item) + "\n")
                    with self.lock:
                        self.index.append(item)
                        self.latest = jpg
                        self.state.update(status="recording", frames=len(self.index), seconds=item["ts"])
                    previous = meta
            dump(self.directory / "capture.json", {
                "complete": complete, "frames": len(self.index), "board": self.board,
                "source_span_s": self.index[-1]["ts"], "wire_sha256": sha256(self.directory / "private.vlp"),
                "config_sha256": sha256(ROOT / "config.yaml"), "plan": PLAN,
                "plan_is_ground_truth": False, "comparison": "JPEG channel sums within existing tolerance",
                "index_sha256": sha256(self.directory / "index.jsonl")})
            with self.lock:
                self.state["status"] = "review"
        except Exception as exc:
            with self.lock:
                self.state.update(status="error", error=str(exc))
            dump(self.directory / "capture_error.json", dict(self.state))
        finally:
            if self.prepared:
                try:
                    # A multi-minute capture log is too large for 115200 baud.
                    # Keep serial closeout bounded; the full log can be fetched
                    # separately over the local link and SHA256-checked.
                    (self.prepared / "human_summary.sh").write_text(
                        "tail -n 25 /tmp/vigi_network.log\nsha256sum /tmp/vigi_network.log\n", encoding="utf-8")
                    self.console("human_summary.sh", 12)
                except Exception as exc:
                    dump(self.directory / "collect_error.json", {"error": str(exc)})

    def save_review(self, request):
        with self.lock:
            if self.state["status"] not in ("review", "reviewed"):
                raise ValueError("capture must finish successfully before annotation")
            reviewer = str(request.get("reviewer", "")).strip()
            if not reviewer or request.get("reviewed_all") is not True:
                raise ValueError("human reviewer and full-coverage confirmation are required")
            rows = validate_labels(request.get("rows"), len(self.index))
            if not rows:
                raise ValueError("empty annotations cannot certify this action protocol")
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=["start_frame", "end_frame", "event", "note"])
            writer.writeheader()
            writer.writerows(rows)
            (self.directory / "annotations.csv").write_text(buffer.getvalue(), encoding="utf-8")
            dump(self.directory / "human_review.json", {
                "reviewer": reviewer, "reviewed_all": True, "annotation_source": "human_manual",
                "wire_sha256": sha256(self.directory / "private.vlp"),
                "annotations_sha256": sha256(self.directory / "annotations.csv"),
                "negative_semantics": "Unmarked frames are negative; uncertain intervals are ignore.",
                "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            self.state["status"] = "reviewed"


def serve(session: Session, port: int):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, data, kind="application/json; charset=utf-8"):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, query):
            return secrets.compare_digest(self.headers.get("X-Vigi-Token", query.get("token", [""])[0]), session.token)

        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if url.path == "/":
                html = Path(__file__).with_suffix(".html").read_text(encoding="utf-8")
                return self.send(200, html.replace("__TOKEN__", session.token).encode("utf-8"), "text/html; charset=utf-8")
            if not self.authorized(query):
                return self.send(403, {"error": "local session token required"})
            try:
                if url.path == "/state":
                    with session.lock:
                        state = {**session.state, "phase": phase_at(session.state["seconds"])}
                    return self.send(200, state)
                if url.path == "/index":
                    return self.send(200, [{"frame_id": x["frame_id"], "ts": x["ts"]} for x in session.index])
                if url.path == "/quick":
                    return self.send(200, {"sections": session.quick_sections(),
                                          "capture_id": str(session.directory.name)})
                if url.path == "/jpeg":
                    if "frame" in query:
                        n = int(query["frame"][0])
                        if not 0 <= n < len(session.index):
                            raise ValueError("frame outside capture")
                        item = session.index[n]
                        with (session.directory / "private.vlp").open("rb") as f:
                            f.seek(item["offset"])
                            data = jpeg_from_packet(f.read(item["size"]))
                    else:
                        data = session.latest
                    return self.send(200 if data else 204, data, "image/jpeg")
                return self.send(404, {"error": "not found"})
            except (ValueError, OSError, IndexError) as exc:
                return self.send(400, {"error": str(exc)})

        def do_POST(self):
            if not self.authorized({}):
                return self.send(403, {"error": "local session token required"})
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 <= size <= 1 << 20:
                    raise ValueError("request too large")
                value = json.loads(self.rfile.read(size) or b"{}")
                if self.path == "/start":
                    session.start()
                elif self.path == "/review":
                    session.save_review(value)
                elif self.path == "/quick_review":
                    session.save_quick_review(value)
                else:
                    return self.send(404, {"error": "not found"})
                return self.send(200, {"ok": True})
            except (ValueError, TypeError) as exc:
                return self.send(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"LOCAL_HUMAN_SESSION http://127.0.0.1:{port}/ directory={session.directory}", flush=True)
    server.serve_forever(poll_interval=0.5)


def replay(directory: Path, output: Path) -> int:
    capture = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
    wire = directory / "private.vlp"
    if not capture["complete"] or sha256(wire) != capture["wire_sha256"]:
        raise ValueError("capture incomplete or wire fingerprint changed")
    output.mkdir(parents=True, exist_ok=False)
    config_before = sha256(ROOT / "config.yaml")
    code_before = {str(p.relative_to(ROOT)): sha256(p) for p in sorted((ROOT / "backend").rglob("*.py"))
                   if "tests" not in p.parts and "__pycache__" not in p.parts}
    (output / "config.snapshot.yaml").write_bytes((ROOT / "config.yaml").read_bytes())
    failures = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(40)
        port = listener.getsockname()[1]

        def deliver():
            try:
                conn, _ = listener.accept()
                with conn, wire.open("rb") as f:
                    for block in iter(lambda: f.read(1 << 20), b""):
                        conn.sendall(block)
            except OSError as exc:
                failures.append(str(exc))

        worker = threading.Thread(target=deliver, daemon=True)
        worker.start()
        cmd = [sys.executable, str(ROOT / "backend/run_pipeline.py"), "--source",
               f"board:127.0.0.1:{port}", "--width", "1280", "--height", "720", "--rotate", "0",
               "--quiet", "--json", str(output / "last.json"), "--jsonl", str(output / "stream.jsonl"),
               "--csv", str(output / "metrics.csv"), "--summary", str(output / "summary.json")]
        with (output / "pipeline.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        worker.join(timeout=45)
    dump(output / "replay.json", {"wire_sha256": capture["wire_sha256"],
        "mode": "offline_original_packets_original_capture_timestamps",
        "real_time_performance_evidence": False, "pipeline_exit": result.returncode,
        "transport_errors": failures, "config_sha256": config_before,
        "backend_sha256": code_before,
        "inputs_changed_during_run": config_before != sha256(ROOT / "config.yaml") or any(
            not (ROOT / p).exists() or sha256(ROOT / p) != h for p, h in code_before.items())})
    return result.returncode or int(bool(failures) or worker.is_alive())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    modes = ap.add_subparsers(dest="mode", required=True)
    record = modes.add_parser("record")
    record.add_argument("--directory", type=Path, required=True)
    record.add_argument("--board", default="board:169.254.73.29:9000")
    record.add_argument("--prepared", type=Path)
    record.add_argument("--port", type=int, default=8010)
    review = modes.add_parser("review")
    review.add_argument("--directory", type=Path, required=True)
    review.add_argument("--port", type=int, default=8010)
    again = modes.add_parser("replay")
    again.add_argument("--directory", type=Path, required=True)
    again.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.mode == "replay":
        return replay(args.directory.resolve(), args.out.resolve())
    directory = args.directory.resolve()
    # Face material must remain inside the already ignored logs tree.
    directory.relative_to((ROOT / "metrics/logs").resolve())
    if args.mode == "review":
        capture = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
        if not capture["complete"] or sha256(directory / "private.vlp") != capture["wire_sha256"]:
            raise ValueError("cannot review an incomplete/changed capture")
        session = Session(directory, capture["board"], None)
        session.index = [json.loads(s) for s in (directory / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        session.state.update(status="review", frames=len(session.index), seconds=capture["source_span_s"])
    else:
        directory.mkdir(parents=True, exist_ok=False)
        session = Session(directory, args.board, args.prepared.resolve() if args.prepared else None)
    serve(session, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
