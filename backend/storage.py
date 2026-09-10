"""storage.py —— JSON / CSV 落盘（《02》任务 A9）。

验收口径："JSON 字段与契约完全一致"。
因此本文件**不自己拼字段**：全部走 contract.new_frame() + contract.validate_frame()，
写出去之前先校验，坏数据直接抛异常而不是静默落盘。

产物分工：
  metrics/logs/last.json   —— 最新一帧快照（原子替换，B 线 REST 兜底读它）
  metrics/logs/stream.jsonl—— 逐帧追加，一行一帧（事后分析 / 回归对比）
  metrics/csv/metrics.csv  —— 展平表格（报告作图、黄金结果比对用）
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

try:
    from .contract import assert_valid, dumps, validate_frame
except ImportError:
    from contract import assert_valid, dumps, validate_frame

CSV_COLUMNS = [
    "ts", "frame_id",
    "face_visible", "bbox_x", "bbox_y", "bbox_w", "bbox_h", "yaw", "pitch", "roll",
    "ear_left", "ear_right", "blink_state", "blink_count", "blink_rate_per_min",
    "perclos", "long_close_count", "mar", "yawn_count",
    "hr_bpm", "hr_conf", "rr_per_min", "rr_conf",
    "light_score", "motion_score", "quality_overall",
    "status", "advice", "reason",
]


def flatten(frame: dict) -> dict[str, Any]:
    """把嵌套契约对象展平成 CSV 一行。"""
    face, beh, vit, q = frame["face"], frame["behavior"], frame["vital"], frame["quality"]
    x, y, w, h = face["bbox"]
    return {
        "ts": frame["ts"], "frame_id": frame["frame_id"],
        "face_visible": face["visible"],
        "bbox_x": x, "bbox_y": y, "bbox_w": w, "bbox_h": h,
        "yaw": face["pose"]["yaw"], "pitch": face["pose"]["pitch"], "roll": face["pose"]["roll"],
        **{k: beh[k] for k in ("ear_left", "ear_right", "blink_state", "blink_count",
                               "blink_rate_per_min", "perclos", "long_close_count", "mar", "yawn_count")},
        **{k: vit[k] for k in ("hr_bpm", "hr_conf", "rr_per_min", "rr_conf")},
        "light_score": q["light_score"], "motion_score": q["motion_score"], "quality_overall": q["overall"],
        "status": frame["status"], "advice": frame["advice"], "reason": frame["reason"],
    }


def write_snapshot(path: str | os.PathLike[str], frame: dict) -> None:
    """原子写最新一帧（先写临时文件再 replace，避免 B 线读到半个文件）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(frame, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class MetricsStorage:
    """一次运行对应一个实例；负责 jsonl + csv 的追加与收尾。"""

    def __init__(self, jsonl_path: str | os.PathLike[str] | None = None,
                 csv_path: str | os.PathLike[str] | None = None,
                 *, validate: bool = True) -> None:
        self.jsonl_path = Path(jsonl_path) if jsonl_path else None
        self.csv_path = Path(csv_path) if csv_path else None
        self.validate = validate
        self._jsonl = None
        self._csv = None
        self._writer: csv.DictWriter | None = None
        self.written = 0
        self.errors: list[str] = []

    def open(self) -> "MetricsStorage":
        if self.jsonl_path:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self._jsonl = self.jsonl_path.open("w", encoding="utf-8")
        if self.csv_path:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            self._csv = self.csv_path.open("w", encoding="utf-8", newline="")
            self._writer = csv.DictWriter(self._csv, fieldnames=CSV_COLUMNS)
            self._writer.writeheader()
        return self

    def write(self, frame: dict) -> bool:
        """校验通过才落盘；返回是否写入。"""
        if self.validate:
            errs = validate_frame(frame)
            if errs:
                self.errors.append(f"frame_id={frame.get('frame_id')}: {errs}")
                raise ValueError("拒绝写入不符合契约的帧：\n  - " + "\n  - ".join(errs))
        if self._jsonl:
            self._jsonl.write(dumps(frame) + "\n")
        if self._writer:
            self._writer.writerow(flatten(frame))
        self.written += 1
        return True

    def close(self) -> None:
        for fh in (self._jsonl, self._csv):
            if fh:
                fh.close()
        self._jsonl = self._csv = None

    def __enter__(self) -> "MetricsStorage":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()


def load_jsonl(path: str | os.PathLike[str]) -> list[dict]:
    """读回 jsonl（回归测试 / 黄金结果比对用）。"""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def check_all(frames: Iterable[dict]) -> list[str]:
    """批量校验，返回所有错误（用于 CI / 回归）。"""
    errs: list[str] = []
    for fr in frames:
        for e in validate_frame(fr):
            errs.append(f"frame_id={fr.get('frame_id')}: {e}")
    return errs


if __name__ == "__main__":  # 自检：python backend/storage.py
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parent))
    from mock import mock_frame

    tmpdir = _P(_P(__file__).resolve().parent.parent) / "metrics" / "logs"
    fr = mock_frame(0)
    with MetricsStorage(tmpdir / "_selftest.jsonl", tmpdir / "_selftest.csv") as st:
        for i in range(3):
            st.write(mock_frame(i))
        print(f"写入 {st.written} 帧 → {st.jsonl_path}")
    assert_valid(fr)
    print("契约校验：通过")
    print("展平后列数：", len(flatten(fr)), "（CSV_COLUMNS =", len(CSV_COLUMNS), "）")
