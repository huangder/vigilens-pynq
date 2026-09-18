/* =============================================================================
 * app.js —— B 线仪表盘逻辑（零依赖，原生 JS + canvas 手绘）
 * =============================================================================
 * 数据源三选一，切换只改一个变量（《02》M2 要的"换数据源开关"）：
 *   ws      : WebSocket（backend/websocket.py 或 backend/api.py 的 /ws）
 *   offline : 本页内置离线 mock（用 mock.js）—— **没有后端也能演示六态**
 *   stopped : 停止
 *
 * 契约纪律：**每一帧都过 VigiLensMock.validateFrame()**，不合法就计入"契约：✗"
 * 并写进日志，而不是把坏数据渲染出来。这样 M2 接 A 线真数据时，
 * 界面上会立刻看出"字段对不上"，而不是等到答辩才发现某个卡片一直是空的。
 * ========================================================================== */
(function () {
  "use strict";

  var M = window.VigiLensMock;
  // 客户端看门狗：**故意比服务端的 ws_disconnect_timeout_s（3.0 s）慢 1 秒**。
  // 断流的权威来源是服务端 —— api.py 的 /ws 在超时后会下发一帧契约合法的 `disconnected`
  // （见 backend/api.py 的 disconnect_frame 与 backend/A_LINE_DEV_STEPS.md §9 第 8 条）。
  // 客户端看门狗只在"服务端整个挂掉、连断开帧都发不出来"时兜底，所以必须比服务端晚触发；
  // 同刻触发会让两边抢着宣布断流，日志里多一条误导性的"超过 Ns 未收到帧"。
  var WS_TIMEOUT_MS = 4000;
  var MAX_POINTS = 120;              // 趋势曲线保留点数（1 Hz → 120 秒）
  var MOCK_PERIOD_MS = 1000;         // 与契约 1 帧/秒一致

  /* 六态配色：与 panel 顶部状态灯、日志 tag 共用一套 */
  var STATUS_COLOR = {
    normal: "#35d07f",
    fatigue_risk: "#ffb020",
    adjust_posture: "#4da3ff",
    unreliable: "#8b7cff",
    disconnected: "#ff5d5d",
    done: "#5d6b85"
  };

  /* --------------------------------------------------------------------------
   * 阈值 —— **唯一来源是仓库根 config.yaml**，下面的数值只是「离线兜底」。
   *
   * 取值顺序（applyServerThresholds()）：
   *   1) 页面由 api.py 托管 → /api/status 会带回 thresholds，**覆盖**下表（权威）；
   *   2) 双击 index.html（file://）或后端不在 → 用下表，保证离线演示照常能跑。
   *
   * 所以：A 线标定后改 config.yaml，**前端会自动跟着变**，不会再无声漂移。
   * 下表只在离线时生效；改阈值仍然只改 config.yaml 一处。
   * ------------------------------------------------------------------------ */
  var THRESHOLDS = {
    perclos_warning: 0.25,        // config.yaml: perclos_warning
    face_visible_min: 0.70,       // config.yaml: face_visible_min
    quality_min_score: 0.60,      // config.yaml: quality_min_score（低于此值系统判 unreliable）
    light_score_min: 0.50,        // config.yaml: light_score_min
    motion_score_max: 0.35,       // config.yaml: motion_score_max
    vital_require_quality: 0.75,  // config.yaml: vital_require_quality（比上面更严：决定心率/呼吸出不出数）
    fatigue_long_close_count: 1   // config.yaml: fatigue_long_close_count
  };

  /* 指标卡定义（顺序即界面顺序） */
  var CARD_DEFS = [
    { key: "ear", label: "EAR 眼睛纵横比", unit: "", digits: 3, path: ["behavior", "ear_left"], get: function (f) { return f.behavior.ear_left; } },
    { key: "perclos", label: "PERCLOS 闭眼比例", unit: "", digits: 3, get: function (f) { return f.behavior.perclos; }, warn: function (f) { return f.behavior.perclos > THRESHOLDS.perclos_warning; } },
    { key: "blinkRate", label: "眨眼率", unit: "/min", digits: 1, get: function (f) { return f.behavior.blink_rate_per_min; } },
    { key: "blinkCount", label: "眨眼次数", unit: "", digits: 0, get: function (f) { return f.behavior.blink_count; } },
    { key: "longClose", label: "长闭眼次数", unit: "次", digits: 0, get: function (f) { return f.behavior.long_close_count; }, warn: function (f) { return f.behavior.long_close_count >= THRESHOLDS.fatigue_long_close_count; } },
    { key: "yawn", label: "打哈欠", unit: "次", digits: 0, get: function (f) { return f.behavior.yawn_count; } },
    { key: "mar", label: "MAR 嘴部纵横比", unit: "", digits: 3, get: function (f) { return f.behavior.mar; } },
    { key: "visible", label: "人脸可见率", unit: "", digits: 3, get: function (f) { return f.face.visible; }, warn: function (f) { return f.face.visible < THRESHOLDS.face_visible_min; } },
    { key: "yaw", label: "头部 yaw/pitch", unit: "°", digits: 1, get: function (f) { return f.face.pose.yaw; }, extra: function (f) { return " / " + f.face.pose.pitch.toFixed(1); } },
    { key: "quality", label: "信号质量 overall", unit: "", digits: 2, get: function (f) { return f.quality.overall; }, warn: function (f) { return f.quality.overall < THRESHOLDS.quality_min_score; } },
    { key: "light", label: "光照分", unit: "", digits: 2, get: function (f) { return f.quality.light_score; } },
    { key: "motion", label: "运动分（越低越好）", unit: "", digits: 2, get: function (f) { return f.quality.motion_score; }, warn: function (f) { return f.quality.motion_score > THRESHOLDS.motion_score_max; } },
    { key: "hr", label: "心率（门控）", unit: "bpm", digits: 1, gate: true, confKey: "hr_conf", get: function (f) { return f.vital.hr_bpm; } },
    // staticNote：只在**没有数值**时显示，用来解释"这张卡为什么出不来数"。
    // 呼吸率是契约级限制（§3.5：63 阶 @45 fps 的过渡带吃掉了 0.1~0.5 Hz 呼吸带），
    // 不加说明的话，答辩现场它看起来就像坏了。
    { key: "rr", label: "呼吸率（门控）", unit: "/min", digits: 1, gate: true, confKey: "rr_conf",
      staticNote: "契约 §3.5：63 阶 @45 fps 做不了呼吸带，待 PS 侧降采样",
      get: function (f) { return f.vital.rr_per_min; } }
  ];

  /* 门控条目：方向 min = 越大越好，max = 越小越好。
     ⚠️ 这里存的是**阈值键名**而不是数值 —— 数值要在渲染时从 THRESHOLDS 现取，
     否则后端下发的阈值覆盖 THRESHOLDS 之后，这里还钉着启动时那份旧值。 */
  var GATE_DEFS = [
    { key: "light",   label: "光照",              dir: "min", limitKey: "light_score_min",    get: function (f) { return f.quality.light_score; } },
    { key: "motion",  label: "运动（越小越好）",   dir: "max", limitKey: "motion_score_max",   get: function (f) { return f.quality.motion_score; } },
    { key: "visible", label: "人脸可见率",         dir: "min", limitKey: "face_visible_min",   get: function (f) { return f.face.visible; } },
    { key: "overall", label: "信号质量总分",       dir: "min", limitKey: "quality_min_score",  get: function (f) { return f.quality.overall; } },
    { key: "vital",   label: "心率/呼吸额外门控",  dir: "min", limitKey: "vital_require_quality", extra: true,
      get: function (f) { return f.quality.overall; } }
  ];

  /* 眨眼状态机（契约 behavior.blink_state 的 4 个取值） */
  var BLINK_ORDER = ["OPEN", "CLOSING", "CLOSED", "OPENING"];
  var BLINK_ZH = { OPEN: "睁眼", CLOSING: "正在闭眼", CLOSED: "闭眼", OPENING: "正在睁眼" };
  var BLINK_COLOR = { OPEN: "#35d07f", CLOSING: "#4da3ff", CLOSED: "#ffb020", OPENING: "#4da3ff" };

  var el = {};                       // DOM 引用
  var state = {
    mode: "stopped",                 // ws | offline | stopped
    ws: null,
    timer: null,
    lastRxAt: 0,
    frameId: 0,
    points: [],
    lastStatus: null,
    valid: 0,
    invalid: 0,
    forcedStatus: "",
    lastFrame: null,
    triggers: null,        // 旁路通道来的判定证据链 {frame_id, items}
    pollTimer: null
  };

  /* ---------------------------------------------------------------- 工具 */
  function $(id) { return document.getElementById(id); }

  function fmt(v, digits) {
    if (v === null || v === undefined || (typeof v === "number" && !isFinite(v))) return null;
    return typeof v === "number" ? v.toFixed(digits) : String(v);
  }

  function nowStr(ts) {
    var d = ts ? new Date(ts * 1000) : new Date();
    return d.toTimeString().slice(0, 8);
  }

  function fitCanvas(cv) {
    var dpr = window.devicePixelRatio || 1;
    var w = cv.clientWidth || cv.width;
    var h = parseInt(cv.getAttribute("data-css-h") || "0", 10) || (cv.id === "video" ? Math.round(w * 480 / 640) : 200);
    cv.setAttribute("data-css-h", String(h));
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
    }
    var ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, w: w, h: h };
  }

  /* ---------------------------------------------------------------- 日志 */
  function log(tag, text, color) {
    var row = document.createElement("div");
    row.className = "row";
    row.innerHTML = '<time>' + nowStr() + '</time><span class="tag" style="color:' +
      (color || "#8d9bb5") + '">' + tag + '</span><span>' + text + "</span>";
    el.log.insertBefore(row, el.log.firstChild);
    while (el.log.childNodes.length > 200) el.log.removeChild(el.log.lastChild);
  }

  /* ------------------------------------------------------- 视频占位 + 检测框 */
  function drawVideo(frame) {
    var c = fitCanvas(el.video);
    var ctx = c.ctx, w = c.w, h = c.h;
    ctx.clearRect(0, 0, w, h);

    // 占位底：暗格子 + 十字准星，明确表达"这里本来该是画面"
    ctx.fillStyle = "#070a10";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "#131c2b";
    ctx.lineWidth = 1;
    for (var x = 0; x < w; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
    for (var y = 0; y < h; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }

    ctx.strokeStyle = "#1b2740";
    ctx.beginPath(); ctx.moveTo(w / 2, h / 2 - 12); ctx.lineTo(w / 2, h / 2 + 12);
    ctx.moveTo(w / 2 - 12, h / 2); ctx.lineTo(w / 2 + 12, h / 2); ctx.stroke();

    if (!frame || frame.status === "disconnected" || frame.face.bbox[2] === 0) {
      ctx.fillStyle = "#5d6b85";
      ctx.font = "13px 'Segoe UI', 'Microsoft YaHei', sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("无视频源", w / 2, h / 2 + 36);
      el.videoBadge.textContent = "无视频源（未接入摄像头/回放）";
      el.videoBadge.className = "badge warn";
      return;
    }

    // face.bbox 是 640×480 坐标系 → 按画布尺寸等比缩放
    var sx = w / 640, sy = h / 480;
    var b = frame.face.bbox;
    var bx = b[0] * sx, by = b[1] * sy, bw = b[2] * sx, bh = b[3] * sy;
    var color = STATUS_COLOR[frame.status] || "#4da3ff";
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.strokeRect(bx, by, bw, bh);
    // 四角强调，便于截图时一眼看到检测框
    ctx.lineWidth = 3;
    var L = Math.min(18, bw / 3, bh / 3);
    [[bx, by, 1, 1], [bx + bw, by, -1, 1], [bx, by + bh, 1, -1], [bx + bw, by + bh, -1, -1]].forEach(function (p) {
      ctx.beginPath();
      ctx.moveTo(p[0] + p[2] * L, p[1]); ctx.lineTo(p[0], p[1]); ctx.lineTo(p[0], p[1] + p[3] * L);
      ctx.stroke();
    });
    ctx.fillStyle = color;
    ctx.font = "12px ui-monospace, Consolas, monospace";
    ctx.textAlign = "left";
    ctx.fillText("face " + frame.face.visible.toFixed(2) + "  yaw " + frame.face.pose.yaw.toFixed(1) + "°",
                 bx, Math.max(12, by - 6));
    el.videoBadge.textContent = "占位画面 + face.bbox 叠加";
    el.videoBadge.className = "badge";
  }

  /* ---------------------------------------------------------------- 状态区 */
  function renderState(frame) {
    var st = frame ? frame.status : "disconnected";
    var color = STATUS_COLOR[st] || "#5d6b85";
    el.stateLamp.style.background = color;
    el.stateLamp.style.color = color;
    el.stateName.textContent = M.STATUS_ZH[st] || st;
    el.stateName.style.color = color;
    el.stateEn.textContent = st;

    el.advice.textContent = frame ? frame.advice : "等待数据…";
    el.reason.textContent = frame ? frame.reason : "—";

    el.triggers.innerHTML = "";
    // 判定证据链优先取帧自带的 _triggers（离线 mock / 单进程场景）；
    // 否则取旁路通道（/api/status 的 triggers）—— 且 frame_id 必须与当前这帧一致，
    // 否则会把"上一帧的理由"贴在"这一帧的状态"旁边，那比没有更糟。
    var list = (frame && frame._triggers) || [];
    // 断流帧不显示证据链：它的 frame_id 是**沿用**上一帧的（见 api.py 的 disconnect_frame），
    // 光比 frame_id 会误判为吻合，于是"上一帧为什么判疲劳"就被贴在"连接中断"旁边 —— 那是误导。
    if (!list.length && frame && frame.status !== "disconnected" &&
        state.triggers && state.triggers.frame_id === frame.frame_id) {
      list = state.triggers.items || [];
    }
    if (!list.length) {
      var li = document.createElement("li");
      li.textContent = frame
        ? "（暂无判定证据链：需 A 线推送时带上 triggers，见 docs/08_B线给A线的接口请求.md）"
        : "—";
      el.triggers.appendChild(li);
    } else {
      list.forEach(function (t) {
        var li = document.createElement("li");
        li.className = t.verdict || "";
        var val = t.value === null || t.value === undefined ? "—" : (typeof t.value === "number" ? t.value.toFixed(3) : t.value);
        var thr = t.threshold === null || t.threshold === undefined ? "—" : t.threshold;
        li.textContent = "[" + (t.verdict || "-") + "] " + t.rule + " · " + t.metric + " = " + val + "（阈值 " + thr + "）";
        el.triggers.appendChild(li);
      });
    }
  }

  /* ------------------------------------------------ 信号质量门控（能不能测） */
  function gatePass(g, v) {
    if (v === null || v === undefined || !isFinite(v)) return false;
    var limit = THRESHOLDS[g.limitKey];
    return g.dir === "min" ? v >= limit : v <= limit;
  }

  function buildGate() {
    el.gateBars.innerHTML = "";
    GATE_DEFS.forEach(function (g) {
      var row = document.createElement("div");
      row.className = "gate-row" + (g.extra ? " extra" : "");
      row.id = "gate_" + g.key;
      row.innerHTML = '<div class="top"><span>' + g.label + '</span><span class="val">—</span></div>' +
        '<div class="bar"><div class="fill" style="width:0%"></div><div class="mark"></div></div>';
      el.gateBars.appendChild(row);
    });
  }

  function renderGate(frame) {
    var failed = [];
    GATE_DEFS.forEach(function (g) {
      var row = $("gate_" + g.key);
      if (!row) return;
      var v = frame ? g.get(frame) : null;
      var ok = gatePass(g, v);
      var has = !(v === null || v === undefined || !isFinite(v));
      var limit = THRESHOLDS[g.limitKey];
      // 条形图统一成"越长越好"：max 型（运动，越小越好）取 1-v 翻转
      var frac = has ? (g.dir === "min" ? v : 1 - v) : 0;
      frac = Math.max(0, Math.min(1, frac));
      var limitFrac = g.dir === "min" ? limit : 1 - limit;
      row.querySelector(".fill").style.width = (frac * 100).toFixed(1) + "%";
      row.querySelector(".mark").style.left = (limitFrac * 100).toFixed(1) + "%";
      row.querySelector(".val").textContent = has
        ? v.toFixed(3) + (g.dir === "min" ? " ≥ " : " ≤ ") + limit
        : "—";
      row.classList.toggle("fail", !!frame && !ok);
      if (frame && !ok && !g.extra) failed.push(g.label);
    });

    var st = frame ? frame.status : null;
    if (!frame) {
      el.gateVerdictText.textContent = "等待数据…";
      el.gateVerdictText.style.color = "";
      el.gateVerdictSub.textContent = "—";
    } else if (st === "disconnected") {
      el.gateVerdictText.textContent = "无数据";
      el.gateVerdictText.style.color = "#ff5d5d";
      el.gateVerdictSub.textContent = "视频源或连接中断，门控不适用";
    } else if (failed.length === 0) {
      el.gateVerdictText.textContent = "可以测量";
      el.gateVerdictText.style.color = "#35d07f";
      el.gateVerdictSub.textContent = "四项门控全部通过，下方指标可信";
    } else {
      el.gateVerdictText.textContent = "测不准，别采信";
      el.gateVerdictText.style.color = "#ff5d5d";
      el.gateVerdictSub.textContent = "未通过：" + failed.join("、");
    }
  }

  /* ------------------------------------------------------- 眨眼状态机指示 */
  function buildBlinkStrip() {
    BLINK_ORDER.forEach(function (s) {
      var d = document.createElement("div");
      d.className = "blink-step";
      d.id = "blink_" + s;
      d.textContent = s;
      d.title = BLINK_ZH[s];
      el.blinkStrip.insertBefore(d, el.blinkLabel);
    });
  }

  function renderBlink(frame) {
    var cur = frame ? frame.behavior.blink_state : null;
    BLINK_ORDER.forEach(function (s) {
      var d = $("blink_" + s);
      if (!d) return;
      var on = (s === cur);
      var col = BLINK_COLOR[s] || "#8d9bb5";
      d.classList.toggle("on", on);
      d.style.background = on ? col : "";
      d.style.borderColor = on ? col : "";
    });
    el.blinkLabel.textContent = "眨眼状态机：" +
      (cur ? (BLINK_ZH[cur] || cur) + "（" + cur + "）" : "—");
  }

  /* ---------------------------------------------------------------- 指标卡 */
  function buildCards() {
    el.cards.innerHTML = "";
    CARD_DEFS.forEach(function (d) {
      var div = document.createElement("div");
      div.className = "card";
      div.id = "card_" + d.key;
      div.innerHTML = '<div class="k">' + d.label + '</div><div class="v"><span class="num">—</span><span class="u">' +
        (d.unit || "") + '</span></div><div class="note"></div>';
      el.cards.appendChild(div);
    });
  }

  /* 生理指标的三态显示。这是本项目最核心的产品承诺：
     测不准时宁可说"没有"，也绝不沿用上一个数字、更不写 0。*/
  function vitalDisplay(frame, value, conf) {
    if (value !== null && value !== undefined && isFinite(value)) {
      return { text: value.toFixed(1),
               note: (conf === null || conf === undefined || !isFinite(conf))
                     ? "" : "置信度 " + conf.toFixed(3) };
    }
    if (frame.status === "disconnected") {
      return { text: "无数据", msg: true, note: "视频源或连接中断" };
    }
    var q = frame.quality.overall;
    if (q < THRESHOLDS.quality_min_score) {
      return { text: "已锁定", msg: true,
               note: "信号不可靠（" + q.toFixed(2) + " < " + THRESHOLDS.quality_min_score +
                     "），按承诺不报数字" };
    }
    if (q < THRESHOLDS.vital_require_quality) {
      return { text: "暂不出数", msg: true,
               note: "质量 " + q.toFixed(2) + " < 门控线 " + THRESHOLDS.vital_require_quality +
                     "，暂不给数" };
    }
    return { text: "计算中", msg: true, note: "门控已通过，尚无有效样本" };
  }

  function renderCards(frame) {
    CARD_DEFS.forEach(function (d) {
      var card = $("card_" + d.key);
      if (!card) return;
      var num = card.querySelector(".num");
      var unit = card.querySelector(".u");
      var note = card.querySelector(".note");
      var v = frame ? d.get(frame) : null;
      var text, isMsg = false, isWarn = false, noteText = "";

      if (d.gate && frame) {
        var g = vitalDisplay(frame, v, d.confKey ? frame.vital[d.confKey] : null);
        text = g.text;
        isMsg = !!g.msg;
        noteText = g.note || "";
      } else if (frame) {
        var s = fmt(v, d.digits);
        isMsg = (s === null);
        text = isMsg ? "暂无" : s + (d.extra ? d.extra(frame) : "");
        isWarn = d.warn ? d.warn(frame) : false;
      } else {
        isMsg = true;
        text = "—";
      }

      card.classList.toggle("null", isMsg);
      card.classList.toggle("msg", isMsg && text.length > 4);
      num.textContent = text;
      if (unit) unit.textContent = isMsg ? "" : (d.unit || "");
      // 静态说明只在"没有数值"时拼进去：否则会出现"卡上有数字、备注却说这个指标出不来"
      // 这种自相矛盾（Mock 数据下呼吸率是有值的）。
      var parts = [];
      if (isMsg && d.staticNote) parts.push(d.staticNote);
      if (noteText) parts.push(noteText);
      if (note) note.textContent = parts.join(" · ");
      card.style.borderColor = isWarn ? "#4d3c14" : "#24304a";
      num.style.color = isWarn ? "#ffb020" : "";
    });
  }

  /* ---------------------------------------------------------------- 曲线 */
  function renderChart() {
    var c = fitCanvas(el.chart);
    var ctx = c.ctx, w = c.w, h = c.h;
    var padL = 34, padR = 8, padT = 10, padB = 18;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#0f1621";
    ctx.fillRect(0, 0, w, h);

    // 网格 + y 轴刻度（0 / 50 / 100，按各自量纲归一化到 0~1 后绘制）
    ctx.strokeStyle = "#1d2740";
    ctx.fillStyle = "#5d6b85";
    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.lineWidth = 1;
    for (var i = 0; i <= 4; i++) {
      var y = padT + (h - padT - padB) * (i / 4);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(String(100 - i * 25) + "%", padL - 6, y + 3);
    }

    var pts = state.points;
    if (pts.length < 2) {
      ctx.fillStyle = "#5d6b85";
      ctx.textAlign = "center";
      ctx.font = "12px 'Segoe UI', 'Microsoft YaHei', sans-serif";
      ctx.fillText("等待数据…", w / 2, h / 2);
      return;
    }

    function series(getter, scale, max) {
      ctx.beginPath();
      var started = false;
      for (var i = 0; i < pts.length; i++) {
        var v = getter(pts[i]);
        if (v === null || v === undefined || !isFinite(v)) { started = false; continue; }
        var x = padL + (w - padL - padR) * (pts.length === 1 ? 0 : i / (MAX_POINTS - 1));
        var nv = Math.max(0, Math.min(1, max ? v / scale : v));
        var y = padT + (h - padT - padB) * (1 - nv);
        if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
      }
      ctx.stroke();
    }

    var defs = [
      { color: "#4da3ff", get: function (p) { return p.behavior.blink_rate_per_min; }, scale: 40 },
      { color: "#ffb020", get: function (p) { return p.behavior.perclos; }, scale: 1 },
      { color: "#35d07f", get: function (p) { return p.vital.hr_bpm; }, scale: 140 },
      { color: "#8b7cff", get: function (p) { return p.quality.overall; }, scale: 1 }
    ];
    defs.forEach(function (d) {
      ctx.strokeStyle = d.color;
      ctx.lineWidth = 1.8;
      ctx.lineJoin = "round";
      series(d.get, d.scale, true);
    });

    // 时间轴两端标注
    ctx.fillStyle = "#5d6b85";
    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.textAlign = "left";
    ctx.fillText(nowStr(pts[0].ts) || "", padL, h - 5);
    ctx.textAlign = "right";
    ctx.fillText(nowStr(pts[pts.length - 1].ts) || "", w - padR, h - 5);
  }

  /* ------------------------------------------------------- 收帧 / 校验 / 渲染 */
  function onFrame(frame, source) {
    if (frame && frame._synthetic_disconnect) frame = synthesizeDisconnected();

    var errs = M.validateFrame(frame);
    if (errs.length) {
      state.invalid++;
      el.validName.textContent = "✗ " + state.invalid;
      el.validPill.style.color = "#ff5d5d";
      el.validPill.style.borderColor = "#4d2020";
      log("契约✗", "frame_id=" + frame.frame_id + " 不合契约：" + errs[0] + "（共 " + errs.length + " 项）", "#ff5d5d");
      return;
    }
    state.valid++;
    el.validName.textContent = "✓ " + state.valid;
    el.validPill.style.color = "#35d07f";
    el.validPill.style.borderColor = "#1d4d35";

    state.lastFrame = frame;
    state.frameId = frame.frame_id;

    if (frame.status !== state.lastStatus) {
      var from = state.lastStatus === null ? "（首帧）" : (M.STATUS_ZH[state.lastStatus] || state.lastStatus);
      log("状态迁移", from + " → <b style='color:" + (STATUS_COLOR[frame.status] || "#8d9bb5") + "'>" +
          (M.STATUS_ZH[frame.status] || frame.status) + "</b> · " + frame.reason,
          STATUS_COLOR[frame.status] || "#8d9bb5");
      state.lastStatus = frame.status;
    }

    // 断流帧不进趋势曲线：把它当"这一段没有数据"（曲线留空），而不是"测出来是 0"。
    // 兜底帧是 mock 造的 disconnected，它的 perclos / 眨眼率 / 质量都是 0，
    // 画上去会变成一根掉到 0 的假尖峰 —— 那等于替系统编了一个它没测到的结论。
    if (frame.status !== "disconnected") {
      state.points.push(frame);
      while (state.points.length > MAX_POINTS) state.points.shift();
    }

    el.srcName.textContent = source;
    el.srcPill.className = "pill " + (source === "WebSocket" ? "live" : "mock");
    el.modeName.textContent = "软件模式";
    el.videoBadge.textContent = frame.status === "disconnected" ? "无视频源" : "占位画面 + face.bbox 叠加";

    renderState(frame);
    renderCards(frame);
    renderGate(frame);
    renderBlink(frame);
    drawVideo(frame);
    renderChart();
  }

  function synthesizeDisconnected() {
    return M.mockFrame(-1, { status: "disconnected" });
  }

  /* ------------------------------------------- 与后端同步（地址 + 阈值） */
  function syncWithServer() {
    // 由 api.py 托管时，一次 /api/status 解决两件事：
    //   ① WS 与页面**同源** → 自动填好地址，省掉"记得手填 :8000/ws"这个演示出错点；
    //   ② 门控阈值的**权威值在 config.yaml**，由后端下发 → 前端那份副本不会再无声漂移。
    // 探测失败（双击 index.html 的 file://、普通静态服务器、后端没起）→ 全部保持内置默认，
    // 离线演示照常能跑。
    if (location.protocol !== "http:" && location.protocol !== "https:") return;
    fetch("/api/status", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || j.ok !== true) return;
        var want = (location.protocol === "https:" ? "wss://" : "ws://") +
                   location.host + "/ws";
        if (el.wsUrl.value.trim() !== want) {
          el.wsUrl.value = want;
          log("系统", "检测到本页由 api.py 托管，地址已自动填为 " + want, "#4da3ff");
        }
        applyServerThresholds(j.thresholds);
      })
      .catch(function () { /* 不是 api.py 托管的：保持默认，不发日志避免误导 */ });
  }

  function applyServerThresholds(fromServer) {
    if (!fromServer || typeof fromServer !== "object") return;
    var changed = [];
    Object.keys(THRESHOLDS).forEach(function (k) {
      var v = fromServer[k];
      if (typeof v === "number" && isFinite(v) && v !== THRESHOLDS[k]) {
        THRESHOLDS[k] = v;
        changed.push(k + "=" + v);
      }
    });
    // 客户端看门狗必须比服务端**慢**（服务端才是断流的权威来源）——跟着服务端超时一起算，
    // 不然 config.yaml 调了 ws_disconnect_timeout_s，两边又会同刻抢着宣布断流。
    var t = fromServer.ws_disconnect_timeout_s;
    if (typeof t === "number" && isFinite(t) && t > 0) {
      WS_TIMEOUT_MS = Math.round((t + 1) * 1000);
    }
    if (changed.length) {
      // 只有真的与内置副本不同才说话，避免每次打开页面都刷一行噪音
      log("系统", "阈值已按 config.yaml 更新：" + changed.join("、"), "#8b7cff");
      renderGate(state.lastFrame);
      renderCards(state.lastFrame);
    }
  }

  /* ---------------------------------------------------------------- WS 客户端 */
  function setConn(kind, text) {
    el.connPill.className = "pill " + kind;
    el.connName.textContent = text;
  }

  /* ------------------------------- 判定证据链（旁路通道，1 Hz） */
  function pollTriggers() {
    // 证据链**不塞进契约帧**（契约 §1 的帧只允许那 9 个顶层字段），走 /api/status
    // 的旁路字段。只在 http(s) 托管下轮询 —— file:// 没有同源后端，轮询只会白报错。
    if (location.protocol !== "http:" && location.protocol !== "https:") return;
    if (state.pollTimer) return;
    state.pollTimer = setInterval(function () {
      if (state.mode !== "ws") return;
      fetch("/api/status", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || !j.ok) return;
          var t = j.triggers || null;
          var changed = JSON.stringify(t) !== JSON.stringify(state.triggers);
          state.triggers = t;
          if (changed && state.lastFrame) renderState(state.lastFrame);
        })
        .catch(function () { /* 轮询失败无所谓：证据链是"有更好"，不是必需 */ });
    }, 1000);
  }

  function stopAll(silent) {
    if (state.ws) {
      try { state.ws.onclose = null; state.ws.close(); } catch (e) { /* ignore */ }
      state.ws = null;
    }
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
    state.triggers = null;
    state.mode = "stopped";
    if (!silent) log("系统", "已停止数据源", "#5d6b85");
  }

  function connectWs() {
    stopAll(true);
    var url = el.wsUrl.value.trim();
    if (!url) { log("错误", "请填写 WebSocket 地址", "#ff5d5d"); return; }

    state.mode = "ws";
    setConn("", "连接中…");
    log("系统", "连接 " + url, "#4da3ff");
    pollTriggers();

    var ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      log("错误", "地址不合法：" + e.message + "（形如 ws://127.0.0.1:8765）", "#ff5d5d");
      setConn("down", "地址错误");
      return;
    }
    state.ws = ws;

    state.lastRxAt = Date.now();
    ws.onopen = function () {
      setConn("live", "已连接");
      log("系统", "WebSocket 已连接", "#35d07f");
    };
    ws.onmessage = function (ev) {
      state.lastRxAt = Date.now();
      var frame;
      try { frame = JSON.parse(ev.data); }
      catch (e) { log("错误", "收到非 JSON 消息，已丢弃：" + String(ev.data).slice(0, 60), "#ff5d5d"); return; }
      // api.py 的 /api/status 包了一层 frame；/ws 直接推裸帧，这里两种都吃
      onFrame(frame.frame && frame.frame.status ? frame.frame : frame, "WebSocket");
    };
    ws.onerror = function () {
      log("错误", "WebSocket 出错（服务是否已启动？地址与端口是否正确？）", "#ff5d5d");
    };
    ws.onclose = function () {
      if (state.mode !== "ws") return;
      setConn("down", "连接中断");
      log("系统", "WebSocket 已断开", "#ff5d5d");
      onFrame(synthesizeDisconnected(), "连接中断");
    };

    // 超时兜底：超过 WS_TIMEOUT_MS 没收到帧 → 界面上明确显示"信号不可靠/连接中断"
    state.timer = setInterval(function () {
      if (state.mode !== "ws") return;
      if (Date.now() - state.lastRxAt > WS_TIMEOUT_MS) {
        setConn("down", "无数据");
        if (!state.lastFrame || state.lastFrame.status !== "disconnected") {
          log("系统", "超过 " + (WS_TIMEOUT_MS / 1000) + "s 未收到帧 → 显示连接中断", "#ffb020");
          onFrame(synthesizeDisconnected(), "连接中断");
        }
      }
    }, 500);
  }

  /* ------------------------------------------------------- 离线 mock（无后端） */
  function startOffline() {
    stopAll(true);
    state.mode = "offline";
    state.lastRxAt = Date.now();
    setConn("mock", "离线模式");
    log("系统", "离线 Mock 演示：不依赖后端、不依赖摄像头（契约与后端 mock 同一套语义）", "#ffb020");

    function tick() {
      var statuses = M.DEMO_SEQUENCE;
      var st = state.forcedStatus || statuses[state.frameId % statuses.length];
      onFrame(M.mockFrame(state.frameId, { status: st }), "离线 Mock");
      state.frameId++;
    }
    tick();
    state.timer = setInterval(tick, MOCK_PERIOD_MS);
  }

  /* ---------------------------------------------------------------- 契约自检 */
  function runSelftest() {
    var res = M.selfTest(20260910);
    if (res.ok) {
      log("契约自检", "通过：检查 " + res.checked + " 项（六态各一帧 + 5 个坏帧必须被抓）", "#35d07f");
      alert("契约自检通过 ✓\n\n检查 " + res.checked + " 项：\n· 六种状态的 mock 帧全部合法\n· 5 个故意构造的坏帧全部被校验器抓住\n\n（跨语言检查请跑：node frontend/mock.js --limit 6 | python metrics/scripts/check_frontend_contract.py -）");
    } else {
      log("契约自检", "失败：" + res.failures.join("；"), "#ff5d5d");
      alert("契约自检失败 ✗\n\n" + res.failures.join("\n"));
    }
  }

  /* ---------------------------------------------------------------- 初始化 */
  function init() {
    el = {
      video: $("video"), videoBadge: $("videoBadge"), chart: $("chart"),
      stateLamp: $("stateLamp"), stateName: $("stateName"), stateEn: $("stateEn"),
      advice: $("advice"), reason: $("reason"), triggers: $("triggers"),
      cards: $("cards"), log: $("log"),
      gateBars: $("gateBars"), gateVerdictText: $("gateVerdictText"), gateVerdictSub: $("gateVerdictSub"),
      blinkStrip: $("blinkStrip"), blinkLabel: $("blinkLabel"),
      wsUrl: $("wsUrl"), connPill: $("connPill"), connName: $("connName"),
      srcPill: $("srcPill"), srcName: $("srcName"), modeName: $("modeName"),
      validPill: $("validPill"), validName: $("validName"), forceStatus: $("forceStatus")
    };

    buildCards();
    buildGate();
    buildBlinkStrip();
    M.STATUS_VALUES.forEach(function (s) {
      var o = document.createElement("option");
      o.value = s;
      o.textContent = M.STATUS_ZH[s] + "（" + s + "）";
      el.forceStatus.appendChild(o);
    });

    $("btnConnect").onclick = connectWs;
    $("btnOffline").onclick = startOffline;
    $("btnStop").onclick = function () { stopAll(false); setConn("down", "未连接"); };
    $("btnSelftest").onclick = runSelftest;
    $("btnClear").onclick = function () { el.log.innerHTML = ""; };
    el.forceStatus.onchange = function () {
      state.forcedStatus = el.forceStatus.value;
      log("系统", state.forcedStatus ? "强制状态：" + M.STATUS_ZH[state.forcedStatus] : "恢复六态轮转", "#8b7cff");
    };

    window.addEventListener("resize", function () { drawVideo(state.lastFrame); renderChart(); });

    renderState(null);
    renderCards(null);
    renderGate(null);
    renderBlink(null);
    drawVideo(null);
    renderChart();
    log("系统", "页面就绪。点\"离线 Mock 演示\"即可看六态；填好地址后点\"连接 WebSocket\"接后端。", "#4da3ff");

    syncWithServer();

    // 未接后端时自动进入离线演示，保证"双击文件就能看到东西"
    startOffline();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
