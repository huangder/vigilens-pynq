
-- =============================================================================
--  VigiLens 年龄分层疲劳数据库 · 结构定义（fatigue-db-v1）
--  生成源：backend/fatigue_db.py::schema_sql()  —— **不要手改本文件**
--  改结构：改 Python 里的常量 → 跑 `python backend/fatigue_db.py --emit-schema`
--  ⚠️ 本文件由 `metrics/scripts/check_age_sidecar.py` 的 T8 做**语义等价检查**
--     （把两个库里 sqlite_master 的建表语句逐条比对），所以手改这里会立刻红。
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
  session_id TEXT,
  frame_id INTEGER,
  ts REAL,
  face_visible REAL,
  bbox_x INTEGER,
  bbox_y INTEGER,
  bbox_w INTEGER,
  bbox_h INTEGER,
  yaw REAL,
  pitch REAL,
  roll REAL,
  ear_left REAL,
  ear_right REAL,
  blink_state TEXT,
  blink_count INTEGER,
  blink_rate_per_min REAL,
  perclos REAL,
  long_close_count INTEGER,
  mar REAL,
  yawn_count INTEGER,
  hr_bpm REAL,
  hr_conf REAL,
  rr_per_min REAL,
  rr_conf REAL,
  light_score REAL,
  motion_score REAL,
  quality_overall REAL,
  status TEXT,
  advice TEXT,
  reason TEXT
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
  contributions TEXT,       -- JSON: [{metric,value,baseline,z,weight,log_odds}]
  interactions TEXT,        -- JSON: [{rule,gain,why}]
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
