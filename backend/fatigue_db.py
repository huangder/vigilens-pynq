"""fatigue_db.py —— 年龄分层疲劳数据库（SQLite，标准库 `sqlite3`，零新依赖）。

这个库要回答两类问题，两类问题的**数据来源完全不同**，因此在本文件里也完全分开：

  A. "这台设备**测到了什么**" —— `sessions` / `frames` / `comparisons` 三张表。
     数据只能来自**真实运行**（`run_pipeline.py` 的落盘产物或 API ingest），
     绝不能手工编造。若当次运行的关键点来自 `StubLandmarker`，整段会话被标
     `provenance='stub'`，**默认不参与任何人群统计**（见 `include_stub` 参数）。

  B. "同龄人的**典型范围**是多少" —— `reference_norms` 表。
     数据只能来自**公开研究/开源项目**，每条都必须带 `source`（URL 或文献）与
     `confidence`（【已验证】/【推测】/【不确定】）。凡没核实到的一律**不填数字**，
     只留 `null` + 【不确定】。宁可比对时说"该指标没有可用基线"，也不许编一个。

   —— 这条纪律是《05》铁律 1（数据真实性）在本模块的具体化。

隐私（《05》铁律 4）：
  · `subjects.signature` 存的是**不可逆的降采样签名**（见 age_infer.FaceSignature），
    不是人脸照片；库本体 `data/fatigue_db/*.sqlite3` 在 `.gitignore` 中，**不入库**；
  · 入库的是 `data/fatigue_db/schema.sql`（结构）与 `data/reference/age_bands.json`
    （带出处的基线），二者不含任何个体数据；
  · 不做诊断、不做健康评分，只服务于"疲劳趋势 / 辅助提示 / 实验室原型"（铁律 5）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    from .config import REPO_ROOT, load_config
except ImportError:  # 直接以脚本方式运行
    from config import REPO_ROOT, load_config

SCHEMA_VERSION = "fatigue-db-v1"

# 逐帧测量的**全部**字段（用户要求"包含所有测量出来的相关疲劳数据"）。
# 与 `backend/storage.py::CSV_COLUMNS` 保持同一套语义 —— 那边是导出表，这边是查询库。
# ⚠️ 加字段必须同时改三处：`schema.sql`、本常量、`add_frame()` 的绑定顺序。
FRAME_COLUMNS: tuple[str, ...] = (
    "session_id", "frame_id", "ts",
    "face_visible", "bbox_x", "bbox_y", "bbox_w", "bbox_h",
    "yaw", "pitch", "roll",
    "ear_left", "ear_right", "blink_state", "blink_count", "blink_rate_per_min",
    "perclos", "long_close_count", "mar", "yawn_count",
    "hr_bpm", "hr_conf", "rr_per_min", "rr_conf",
    "light_score", "motion_score", "quality_overall",
    "status", "advice", "reason",
)

SESSION_COLUMNS: tuple[str, ...] = (
    "session_id", "started_at", "ended_at", "frames", "fps",
    "subject", "age_years", "age_band", "age_source", "confirmed_by",
    "engine", "engine_placeholder",
    "landmark_source", "frame_source", "provenance",
    "reference_version",
    "perclos_mean", "blink_rate_mean", "long_close_sum", "yawn_sum",
    "quality_mean", "risk_mean", "risk_level_last", "status_counts",
    "logical_duration_s", "notes",
)


def schema_sql() -> str:
    """建表 SQL。**与 `data/fatigue_db/schema.sql` 必须逐字一致**。

    为什么在 Python 里再存一份：干净机器上不该依赖"先手动跑一遍 schema.sql"
    才能用库；同时 `data/fatigue_db/schema.sql` 是**入库的**可读文件（供评审查库结构），
    两者由 `metrics/scripts/check_age_sidecar.py` 做一致性核对，防止漂移。
    """
    # 逐列给出**诚实的**SQL 类型：frame_id / bbox / 各类计数是整数，
    # 状态与文本是 TEXT，其余测量量是 REAL。SQLite 是动态类型、写 REAL 也能跑，
    # 但入库的 schema 是给人看、给评审查的 —— 类型写错会让人误以为 frame_id 是浮点。
    _TEXT = ("session_id", "blink_state", "status", "advice", "reason")
    _INT = ("frame_id", "bbox_x", "bbox_y", "bbox_w", "bbox_h",
            "blink_count", "long_close_count", "yawn_count")
    cols = ",\n  ".join(
        f"{c} " + ("TEXT" if c in _TEXT else ("INTEGER" if c in _INT else "REAL"))
        for c in FRAME_COLUMNS)
    return f"""
-- =============================================================================
--  VigiLens 年龄分层疲劳数据库 · 结构定义（fatigue-db-v1）
--  生成源：backend/fatigue_db.py::schema_sql()  —— **不要手改本文件**
--  改结构：改 Python 里的常量 → 跑 `python backend/fatigue_db.py --emit-schema`
-- =============================================================================

PRAGMA journal_mode = WAL;

-- 一次测量会话（一次 run_pipeline 运行 / 一次连续监测）
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  started_at REAL NOT NULL,
  ended_at REAL,
  frames INTEGER DEFAULT 0,
  fps REAL,
  -- 档案（来自 age_infer.AgeProfileStore；未确认时 subject/age_* 为 NULL）
  subject TEXT,
  age_years INTEGER,
  age_band TEXT,
  age_source TEXT,          -- unset/image_estimate/manual_input/enrolled_profile
  confirmed_by TEXT,        -- unset/user_manual/user_accepted_estimate
  engine TEXT,              -- none/manual/opencv_caffe/heuristic
  engine_placeholder INTEGER,
  -- 数据来源与可信度（stub = 占位几何量，**不得参与人群统计**）
  landmark_source TEXT,
  frame_source TEXT,
  provenance TEXT,          -- live | stub | unknown
  reference_version TEXT,
  -- 会话级汇总（由逐帧数据算出，便于快速查询）
  perclos_mean REAL, blink_rate_mean REAL, long_close_sum INTEGER, yawn_sum INTEGER,
  quality_mean REAL, risk_mean REAL, risk_level_last TEXT,
  status_counts TEXT,       -- JSON
  logical_duration_s REAL,
  notes TEXT
);

-- 逐帧测量（**所有**测出来的疲劳相关字段）
CREATE TABLE IF NOT EXISTS frames (
  {cols}
);

-- 逐帧的"与同龄基线比对"结果（耦合判定模型的输出，含可解释的分项贡献）
CREATE TABLE IF NOT EXISTS comparisons (
  session_id TEXT NOT NULL,
  frame_id INTEGER NOT NULL,
  band_applied TEXT,        -- 参照组 id，或 global（未分龄兜底）
  comparable INTEGER,       -- 0 = 质量门控不过，此时 risk 必须为 NULL
  risk REAL,
  risk_level TEXT,          -- low/medium/high/unknown
  deviation REAL,
  quality_gate REAL,
  contributions TEXT,       -- JSON: [{{metric,value,baseline,z,weight,log_odds}}]
  interactions TEXT,        -- JSON: [{{rule,gain,why}}]
  reason TEXT,
  reference_version TEXT,
  reference_sources TEXT,   -- JSON
  PRIMARY KEY (session_id, frame_id)
);

-- 年龄档案事件（记录功能 / 审计轨迹：推断、收录判断、人工确认、重置）
CREATE TABLE IF NOT EXISTS age_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  kind TEXT NOT NULL,
  session_id TEXT,
  detail TEXT               -- JSON
);

-- 已收录主体（**签名不是照片**：256 字节的降采样量化特征，见 age_infer.FaceSignature）
CREATE TABLE IF NOT EXISTS subjects (
  subject TEXT PRIMARY KEY,
  first_seen REAL, last_seen REAL,
  age_years INTEGER, age_band TEXT, age_source TEXT, confirmed_at REAL,
  engine TEXT,
  signature BLOB, signature_bytes INTEGER,
  sessions INTEGER DEFAULT 0,
  notes TEXT
);

-- 参照基线（**每条都必须带出处**；没核实到的一律不填数字）
CREATE TABLE IF NOT EXISTS reference_norms (
  band TEXT NOT NULL,
  metric TEXT NOT NULL,
  unit TEXT,
  low REAL, high REAL, typical REAL, scale REAL,
  source TEXT,              -- URL 或文献引用
  confidence TEXT,          -- 已验证 / 推测 / 不确定
  notes TEXT,
  checked_on TEXT,
  PRIMARY KEY (band, metric)
);

CREATE INDEX IF NOT EXISTS idx_frames_session ON frames(session_id, frame_id);
CREATE INDEX IF NOT EXISTS idx_comp_session   ON comparisons(session_id, frame_id);
CREATE INDEX IF NOT EXISTS idx_events_at      ON age_events(at);
CREATE INDEX IF NOT EXISTS idx_sessions_band  ON sessions(age_band, provenance);
"""


# ---------------------------------------------------------------------------
# 参照基线
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Baseline:
    """某个（参照组, 指标）的典型范围。`typical`/`scale` 供标准化偏离量使用。"""

    metric: str
    band: str
    unit: str | None
    low: float | None
    high: float | None
    typical: float | None
    scale: float | None
    source: str | None
    confidence: str
    notes: str | None = None

    @property
    def usable(self) -> bool:
        """只有当典型值与尺度都可算时才算"可用基线"。

        只有 `low/high` 而没给 `scale` 时仍然可用：尺度取 (high-low)/2 —— 这是
        **算术约定**（半个区间宽度当作 1 个标准差量级），会在 JSON 里如实标注。
        """
        return self.typical is not None and self.effective_scale is not None

    @property
    def effective_scale(self) -> float | None:
        if self.scale is not None and self.scale > 0:
            return float(self.scale)
        if self.high is not None and self.low is not None and self.high > self.low:
            return float(self.high - self.low) / 2.0
        return None


class ReferenceNorms:
    """`data/reference/age_bands.json` 的只读视图。"""

    def __init__(self, data: dict[str, Any], path: str | Path | None = None) -> None:
        self.raw = data
        self.path = str(path) if path else None
        self.version = str(data.get("version") or "unversioned")
        self.checked_on = data.get("checked_on")
        # `evidence` 段：**证据，不是基线**。这里放"某个研究在别的场景下测到的数字"，
        # 它们**不许**被当成基线用（场景/测量口径不同），只用来解释"为什么这样定权重"。
        # 之所以要跟 `bands` 分开存：一旦混进去，代码会心安理得地把驾驶场景的 PERCLOS
        # 当成桌面场景的基线 —— 那是最隐蔽的一类错误。
        self.evidence: dict[str, Any] = dict(data.get("evidence") or {})
        self.scenario_default = str(data.get("scenario_default") or "reading")
        self.notes: list[str] = list(data.get("notes") or [])
        self.bands: dict[str, dict[str, Baseline]] = {}
        for band, metrics in (data.get("bands") or {}).items():
            self.bands[band] = {}
            for metric, spec in (metrics or {}).items():
                self.bands[band][metric] = Baseline(
                    metric=metric, band=band,
                    unit=spec.get("unit"),
                    low=_f(spec.get("low")), high=_f(spec.get("high")),
                    typical=_f(spec.get("typical")), scale=_f(spec.get("scale")),
                    source=spec.get("source"), confidence=spec.get("confidence") or "不确定",
                    notes=spec.get("notes"),
                )

    # -- 查询 ------------------------------------------------------------
    def baseline(self, metric: str, band: str) -> Baseline | None:
        """取基线。**顺序**：精确参照组 → global 兜底 → 全库任意组。

        最后那一级是"同一个指标在任何组都没标？"的兜底查询，
        用它只为了在报告里如实写出"没有可用基线"，而不是悄悄用别的组的数字。
        """
        for key in (band, "global"):
            b = self.bands.get(key, {}).get(metric)
            if b is not None and b.usable:
                return b
        for group in self.bands.values():
            if metric in group and group[metric].usable:
                return group[metric]
        return None

    def metrics(self, band: str) -> list[str]:
        return sorted(self.bands.get(band, {}))

    def sources(self, band: str | None = None) -> list[str]:
        """本组用到的出处清单（去重、保持稳定顺序）。含 evidence 段的出处 ——
        报告里要能一眼看到"这次比对的判断依据来自哪几篇"。"""
        out: list[str] = []
        groups = [band] if band else list(self.bands)
        for g in groups:
            for b in self.bands.get(g, {}).values():
                if b.usable and b.source and b.source not in out:
                    out.append(b.source)
        for ev in self.evidence.values():
            src = ev.get("source") if isinstance(ev, dict) else None
            if src and src not in out:
                out.append(src)
        return out

    def evidence_for(self, *keys: str) -> list[dict]:
        """按 key 取证据条目（找不到就跳过，返回空列表）。"""
        out = []
        for k in keys:
            ev = self.evidence.get(k)
            if isinstance(ev, dict):
                out.append({"key": k, **ev})
        return out


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_reference(path: str | Path | None = None,
                   cfg: dict[str, Any] | None = None) -> ReferenceNorms:
    """读参照基线 JSON。文件缺失时返回**空基线**（而不是抛错）。

    为什么要容忍缺失：基线文件缺失时系统仍应能测量、能收录、能落库，
    只是"比对"一栏如实显示"没有可用基线"。这与 `vital.*` 为 null 的口径一致：
    **没有数据就明说没有，不要编一个。**
    """
    c = cfg or load_config()
    p = Path(path) if path else (REPO_ROOT / str(c.get("age_bands_path")
                                                or "data/reference/age_bands.json"))
    if not p.exists():
        return ReferenceNorms({"version": "missing", "bands": {}}, p)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"参照基线 JSON 解析失败：{p} —— {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"参照基线顶层必须是 object：{p}")
    return ReferenceNorms(data, p)


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------

class FatigueDB:
    """SQLite 封装。线程安全（一把锁 + check_same_thread=False，给 uvicorn 用）。"""

    def __init__(self, path: str | Path | None = None, *,
                 cfg: dict[str, Any] | None = None, create: bool = True) -> None:
        c = cfg or load_config()
        raw = path if path is not None else (REPO_ROOT / str(c.get("age_db_path")
                                                             or "data/fatigue_db/vigilens_fatigue.sqlite3"))
        # `:memory:` 必须原样传给 sqlite，不能被当成路径。
        self.path = str(raw)
        self._lock = threading.RLock()
        self._memory = self.path == ":memory:"
        if not self._memory:
            p = Path(self.path)
            if not create and not p.exists():
                raise FileNotFoundError(f"数据库不存在：{p}")
            p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(schema_sql())
        self.conn.commit()

    # -- 生命周期 --------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> "FatigueDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- 会话 / 帧 -------------------------------------------------------
    def start_session(self, *, session_id: str, started_at: float, fps: float | None = None,
                      subject: str | None = None, age_years: int | None = None,
                      age_band: str | None = None, age_source: str | None = None,
                      confirmed_by: str | None = None, engine: str | None = None,
                      engine_placeholder: bool | None = None,
                      landmark_source: str | None = None, frame_source: str | None = None,
                      provenance: str = "unknown", reference_version: str | None = None,
                      notes: str | None = None) -> str:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, started_at, fps, subject, age_years,"
                " age_band, age_source, confirmed_by, engine, engine_placeholder, landmark_source,"
                " frame_source, provenance, reference_version, notes, frames)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (session_id, float(started_at), fps, subject, age_years, age_band, age_source,
                 confirmed_by, engine, None if engine_placeholder is None else int(engine_placeholder),
                 landmark_source, frame_source, provenance, reference_version, notes))
            if subject:
                self.conn.execute(
                    "INSERT INTO subjects (subject, first_seen, last_seen, sessions)"
                    " VALUES (?,?,?,1)"
                    " ON CONFLICT(subject) DO UPDATE SET last_seen=excluded.last_seen,"
                    " sessions=sessions+1",
                    (subject, float(started_at), float(started_at)))
            self.conn.commit()
        return session_id

    def add_frame(self, session_id: str, frame: dict, *, commit: bool = False) -> None:
        """写入一帧**契约帧**。字段名与 `contract.py` 一致，取值只做展平不做加工。"""
        face, beh, vit, q = frame["face"], frame["behavior"], frame["vital"], frame["quality"]
        x, y, w, h = face["bbox"]
        row = (
            session_id, int(frame["frame_id"]), float(frame["ts"]),
            face["visible"], x, y, w, h,
            face["pose"]["yaw"], face["pose"]["pitch"], face["pose"]["roll"],
            beh["ear_left"], beh["ear_right"], beh["blink_state"], beh["blink_count"],
            beh["blink_rate_per_min"], beh["perclos"], beh["long_close_count"],
            beh["mar"], beh["yawn_count"],
            vit["hr_bpm"], vit["hr_conf"], vit["rr_per_min"], vit["rr_conf"],
            q["light_score"], q["motion_score"], q["overall"],
            frame["status"], frame["advice"], frame["reason"],
        )
        if len(row) != len(FRAME_COLUMNS):
            raise ValueError(f"帧字段数 {len(row)} != FRAME_COLUMNS {len(FRAME_COLUMNS)}")
        placeholders = ",".join("?" * len(FRAME_COLUMNS))
        with self._lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO frames ({','.join(FRAME_COLUMNS)}) VALUES ({placeholders})",
                row)
            if commit:
                self.conn.commit()

    def add_comparison(self, session_id: str, frame_id: int, comparison: dict) -> None:
        """写入一次比对结果。不可比（comparable=False）时也写 —— "当时测不了"本身是要留档的事实。"""
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO comparisons (session_id, frame_id, band_applied, comparable,"
                " risk, risk_level, deviation, quality_gate, contributions, interactions, reason,"
                " reference_version, reference_sources) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, int(frame_id), comparison.get("band_applied"),
                 int(bool(comparison.get("comparable"))), comparison.get("risk"),
                 comparison.get("risk_level"), comparison.get("deviation"),
                 comparison.get("quality_gate"),
                 json.dumps(comparison.get("contributions") or [], ensure_ascii=False),
                 json.dumps(comparison.get("interactions") or [], ensure_ascii=False),
                 comparison.get("reason"), comparison.get("reference_version"),
                 json.dumps(comparison.get("reference_sources") or [], ensure_ascii=False)))

    def finish_session(self, session_id: str, *, ended_at: float,
                       summary: dict | None = None) -> None:
        """收尾：把会话级汇总写回。`summary` 缺省时**从库里的逐帧数据现算**（可信度高）。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) n, AVG(perclos) p, AVG(blink_rate_per_min) b,"
                " SUM(long_close_count) lc, SUM(yawn_count) y, AVG(quality_overall) q,"
                " MAX(ts) last_ts, MIN(ts) first_ts FROM frames WHERE session_id=?",
                (session_id,)).fetchone()
            risk = self.conn.execute(
                "SELECT AVG(risk) r, COUNT(*) n FROM comparisons"
                " WHERE session_id=? AND comparable=1", (session_id,)).fetchone()
            last_level = self.conn.execute(
                "SELECT risk_level FROM comparisons WHERE session_id=?"
                " ORDER BY frame_id DESC LIMIT 1", (session_id,)).fetchone()
            s = summary or {}
            self.conn.execute(
                "UPDATE sessions SET ended_at=?, frames=?, perclos_mean=?, blink_rate_mean=?,"
                " long_close_sum=?, yawn_sum=?, quality_mean=?, risk_mean=?, risk_level_last=?,"
                " status_counts=?, logical_duration_s=?, notes=COALESCE(?, notes)"
                " WHERE session_id=?",
                (float(ended_at), int(row["n"] or 0), row["p"], row["b"], row["lc"], row["y"],
                 row["q"], risk["r"], None if not last_level else last_level["risk_level"],
                 json.dumps(s.get("status_counts") or {}, ensure_ascii=False),
                 s.get("logical_duration_s"),
                 s.get("notes"), session_id))
            self.conn.commit()

    # -- 年龄档案记录 ----------------------------------------------------
    def log_age_event(self, event: dict, *, session_id: str | None = None) -> None:
        """写一条档案事件（记录功能）。`event` 形如 {"at":..,"kind":..,"detail":{..}}。"""
        with self._lock:
            self.conn.execute(
                "INSERT INTO age_events (at, kind, session_id, detail) VALUES (?,?,?,?)",
                (float(event.get("at") or 0.0), str(event.get("kind") or "unknown"),
                 session_id, json.dumps(event.get("detail") or {}, ensure_ascii=False)))
            self.conn.commit()

    def update_session_age(self, session_id: str, *, subject: str | None = None,
                           age_years: int | None = None, age_band: str | None = None,
                           age_source: str | None = None, confirmed_by: str | None = None,
                           engine: str | None = None,
                           engine_placeholder: bool | None = None) -> None:
        """把**最终**档案写回会话行。

        为什么需要：收录与年龄确认都可能发生在测量进行中（甚至最后一秒），
        开跑时写进 `sessions` 的那份档案可能是"当时还没有"。不回头更新的话，
        库里会留下一个"没有年龄"的会话 —— 而按年龄分组的统计会因此**静默漏掉**
        它，那是最难发现的一类数据缺失。
        """
        with self._lock:
            self.conn.execute(
                "UPDATE sessions SET subject=COALESCE(?, subject),"
                " age_years=COALESCE(?, age_years), age_band=COALESCE(?, age_band),"
                " age_source=COALESCE(?, age_source), confirmed_by=COALESCE(?, confirmed_by),"
                " engine=COALESCE(?, engine),"
                " engine_placeholder=COALESCE(?, engine_placeholder) WHERE session_id=?",
                (subject, age_years, age_band, age_source, confirmed_by, engine,
                 None if engine_placeholder is None else int(engine_placeholder), session_id))
            self.conn.commit()

    def upsert_subject(self, subject: dict, *,
                       age_years: int | None = None, age_band: str | None = None,
                       age_source: str | None = None, engine: str | None = None,
                       confirmed_at: float | None = None) -> None:
        sig = subject.get("signature")
        with self._lock:
            self.conn.execute(
                "INSERT INTO subjects (subject, first_seen, last_seen, age_years, age_band,"
                " age_source, engine, signature, signature_bytes, confirmed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(subject) DO UPDATE SET last_seen=excluded.last_seen,"
                " age_years=COALESCE(excluded.age_years, age_years),"
                " age_band=COALESCE(excluded.age_band, age_band),"
                " age_source=COALESCE(excluded.age_source, age_source),"
                " engine=COALESCE(excluded.engine, engine),"
                " confirmed_at=COALESCE(excluded.confirmed_at, confirmed_at),"
                " signature=COALESCE(excluded.signature, signature)",
                (subject.get("subject"), float(subject.get("at") or 0.0),
                 float(subject.get("at") or 0.0), age_years, age_band, age_source, engine,
                 sqlite3.Binary(sig) if sig else None,
                 len(sig) if sig else None, confirmed_at))

    def load_reference_into_db(self, norms: ReferenceNorms) -> int:
        """把基线 JSON 灌进 `reference_norms` 表，让查询自带出处（可审计）。"""
        n = 0
        with self._lock:
            for band, metrics in norms.bands.items():
                for metric, b in metrics.items():
                    self.conn.execute(
                        "INSERT OR REPLACE INTO reference_norms (band, metric, unit, low, high,"
                        " typical, scale, source, confidence, notes, checked_on)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (band, metric, b.unit, b.low, b.high, b.typical, b.scale,
                         b.source, b.confidence, b.notes, norms.checked_on))
                    n += 1
            self.conn.commit()
        return n

    # -- 查询 ------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                t: int(self.conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"])
                for t in ("sessions", "frames", "comparisons", "age_events", "subjects",
                          "reference_norms")
            }

    def summary(self, *, include_stub: bool = False, recent: int = 5) -> dict:
        """给 `/api/fatigue_db/summary` 用的汇总。

        `include_stub=False`（默认）时，**占位数据（provenance=stub）不计入**
        任何人群统计 —— 这是防止"用占位几何量凑出一个漂亮的人群分布"的关键开关。
        """
        where = "" if include_stub else " WHERE provenance='live'"
        with self._lock:
            by_band = {r["age_band"] or "未定": int(r["c"]) for r in self.conn.execute(
                f"SELECT age_band, COUNT(*) c FROM sessions{where} GROUP BY age_band")}
            by_prov = {r["provenance"] or "unknown": int(r["c"]) for r in self.conn.execute(
                "SELECT provenance, COUNT(*) c FROM sessions GROUP BY provenance")}
            rows = self.conn.execute(
                f"SELECT * FROM sessions{where} ORDER BY started_at DESC LIMIT ?",
                (max(1, int(recent)),)).fetchall()
            ref = self.conn.execute(
                "SELECT COUNT(*) c, MAX(checked_on) d FROM reference_norms").fetchone()
        sessions = [dict(r) for r in rows]
        for s in sessions:
            s["status_counts"] = json.loads(s.get("status_counts") or "{}")
        return {
            "path": self.path,
            "exists": self._memory or Path(self.path).exists(),
            "schema_version": SCHEMA_VERSION,
            "counts": self.counts(),
            "by_band": by_band,
            "by_provenance": by_prov,
            "include_stub": include_stub,
            "reference_rows": int(ref["c"] or 0),
            "reference_checked_on": ref["d"],
            "recent": sessions,
        }

    def iter_frames(self, session_id: str) -> Iterator[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM frames WHERE session_id=? ORDER BY frame_id", (session_id,)).fetchall()
        for r in rows:
            yield dict(r)


# ---------------------------------------------------------------------------
# 从 A 线产物入库
# ---------------------------------------------------------------------------

def import_session(db: FatigueDB, jsonl_path: str | Path, *,
                   summary_path: str | Path | None = None,
                   session_id: str | None = None,
                   subject: str | None = None, age_years: int | None = None,
                   age_band: str | None = None, age_source: str | None = None,
                   confirmed_by: str | None = None, engine: str | None = None,
                   reference_version: str | None = None) -> dict:
    """把 `run_pipeline.py --jsonl` 的产物 + `--summary` 摘要导入数据库。

    `provenance` 的判定**完全依据 summary 的 `landmark_source`**：
      · `mediapipe` → `live`（真实关键点）
      · `stub`      → `stub`（占位几何量，不参与统计）
    这条不能靠调用方"自觉传入" —— 一旦有人把 stub 段落标成 live，
    人群基线就被污染了，而且事后无从分辨。
    """
    jp = Path(jsonl_path)
    if not jp.exists():
        raise FileNotFoundError(f"找不到 jsonl：{jp}")
    summary: dict[str, Any] = {}
    if summary_path:
        sp = Path(summary_path)
        if sp.exists():
            summary = json.loads(sp.read_text(encoding="utf-8"))
    frames = [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not frames:
        return {"ok": False, "errors": ["jsonl 里没有任何帧"], "frames": 0}

    lm = (summary.get("landmark_source") or "unknown").lower()
    provenance = {"mediapipe": "live", "stub": "stub"}.get(lm, "unknown")
    sid = session_id or f"import-{jp.stem}-{int(frames[0].get('ts') or 0)}"

    db.start_session(
        session_id=sid, started_at=float(frames[0].get("ts") or 0.0),
        fps=summary.get("logical_fps"), subject=subject, age_years=age_years,
        age_band=age_band, age_source=age_source, confirmed_by=confirmed_by,
        engine=engine, landmark_source=lm, frame_source=summary.get("frame_source"),
        provenance=provenance, reference_version=reference_version,
        notes=f"导入自 {jp.name}（pattern={summary.get('pattern')}）")
    n = 0
    for fr in frames:
        if fr.get("status") == "done":
            continue     # 收尾帧不是测量结果（与 storage.py 的口径一致）
        db.add_frame(sid, fr)
        n += 1
    db.finish_session(sid, ended_at=float(frames[-1].get("ts") or 0.0),
                      summary={"status_counts": summary.get("status_counts"),
                               "logical_duration_s": summary.get("logical_duration_s")})
    return {"ok": True, "session_id": sid, "frames": n, "provenance": provenance,
            "landmark_source": lm, "errors": []}


if __name__ == "__main__":  # 自检：python backend/fatigue_db.py [--emit-schema]
    import sys

    if "--emit-schema" in sys.argv:
        out = REPO_ROOT / "data" / "fatigue_db" / "schema.sql"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(schema_sql(), encoding="utf-8")
        print(f"已写出 {out}")
        raise SystemExit(0)

    cfg = load_config()
    norms = load_reference(cfg=cfg)
    print(f"参照基线 : {norms.path} | 版本 {norms.version} | 组 {list(norms.bands)}")
    with FatigueDB(":memory:", cfg=cfg) as db:
        norm_rows = db.load_reference_into_db(norms)
        print(f"基线入库 : {norm_rows} 行")
        db.start_session(session_id="selftest", started_at=0.0, age_band="adult",
                         provenance="live", landmark_source="stub-probe")
        from mock import mock_frame
        for i in range(3):
            db.add_frame("selftest", mock_frame(i), commit=False)
        db.finish_session("selftest", ended_at=2.0)
        print("计数     :", db.counts())
        print("汇总     :", json.dumps(db.summary(), ensure_ascii=False)[:220], "…")
