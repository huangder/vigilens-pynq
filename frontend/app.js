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
  var P = window.VigiLensPreview;
  var U = window.VigiLensUIState;
  // 客户端看门狗：**故意比服务端的 ws_disconnect_timeout_s（3.0 s）慢 1 秒**。
  // 断流的权威来源是服务端 —— api.py 的 /ws 在超时后会下发一帧契约合法的 `disconnected`
  // （见 backend/api.py 的 disconnect_frame 与 backend/A_LINE_DEV_STEPS.md §9 第 8 条）。
  // 客户端看门狗只在"服务端整个挂掉、连断开帧都发不出来"时兜底，所以必须比服务端晚触发；
  // 同刻触发会让两边抢着宣布断流，日志里多一条误导性的"超过 Ns 未收到帧"。
  var WS_TIMEOUT_MS = 4000;
  var MAX_POINTS = 120;              // 趋势曲线保留点数（1 Hz → 120 秒）
  var MOCK_PERIOD_MS = 1000;         // 与契约 1 帧/秒一致
  var PREVIEW_POLL_MS = 80;
  var PREVIEW_STALE_MS = 1500;

  /* 六态配色：与 panel 顶部状态灯、日志 tag 共用一套 */
  var STATUS_COLOR = {
    normal: "#248a3d",
    fatigue_risk: "#c93400",
    adjust_posture: "#007aff",
    unreliable: "#8944ab",
    disconnected: "#d70015",
    done: "#6e6e73"
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
  var BLINK_COLOR = { OPEN: "#248a3d", CLOSING: "#007aff", CLOSED: "#c93400", OPENING: "#007aff" };

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
    previewTimer: null,
    previewWatchdog: null,
    previewBusy: false,
    previewEtag: "",
    previewMeta: null,
    previewObjectUrl: "",
    previewLastRxAt: 0,
    triggers: null,        // 旁路通道来的判定证据链 {frame_id, items}
    pollTimer: null,
    serverSource: "",      // 当前连接的 /api/status.source；独立 WS 时为空
    hostedServerSource: "",// 托管本页的 api.py 来源，供同源 /ws 连接时使用
    usesApiStatus: false    // 当前 WS 是否就是托管页面的同源 /ws
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
    var h = cv.clientHeight || (cv.id === "video" ? Math.round(w * 480 / 640) : 210);
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
      (color || "#8e8e93") + '">' + tag + '</span><span>' + text + "</span>";
    el.log.insertBefore(row, el.log.firstChild);
    while (el.log.childNodes.length > 200) el.log.removeChild(el.log.lastChild);
  }

  /* ------------------------------------------------------- 同源预览 + 同帧检测框 */
  function setPreviewBadge(kind, text) {
    el.videoBadge.className = "badge" + (kind ? " " + kind : "");
    el.videoBadge.textContent = text;
  }

  function clearPreviewImage(message) {
    if (state.previewObjectUrl) URL.revokeObjectURL(state.previewObjectUrl);
    state.previewObjectUrl = "";
    state.previewMeta = null;
    state.previewEtag = "";
    el.previewImage.removeAttribute("src");
    el.previewImage.classList.remove("ready");
    el.videoPlaceholder.hidden = false;
    el.videoPlaceholderText.textContent = message || "等待同源预览";
    el.previewFreshness.textContent = "—";
    el.previewFreshness.className = "badge";
    el.previewSync.textContent = "未配对";
    el.previewSync.className = "badge";
    drawVideo();
  }

  function drawVideo() {
    var c = fitCanvas(el.video);
    var ctx = c.ctx, w = c.w, h = c.h;
    ctx.clearRect(0, 0, w, h);
    var meta = state.previewMeta;
    if (!meta) {
      if (state.mode === "offline" && state.lastFrame && state.lastFrame.face.bbox[2] > 0) {
        meta = {
          frameId: state.lastFrame.frame_id, bbox: state.lastFrame.face.bbox,
          sourceWidth: 640, sourceHeight: 480, faceVisible: state.lastFrame.face.visible,
          status: state.lastFrame.status, mock: true
        };
      } else {
        return;
      }
    }
    if (!U.shouldDrawBbox(state.lastFrame, meta)) return;
    var mapped = P.mapBbox(meta.bbox, w, h, meta.sourceWidth, meta.sourceHeight);
    if (!mapped) {
      return;
    }
    var bx = mapped.x, by = mapped.y, bw = mapped.width, bh = mapped.height;
    var color = STATUS_COLOR[meta.status] || "#007aff";
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.strokeRect(bx, by, bw, bh);
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
    ctx.fillText((meta.mock ? "mock face " : "face ") + Number(meta.faceVisible).toFixed(2),
                 bx, Math.max(14, by - 7));
  }

  /* ---------------------------------------------------------------- 状态区 */
  function renderState(frame) {
    var st = frame ? frame.status : "waiting";
    var color = STATUS_COLOR[st] || "#8e8e93";
    el.stateLamp.style.background = color;
    el.stateLamp.style.color = color;
    el.stateName.textContent = frame ? (M.STATUS_ZH[st] || st) : "等待数据";
    el.stateName.style.color = color;
    el.stateEn.textContent = frame ? st : "waiting";

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
    var measurement = U.measurementState(frame);
    var measured = measurement === U.MEASUREMENT.MEASURED;
    GATE_DEFS.forEach(function (g) {
      var row = $("gate_" + g.key);
      if (!row) return;
      var v = measured ? g.get(frame) : null;
      var ok = gatePass(g, v);
      var has = !(v === null || v === undefined || !isFinite(v));
      var limit = THRESHOLDS[g.limitKey];
      // 条形图统一成"越长越好"：max 型（运动，越小越好）取 1-v 翻转
      var frac = has ? (g.dir === "min" ? v : 1 - v) : 0;
      frac = Math.max(0, Math.min(1, frac));
      var limitFrac = g.dir === "min" ? limit : 1 - limit;
      row.querySelector(".fill").style.transform = "scaleX(" + frac.toFixed(4) + ")";
      row.querySelector(".mark").style.left = (limitFrac * 100).toFixed(1) + "%";
      row.querySelector(".val").textContent = has
        ? v.toFixed(3) + (g.dir === "min" ? " ≥ " : " ≤ ") + limit
        : "—";
      row.classList.toggle("fail", measured && !ok);
      if (measured && !ok && !g.extra) failed.push(g.label);
    });

    var st = frame ? frame.status : null;
    if (!frame) {
      el.gateVerdictText.textContent = "等待数据…";
      el.gateVerdictText.style.color = "";
      el.gateVerdictSub.textContent = "—";
    } else if (st === "disconnected") {
      el.gateVerdictText.textContent = "无数据";
      el.gateVerdictText.style.color = STATUS_COLOR.disconnected;
      el.gateVerdictSub.textContent = "视频源或连接中断，门控不适用";
    } else if (measurement === U.MEASUREMENT.NO_FACE) {
      el.gateVerdictText.textContent = "无测量";
      el.gateVerdictText.style.color = STATUS_COLOR.unreliable;
      el.gateVerdictSub.textContent = "未检测到人脸";
    } else if (failed.length === 0) {
      el.gateVerdictText.textContent = "可以测量";
      el.gateVerdictText.style.color = STATUS_COLOR.normal;
      el.gateVerdictSub.textContent = "四项门控全部通过，下方指标可信";
    } else {
      el.gateVerdictText.textContent = "测不准，别采信";
      el.gateVerdictText.style.color = STATUS_COLOR.disconnected;
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
    var cur = U.measurementState(frame) === U.MEASUREMENT.MEASURED ? frame.behavior.blink_state : null;
    BLINK_ORDER.forEach(function (s) {
      var d = $("blink_" + s);
      if (!d) return;
      var on = (s === cur);
      var col = BLINK_COLOR[s] || "#8e8e93";
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
      div.dataset.group = d.gate ? "vital" : (["quality", "light", "motion"].indexOf(d.key) >= 0 ? "quality" :
        (["visible", "yaw"].indexOf(d.key) >= 0 ? "face" : "behavior"));
      div.innerHTML = '<div class="card-top"><span class="k">' + d.label + '</span><span class="card-meta">当前</span></div>' +
        '<div class="v"><span class="num">—</span><span class="u">' +
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
    var measurement = U.measurementState(frame);
    CARD_DEFS.forEach(function (d) {
      var card = $("card_" + d.key);
      if (!card) return;
      var num = card.querySelector(".num");
      var unit = card.querySelector(".u");
      var note = card.querySelector(".note");
      var v = U.measurementValue(frame, d.get);
      var text, isMsg = false, isWarn = false, noteText = "";

      if (measurement !== U.MEASUREMENT.MEASURED) {
        text = U.placeholder(frame);
        isMsg = true;
      } else if (d.gate && frame) {
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
      if (measurement === U.MEASUREMENT.MEASURED && isMsg && d.staticNote) parts.push(d.staticNote);
      if (noteText) parts.push(noteText);
      if (note) note.textContent = parts.join(" · ");
      card.classList.toggle("warning", isWarn);
      num.style.color = isWarn ? STATUS_COLOR.fatigue_risk : "";
    });
  }

  /* ---------------------------------------------------------------- 曲线 */
  function renderChart() {
    var c = fitCanvas(el.chart);
    var ctx = c.ctx, w = c.w, h = c.h;
    var padL = 34, padR = 8, padT = 10, padB = 18;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);

    // 网格 + y 轴刻度（0 / 50 / 100，按各自量纲归一化到 0~1 后绘制）
    ctx.strokeStyle = "#e6ebee";
    ctx.fillStyle = "#89949e";
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
      ctx.fillStyle = "#89949e";
      ctx.textAlign = "center";
      ctx.font = "12px 'Segoe UI', 'Microsoft YaHei', sans-serif";
      ctx.fillText("等待数据…", w / 2, h / 2);
      return;
    }

    function series(getter, scale, max) {
      ctx.beginPath();
      var started = false;
      for (var i = 0; i < pts.length; i++) {
        var v = U.shouldPlot(pts[i]) ? getter(pts[i]) : null;
        if (v === null || v === undefined || !isFinite(v)) { started = false; continue; }
        var x = padL + (w - padL - padR) * (pts.length === 1 ? 0 : i / (MAX_POINTS - 1));
        var nv = Math.max(0, Math.min(1, max ? v / scale : v));
        var y = padT + (h - padT - padB) * (1 - nv);
        if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
      }
      ctx.stroke();
    }

    var defs = [
      { color: "#007aff", get: function (p) { return p.behavior.blink_rate_per_min; }, scale: 40 },
      { color: "#ff9500", get: function (p) { return p.behavior.perclos; }, scale: 1 },
      { color: "#34c759", get: function (p) { return p.vital.hr_bpm; }, scale: 140 },
      { color: "#af52de", get: function (p) { return p.quality.overall; }, scale: 1 }
    ];
    defs.forEach(function (d) {
      ctx.strokeStyle = d.color;
      ctx.lineWidth = 1.8;
      ctx.lineJoin = "round";
      series(d.get, d.scale, true);
    });

    // 时间轴两端标注
    ctx.fillStyle = "#89949e";
    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.textAlign = "left";
    ctx.fillText(nowStr(pts[0].ts) || "", padL, h - 5);
    ctx.textAlign = "right";
    ctx.fillText(nowStr(pts[pts.length - 1].ts) || "", w - padR, h - 5);
  }

  /* ------------------------------------------------------- 收帧 / 校验 / 渲染 */
  function onFrame(frame) {
    if (frame && frame._synthetic_disconnect) frame = synthesizeDisconnected();

    var errs = M.validateFrame(frame);
    if (errs.length) {
      state.invalid++;
      el.validName.textContent = "✗ " + state.invalid;
      el.validPill.style.color = STATUS_COLOR.disconnected;
      el.validPill.style.borderColor = "#f0d9dc";
      log("契约✗", "frame_id=" + frame.frame_id + " 不合契约：" + errs[0] + "（共 " + errs.length + " 项）", STATUS_COLOR.disconnected);
      return;
    }
    state.valid++;
    el.validName.textContent = "✓ " + state.valid;
    el.validPill.style.color = STATUS_COLOR.normal;
    el.validPill.style.borderColor = "#cce8dd";

    state.lastFrame = frame;
    state.frameId = frame.frame_id;

    if (frame.status !== state.lastStatus) {
      var from = state.lastStatus === null ? "（首帧）" : (M.STATUS_ZH[state.lastStatus] || state.lastStatus);
      log("状态迁移", from + " → <b style='color:" + (STATUS_COLOR[frame.status] || "#8e8e93") + "'>" +
          (M.STATUS_ZH[frame.status] || frame.status) + "</b> · " + frame.reason,
          STATUS_COLOR[frame.status] || "#8e8e93");
      state.lastStatus = frame.status;
    }

    // 断流帧不进趋势曲线：把它当"这一段没有数据"（曲线留空），而不是"测出来是 0"。
    // 兜底帧是 mock 造的 disconnected，它的 perclos / 眨眼率 / 质量都是 0，
    // 画上去会变成一根掉到 0 的假尖峰 —— 那等于替系统编了一个它没测到的结论。
    if (frame.status !== "disconnected") {
      state.points.push(frame);
      while (state.points.length > MAX_POINTS) state.points.shift();
    }

    renderSource();
    el.modeName.textContent = "软件模式";
    if (state.mode !== "ws") {
      setPreviewBadge("warn", "模拟定位框 · 非真实视频");
      el.previewFreshness.textContent = "离线 Mock";
      el.previewSync.textContent = "同一 Mock 帧";
      el.previewSync.className = "badge ok";
    } else if (frame.status === "disconnected" && !state.previewMeta) {
      setPreviewBadge("danger", "预览中断");
    }

    renderState(frame);
    renderCards(frame);
    renderGate(frame);
    renderBlink(frame);
    drawVideo();
    renderChart();
  }

  function synthesizeDisconnected() {
    return M.mockFrame(-1, { status: "disconnected" });
  }

  /* ------------------------------------------- 与后端同步（地址 + 阈值） */
  function renderSource() {
    if (!el.srcName || !el.srcPill) return;
    var view = U.sourceView(state.mode, state.serverSource);
    el.srcName.textContent = view.label;
    el.srcPill.className = "pill" + (view.kind ? " " + view.kind : "");
  }

  function setServerSource(source) {
    state.serverSource = source === "mock" || source === "ingest" ? source : "";
    state.hostedServerSource = state.serverSource;
    renderSource();
  }

  function rememberHostedServerSource(source) {
    state.hostedServerSource = source === "mock" || source === "ingest" ? source : "";
    if (state.mode === "stopped" || state.usesApiStatus) {
      state.serverSource = state.hostedServerSource;
      renderSource();
    }
  }

  function isSameSourceApiWs(url) {
    if (location.protocol !== "http:" && location.protocol !== "https:") return false;
    try {
      var parsed = new URL(url, location.href);
      return parsed.host === location.host && parsed.pathname === "/ws";
    } catch (e) {
      return false;
    }
  }

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
          log("系统", "检测到本页由 api.py 托管，地址已自动填为 " + want, "#007aff");
        }
        rememberHostedServerSource(j.source);
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
      log("系统", "阈值已按 config.yaml 更新：" + changed.join("、"), "#8944ab");
      renderGate(state.lastFrame);
      renderCards(state.lastFrame);
    }
  }

  /* ------------------------------------------- 同源 JPEG 旁路（约 12 Hz） */
  function previewEndpointAvailable() {
    return location.protocol === "http:" || location.protocol === "https:";
  }

  function updatePreviewFreshness() {
    if (!state.previewLastRxAt || !state.previewMeta) return;
    var age = Date.now() - state.previewLastRxAt;
    el.previewFreshness.textContent = age < 1000 ? "刚刚" : (age / 1000).toFixed(1) + "s 前";
    el.previewFreshness.className = "badge" + (age > PREVIEW_STALE_MS ? " danger" : "");
    if (age > PREVIEW_STALE_MS) {
      setPreviewBadge("danger", "预览中断");
      el.previewSync.textContent = "已清除过期框";
      el.previewSync.className = "badge danger";
      state.previewMeta = null;
      el.previewImage.classList.remove("ready");
      el.videoPlaceholder.hidden = false;
      el.videoPlaceholderText.textContent = "预览已停止更新";
      drawVideo();
    }
  }

  function renderPreviewDebug(meta, etag) {
    if (!meta) {
      el.previewDebug.textContent = "preview: 尚未收到同源 JPEG";
      return;
    }
    el.previewDebug.textContent = "preview: frame_id=" + meta.frameId +
      " · " + meta.sourceWidth + "×" + meta.sourceHeight +
      " · bbox=[" + meta.bbox.join(",") + "]" +
      " · detector=" + (meta.detector || "unknown") + " · etag=" + etag;
  }

  function replacePreviewImage(blob, meta, etag) {
    var nextUrl = URL.createObjectURL(blob);
    var probe = new Image();
    probe.onload = function () {
      var old = state.previewObjectUrl;
      state.previewObjectUrl = nextUrl;
      state.previewMeta = meta;
      state.previewEtag = etag || "";
      state.previewLastRxAt = Date.now();
      el.previewImage.src = nextUrl;
      el.previewImage.classList.add("ready");
      el.videoPlaceholder.hidden = true;
      if (old) URL.revokeObjectURL(old);
      setPreviewBadge("ok", "同源预览");
      el.previewSync.textContent = "frame " + meta.frameId + " 同帧框";
      el.previewSync.className = "badge ok";
      if (meta.detector === "stub") {
        setPreviewBadge("warn", "预览帧 · stub 框（非人脸检测）");
      }
      renderPreviewDebug(meta, state.previewEtag);
      updatePreviewFreshness();
      drawVideo();
    };
    probe.onerror = function () {
      URL.revokeObjectURL(nextUrl);
      setPreviewBadge("danger", "JPEG 解码失败");
    };
    probe.src = nextUrl;
  }

  function fetchPreviewOnce() {
    if (!previewEndpointAvailable() || !state.usesApiStatus || state.previewBusy || state.mode !== "ws") return;
    state.previewBusy = true;
    var headers = state.previewEtag ? { "If-None-Match": state.previewEtag } : {};
    fetch("/api/preview/latest", { cache: "no-store", headers: headers })
      .then(function (response) {
        if (response.status === 304) return null;
        if (response.status === 404) {
          if (!state.previewMeta) {
            setPreviewBadge("warn", "等待同源预览");
            el.videoPlaceholderText.textContent = "A 线尚未推送同源 JPEG";
          }
          return null;
        }
        if (!response.ok) throw new Error("HTTP " + response.status);
        var meta = P.parseMeta(response.headers);
        var etag = response.headers.get("etag") || "";
        return response.blob().then(function (blob) { return { blob: blob, meta: meta, etag: etag }; });
      })
      .then(function (packet) {
        if (packet) replacePreviewImage(packet.blob, packet.meta, packet.etag);
      })
      .catch(function (err) {
        if (state.previewMeta) setPreviewBadge("danger", "预览读取失败");
        el.previewDebug.textContent = "preview error: " + err.message;
      })
      .then(function () { state.previewBusy = false; });
  }

  function startPreviewPolling() {
    if (!previewEndpointAvailable() || !state.usesApiStatus) return;
    if (!state.previewTimer) state.previewTimer = setInterval(fetchPreviewOnce, PREVIEW_POLL_MS);
    if (!state.previewWatchdog) state.previewWatchdog = setInterval(updatePreviewFreshness, 250);
    fetchPreviewOnce();
  }

  function stopPreviewPolling(clearImage) {
    if (state.previewTimer) { clearInterval(state.previewTimer); state.previewTimer = null; }
    if (state.previewWatchdog) { clearInterval(state.previewWatchdog); state.previewWatchdog = null; }
    state.previewBusy = false;
    if (clearImage) clearPreviewImage("等待同源预览");
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
    if ((location.protocol !== "http:" && location.protocol !== "https:") || !state.usesApiStatus) return;
    if (state.pollTimer) return;
    state.pollTimer = setInterval(function () {
      if (state.mode !== "ws") return;
      fetch("/api/status", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || !j.ok) return;
          setServerSource(j.source);
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
    stopPreviewPolling(false);
    state.triggers = null;
    state.usesApiStatus = false;
    state.mode = "stopped";
    renderSource();
    if (!silent) log("系统", "已停止数据源", "#5d6b85");
  }

  function connectWs() {
    stopAll(true);
    clearPreviewImage("等待同源预览");
    var url = el.wsUrl.value.trim();
    if (!url) { log("错误", "请填写 WebSocket 地址", STATUS_COLOR.disconnected); return; }

    state.mode = "ws";
    state.usesApiStatus = isSameSourceApiWs(url);
    state.serverSource = state.usesApiStatus ? state.hostedServerSource : "";
    renderSource();
    setConn("", "连接中…");
    log("系统", "连接 " + url, "#007aff");
    pollTriggers();
    startPreviewPolling();
    if (!state.usesApiStatus) {
      setPreviewBadge("warn", "独立 WebSocket · 无同源预览");
      el.videoPlaceholderText.textContent = "当前连接未声明同源预览";
    }

    var ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      log("错误", "地址不合法：" + e.message + "（形如 ws://127.0.0.1:8765）", STATUS_COLOR.disconnected);
      setConn("down", "地址错误");
      return;
    }
    state.ws = ws;

    state.lastRxAt = Date.now();
    ws.onopen = function () {
      setConn("live", "已连接");
      log("系统", "WebSocket 已连接", STATUS_COLOR.normal);
    };
    ws.onmessage = function (ev) {
      state.lastRxAt = Date.now();
      var frame;
      try { frame = JSON.parse(ev.data); }
      catch (e) { log("错误", "收到非 JSON 消息，已丢弃：" + String(ev.data).slice(0, 60), STATUS_COLOR.disconnected); return; }
      // api.py 的 /api/status 包了一层 frame；/ws 直接推裸帧，这里两种都吃
      onFrame(frame.frame && frame.frame.status ? frame.frame : frame);
    };
    ws.onerror = function () {
      log("错误", "WebSocket 出错（服务是否已启动？地址与端口是否正确？）", STATUS_COLOR.disconnected);
    };
    ws.onclose = function () {
      if (state.mode !== "ws") return;
      setConn("down", "连接中断");
      log("系统", "WebSocket 已断开", STATUS_COLOR.disconnected);
      onFrame(synthesizeDisconnected());
    };

    // 超时兜底：超过 WS_TIMEOUT_MS 没收到帧 → 界面上明确显示"信号不可靠/连接中断"
    state.timer = setInterval(function () {
      if (state.mode !== "ws") return;
      if (Date.now() - state.lastRxAt > WS_TIMEOUT_MS) {
        setConn("down", "无数据");
        if (!state.lastFrame || state.lastFrame.status !== "disconnected") {
          log("系统", "超过 " + (WS_TIMEOUT_MS / 1000) + "s 未收到帧 → 显示连接中断", STATUS_COLOR.fatigue_risk);
          onFrame(synthesizeDisconnected());
        }
      }
    }, 500);
  }

  function startMonitoring() {
    if (location.protocol === "file:" || location.protocol === "") {
      startOffline();
      return;
    }
    el.wsUrl.value = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
    connectWs();
  }

  /* ------------------------------------------------------- 离线 mock（无后端） */
  function startOffline() {
    stopAll(true);
    clearPreviewImage("离线 Mock · 非真实视频");
    state.mode = "offline";
    state.serverSource = "";
    state.usesApiStatus = false;
    renderSource();
    state.lastRxAt = Date.now();
    setConn("mock", "离线模式");
    log("系统", "离线 Mock 演示：不依赖后端、不依赖摄像头（契约与后端 mock 同一套语义）", STATUS_COLOR.fatigue_risk);

    function tick() {
      var statuses = M.DEMO_SEQUENCE;
      var st = state.forcedStatus || statuses[state.frameId % statuses.length];
      onFrame(M.mockFrame(state.frameId, { status: st }));
      state.frameId++;
    }
    tick();
    state.timer = setInterval(tick, MOCK_PERIOD_MS);
  }

  /* ---------------------------------------------------------------- 契约自检 */
  function runSelftest() {
    var res = M.selfTest(20260910);
    var uiRes = U.selfTest();
    if (res.ok && uiRes.ok) {
      log("前端自检", "通过：契约 " + res.checked + " 项，UI 语义 " + uiRes.checked + " 项", STATUS_COLOR.normal);
      alert("前端自检通过 ✓\n\n· 契约检查 " + res.checked + " 项\n· UI 语义检查 " + uiRes.checked + " 项\n\n（跨语言检查请跑：node frontend/mock.js --limit 6 | python metrics/scripts/check_frontend_contract.py -）");
    } else {
      var failures = res.failures.concat(uiRes.failures);
      log("前端自检", "失败：" + failures.join("；"), STATUS_COLOR.disconnected);
      alert("前端自检失败 ✗\n\n" + failures.join("\n"));
    }
  }

  /* ---------------------------------------------------------------- 初始化 */
  function init() {
    el = {
      previewImage: $("previewImage"), video: $("video"), videoBadge: $("videoBadge"),
      videoPlaceholder: $("videoPlaceholder"), videoPlaceholderText: $("videoPlaceholderText"),
      previewFreshness: $("previewFreshness"), previewSync: $("previewSync"),
      previewDebug: $("previewDebug"), chart: $("chart"),
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
    $("btnStart").onclick = startMonitoring;
    $("btnOffline").onclick = startOffline;
    $("btnStop").onclick = function () { stopAll(false); clearPreviewImage("已停止数据源"); setConn("down", "未连接"); };
    $("btnSelftest").onclick = runSelftest;
    $("btnClear").onclick = function () { el.log.innerHTML = ""; };
    el.forceStatus.onchange = function () {
      state.forcedStatus = el.forceStatus.value;
      log("系统", state.forcedStatus ? "强制状态：" + M.STATUS_ZH[state.forcedStatus] : "恢复六态轮转", "#8944ab");
    };

    window.addEventListener("resize", function () { drawVideo(); renderChart(); });
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(function () { drawVideo(); }).observe(el.video.parentElement);
    }

    renderState(null);
    renderCards(null);
    renderGate(null);
    renderBlink(null);
    drawVideo();
    renderSource();
    renderChart();
    log("系统", "页面就绪。点\"离线 Mock 演示\"即可看六态；填好地址后点\"连接 WebSocket\"接后端。", "#007aff");

    syncWithServer();

    // file:// 离线打开时自动演示；由 api.py 托管时必须等待用户主动连接，
    // 避免把 Mock 六态误认为摄像头/后端的实时数据。
    if (location.protocol === "file:" || location.protocol === "") {
      $("btnStartLabel").textContent = "重新演示";
      startOffline();
    } else {
      state.mode = "stopped";
      setConn("down", "未连接");
      log("系统", "实时页面已就绪：请点击“连接 WebSocket”；当前不自动播放 Mock", "#007aff");
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
