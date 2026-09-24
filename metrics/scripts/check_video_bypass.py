"""check_video_bypass.py —— 验证「旁路画面」这条链路，并证明它**没有污染冻结契约**。

为什么要单独一个检查脚本：
    画面走的是**旁路**（POST /api/frame → GET /video.mjpg），不是契约帧的一部分。
    旁路最容易出的两类事故都不是"报错"，而是**静默**的：
      1. 有人图省事把图像字段塞进契约帧 → `validate_frame` 拒收 → M2 集成炸掉；
      2. 推送端挂了，前端却拿着最后一帧**永远显示一张冻结的旧画面**（看起来像卡顿）。
    这两条都必须由测试钉死，不能靠人工看。

用法（仓库根）：
    .venv\\Scripts\\python.exe metrics/scripts/check_video_bypass.py
退出码：0 = 全部通过；1 = 有失败项。
"""

from __future__ import annotations

import json
import socket
import subprocess
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


def http(url: str, *, data: bytes | None = None, ctype: str | None = None,
         timeout: float = 5.0, method: str | None = None) -> tuple[int, bytes]:
    headers = {}
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 —— 本机回环
            return int(r.status), r.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.read()


def wait_ready(base: str, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            code, _ = http(f"{base}/api/status", timeout=2.0)
            if code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        return bool(ok)

    def report(self) -> int:
        bad = [r for r in self.rows if not r[1]]
        print("=" * 78)
        for name, ok, detail in self.rows:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if detail:
                print(f"          {detail}")
        print("=" * 78)
        print(f"{'全部通过' if not bad else '有失败'}：{len(self.rows) - len(bad)}/{len(self.rows)} 项通过")
        return 1 if bad else 0


def make_test_video(path: Path, frames: int = 45) -> bool:
    """造一段短片，给 run_pipeline 当帧源。返回是否成功。"""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:  # noqa: BLE001
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 15.0, (640, 480))
    if not w.isOpened():
        return False
    try:
        for i in range(frames):
            img = np.full((480, 640, 3), 40, np.uint8)
            x = (i * 12) % 560
            img[180:300, x:x + 80] = (60, 60, 200)
            w.write(img)
    finally:
        w.release()
    return path.exists() and path.stat().st_size > 0


def make_jpeg(w: int = 320, h: int = 240) -> bytes:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :, 1] = 190
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return buf.tobytes() if ok else b""


def main() -> int:
    py = resolve_python()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    ck = Checks()

    print("=== 旁路画面链路检查 ===")
    print(f"服务地址: {base}")
    print(f"解释器  : {py}\n")

    proc = subprocess.Popen(
        [py, "backend/api.py", "--no-mock", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    try:
        if not wait_ready(base):
            print("[FAIL] 服务没起来")
            return 1

        # ---- 1. 未推画面时：明确是"无画面"，而不是"有画面但空" -----------------
        code, body = http(f"{base}/api/video_status")
        st = json.loads(body)
        ck.add("T1 未推画面时 has_video=False（不是空画面冒充有画面）",
               code == 200 and st.get("has_video") is False,
               f"HTTP {code} {st}")

        # ---- 2. 推一张真 JPEG -------------------------------------------------
        jpeg = make_jpeg()
        ck.add("T0 测试用 JPEG 造好了", len(jpeg) > 500, f"{len(jpeg)} B")
        code, body = http(f"{base}/api/frame?frame_id=42&ts=1.5", data=jpeg, ctype="image/jpeg")
        ok = code == 200
        detail = f"HTTP {code} {body[:120].decode('utf-8', 'replace')}"
        if ok:
            meta = json.loads(body)
            ok = meta.get("bytes") == len(jpeg) and meta.get("frame_id") == 42
            detail = f"bytes={meta.get('bytes')} frame_id={meta.get('frame_id')}"
        ck.add("T2 POST /api/frame 收下 JPEG 并回显字节数与 frame_id", ok, detail)

        code, body = http(f"{base}/api/video_status")
        st = json.loads(body)
        ck.add("T3 推过之后 has_video=True（过期判定之内）",
               st.get("has_video") is True,
               f"age_s={st.get('age_s')} stale_after_s={st.get('stale_after_s')}")

        # ---- 3. 非 JPEG 必须被拒（不能让坏数据变成"画面"） --------------------
        code, body = http(f"{base}/api/frame", data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
                          ctype="image/png")
        ck.add("T4 非 JPEG 被 422 拒收（并说明原因）", code == 422,
               f"HTTP {code} {body[:110].decode('utf-8', 'replace')}")

        # ---- 4. MJPEG 流真的能读出来 -----------------------------------------
        # 先补推一帧，确保流里有内容可发（T3 之后停了一下，正是要验证"过期"）
        http(f"{base}/api/frame?frame_id=43", data=jpeg, ctype="image/jpeg")
        req = urllib.request.Request(f"{base}/video.mjpg")
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
            ctype = resp.headers.get("Content-Type", "")
            chunk = resp.read(4096)
        ck.add("T5 /video.mjpg 返回 multipart MJPEG 且含 JPEG 数据",
               "multipart/x-mixed-replace" in ctype and b"--frame" in chunk and b"\xff\xd8" in chunk,
               f"Content-Type={ctype} 首块 {len(chunk)} B")

        # ---- 5. 画面过期语义：停推之后必须变回"无画面" ------------------------
        stale_s = float(st.get("stale_after_s") or 1.5)
        time.sleep(stale_s + 0.4)
        code, body = http(f"{base}/api/video_status")
        st2 = json.loads(body)
        ck.add("T6 停推超过 stale 之后 has_video 变回 False（不显示冻结旧画面）",
               st2.get("has_video") is False,
               f"age_s={st2.get('age_s')} / stale={stale_s}s / reason={st2.get('reason')}")

        # ---- 6. **契约未被污染**：帧里不许出现任何图像字段 ---------------------
        from sys import path as _p
        _p.insert(0, str(REPO_ROOT / "backend"))
        from contract import validate_frame  # type: ignore
        from mock import mock_frame  # type: ignore

        frame = mock_frame(9001)
        keys_before = sorted(frame.keys())
        code, body = http(f"{base}/api/ingest", data=json.dumps({"frame": frame}).encode(),
                          ctype="application/json")
        ck.add("T7 契约帧仍能正常 ingest（旁路没有干扰它）", code == 200,
               f"HTTP {code} {body[:110].decode('utf-8', 'replace')}")

        code, body = http(f"{base}/api/metrics?limit=5")
        m = json.loads(body)
        got = (m.get("latest") or {}).get("frame_id")
        ck.add("T8 旁路开启后 /api/metrics 仍返回契约帧", got == 9001, f"latest.frame_id={got}")

        ck.add("T9 契约帧字段仍恰好是那 9 个（图像没有混进来）",
               keys_before == ["advice", "behavior", "face", "frame_id", "quality",
                               "reason", "status", "ts", "vital"],
               f"{keys_before}")
        ck.add("T10 validate_frame 对 mock 帧仍然无错", validate_frame(frame) == [],
               str(validate_frame(frame)))

        # ---- 7. 真跑一次 --push-video（端到端） -------------------------------
        clip = LOGS / "_video_bypass_clip.avi"
        if not make_test_video(clip):
            ck.add("T11 端到端 --push-video", False, "测试视频没造出来（OpenCV 不可用？）")
        else:
            summ = LOGS / "_video_bypass_summary.json"
            r = subprocess.run(
                [py, "backend/run_pipeline.py", "--source", str(clip), "--stub",
                 "--seconds", "3", "--post", f"{base}/api/ingest", "--push-video",
                 "--json", str(LOGS / "_video_bypass_last.json"), "--summary", str(summ),
                 "--quiet"],
                cwd=str(REPO_ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180,
            )
            out = (r.stdout or "") + (r.stderr or "")
            ok = r.returncode == 0 and summ.exists()
            detail = f"退出码 {r.returncode}"
            if ok:
                s = json.loads(summ.read_text(encoding="utf-8"))
                v = s.get("video") or {}
                ok = bool(v.get("posted", 0) > 0)
                detail = (f"video: 投递 {v.get('offered')} / 编码 {v.get('encoded')} / "
                          f"送达 {v.get('posted')} / 丢弃 {v.get('dropped')}"
                          + (f" | errors={v.get('errors')}" if v.get("errors") else ""))
                ck.add("T12 旁路画面统计里没有 'ok' 字段（它不参与退出码判定）",
                       "ok" not in v, f"keys={sorted(v.keys())}")
            ck.add("T11 端到端 --push-video：run_pipeline 真的把画面推到了 B 线", ok, detail)

        # ---- 8. 页面真的带上了画面元素（前端改动已生效，不是只改了后端） --------
        code, body = http(f"{base}/")
        html = body.decode("utf-8", "replace")
        ck.add("T13 网页 HTML 含旁路画面元素 #videostream 且引用 /video.mjpg",
               code == 200 and 'id="videostream"' in html and "video.mjpg" in html,
               f"HTTP {code} {len(body)} B；含 id={('id=\"videostream\"' in html)} "
               f"含 video.mjpg={('video.mjpg' in html)}")

        code, js = http(f"{base}/app.js")
        jstext = js.decode("utf-8", "replace")
        ck.add("T14 app.js 会用 /api/video_status 的过期判定（不是'收到过就显示'）",
               code == 200 and "/api/video_status" in jstext and "has_video" in jstext,
               f"HTTP {code} {len(js)} B")

        return ck.report()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
