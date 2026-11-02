"""Replay local saved packets through POS and green on identical observations.

No upload, image export, labels or human HR accuracy claim. A read-only wrapper
records RGB ROI sums and runs the scalar baseline beside production POS. Source
timestamps are preserved; processing speed is NOT a live FPS measurement.
"""
from __future__ import annotations
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import run_pipeline as pipeline
from backend.config import load_config
from backend.vital import GreenRppg
from metrics.scripts.human_session import sha256


def compare(directory: Path, output: Path) -> int:
    cfg = load_config()
    capture = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
    wire = directory / "private.vlp"
    if not capture["complete"] or sha256(wire) != capture["wire_sha256"]:
        raise ValueError("capture incomplete or changed")
    output.relative_to((ROOT / "metrics/logs").resolve())
    output.mkdir(parents=True, exist_ok=False)
    config_hash = sha256(ROOT / "config.yaml")
    fingerprint = {str(p.relative_to(ROOT)): sha256(p) for p in (ROOT / "backend").rglob("*.py")
                   if "tests" not in p.parts and "__pycache__" not in p.parts}
    (output / "config.snapshot.yaml").write_bytes((ROOT / "config.yaml").read_bytes())
    baseline = GreenRppg.from_config(cfg)
    original_update, original_roi = pipeline.update_rppg, pipeline.roi_channel_sum
    context = {}
    frame_count = 0
    production = None
    errors = []
    with (output / "features.jsonl").open("w", encoding="utf-8") as trace:
        def roi(image, bounds, channel=1):
            total, count = original_roi(image, bounds, channel=channel)
            context[f"sum_{'bgr'[channel]}"] = total
            context["count"] = count
            return total, count

        def observe(estimator, engine, **kwargs):
            nonlocal frame_count, production
            reference = original_update(baseline, engine, **kwargs)
            context.clear()
            result = original_update(estimator, engine, **kwargs)
            production = estimator
            row = {"frame_id": kwargs["frame_id"], "ts": kwargs["ts"], **context,
                   "pos": {"result": result, "reason": estimator.last_reject,
                           "peak_fraction": estimator.last_conf, "samples": estimator.samples},
                   "green": {"result": reference, "reason": baseline.last_reject,
                             "peak_fraction": baseline.last_conf, "samples": baseline.samples}}
            trace.write(json.dumps(row) + "\n")
            frame_count += 1
            return result

        pipeline.roi_channel_sum, pipeline.update_rppg = roi, observe
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                listener.settimeout(40)
                port = listener.getsockname()[1]

                def deliver():
                    try:
                        connection, _ = listener.accept()
                        with connection, wire.open("rb") as source:
                            for block in iter(lambda: source.read(1 << 20), b""):
                                connection.sendall(block)
                    except OSError as exc:
                        errors.append(str(exc))

                worker = threading.Thread(target=deliver, daemon=True)
                worker.start()
                with (output / "pipeline.log").open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
                    rc = pipeline.main(["--source", f"board:127.0.0.1:{port}", "--rppg-method", "pos", "--width", "1280", "--height", "720",
                        "--rotate", "0", "--quiet", "--json", str(output / "last.json"),
                        "--jsonl", str(output / "stream.jsonl"), "--csv", str(output / "metrics.csv"),
                        "--summary", str(output / "summary.json")])
                worker.join(timeout=45)
                if worker.is_alive():
                    errors.append("packet delivery did not finish")
        finally:
            pipeline.roi_channel_sum, pipeline.update_rppg = original_roi, original_update
    manifest = {"wire_sha256": capture["wire_sha256"], "mode": "offline_original_packets_original_capture_timestamps",
                "real_time_performance_evidence": False, "pipeline_exit": rc, "transport_errors": errors,
                "rppg_method_override": "pos", "configured_default": cfg["rppg_method"],
                "config_sha256": config_hash, "backend_sha256": fingerprint,
                "inputs_changed_during_run": config_hash != sha256(ROOT / "config.yaml") or any(
                    sha256(ROOT / path) != value for path, value in fingerprint.items()),
                "comparison_wrapper_sha256": sha256(Path(__file__)), "frames": frame_count,
                "pos_diagnostics": production.stats() if production else None,
                "green_diagnostics": baseline.stats(), "human_hr_reference_available": False}
    (output / "replay.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"frames": frame_count, "pipeline_exit": rc, "transport_errors": errors,
                      "inputs_changed_during_run": manifest["inputs_changed_during_run"]}))
    return rc or int(bool(errors) or manifest["inputs_changed_during_run"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(compare(args.directory.resolve(), args.out.resolve()))
