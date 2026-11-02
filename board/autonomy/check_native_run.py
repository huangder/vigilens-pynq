"""Decode a local serial metrics/log archive and verify a native board run.

Archive contains only contracts.jsonl, camera.log, latest.json (no images).
This validates execution/provenance and contracts, not fatigue accuracy.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter
import io
import json
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.contract import validate_frame


def check(transcript: Path, output: Path, manifest: Path | None = None):
    text = transcript.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^NATIVE_PAYLOAD_BEGIN\r?\n(.*?)^NATIVE_PAYLOAD_END\r?$", text)
    if not match:
        raise ValueError("serial payload markers absent")
    encoded = "".join(line.strip() for line in match[1].splitlines()
                      if re.fullmatch(r"[A-Za-z0-9+/]{4,}={0,2}", line.strip()))
    payload = base64.b64decode(encoded, validate=True)
    output.mkdir(parents=True, exist_ok=True)
    permitted = {"contracts.jsonl", "camera.log", "latest.json"}
    found = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive:
            if member.name not in permitted or not member.isfile() or member.size > 16*1024*1024:
                raise ValueError("unexpected archive member")
            (output / member.name).write_bytes(archive.extractfile(member).read())
            found.add(member.name)
    if found != permitted:
        raise ValueError("incomplete board archive")
    rows = [json.loads(line) for line in (output/"contracts.jsonl").read_text(encoding="utf-8").splitlines()]
    if not rows or any(validate_frame(row) for row in rows):
        raise ValueError("missing/invalid contract frames")
    if any(a["frame_id"] >= b["frame_id"] or a["ts"] >= b["ts"] for a,b in zip(rows, rows[1:])):
        raise ValueError("nonmonotonic contract frames")
    log = (output/"camera.log").read_text(encoding="utf-8")
    def fields(marker):
        lines = [line for line in log.splitlines() if line.startswith(marker+" ")]
        if len(lines) != 1:
            raise ValueError(f"expected one {marker}")
        return dict(re.findall(r"(\w+)=([^\s]+)",lines[0]))
    stats, audit, sample = fields("AUTONOMY_SUMMARY"),fields("AUDIT_SUMMARY"),fields("SAMPLE_RESULT")
    if int(stats["succeeded"]) != len(rows) or int(stats["failed"]) or int(audit["failures"]):
        raise ValueError("native count/hard failure/audit mismatch")
    if int(stats["processed"]) != int(stats["accepted"]):
        raise ValueError("queued frames did not drain")
    if "CAMERA_PL_PASS" not in log:
        raise ValueError("camera/PL completion marker missing")
    summary = {"kind":"native_board_camera_vision_contract", "hardware_test":True,
               "contract_frames":len(rows), "face_frames":sum(row["face"]["visible"]>0 for row in rows),
               "status_counts":dict(Counter(row["status"] for row in rows)),
               "contract_valid":True,"strictly_monotonic":True,
               "autonomy":stats,"pl_audit":audit,"camera_sampling":sample,
               "measurement_on_pc":False,"accuracy_verified":False,
               "full_30fps_verified":False,"cold_boot_verified":False}
    if manifest:
        summary["deployment_manifest_sha256"] = __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
    (output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--manifest",type=Path)
    args=parser.parse_args()
    print(json.dumps(check(args.transcript,args.output,args.manifest),ensure_ascii=False,indent=2))
