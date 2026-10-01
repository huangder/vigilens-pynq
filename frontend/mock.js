/* =============================================================================
 * mock.js —— B 线的契约一致假数据源 + 契约校验器（纯逻辑，无 DOM）
 * =============================================================================
 * 为什么要有这个文件（而不是全塞进 app.js）：
 *   1. 它是**跨语言契约检查点**：本文件产出的帧会被 backend/contract.py 校验
 *      （node frontend/mock.js --limit 6 | python tools/check_contract.py）。
 *      A 线的 Python、B 线的 JS 必须对同一份契约得出同样的结论，
 *      否则 M2 把真数据接进来时才发现字段不一致 —— 那时改就晚了。
 *   2. 浏览器直接双击 index.html 时（没有后端），前端靠它兜底演示六态。
 *
 * ⚠️ 本文件里 status→指标的映射必须与 backend/mock.py 保持同一套语义：
 *    状态写"疲劳"时数字就必须疲劳。改一边必须改另一边。
 * ========================================================================== */
(function (root) {
  "use strict";

  var STATUS_VALUES = ["normal", "fatigue_risk", "adjust_posture", "unreliable", "disconnected", "done"];
  var BLINK_STATES = ["OPEN", "CLOSING", "CLOSED", "OPENING"];
  var VITAL_KEYS = ["hr_bpm", "hr_conf", "rr_per_min", "rr_conf"];
  var TOP_KEYS = ["ts", "frame_id", "face", "behavior", "vital", "quality", "status", "advice", "reason"];
  var DEMO_SEQUENCE = STATUS_VALUES.slice();

  var STATUS_ZH = {
    normal: "正常",
    fatigue_risk: "疲劳风险升高",
    adjust_posture: "请调整姿势",
    unreliable: "信号不可靠",
    disconnected: "连接中断",
    done: "测量完成"
  };

  var ADVICE = {
    normal: ["状态正常", "各项指标均在正常范围，信号质量良好"],
    fatigue_risk: ["疲劳风险升高，建议休息 5 分钟或远眺放松", "触发：PERCLOS 偏高；长闭眼次数增加"],
    adjust_posture: ["请正对摄像头，保持面部完整出现在画面内", "人脸可见率或头部姿态超出允许范围"],
    unreliable: ["信号不可靠，暂不输出测量结果", "光照/运动导致信号质量低于门控阈值"],
    disconnected: ["连接中断，请检查视频源或后端服务", "超过 ws_disconnect_timeout_s 未收到数据帧"],
    done: ["测量完成", "本次测量正常结束，结果已归档"]
  };

  /* ---- 确定性 PRNG（mulberry32）：同 seed 必得同序列 ---- */
  function makeRng(seed) {
    var s = seed >>> 0;
    return function () {
      s = (s + 0x6d2b79f5) >>> 0;
      var t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function rngFor(seed, frameId) {
    // 与 backend/mock.py 的 _rng 同思路：每帧一个独立、可复现的随机源
    return makeRng((Math.imul(seed, 1000003) ^ Math.imul(frameId, 2654435761)) >>> 0);
  }

  function round(x, n) {
    var f = Math.pow(10, n);
    return Math.round(x * f) / f;
  }
  function clamp01(x) {
    return x < 0 ? 0 : x > 1 ? 1 : x;
  }

  /* --------------------------------------------------------------------------
   * mockFrame —— 按 status 反推指标值，保证"状态与数字自洽"
   * ------------------------------------------------------------------------ */
  function mockFrame(frameId, opts) {
    opts = opts || {};
    var seed = opts.seed === undefined ? 20260910 : opts.seed;
    var statuses = opts.statuses || DEMO_SEQUENCE;
    var status = opts.status || statuses[((frameId % statuses.length) + statuses.length) % statuses.length];
    if (STATUS_VALUES.indexOf(status) < 0) throw new Error("未知 status: " + status);

    var r = rngFor(seed, frameId);
    var adv = ADVICE[status];

    var visible = Math.min(0.999, 0.96 + (r() - 0.5) * 0.04);
    var bbox = [320 + Math.round((r() - 0.5) * 4), 180 + Math.round((r() - 0.5) * 4),
                180 + Math.round((r() - 0.5) * 6), 220 + Math.round((r() - 0.5) * 6)];
    var yaw = (r() - 0.5) * 6, pitch = (r() - 0.5) * 4, roll = (r() - 0.5) * 4;

    var earLeft = 0.27 + (r() - 0.5) * 0.04;
    var earRight = earLeft + (r() - 0.5) * 0.02;
    var blinkState = "OPEN";
    var blinkCount = 12 + Math.floor(frameId / 20);
    var blinkRate = 17 + (r() - 0.5) * 4;
    var perclos = 0.05 + r() * 0.03;
    var longClose = 0;
    var mar = 0.10 + r() * 0.05;
    var yawn = 0;
    var light = 0.82 + (r() - 0.5) * 0.10;
    var motion = 0.03 + r() * 0.03;
    var overall = 0.86 + (r() - 0.5) * 0.08;
    var vital = {};

    if (status === "fatigue_risk") {
      perclos = 0.30 + r() * 0.12;
      blinkRate = 9 + (r() - 0.5) * 4;
      longClose = 1 + Math.floor(r() * 3);
      yawn = 2 + Math.floor(r() * 3);
      earLeft = 0.20 + (r() - 0.5) * 0.05;
      earRight = earLeft + (r() - 0.5) * 0.02;
      blinkState = r() < 0.35 ? "CLOSED" : "OPEN";
    } else if (status === "adjust_posture") {
      visible = 0.35 + r() * 0.25;
      yaw = (32 + r() * 14) * (r() < 0.5 ? 1 : -1);
      pitch = (r() - 0.5) * 12;
    } else if (status === "unreliable") {
      light = 0.22 + r() * 0.22;
      motion = 0.45 + r() * 0.40;
      overall = 0.30 + r() * 0.25;
    } else if (status === "disconnected") {
      visible = 0; bbox = [0, 0, 0, 0];
      yaw = pitch = roll = 0;
      earLeft = earRight = 0;
      perclos = 0; blinkRate = 0; mar = 0;
      light = motion = overall = 0;
    } else if (status === "done") {
      perclos = 0.08; blinkRate = 16;
    }

    if ((status === "normal" || status === "fatigue_risk" || status === "done") && overall >= 0.75) {
      vital = {
        hr_bpm: round(68 + (r() - 0.5) * 10, 1),
        hr_conf: round(Math.min(0.98, 0.62 + overall * 0.35 + (r() - 0.5) * 0.1), 3),
        rr_per_min: round(15 + (r() - 0.5) * 4, 1),
        rr_conf: round(Math.min(0.95, 0.55 + overall * 0.3 + (r() - 0.5) * 0.1), 3)
      };
    }

    return {
      ts: opts.ts === undefined || opts.ts === null ? round(Date.now() / 1000, 3) : opts.ts,
      frame_id: frameId,
      face: { visible: round(visible, 3), bbox: bbox, pose: { yaw: round(yaw, 2), pitch: round(pitch, 2), roll: round(roll, 2) } },
      behavior: {
        ear_left: round(earLeft, 4), ear_right: round(earRight, 4), blink_state: blinkState,
        blink_count: blinkCount, blink_rate_per_min: round(blinkRate, 2), perclos: round(perclos, 4),
        long_close_count: longClose, mar: round(mar, 4), yawn_count: yawn
      },
      vital: {
        hr_bpm: vital.hr_bpm === undefined ? null : vital.hr_bpm,
        hr_conf: vital.hr_conf === undefined ? null : vital.hr_conf,
        rr_per_min: vital.rr_per_min === undefined ? null : vital.rr_per_min,
        rr_conf: vital.rr_conf === undefined ? null : vital.rr_conf
      },
      quality: { light_score: round(clamp01(light), 4), motion_score: round(clamp01(motion), 4), overall: round(clamp01(overall), 4) },
      status: status,
      advice: adv[0],
      reason: adv[1]
    };
  }

  /* --------------------------------------------------------------------------
   * validateFrame —— 与 backend/contract.py 同规则的校验器
   * 目的：A 线的 Python 与 B 线的 JS 对契约的判断必须一致。
   * ------------------------------------------------------------------------ */
  function sameKeys(obj, keys) {
    // 集合比较，不要写死排序后的字符串 —— 写死顺序时 rr_conf/rr_per_min 的字典序
    // 与声明序不同，会制造"合法的帧被判非法"这种最难查的假阳性（这里踩过）。
    var a = Object.keys(obj).sort();
    var b = keys.slice().sort();
    return a.length === b.length && a.every(function (k, i) { return k === b[i]; });
  }

  function validateFrame(f) {
    var e = [];
    if (typeof f !== "object" || f === null || Array.isArray(f)) return ["顶层必须是 object"];

    TOP_KEYS.forEach(function (k) { if (!(k in f)) e.push("缺少顶层字段：" + k); });
    Object.keys(f).forEach(function (k) { if (TOP_KEYS.indexOf(k) < 0) e.push("出现契约外顶层字段：" + k); });

    if ("ts" in f && typeof f.ts !== "number") e.push("ts 必须是数字");
    if ("frame_id" in f && !Number.isInteger(f.frame_id)) e.push("frame_id 必须是整数");

    var face = f.face;
    if (typeof face !== "object" || face === null) e.push("face 必须是 object");
    else {
      if (!sameKeys(face, ["visible", "bbox", "pose"])) {
        e.push("face 字段应恰为 visible/bbox/pose，实际 " + Object.keys(face).sort().join("/"));
      }
      if (typeof face.visible !== "number" || face.visible < 0 || face.visible > 1) e.push("face.visible 必须是 0~1 的数");
      if (!Array.isArray(face.bbox) || face.bbox.length !== 4 || !face.bbox.every(Number.isInteger)) {
        e.push("face.bbox 必须是 4 个整数的数组 [x, y, w, h]");
      }
      if (typeof face.pose !== "object" || face.pose === null) e.push("face.pose 必须是 object");
      else {
        if (!sameKeys(face.pose, ["yaw", "pitch", "roll"])) {
          e.push("face.pose 字段应恰为 yaw/pitch/roll，实际 " + Object.keys(face.pose).sort().join("/"));
        }
        ["yaw", "pitch", "roll"].forEach(function (k) {
          if (typeof face.pose[k] !== "number") e.push("face.pose." + k + " 必须是数字");
        });
      }
    }

    var NEED_BEH = ["ear_left", "ear_right", "blink_state", "blink_count", "blink_rate_per_min",
                    "perclos", "long_close_count", "mar", "yawn_count"];
    var beh = f.behavior;
    if (typeof beh !== "object" || beh === null) e.push("behavior 必须是 object");
    else {
      if (!sameKeys(beh, NEED_BEH)) {
        e.push("behavior 字段不符：实际 " + Object.keys(beh).sort().join("/"));
      }
      if (BLINK_STATES.indexOf(beh.blink_state) < 0) e.push("behavior.blink_state 必须是 " + BLINK_STATES.join("/"));
    }

    if (typeof f.vital !== "object" || f.vital === null) e.push("vital 必须是 object（没有数据用 null 填充，不要省略键）");
    else {
      if (!sameKeys(f.vital, VITAL_KEYS)) {
        e.push("vital 字段应恰为 " + VITAL_KEYS.join("/") + "，实际 " + Object.keys(f.vital).sort().join("/"));
      }
      ["hr_conf", "rr_conf"].forEach(function (k) {
        var v = f.vital[k];
        if (v !== null && (typeof v !== "number" || v < 0 || v > 1)) e.push("vital." + k + " 是置信度，必须在 0~1");
      });
    }

    var q = f.quality;
    if (typeof q !== "object" || q === null || !sameKeys(q, ["light_score", "motion_score", "overall"])) {
      e.push("quality 字段应恰为 light_score/motion_score/overall");
    } else {
      ["light_score", "motion_score", "overall"].forEach(function (k) {
        if (typeof q[k] !== "number" || q[k] < 0 || q[k] > 1) e.push("quality." + k + " 必须在 0~1");
      });
    }

    if (STATUS_VALUES.indexOf(f.status) < 0) e.push("status 必须是 " + STATUS_VALUES.join("/"));
    ["advice", "reason"].forEach(function (k) {
      if (typeof f[k] !== "string" || !f[k].trim()) e.push(k + " 必须是非空字符串");
    });

    return e;
  }

  /* ==========================================================================
   * 年龄档案件（age sidecar）—— 旁路载荷，**不属于**上面那 9 个顶层字段的契约帧
   * ==========================================================================
   * 唯一 schema 来源：`backend/age_contract.py`。本节是它的 JS 侧镜像：规则必须
   * **逐条对齐**，否则 A 线的 Python 与 B 线的 JS 会对同一份档案件得出不同结论
   * （与 validateFrame／contract.py 是同一条纪律，也是本项目最贵的一类事故）。
   * 枚举与字段名只在这里出现一次；改这里必须同时改 age_contract.py。
   *
   * 三条不可动摇的设计约束（照抄 age_contract.py，前端不许走样）：
   *   · 没有证据的年龄推断不许冒充结论（engine=heuristic ⇒ placeholder=true）；
   *   · 一次正式确认（confirmed && locked）之后，图像推断停用
   *     （inference_calls_after_lock 必须为 0）；
   *   · comparison.comparable=false 时**一个数字都不许有**（不报跳变数字）。
   * ======================================================================= */

  var AGE_SCHEMA = "age-v1";
  var AGE_TOP_KEYS = ["schema", "ts", "frame_id", "profile", "enrollment",
                      "estimate", "comparison", "privacy"];
  var AGE_PROFILE_KEYS = ["subject", "age_years", "band", "source", "confirmed",
                          "confirmed_by", "confirmed_at", "locked", "engine",
                          "placeholder", "inference_calls_after_lock"];
  var AGE_ENROLL_KEYS = ["state", "window_s", "observed_s", "samples", "needed",
                         "progress", "same_face", "distance", "reason"];
  var AGE_ESTIMATE_KEYS = ["band", "age_years", "age_low", "age_high", "confidence",
                           "engine", "placeholder", "cv_band", "cv_probs", "note"];
  var AGE_COMPARISON_KEYS = ["comparable", "band_applied", "risk", "risk_level", "deviation",
                             "quality_gate", "contributions", "interactions", "reason",
                             "reference_version", "reference_sources"];
  var AGE_PRIVACY_KEYS = ["stores_face_image", "signature_kind", "note"];

  /* 年龄参照组。边界**不是随便切的**（分组依据见 age_contract.py 与 config.yaml 的
     age_bands_path）。半开区间 [min, max)，与项目 ROI 的半开口径一致。 */
  var AGE_BANDS = ["child", "teen", "young_adult", "adult", "middle_aged", "older_adult"];
  var AGE_BAND_RANGES = {
    child: [6, 13], teen: [13, 18], young_adult: [18, 26],
    adult: [26, 46], middle_aged: [46, 60], older_adult: [60, null]
  };
  var AGE_BAND_ZH = {
    child: "儿童（6~12）", teen: "青少年（13~17）", young_adult: "青年（18~25）",
    adult: "成年（26~45）", middle_aged: "中年（46~59）", older_adult: "老年（60+）"
  };
  /* 没有确认年龄时的全人群兜底组。刻意不叫 unknown：它不是一个"人群"，
     只是混合基线，所以比对结果里必须如实写 band_applied=global（界面照原样显示）。 */
  var GLOBAL_BAND = "global";

  var AGE_SOURCES = ["unset", "image_estimate", "manual_input", "enrolled_profile"];
  var AGE_ENGINES = ["none", "manual", "opencv_caffe", "heuristic"];
  var ENROLL_STATES = ["idle", "observing", "enrolled", "rejected", "locked"];
  var RISK_LEVELS = ["low", "medium", "high", "unknown"];
  var CONFIRMED_BY = ["unset", "user_manual", "user_accepted_estimate"];
  /* Levi & Hassner (2015) 8 档年龄模型的原始标签（OpenCV age_net 用的就是它）。
     ⚠️ 这 8 档的边界与本文件的 AGE_BANDS **不一致**（如 25~32 跨了 26 岁界），
     因此按**档中心**归入参照组，并如实保留 cv_band 原文，绝不假装两者等价。 */
  var CV_AGE_BANDS = ["(0-2)", "(4-6)", "(8-12)", "(15-20)",
                      "(25-32)", "(38-43)", "(48-53)", "(60-100)"];
  var CV_BAND_TO_AGE_BAND = {
    "(0-2)": "child", "(4-6)": "child", "(8-12)": "child", "(15-20)": "teen",
    "(25-32)": "young_adult", "(38-43)": "adult", "(48-53)": "middle_aged",
    "(60-100)": "older_adult"
  };
  var CV_BAND_CENTER_YEARS = [1.0, 5.0, 10.0, 17.5, 28.5, 40.5, 50.5, 80.0];

  /* 耦合风险模型的常数：**与 config.yaml 的 couple_* 键一一对应**，而且它们全都是
     config.yaml 里明确标注过「未标定 · 占位值」的数。这里留一份是为了让离线演示能
     **由算术算出一个自洽的风险值**，而不是硬写一个好看的数字。 */
  var AGE_COUPLE = {
    prior_logit: -2.2,                                  // couple_prior_logit
    gain: { perclos: 1.6, long_close_count: 1.2,        // couple_gain_perclos / _long_close
            yawn_count: 1.0, blink_rate_per_min: 0.9 },  // couple_gain_yawn / _blink_low
    interaction: { "perclos×long_close": 0.7,            // couple_interaction_perclos_longclose
                   "perclos×blink_suppressed": 0.5 },    // couple_interaction_perclos_blinksupp
    clip: 3.0,                                           // couple_clip
    weight_floor: 0.15,                                  // couple_weight_floor
    risk_medium: 0.45, risk_high: 0.70                   // couple_risk_medium / _high
  };

  /* 与 age_contract.py 的 _num 同口径：数字且不是布尔。
     刻意**不排除** NaN/Infinity —— Python 的 isinstance(float) 也不排除它们，
     区间判断会兜住；两边判据必须一致，不能一边严一边松。 */
  function isNum(v) { return typeof v === "number" && !Number.isNaN(v); }
  function isInt(v) { return typeof v === "number" && Number.isInteger(v); }

  function bandForAge(ageYears) {
    if (ageYears === null || ageYears === undefined) return null;
    if (!isNum(ageYears) || ageYears < 0 || ageYears > 120) return null;
    for (var i = 0; i < AGE_BANDS.length; i++) {
      var lo = AGE_BAND_RANGES[AGE_BANDS[i]][0], hi = AGE_BAND_RANGES[AGE_BANDS[i]][1];
      if ((lo === null || ageYears >= lo) && (hi === null || ageYears < hi)) return AGE_BANDS[i];
    }
    return null;
  }

  function ageEmptyProfile() {
    return { subject: null, age_years: null, band: null, source: "unset", confirmed: false,
             confirmed_by: "unset", confirmed_at: null, locked: false, engine: "none",
             placeholder: false, inference_calls_after_lock: 0 };
  }

  function ageEmptyEnrollment(windowS, needed) {
    return { state: "idle", window_s: windowS || 0, observed_s: 0, samples: 0,
             needed: needed || 0, progress: 0, same_face: null, distance: null,
             reason: "未开始观察" };
  }

  /* engine=none + 全 null = "本次没有推断"。这与"推断失败"是两回事：
     前端必须能区分（engine=none 时不显示任何推断数值）。 */
  function ageEmptyEstimate() {
    return { band: null, age_years: null, age_low: null, age_high: null, confidence: null,
             engine: "none", placeholder: false, cv_band: null, cv_probs: null, note: null };
  }

  /* comparable=false + risk=null 是**正确输出**，不是"功能没做好"（同 vital.* 为 null 的口径）。 */
  function ageEmptyComparison(reason) {
    return { comparable: false, band_applied: null, risk: null, risk_level: "unknown",
             deviation: null, quality_gate: null, contributions: [], interactions: [],
             reason: reason || "尚未开始比对", reference_version: null, reference_sources: [] };
  }

  function ageHex(r, n) {
    var s = "";
    for (var i = 0; i < n; i++) s += "0123456789abcdef".charAt(Math.floor(r() * 16) % 16);
    return s;
  }

  /* 匿名主体标识：与年龄档案件同一条"不保存人脸原图"的隐私口径（签名哈希前缀）。
     与 frameId 无关（同一个人在同一台设备上总是同一个 id）。 */
  function ageDemoSubject(seed) {
    return "sub-" + ageHex(rngFor(seed, 424242), 12);
  }

  /* 由观测到的偏离量合成风险。**这是算术，不是实验结论**：
       logit = prior + Σ w·clip(gain_i·z_i) + Σ 耦合项,  risk = sigmoid(logit)
     z 的方向已归一到「越疲劳越大」（例如眨眼率低于基线记为正的"抑制量"）。
     全部增益都是 config.yaml 里标注过「未标定」的占位值。 */
  function ageCoupleRisk(items, interactionNames, quality) {
    var w = Math.max(AGE_COUPLE.weight_floor, Math.min(1, quality));
    var contributions = [], total = AGE_COUPLE.prior_logit, zSum = 0;
    items.forEach(function (it) {
      var logOdds = Math.max(-AGE_COUPLE.clip, Math.min(AGE_COUPLE.clip, it.gain * it.z));
      logOdds = round(logOdds * w, 4);
      total += logOdds;
      zSum += it.z;
      contributions.push({ metric: it.metric, value: it.value, baseline: it.baseline,
                           z: it.z, weight: round(w, 4), log_odds: logOdds });
    });
    var fired = [];
    interactionNames.forEach(function (name) {
      var gain = AGE_COUPLE.interaction[name];
      if (gain === undefined) return;
      fired.push(name);
      total += gain * w;
    });
    var risk = 1 / (1 + Math.exp(-total));
    if (!isFinite(risk)) risk = 0.5;                       // 只可能出现在极端 logit 上，兜底不留 NaN
    risk = Math.min(0.999, Math.max(0.001, round(risk, 4)));
    return {
      contributions: contributions, interactions: fired, risk: risk,
      risk_level: risk >= AGE_COUPLE.risk_high ? "high"
                : risk >= AGE_COUPLE.risk_medium ? "medium" : "low",
      deviation: items.length ? round(zSum / items.length, 4) : null
    };
  }

  /* 演示生命周期阈值：**这是离线演示的状态机**，与契约无关 —— 改它不会影响任何
     校验规则，只会改变"第几帧演示到哪一步"。六个状态各取一帧进自检。 */
  var AGE_LIFECYCLE = {
    observing_from: 2,    // [2, 10)  ：未确定年龄，正在观察是否收录（进度随时间增长）
    rejected_from: 10,    // [10, 13) ：窗口结束但有效样本不足 → 不收录
    enrolled_from: 13,    // [13, 17) ：重新观察后样本充足 → 已收录这张脸
    estimate_from: 17,    // [17, 23) ：opencv_caffe 提出年龄建议 + 同龄基线比对（global 兜底）
    confirmed_from: 23,   // [23, ∞)  ：用户手动确认 → 已确认并锁定，图像年龄推测停用
    cycle_frames: 34      // 离线面板按这个周期循环播放，便于反复演示
  };

  /* --------------------------------------------------------------------------
   * mockAgeState —— 确定性的年龄档案件演示器（离线面板用）
   * --------------------------------------------------------------------------
   * ts 默认取**逻辑时间**（= frameId，1 Hz 演示）：同一 frameId + seed 必得同一份
   * JSON，符合项目的确定性纪律。需要墙钟时间的调用方（离线面板）显式传 opts.ts。
   * ⚠️ 这里所有数值都是**演示值**，不是真实推断结果；参照基线与耦合增益全部是
   *    config.yaml 里标注过「未标定 · 占位值」的数。界面必须把它标成"离线演示"。
   * ------------------------------------------------------------------------ */
  function mockAgeState(frameId, opts) {
    opts = opts || {};
    var seed = opts.seed === undefined ? 20260910 : opts.seed;
    var fid = Math.trunc(Number(frameId));
    if (!isFinite(fid) || fid < 0) throw new Error("mockAgeState 的 frameId 必须是非负整数");
    var ts = (opts.ts === undefined || opts.ts === null) ? round(fid, 3) : opts.ts;
    var r = rngFor(seed, fid + 900001);
    var L = AGE_LIFECYCLE;
    var subject = ageDemoSubject(seed);

    var profile = ageEmptyProfile();
    // 观察窗口与样本下限取自 config.yaml（age_enroll_window_s / age_enroll_min_samples）
    var enrollment = ageEmptyEnrollment(20.0, 12);
    var estimate = ageEmptyEstimate();
    var comparison = ageEmptyComparison("尚未开始比对（年龄未确定且无有效样本）");

    var phase = fid < L.observing_from ? "idle"
              : fid < L.rejected_from ? "observing"
              : fid < L.enrolled_from ? "rejected"
              : fid < L.estimate_from ? "enrolled"
              : fid < L.confirmed_from ? "estimate" : "confirmed";

    /* 收录判断在演示里被推进到"已收录"之后共用的那段（enrolled / estimate 两相同） */
    function enrolledBlock() {
      enrollment.state = "enrolled";
      enrollment.observed_s = 20.0;
      enrollment.samples = 12;
      enrollment.progress = 1.0;
      enrollment.same_face = true;
      enrollment.distance = round(0.02 + r() * 0.03, 4);
      enrollment.reason = "已收录（窗口到期自动判定，有效样本 12/12）";
    }

    /* 与 frameId 有关的那份"疲劳证据"，两个阶段共用（confirmed 阶段整体打折：
       有了同龄基线，同一个人的偏离量本来就更小）。 */
    function evidenceItems(scale) {
      return [
        { metric: "perclos", value: round(0.30 + r() * 0.06, 4), baseline: 0.16,
          z: round((0.4 + r() * 0.5) * scale, 3), gain: AGE_COUPLE.gain.perclos },
        { metric: "long_close_count", value: 2, baseline: 0.5,
          z: round((0.15 + r() * 0.3) * scale, 3), gain: AGE_COUPLE.gain.long_close_count },
        { metric: "yawn_count", value: 1, baseline: 0.4,
          z: round((-0.1 + r() * 0.3) * scale, 3), gain: AGE_COUPLE.gain.yawn_count },
        // 眨眼率**越低越疲劳** ⇒ z 记为正的"抑制量"（方向已归一）
        { metric: "blink_rate_per_min", value: round(9.0 + r() * 2.5, 2), baseline: 17.0,
          z: round((0.5 + r() * 0.4) * scale, 3), gain: AGE_COUPLE.gain.blink_rate_per_min }
      ];
    }

    if (phase === "idle") {
      enrollment.reason = "未开始观察（本帧没有可用的人脸裁剪或质量不达标）";

    } else if (phase === "observing") {
      // 1 Hz 演示：每帧推进 1 s ⇒ [2, 10) 区间内观测到 1.0 ~ 8.0 s
      var observed = round(Math.min(20.0, (fid - L.observing_from + 1) * 1.0), 3);
      var samples = Math.min(12, Math.floor(observed * 0.625));
      enrollment.state = "observing";
      enrollment.observed_s = observed;
      enrollment.samples = samples;
      enrollment.progress = round(Math.min(1, Math.max(observed / 20.0, samples / 12)), 4);
      enrollment.same_face = true;
      enrollment.distance = round(0.02 + r() * 0.04, 4);
      enrollment.reason = "正在判断是否收录人脸：" + observed.toFixed(1) + "/20.0 s，有效样本 " +
                          samples + "/12（质量不足的帧不计入观察）";

    } else if (phase === "rejected") {
      // "不收录"是**如实的结论**，不是错误：窗口到期而有效样本不够。
      enrollment.state = "rejected";
      enrollment.observed_s = 20.0;
      enrollment.samples = 4;
      enrollment.progress = 0.0;
      enrollment.same_face = true;
      enrollment.distance = round(0.02 + r() * 0.04, 4);
      enrollment.reason = "不收录（有效样本 4/12，观察 20.0/20.0 s：样本不足）";

    } else if (phase === "enrolled") {
      profile.subject = subject;
      profile.source = "enrolled_profile";
      enrolledBlock();

    } else if (phase === "estimate") {
      profile.subject = subject;
      profile.source = "enrolled_profile";
      enrolledBlock();

      /* ---- 图像年龄推断（仅建议）----
         概率向量峰值固定在 (25-32) 档，这样 cv_band / band / 点估计三者自洽；
         点估计与区间用与 age_infer.py 完全相同的**算术折算**（Σ p·档中心 与
         累计概率 ≥0.6 的档并集），不是另编一个数字。 */
      var probs = [0.02, 0.02, 0.04, 0.10, 0.52, 0.16, 0.09, 0.05].map(function (p) {
        return round(Math.max(0.001, p + (r() - 0.5) * 0.03), 6);
      });
      var top = 0, i;
      for (i = 1; i < probs.length; i++) if (probs[i] > probs[top]) top = i;
      var cvBand = CV_AGE_BANDS[top];
      var point = 0;
      for (i = 0; i < probs.length; i++) point += probs[i] * CV_BAND_CENTER_YEARS[i];
      var order = probs.map(function (p, k) { return k; })
                       .sort(function (a2, b2) { return probs[b2] - probs[a2]; });
      var covered = 0, lowIdx = top, highIdx = top;
      for (i = 0; i < order.length; i++) {
        covered += probs[order[i]];
        if (order[i] < lowIdx) lowIdx = order[i];
        if (order[i] > highIdx) highIdx = order[i];
        if (covered >= 0.60) break;
      }
      estimate = {
        band: CV_BAND_TO_AGE_BAND[cvBand],
        age_years: round(point, 1),
        age_low: CV_BAND_CENTER_YEARS[lowIdx],
        age_high: CV_BAND_CENTER_YEARS[highIdx],
        confidence: probs[top],
        engine: "opencv_caffe",
        placeholder: false,
        cv_band: cvBand,
        cv_probs: probs,
        note: "Levi & Hassner (2015) 8 档分类模型（OpenCV age_net）。公开数据集指标不等于本项目精度，"
            + "PS 侧单帧耗时未测；输出仅作建议，须由用户确认后才用于选择参照组。"
      };

      var c1 = ageCoupleRisk(evidenceItems(1.0),
                             ["perclos×long_close", "perclos×blink_suppressed"], 0.88);
      comparison = {
        comparable: true, band_applied: GLOBAL_BAND, risk: c1.risk, risk_level: c1.risk_level,
        deviation: c1.deviation, quality_gate: 0.88,
        contributions: c1.contributions, interactions: c1.interactions,
        reason: "年龄未确认 → 用全人群兜底组 global 比对（这**不是**同龄基线，必须如实标注）。"
              + "触发 " + c1.interactions.length + " 项耦合证据；证据强度已按质量 0.88 收缩。"
              + "⚠ 基线与增益均为 config.yaml 标注的占位值（未标定），离线演示数据。",
        reference_version: "demo-placeholder",
        reference_sources: ["离线演示：仓库内尚无 data/reference/age_bands.json（未入库、未核实），"
                          + "本条基线不是实测人群基线"]
      };

    } else {  // confirmed：用户手动确认年龄 → 锁定 + 停用图像推测
      profile = {
        subject: subject, age_years: 31, band: bandForAge(31), source: "manual_input",
        confirmed: true, confirmed_by: "user_manual", confirmed_at: ts,
        locked: true, engine: "manual", placeholder: false, inference_calls_after_lock: 0
      };
      enrollment.state = "locked";
      enrollment.observed_s = 20.0;
      enrollment.samples = 12;
      enrollment.progress = 1.0;
      enrollment.same_face = true;
      enrollment.distance = round(0.02 + r() * 0.03, 4);
      enrollment.reason = "年龄已确认，已停止收录判断";
      // confirmed && locked ⇒ estimate 必须是 engine=none 且**不带任何推断数值**
      estimate = ageEmptyEstimate();

      var c2 = ageCoupleRisk(evidenceItems(0.7),
                             ["perclos×long_close", "perclos×blink_suppressed"], 0.90);
      comparison = {
        comparable: true, band_applied: profile.band, risk: c2.risk, risk_level: c2.risk_level,
        deviation: c2.deviation, quality_gate: 0.90,
        contributions: c2.contributions, interactions: c2.interactions,
        reason: "按已确认的 31 岁 → 参照组 " + profile.band + "（成年 26~45）比对；"
              + "同一批证据在同龄基线下偏离量比 global 兜底更小。"
              + "⚠ 基线与增益均为 config.yaml 标注的占位值（未标定），离线演示数据。",
        reference_version: "demo-placeholder",
        reference_sources: ["离线演示：仓库内尚无 data/reference/age_bands.json（未入库、未核实），"
                          + "本条基线不是实测人群基线"]
      };
    }

    return {
      schema: AGE_SCHEMA,
      ts: ts,
      frame_id: fid,
      profile: profile,
      enrollment: enrollment,
      estimate: estimate,
      comparison: comparison,
      privacy: {
        stores_face_image: false,
        signature_kind: "gray16x16-int8",
        note: "档案只保存不可逆的降采样签名，不保存人脸原图，也不外传（《05》铁律 4）"
      }
    };
  }

  /* --------------------------------------------------------------------------
   * mockAgeEvents —— 与 mockAgeState 同一条生命周期的确定性事件流
   * --------------------------------------------------------------------------
   * kind 取值来自 backend/age_infer.py::EVENT_KINDS（前端按它分组显示）。
   * at 用"当前 ts 往回推 (fid - 事件帧)"，因此 ts 传逻辑时间时 at 也是逻辑时间。
   * ⚠️ 离线演示事件，不是真实运行记录 —— 界面必须标注。
   * ------------------------------------------------------------------------ */
  function mockAgeEvents(frameId, opts) {
    opts = opts || {};
    var seed = opts.seed === undefined ? 20260910 : opts.seed;
    var fid = Math.trunc(Number(frameId));
    if (!isFinite(fid) || fid < 0) throw new Error("mockAgeEvents 的 frameId 必须是非负整数");
    var ts = (opts.ts === undefined || opts.ts === null) ? round(fid, 3) : opts.ts;
    var L = AGE_LIFECYCLE;
    var sub = ageDemoSubject(seed);
    // 事件里的数字直接取自生成器，避免出现"事件说 28.5 岁、卡片说 27.9 岁"这种自相矛盾
    var snapEst = mockAgeState(L.estimate_from, { seed: seed, ts: ts });
    var snapConf = mockAgeState(L.confirmed_from, { seed: seed, ts: ts });

    var plan = [
      { frame: L.observing_from, kind: "enroll_observe_start",
        detail: { subject_guess: sub, window_s: 20.0, needed: 12 } },
      { frame: L.rejected_from, kind: "enroll_decided",
        detail: { accept: false, automatic: true, samples: 4, needed: 12 } },
      { frame: L.enrolled_from, kind: "enroll_observe_start",
        detail: { subject_guess: sub, window_s: 20.0, needed: 12 } },
      { frame: L.enrolled_from, kind: "enroll_decided",
        detail: { accept: true, automatic: true, subject: sub, samples: 12, signature_bytes: 256 } },
      { frame: L.estimate_from, kind: "estimate_proposed",
        detail: { engine: snapEst.estimate.engine, placeholder: snapEst.estimate.placeholder,
                  band: snapEst.estimate.band, age_years: snapEst.estimate.age_years,
                  confidence: snapEst.estimate.confidence } },
      { frame: L.confirmed_from, kind: "manual_confirmed",
        detail: { age_years: snapConf.profile.age_years, band: snapConf.profile.band,
                  locked: snapConf.profile.locked } }
    ];

    var out = [];
    plan.forEach(function (p) {
      if (p.frame > fid) return;
      out.push({ at: round(ts - (fid - p.frame) * 1.0, 3), kind: p.kind, detail: p.detail });
    });
    return out;
  }

  /* --------------------------------------------------------------------------
   * validateAgeState —— 与 backend/age_contract.py::validate_age_state 同规则的校验器
   * 返回错误列表，空列表 = 通过。**判据必须与 Python 完全一致**：
   * JS 认为非法而 Python 认为合法（或反过来）都等于集成时返工。
   * ------------------------------------------------------------------------ */
  function keysOnlyIn(obj, keys) {
    return Object.keys(obj).filter(function (k) { return keys.indexOf(k) < 0; }).sort().join("/");
  }

  function validateAgeState(a) {
    var e = [];
    if (typeof a !== "object" || a === null || Array.isArray(a)) return ["顶层必须是 object"];

    var missing = AGE_TOP_KEYS.filter(function (k) { return !(k in a); });
    if (missing.length) e.push("缺少顶层字段：" + missing.join("/"));
    var extra = keysOnlyIn(a, AGE_TOP_KEYS);
    if (extra) e.push("出现 schema 外顶层字段：" + extra + "（schema 不改就不许加字段）");

    if (a.schema !== AGE_SCHEMA) e.push("schema 必须是 " + AGE_SCHEMA + "，实际 " + a.schema);
    if (!isNum(a.ts)) e.push("ts 必须是数字");
    if (!isInt(a.frame_id)) e.push("frame_id 必须是整数");

    /* ---- profile ---- */
    var p = a.profile;
    if (typeof p !== "object" || p === null || Array.isArray(p)) {
      e.push("profile 必须是 object");
    } else if (!sameKeys(p, AGE_PROFILE_KEYS)) {
      e.push("profile 字段不符：缺少 " + keysOnlyIn(AGE_PROFILE_KEYS, Object.keys(p)) +
             "，多出 " + keysOnlyIn(p, AGE_PROFILE_KEYS));
    } else {
      if (AGE_SOURCES.indexOf(p.source) < 0) e.push("profile.source 必须是 " + AGE_SOURCES.join("/") + " 之一");
      if (AGE_ENGINES.indexOf(p.engine) < 0) e.push("profile.engine 必须是 " + AGE_ENGINES.join("/") + " 之一");
      ["confirmed", "locked", "placeholder"].forEach(function (k) {
        if (typeof p[k] !== "boolean") e.push("profile." + k + " 必须是布尔");
      });
      if (!isInt(p.inference_calls_after_lock) || p.inference_calls_after_lock < 0) {
        e.push("profile.inference_calls_after_lock 必须是非负整数");
      }
      // ⚠️ 本文件最重要的一条业务规则，写成断言而不是注释
      if (p.confirmed && p.locked && p.inference_calls_after_lock > 0) {
        e.push("年龄已确认并锁定，但 inference_calls_after_lock > 0（违反「确认后停用年龄推测」的承诺）");
      }
      if (p.age_years !== null && (!isNum(p.age_years) || p.age_years < 0 || p.age_years > 120)) {
        e.push("profile.age_years 必须在 0~120");
      }
      if (p.band !== null && AGE_BANDS.indexOf(p.band) < 0) {
        e.push("profile.band 必须是 " + AGE_BANDS.join("/") + " 之一");
      }
      if (CONFIRMED_BY.indexOf(p.confirmed_by) < 0) {
        e.push("profile.confirmed_by 必须是 " + CONFIRMED_BY.join("/") + " 之一");
      }
      if (p.confirmed) {
        if (p.age_years === null) e.push("profile.confirmed=True 时必须有 age_years");
        if (p.source === "unset") e.push("profile.confirmed=True 时 source 不能是 unset");
        if (p.confirmed_at === null || !isNum(p.confirmed_at)) {
          e.push("profile.confirmed=True 时必须有 confirmed_at 时间戳");
        }
        if (p.confirmed_by === "unset") {
          e.push("profile.confirmed=True 时 confirmed_by 必须是 user_manual / user_accepted_estimate");
        }
      } else {
        if (p.confirmed_by !== "unset") e.push("profile.confirmed=False 时 confirmed_by 必须是 unset");
        if (p.locked) e.push("profile.confirmed=False 时 locked 必须为 False");
      }
      // "这个数字是人填的还是模型猜的"必须可审计 —— 接受推断要如实标注
      if (p.confirmed && p.source === "image_estimate" && p.confirmed_by !== "user_accepted_estimate") {
        e.push("source=image_estimate 且 confirmed=True 时，confirmed_by 必须是 user_accepted_estimate");
      }
      if (p.subject !== null && typeof p.subject !== "string") {
        e.push("profile.subject 必须是字符串或 null");
      }
    }

    /* ---- enrollment ---- */
    var en = a.enrollment;
    if (typeof en !== "object" || en === null || Array.isArray(en)) {
      e.push("enrollment 必须是 object");
    } else if (!sameKeys(en, AGE_ENROLL_KEYS)) {
      e.push("enrollment 字段不符：缺少 " + keysOnlyIn(AGE_ENROLL_KEYS, Object.keys(en)) +
             "，多出 " + keysOnlyIn(en, AGE_ENROLL_KEYS));
    } else {
      if (ENROLL_STATES.indexOf(en.state) < 0) {
        e.push("enrollment.state 必须是 " + ENROLL_STATES.join("/") + " 之一");
      }
      ["window_s", "observed_s", "progress"].forEach(function (k) {
        if (!isNum(en[k]) || en[k] < 0) e.push("enrollment." + k + " 必须是非负数字");
      });
      ["samples", "needed"].forEach(function (k) {
        if (!isInt(en[k]) || en[k] < 0) e.push("enrollment." + k + " 必须是非负整数");
      });
      if (!(en.progress >= 0 && en.progress <= 1)) e.push("enrollment.progress 必须在 0~1");
      if (en.same_face !== null && typeof en.same_face !== "boolean") {
        e.push("enrollment.same_face 必须是布尔或 null");
      }
      if (en.distance !== null && !isNum(en.distance)) e.push("enrollment.distance 必须是数字或 null");
      if (typeof en.reason !== "string" || !en.reason.trim()) e.push("enrollment.reason 必须是非空字符串");
    }

    /* ---- estimate ---- */
    var es = a.estimate;
    if (typeof es !== "object" || es === null || Array.isArray(es)) {
      e.push("estimate 必须是 object");
    } else if (!sameKeys(es, AGE_ESTIMATE_KEYS)) {
      e.push("estimate 字段不符：缺少 " + keysOnlyIn(AGE_ESTIMATE_KEYS, Object.keys(es)) +
             "，多出 " + keysOnlyIn(es, AGE_ESTIMATE_KEYS));
    } else {
      if (AGE_ENGINES.indexOf(es.engine) < 0) e.push("estimate.engine 必须是 " + AGE_ENGINES.join("/") + " 之一");
      if (es.band !== null && AGE_BANDS.indexOf(es.band) < 0) {
        e.push("estimate.band 必须是 " + AGE_BANDS.join("/") + " 之一");
      }
      if (es.cv_band !== null && CV_AGE_BANDS.indexOf(es.cv_band) < 0) {
        e.push("estimate.cv_band 必须是 " + CV_AGE_BANDS.join("/") + " 之一");
      }
      ["age_years", "age_low", "age_high"].forEach(function (k) {
        if (es[k] !== null && (!isNum(es[k]) || es[k] < 0 || es[k] > 120)) {
          e.push("estimate." + k + " 必须是 0~120 的数字或 null");
        }
      });
      if (es.confidence !== null && (!isNum(es.confidence) || es.confidence < 0 || es.confidence > 1)) {
        e.push("estimate.confidence 必须在 0~1");
      }
      // ⚠️ 没有推断却带数值 = 幻觉数据的温床，直接禁止
      if (es.engine === "none" && ["band", "age_years", "age_low", "age_high", "confidence"]
            .some(function (k) { return es[k] !== null; })) {
        e.push("estimate.engine=none 时不允许带任何推断数值（没有推断就是没有推断）");
      }
      // 占位引擎必须自我标注 —— 防止有人把启发式当结论引用
      if (es.engine === "heuristic" && !es.placeholder) {
        e.push("estimate.engine=heuristic 必须 placeholder=True（该引擎无任何精度证据）");
      }
      if (typeof es.placeholder !== "boolean") e.push("estimate.placeholder 必须是布尔");
      if (es.cv_probs !== null) {
        var okProbs = Array.isArray(es.cv_probs) && es.cv_probs.length === CV_AGE_BANDS.length &&
          es.cv_probs.every(function (v) { return isNum(v) && v >= 0 && v <= 1; });
        if (!okProbs) e.push("estimate.cv_probs 必须是长度 " + CV_AGE_BANDS.length + " 的 0~1 数组或 null");
      }
    }

    /* ---- comparison ---- */
    var c = a.comparison;
    if (typeof c !== "object" || c === null || Array.isArray(c)) {
      e.push("comparison 必须是 object");
    } else if (!sameKeys(c, AGE_COMPARISON_KEYS)) {
      e.push("comparison 字段不符：缺少 " + keysOnlyIn(AGE_COMPARISON_KEYS, Object.keys(c)) +
             "，多出 " + keysOnlyIn(c, AGE_COMPARISON_KEYS));
    } else {
      if (typeof c.comparable !== "boolean") e.push("comparison.comparable 必须是布尔");
      if (RISK_LEVELS.indexOf(c.risk_level) < 0) {
        e.push("comparison.risk_level 必须是 " + RISK_LEVELS.join("/") + " 之一");
      }
      if (c.band_applied !== null &&
          AGE_BANDS.concat([GLOBAL_BAND]).indexOf(c.band_applied) < 0) {
        e.push("comparison.band_applied 必须是参照组或 " + GLOBAL_BAND);
      }
      if (c.risk !== null && (!isNum(c.risk) || c.risk < 0 || c.risk > 1)) {
        e.push("comparison.risk 必须在 0~1 或 null");
      }
      // ⚠️ 两条一致性铁律：不可比时不许有数字；可比时必须有数字与档位
      if (!c.comparable) {
        if (c.risk !== null || c.deviation !== null) {
          e.push("comparable=False 时 risk/deviation 必须是 null（不报跳变数字是本产品的承诺）");
        }
        if (c.risk_level !== "unknown") e.push("comparable=False 时 risk_level 必须是 unknown");
      } else {
        if (c.risk === null) e.push("comparable=True 时 risk 不许为 null");
        if (c.risk_level === "unknown") e.push("comparable=True 时 risk_level 不能是 unknown");
        if (c.band_applied === null) e.push("comparable=True 时必须写明 band_applied（参照组或 global）");
      }
      if (!Array.isArray(c.contributions)) {
        e.push("comparison.contributions 必须是数组");
      } else {
        c.contributions.forEach(function (item, i) {
          if (typeof item !== "object" || item === null || Array.isArray(item) ||
              !("metric" in item) || !("log_odds" in item)) {
            e.push("comparison.contributions[" + i + "] 至少要有 metric 与 log_odds");
          }
        });
      }
      if (!Array.isArray(c.interactions)) e.push("comparison.interactions 必须是数组");
      if (typeof c.reason !== "string" || !c.reason.trim()) e.push("comparison.reason 必须是非空字符串");
      if (!Array.isArray(c.reference_sources)) e.push("comparison.reference_sources 必须是数组");
    }

    /* ---- privacy ---- */
    var pv = a.privacy;
    if (typeof pv !== "object" || pv === null || Array.isArray(pv)) {
      e.push("privacy 必须是 object");
    } else if (!sameKeys(pv, AGE_PRIVACY_KEYS)) {
      e.push("privacy 字段不符：缺少 " + keysOnlyIn(AGE_PRIVACY_KEYS, Object.keys(pv)) +
             "，多出 " + keysOnlyIn(pv, AGE_PRIVACY_KEYS));
    } else {
      if (typeof pv.stores_face_image !== "boolean") e.push("privacy.stores_face_image 必须是布尔");
      if (typeof pv.note !== "string" || !pv.note.trim()) e.push("privacy.note 必须是非空字符串");
    }

    return e;
  }

  /* 契约自检：六态各产一帧 + 故意造 5 个坏帧，校验器必须抓住 */
  function selfTest(seed) {
    var failures = [];
    var checked = 0;
    DEMO_SEQUENCE.forEach(function (st, i) {
      var fr = mockFrame(i, { status: st, seed: seed });
      var errs = validateFrame(fr);
      checked++;
      if (errs.length) failures.push("六态帧 " + st + " 不合法：" + errs.join("；"));
    });

    var base = mockFrame(0, { status: "normal", seed: seed });
    function clone(o) { return JSON.parse(JSON.stringify(o)); }
    var bad = [
      ["未知 status", (function () { var x = clone(base); x.status = "sleeping"; return x; })()],
      ["bbox 少一个", (function () { var x = clone(base); x.face.bbox = [320, 180, 180]; return x; })()],
      ["多出契约外字段", (function () { var x = clone(base); x.debug = 1; return x; })()],
      ["缺 quality", (function () { var x = clone(base); delete x.quality; return x; })()],
      ["visible 越界", (function () { var x = clone(base); x.face.visible = 1.4; return x; })()]
    ];
    bad.forEach(function (pair) {
      checked++;
      if (validateFrame(pair[1]).length === 0) failures.push("校验器漏检：" + pair[0]);
    });

    /* ---- 年龄档案件（旁路载荷）：6 个生命周期状态必须合法 ---- */
    var AGE_PHASE_FRAMES = [
      ["idle", 0], ["observing", 4], ["rejected", 11],
      ["enrolled", 14], ["estimate", 19], ["confirmed", 25]
    ];
    AGE_PHASE_FRAMES.forEach(function (pair) {
      var st = mockAgeState(pair[1], { seed: seed, ts: 1.0 + pair[1] });
      var errs = validateAgeState(st);
      checked++;
      if (errs.length) failures.push("年龄生命周期 " + pair[0] + " 不合法：" + errs.join("；"));
    });

    /* ---- 年龄校验器必须抓住以下坏例（每一条都对应 age_contract.py 的一条铁律）---- */
    var badAge = [
      ["锁定后仍调用推断（inference_calls_after_lock=1）", function () {
        var x = clone(mockAgeState(25, { seed: seed, ts: 30 }));
        x.profile.inference_calls_after_lock = 1;
        return x;
      }],
      ["comparable=false 却给了 risk=0.5", function () {
        var x = clone(mockAgeState(0, { seed: seed, ts: 1 }));
        x.comparison.risk = 0.5;
        return x;
      }],
      ["heuristic 引擎却标 placeholder=false", function () {
        var x = clone(mockAgeState(19, { seed: seed, ts: 20 }));
        x.estimate.engine = "heuristic";
        x.estimate.placeholder = false;
        return x;
      }],
      ["多出 schema 外的顶层字段", function () {
        var x = clone(mockAgeState(4, { seed: seed, ts: 5 }));
        x.debug = 1;
        return x;
      }],
      ["source=image_estimate 却 confirmed_by=user_manual", function () {
        var x = clone(mockAgeState(25, { seed: seed, ts: 30 }));
        x.profile.source = "image_estimate";
        return x;
      }]
    ];
    badAge.forEach(function (pair) {
      checked++;
      if (validateAgeState(pair[1]()).length === 0) failures.push("年龄校验器漏检：" + pair[0]);
    });

    return { checked: checked, failures: failures, ok: failures.length === 0 };
  }

  function stream(hz, limit, seed, statuses) {
    var out = [];
    var period = hz > 0 ? 1 / hz : 0;
    var n = limit === undefined || limit === null ? 6 : limit;
    for (var i = 0; i < n; i++) {
      out.push(mockFrame(i, { seed: seed, statuses: statuses, ts: period ? round(i / hz, 3) : null }));
    }
    return out;
  }

  var API = {
    STATUS_VALUES: STATUS_VALUES,
    BLINK_STATES: BLINK_STATES,
    VITAL_KEYS: VITAL_KEYS,
    TOP_KEYS: TOP_KEYS,
    DEMO_SEQUENCE: DEMO_SEQUENCE,
    STATUS_ZH: STATUS_ZH,
    mockFrame: mockFrame,
    validateFrame: validateFrame,
    selfTest: selfTest,
    stream: stream,
    /* ---- 年龄档案件（旁路载荷，不属于契约帧）的 JS 侧镜像 ---- */
    AGE_SCHEMA: AGE_SCHEMA,
    AGE_TOP_KEYS: AGE_TOP_KEYS,
    AGE_BANDS: AGE_BANDS,
    AGE_BAND_RANGES: AGE_BAND_RANGES,
    AGE_BAND_ZH: AGE_BAND_ZH,
    AGE_SOURCES: AGE_SOURCES,
    AGE_ENGINES: AGE_ENGINES,
    AGE_LIFECYCLE: AGE_LIFECYCLE,
    ENROLL_STATES: ENROLL_STATES,
    RISK_LEVELS: RISK_LEVELS,
    CONFIRMED_BY: CONFIRMED_BY,
    CV_AGE_BANDS: CV_AGE_BANDS,
    GLOBAL_BAND: GLOBAL_BAND,
    bandForAge: bandForAge,
    validateAgeState: validateAgeState,
    mockAgeState: mockAgeState,
    mockAgeEvents: mockAgeEvents
  };

  if (typeof module !== "undefined" && module.exports) module.exports = API;   // node
  root.VigiLensMock = API;                                                      // browser
})(typeof globalThis !== "undefined" ? globalThis : this);

/* ---- node CLI：给跨语言契约检查用 ----
 *   node frontend/mock.js --limit 6            → 每行一帧 JSON
 *   node frontend/mock.js --selftest           → 跑 JS 侧契约自检
 */
if (typeof require !== "undefined" && typeof module !== "undefined" && require.main === module) {
  var M = module.exports;
  var argv = process.argv.slice(2);
  function arg(name, dflt) {
    var i = argv.indexOf(name);
    return i >= 0 && i + 1 < argv.length ? argv[i + 1] : dflt;
  }
  if (argv.indexOf("--selftest") >= 0) {
    var res = M.selfTest(Number(arg("--seed", 20260910)));
    console.log(JSON.stringify(res, null, 2));
    process.exit(res.ok ? 0 : 1);
  }
  var limit = Number(arg("--limit", 6));
  var status = arg("--status", "all");
  var statuses = status === "all" ? M.DEMO_SEQUENCE : [status];
  M.stream(1, limit, Number(arg("--seed", 20260910)), statuses).forEach(function (f) {
    console.log(JSON.stringify(f));
  });
}
