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
    stream: stream
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
