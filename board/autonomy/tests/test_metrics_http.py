"""Real loopback HTTP tests; no board, camera, model, or measurement claims.

Run on Linux/WSL: python3 board/autonomy/tests/test_metrics_http.py
"""
import http.client
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]


class MetricsHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "posix" or not shutil.which("gcc"):
            raise unittest.SkipTest("Linux host gcc required")
        cls.temp = tempfile.TemporaryDirectory(prefix="vg_http_")
        cls.work = Path(cls.temp.name)
        cls.binary = cls.work / "metrics_http"
        subprocess.run([sys.executable,str(ROOT/"board/autonomy/export_vision_config.py"),
                        "--output",str(cls.work/"vg_vision_config.h")],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I",str(cls.work),
                        str(ROOT / "board/autonomy/metrics_http.c"), "-lm", "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.latest = self.work / "latest.json"
        self.preview = self.work / "preview.jpg"
        self.preview.unlink(missing_ok=True)
        self.display = ROOT / "board/autonomy/display.html"
        self.assets = self.work / 'ui'
        self.assets.mkdir(exist_ok=True)
        (self.assets/'app.js').write_bytes(b'/* explicit static asset fixture */')
        self.fixture = {"ts": 1.0, "frame_id": 1,
            "face": {"visible": 0, "bbox": [0, 0, 0, 0], "pose": {"yaw": 0, "pitch": 0, "roll": 0}},
            "behavior": {"ear_left": 0, "ear_right": 0, "blink_state": "OPEN", "blink_count": 0,
                "blink_rate_per_min": 0, "perclos": 0, "long_close_count": 0, "mar": 0, "yawn_count": 0},
            "vital": {"hr_bpm": None, "hr_conf": None, "rr_per_min": None, "rr_conf": None},
            "quality": {"light_score": 0, "motion_score": 0, "overall": 0},
            "status": "unreliable", "advice": "test fixture", "reason": "synthetic transport test"}
        self.publish()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.process = subprocess.Popen([str(self.binary), "--bind", "127.0.0.1", "--port", str(self.port),
            "--latest", str(self.latest), "--display", str(self.display), "--preview", str(self.preview), "--assets", str(self.assets), "--stale-seconds", "3"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready, _, _ = select.select([self.process.stdout], [], [], 5)
        self.assertTrue(ready, "server did not become ready")
        self.assertIn("METRICS_HTTP_READY", self.process.stdout.readline())

    def tearDown(self):
        self.process.terminate()
        try:
            self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate()
            self.fail("server did not stop within its bounded I/O timeout")

    def publish(self, raw=None):
        temp = self.latest.with_suffix(".tmp")
        temp.write_text(json.dumps(self.fixture) if raw is None else raw, encoding="utf-8")
        os.replace(temp, self.latest)

    def get(self, path="/api/latest", method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        status, headers = response.status, dict(response.getheaders())
        connection.close()
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(headers["Cache-Control"], "no-store")
        return status, headers, body

    def test_latest_missing_stale_and_atomic_recovery(self):
        code, _, body = self.get()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), self.fixture)
        os.utime(self.latest, (time.time()-10, time.time()-10))
        code, _, body = self.get()
        self.assertEqual(code, 503)
        self.assertEqual(json.loads(body)["error"], "measurement_stale")
        self.latest.unlink()
        self.assertEqual(self.get()[0], 503)
        self.fixture["frame_id"] = 2
        self.fixture["ts"] = 2.0
        self.publish()
        code, _, body = self.get()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["frame_id"], 2)

    def test_display_exact_routes_and_method(self):
        code, headers, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(body, self.display.read_bytes())
        for path in ("/config.yaml", "/../config.yaml", "/%2e%2e/config.yaml", "/api/latest?file=x", "/video.mjpg"):
            self.assertEqual(self.get(path)[0], 404, path)
        self.assertEqual(self.get(method="POST")[0], 405)

    def test_preview_is_separate_bounded_fresh_and_atomic(self):
        self.assertEqual(self.get('/api/frame.jpg')[0], 503)
        # Transport fixture only; actual JPEG encoding is tested separately.
        image=b'\xff\xd8explicit software transport fixture\xff\xd9'
        self.preview.write_bytes(image)
        code, headers, body=self.get('/api/frame.jpg?t=fixture')
        self.assertEqual(code,200)
        self.assertEqual(headers['Content-Type'],'image/jpeg')
        self.assertEqual(body,image)
        self.assertEqual(json.loads(self.get()[2]),self.fixture)
        os.utime(self.preview,(time.time()-10,time.time()-10))
        code, _, body=self.get('/api/frame.jpg')
        self.assertEqual(code,503)
        self.assertEqual(json.loads(body)['error'],'preview_stale')
        temp=self.preview.with_suffix('.tmp')
        temp.write_bytes(image+b'incomplete')
        os.replace(temp,self.preview)
        self.assertEqual(self.get('/api/frame.jpg')[0],503)
        self.preview.write_bytes(image)
        self.assertEqual(self.get('/api/frame.jpg')[0],200)
        self.assertEqual(self.get('/api/frame.jpg/other')[0],404)
        self.assertEqual(self.get('/api/frame.jpgx')[0],404)

    def test_static_dashboard_assets_and_path_rejection(self):
        code,headers,body=self.get('/app.js')
        self.assertEqual(code,200)
        self.assertIn('javascript',headers['Content-Type'])
        self.assertEqual(body,(self.assets/'app.js').read_bytes())
        for path in ['/../app.js','/%2e%2e/app.js','/app.js?file=other','/missing.js','/config.yaml']:
            self.assertEqual(self.get(path)[0],404,path)

    def test_malformed_and_partial_files_are_never_served(self):
        for raw in ('{"ts":1}', json.dumps(self.fixture)[:-1], json.dumps(self.fixture)+" garbage",
                    json.dumps(self.fixture).replace('"ts": 1.0', '"ts": NaN'),
                    json.dumps(self.fixture).replace('"ts": 1.0', '"ts": 1e999'),
                    json.dumps(self.fixture).replace('"ts": 1.0', '"ts": 1, "ts": 2')):
            self.publish(raw)
            self.assertEqual(self.get()[0], 503, raw)
        self.publish()
        self.assertEqual(self.get()[0], 200)

    def test_bounded_headers_timeout_and_disconnect(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=4) as sock:
            sock.sendall(b"GET /api/latest HTTP/1.1\r\nX: " + b"x"*8200)
            self.assertIn(b"431", sock.recv(512))
        with socket.create_connection(("127.0.0.1", self.port), timeout=4) as sock:
            sock.sendall(b"GET /api/latest HTTP/1.1\r\n")
            started = time.monotonic()
            self.assertIn(b"408", sock.recv(512))
            self.assertLess(time.monotonic()-started, 3.5)
        with socket.create_connection(("127.0.0.1", self.port), timeout=4) as sock:
            sock.sendall(b"GET /api/latest HTTP/1.1\r\n\r\n")
        self.publish()
        self.assertEqual(self.get()[0], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
