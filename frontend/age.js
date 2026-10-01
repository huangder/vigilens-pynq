/* =============================================================================
 * age.js —— 「年龄档案 · 同龄基线比对」面板（B 线，零依赖，原生 JS）
 * =============================================================================
 * 为什么单独一个文件、而不是塞进 app.js：
 *   年龄档案件是**旁路载荷**（唯一 schema 见 backend/age_contract.py）：它既不进冻结的
 *   9 字段契约帧，也不参与 app.js 的接线。把轮询、渲染、离线演示全部收在这里，
 *   app.js 就基本不用动 —— 少动一处就少一处返工的理由。
 *
 * 三条产品承诺（与 age_contract.py 的注释一一对应，界面不许走样）：
 *   1. 没有证据的推断不冒充结论：estimate.placeholder 为真时显示
 *      「占位引擎 · 无精度证据，不得作为结论」；engine=none 时显示「本次未做推断」，
 *      **绝不把一个空推断渲染成一次结果**；
 *   2. 一次正式确认之后图像推断停用：profile.confirmed && profile.locked 时显示
 *      「已确认年龄，年龄推测已停用」；
 *   3. 不可比时**一个数字都不显示**（comparison.comparable === false）——
 *      「先判断能不能测，再决定测出什么」在产品上的落点。
 *
 * 离线降级（后端更老 / 后端没起 / file:// 双击打开）：
 *   走 frontend/mock.js 的确定性演示器，并且**处处标注"离线演示"**；
 *   数据库统计在离线时明确写"不可用"，不编造 session 数。
 *   轮询失败按指数退避（2 s → 最多 15 s），连续失败 3 次即切离线演示；
 *   之后仍慢速探测，后端回来就自动切回真实数据。
 *   file:// 下**不发任何 fetch**（没有同源后端，发了只会在控制台刷错误）。
 *
 * 接口（全部来自 docs/interface.md 之外的年龄旁路约定，见任务契约）：
 *   GET  /api/age                     → {ok, age, server_time}
 *   POST /api/age                     → {action:"set_manual"|"accept_estimate"|
 *                                        "reject_estimate"|"enroll_decision"|"reset", ...}
 *   GET  /api/age/events?limit=50      → {ok, count, events:[{at,kind,detail}]}
 *   GET  /api/fatigue_db/summary       → {ok, db:{...}, recent:[...]}
 * ========================================================================== */
(function (root, factory) {
  "use strict";
  var api = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VigiLensAge = api;
  // 浏览器里自动接线；Node 里没有 document，直接跳过（本文件仍可被 require 做纯函数检查）
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { api.start(); });
    } else {
      api.start();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  var POLL_MS = 2000;           // 在线轮询间隔
  var MAX_POLL_MS = 15000;      // 指数退避上限；也是离线时的慢速探测间隔
  var OFFLINE_AFTER = 3;        // 连续失败这么多次 → 切离线演示
  var AUX_MS = 10000;           // 事件日志 / 数据库统计的刷新间隔
  var ACTION_MSG_MS = 10000;    // 动作提示的显示时长
  var OFFLINE_TICK_MS = 1000;   // 离线演示的推进节奏（与契约帧 1 帧/秒同口径）
  var EVENT_LIMIT = 50;
  var SEED = 20260910;          // 离线演示的确定性种子（与 mock.js 默认值一致）

  var ENROLL_ZH = { idle: "未开始", observing: "正在观察", enrolled: "已收录",
                    rejected: "不收录", locked: "已锁定" };
  var RISK_ZH = { low: "低", medium: "中", high: "高", unknown: "未知" };
  /* 事件 kind → 中文。取值来自 backend/age_infer.py::EVENT_KINDS（未知 kind 原样显示） */
  var EVENT_ZH = {
    enroll_observe_start: "开始观察收录",
    enroll_observe_restart: "换人 · 重启观察",
    enroll_decided: "收录判定",
    estimate_proposed: "提出年龄推断",
    estimate_rejected: "忽略推断",
    manual_confirmed: "手动确认年龄",
    estimate_accepted: "接受图像推断",
    profile_reset: "重置档案",
    inference_refused_locked: "锁定后拒绝推断调用"
  };

  var el = {};
  var state = {
    started: false,
    mode: "stopped",      // live | offline | probing | stopped
    pollTimer: null,
    auxTimer: null,
    offlineTimer: null,
    actionTimer: null,
    failCount: 0,
    lastError: "",
    lastServerTime: null,
    offlineFrameId: 0,
    busy: false
  };

  /* ---------------------------------------------------------------- 小工具 */
  function $(id) { return document.getElementById(id); }
  function setText(node, text) { if (node) node.textContent = text; }
  function show(node, visible) { if (node) node.hidden = !visible; }
  function numOrNull(v) { return (typeof v === "number" && isFinite(v)) ? v : null; }
  function fmt(v, digits) {
    var n = numOrNull(v);
    return n === null ? "—" : n.toFixed(digits);
  }
  function intOrDash(v) {
    return (typeof v === "number" && isFinite(v)) ? String(Math.trunc(v)) : "—";
  }
  function clockStr(at) {
    var n = numOrNull(at);
    if (n === null) return "--:--:--";
    var d = new Date(n * 1000);
    return isNaN(d.getTime()) ? "--:--:--" : d.toTimeString().slice(0, 8);
  }
  function mockApi() {
    return root.VigiLensMock || (typeof window !== "undefined" ? window.VigiLensMock : null);
  }
  // file:// 下没有同源后端：**一次 fetch 都不发**，这样控制台不会出现任何报错
  function isHttp() {
    return typeof location !== "undefined" &&
      (location.protocol === "http:" || location.protocol === "https:");
  }

  function bandZh(band) {
    var M = mockApi();
    var globalBand = M ? M.GLOBAL_BAND : "global";
    if (band === null || band === undefined || band === "") return "无可用同龄基线";
    if (band === globalBand) return "global · 未分龄兜底（全人群混合基线）";
    if (M && M.AGE_BAND_ZH && M.AGE_BAND_ZH[band]) return M.AGE_BAND_ZH[band];
    return String(band);
  }

  /* 来源 badge。⚠️ "接受图像推断"必须由 confirmed_by 决定，不能只看 source：
     同一个 source=image_estimate 既可能是"还没确认的建议"，也可能是"用户点过接受"。 */
  function sourceLabel(p) {
    if (p.confirmed_by === "user_accepted_estimate") return "接受图像推断";
    if (p.confirmed_by === "user_manual" || p.source === "manual_input") return "手动输入";
    if (p.source === "enrolled_profile") return "已收录档案";
    if (p.source === "image_estimate") return "图像推断（未确认）";
    return "未定";
  }

  /* ------------------------------------------------------------ 当前档案行 */
  function renderProfile(p, demo) {
    var line;
    if (p.confirmed) {
      line = "已确认 " + intOrDash(p.age_years) + " 岁 · " + bandZh(p.band);
    } else {
      line = "未确定";
    }
    setText(el.stateText, (demo ? "离线演示 · " : "") + line);

    setText(el.sourceBadge, "来源：" + sourceLabel(p));
    if (el.sourceBadge) el.sourceBadge.className = "badge" + (p.confirmed ? " ok" : "");

    // 锁定 badge：这是用户明确要求的行为（确认一次之后不再用图像推测）
    show(el.lockBadge, p.locked === true);

    setText(el.subjectText, p.subject ? "匿名主体 " + p.subject + "（不可逆签名哈希，不是人脸照片）"
                                      : "匿名主体 ——（尚未收录）");
  }

  /* ------------------------------------------------------------ 图像推断卡 */
  function renderEstimate(es) {
    show(el.estimateWarnBadge, es.placeholder === true);
    if (es.engine === "none") {
      // 没有推断就是没有推断：**不显示任何数值**，也不显示成"未识别"
      setText(el.estimateValue, "本次未做推断");
      if (el.estimateValue) el.estimateValue.className = "age-value none";
      setText(el.estimateMeta, "引擎 none —— 未运行任何图像年龄引擎（已确认锁定 / 未启用 / 无可用引擎 / 未检测到人脸）");
      setText(el.estimateNote, "");
      return;
    }
    if (es.age_years === null && es.band === null) {
      setText(el.estimateValue, "本次未产出结果");
      if (el.estimateValue) el.estimateValue.className = "age-value none";
      setText(el.estimateMeta, "引擎 " + es.engine + " 已运行但未给出结果（推断失败不等于年龄为 0）");
      setText(el.estimateNote, es.note || "");
      return;
    }
    if (el.estimateValue) el.estimateValue.className = "age-value";
    setText(el.estimateValue, (es.age_years === null ? "—" : fmt(es.age_years, 1) + " 岁") +
      " · " + bandZh(es.band));
    var range = (es.age_low === null || es.age_high === null)
      ? "未给出区间" : fmt(es.age_low, 1) + "~" + fmt(es.age_high, 1) + " 岁";
    setText(el.estimateMeta, "引擎 " + es.engine + " · 置信度 " + fmt(es.confidence, 3) +
      " · 建议区间 " + range + (es.cv_band ? " · CV 档 " + es.cv_band : ""));
    setText(el.estimateNote, es.note || "");
  }

  /* -------------------------------------------------------------- 收录卡 */
  function renderEnrollment(en) {
    setText(el.enrollBadge, ENROLL_ZH[en.state] || en.state || "—");
    if (el.enrollBadge) el.enrollBadge.className = "badge" +
      (en.state === "enrolled" ? " ok" : en.state === "rejected" ? " danger"
        : en.state === "observing" ? " warn" : "");

    var same = (en.same_face === null || en.same_face === undefined)
      ? "未知" : (en.same_face ? "是" : "否");
    var lead = en.state === "observing"
      ? "正在判断是否收录人脸" : (ENROLL_ZH[en.state] || "收录判断") + "：";
    setText(el.enrollText, lead + " " + fmt(en.observed_s, 1) + "/" + fmt(en.window_s, 1) +
      " s · 样本 " + intOrDash(en.samples) + "/" + intOrDash(en.needed) +
      " · 同一张脸：" + same);

    var progress = numOrNull(en.progress);
    if (el.enrollFill) {
      el.enrollFill.style.transform =
        "scaleX(" + (progress === null ? 0 : Math.max(0, Math.min(1, progress))).toFixed(4) + ")";
      el.enrollFill.className = "fill" + (en.state === "rejected" ? " rejected" : "");
    }

    setText(el.enrollReason, en.reason || "—");
  }

  /* ------------------------------------------------------- 同龄基线比对卡 */
  function renderContributions(list) {
    var box = el.compareContrib;
    if (!box) return;
    box.innerHTML = "";
    if (!list.length) {
      var none = document.createElement("div");
      none.className = "age-src";
      none.textContent = "逐项贡献：无（comparison.contributions 为空）";
      box.appendChild(none);
      return;
    }
    // 条长只用来看相对大小：按 |log_odds| 归一化，不改变任何数值口径
    var maxAbs = 0;
    list.forEach(function (it) {
      var v = Math.abs(numOrNull(it && it.log_odds) === null ? 0 : it.log_odds);
      if (v > maxAbs) maxAbs = v;
    });
    if (maxAbs <= 0) maxAbs = 1;
    list.forEach(function (it) {
      it = it || {};
      var row = document.createElement("div");
      row.className = "contrib";

      var name = document.createElement("span");
      name.className = "m";
      name.textContent = (it.metric === undefined || it.metric === null) ? "（未命名字段）" : String(it.metric);

      var detail = document.createElement("span");
      detail.className = "n";
      detail.textContent = "值 " + fmt(it.value, 3) + " · 基线 " + fmt(it.baseline, 3) +
        " · z " + fmt(it.z, 3) + " · w " + fmt(it.weight, 3) +
        " · log_odds " + (numOrNull(it.log_odds) === null ? "—" : (it.log_odds >= 0 ? "+" : "") + fmt(it.log_odds, 3));

      var bar = document.createElement("span");
      bar.className = "cbar";
      var fill = document.createElement("i");
      fill.style.width = (Math.abs(numOrNull(it.log_odds) === null ? 0 : it.log_odds) / maxAbs * 100).toFixed(1) + "%";
      fill.style.background = (numOrNull(it.log_odds) !== null && it.log_odds < 0) ? "#3bd486" : "#9184ff";
      bar.appendChild(fill);

      row.appendChild(name);
      row.appendChild(detail);
      row.appendChild(bar);
      box.appendChild(row);
    });
  }

  function renderComparison(c) {
    var comparable = c.comparable === true;
    setText(el.compareVerdict, comparable
      ? "可比 —— 已按参照组基线比对（风险提示，不是诊断）"
      : "不可比 —— 条件不满足，按承诺不报数字");
    show(el.compareNumbers, comparable);
    show(el.compareIncomparable, !comparable);

    if (!comparable) {
      // 一个数字都不显示：这是产品承诺，不是功能缺失
      setText(el.compareIncomparableText, "不可比：" + (c.reason || "未给出原因"));
      return;
    }

    var risk = numOrNull(c.risk);
    if (el.compareFill) {
      el.compareFill.style.transform =
        "scaleX(" + (risk === null ? 0 : Math.max(0, Math.min(1, risk))).toFixed(4) + ")";
      el.compareFill.className = "fill" + (c.risk_level === "high" ? " high"
        : c.risk_level === "medium" ? " medium" : " low");
    }
    setText(el.compareValue, "风险 " + fmt(risk, 3) + " / 1.000");
    setText(el.compareLevel, "等级：" + (RISK_ZH[c.risk_level] || c.risk_level));
    if (el.compareLevel) el.compareLevel.className = "badge" + (c.risk_level === "high" ? " danger"
      : c.risk_level === "medium" ? " warn" : " ok");

    var M = mockApi();
    var isGlobal = c.band_applied === (M ? M.GLOBAL_BAND : "global");
    if (isGlobal) {
      setText(el.compareBand, "应用参照组：global —— 未分龄兜底（没有已确认年龄，用的是全人群混合基线，" +
        "它不是「同龄」人群，所以这一条必须照原样显示出来）");
    } else {
      setText(el.compareBand, "应用参照组：" + bandZh(c.band_applied) +
        (c.band_applied ? "（" + c.band_applied + "）" : ""));
    }
    if (el.compareBand) el.compareBand.className = "age-sub" + (isGlobal ? " warnline" : "");

    renderContributions(Array.isArray(c.contributions) ? c.contributions : []);

    setText(el.compareInteractions, (Array.isArray(c.interactions) && c.interactions.length)
      ? "触发的耦合项：" + c.interactions.join(" + ")
      : "触发的耦合项：无");

    setText(el.compareReason, c.reason || "—");

    var sources = Array.isArray(c.reference_sources) ? c.reference_sources : [];
    var head = c.reference_version ? "参照基线版本 " + c.reference_version : "参照基线版本：未标注";
    setText(el.compareSources, head + " · 来源：" +
      (sources.length ? sources.join("；") : "未列出"));
  }

  /* ------------------------------------------------------- 数据库 / 事件 */
  function renderDb(db, demo) {
    if (!db) {
      setText(el.dbText, demo
        ? "离线演示 · 未连接后端：数据库统计不可用（不编造 session 数）"
        : "尚未取到 /api/fatigue_db/summary");
      setText(el.dbBands, "—");
      return;
    }
    setText(el.dbText, "测量库 " + (db.path || "—") + " · " +
      (db.exists === true ? "已建库" : "尚未建库") +
      " · session " + intOrDash(db.sessions) +
      " · 参照基线版本 " + (db.reference_version ? db.reference_version : "未标注"));
    var bands = db.by_band && typeof db.by_band === "object" ? db.by_band : {};
    var keys = Object.keys(bands);
    if (!keys.length) {
      setText(el.dbBands, "按年龄档分组的 session 数：（暂无）");
    } else {
      setText(el.dbBands, "按年龄档分组：" + keys.map(function (k) {
        return bandZh(k) + " " + intOrDash(bands[k]);
      }).join(" · "));
    }
  }

  function detailSummary(detail) {
    if (!detail || typeof detail !== "object") return "";
    var parts = [];
    Object.keys(detail).forEach(function (k) {
      if (parts.length >= 6) return;
      var v = detail[k];
      if (v === null || v === undefined) return;
      if (typeof v === "object") return;   // 嵌套对象不展开，免得一行刷屏
      parts.push(k + "=" + String(v));
    });
    var s = parts.join(" · ");
    return s.length > 140 ? s.slice(0, 140) + "…" : s;
  }

  function renderEvents(count, events, demo) {
    var box = el.eventList;
    if (!box) return;
    box.innerHTML = "";
    show(el.eventDemoBadge, !!demo);
    var list = Array.isArray(events) ? events : [];
    setText(el.eventCount, (demo ? "（离线演示，非真实事件）" : "") + "共 " +
      intOrDash(typeof count === "number" ? count : list.length) + " 条");

    if (!list.length) {
      var li = document.createElement("li");
      li.className = "detail";
      li.textContent = demo ? "离线演示：本帧之前还没有年龄档案事件" : "（暂无年龄档案事件）";
      box.appendChild(li);
      return;
    }
    // 最新的在最上面，与开发者工具里的事件日志同向
    list.slice().reverse().forEach(function (ev) {
      ev = ev || {};
      var row = document.createElement("li");
      var t = document.createElement("time");
      t.textContent = clockStr(ev.at);
      var kind = document.createElement("span");
      kind.className = "kind";
      kind.textContent = EVENT_ZH[ev.kind] || ev.kind || "未知事件";
      var detail = document.createElement("span");
      detail.className = "detail";
      detail.textContent = detailSummary(ev.detail);
      row.appendChild(t);
      row.appendChild(kind);
      row.appendChild(detail);
      box.appendChild(row);
    });
  }

  /* ------------------------------------------------------------ 数据源状态 */
  function updateSourcePill() {
    var mode = state.mode;
    if (el.sourcePill) {
      el.sourcePill.className = "pill " + (mode === "live" ? "live" : mode === "offline" ? "mock" : "down");
    }
    if (mode === "live") {
      setText(el.sourceText, "后端 /api/age · " + (POLL_MS / 1000) + " s 轮询" +
        (state.lastServerTime === null ? "" : " · 最近 " + clockStr(state.lastServerTime)));
    } else if (mode === "offline") {
      setText(el.sourceText, "离线演示（后端 /api/age 不可用）");
    } else {
      setText(el.sourceText, "探测中…");
    }
    // 离线演示 badge：**这是"这些数字不是真实推断结果"的唯一视觉保证**，不许省
    var showDemo = mode !== "live";
    show(el.demoBadge, showDemo);
    if (showDemo && el.demoBadge) {
      el.demoBadge.className = "badge warn";
      setText(el.demoBadge, mode === "offline"
        ? "离线演示 · 数值由本地演示器产生，不是真实推断/测量结果"
        : "尚未连上后端");
    }
    // 在线但刚失败过：明确提示"这份数据可能已经过期"，不假装它是最新的
    if (mode === "live" && state.failCount > 0 && el.demoBadge) {
      show(el.demoBadge, true);
      el.demoBadge.className = "badge danger";
      setText(el.demoBadge, "上次轮询失败（" + state.failCount + " 次）：" + state.lastError + " —— 显示的可能是旧数据");
    }
  }

  function setManualError(text) { setText(el.manualError, text || ""); }

  function setAction(text, bad) {
    setText(el.actionMsg, text || "");
    if (el.actionMsg) el.actionMsg.className = "age-msg" + (bad ? " bad" : "");
    if (state.actionTimer) clearTimeout(state.actionTimer);
    state.actionTimer = null;
    if (text) {
      state.actionTimer = setTimeout(function () { setText(el.actionMsg, ""); }, ACTION_MSG_MS);
    }
  }

  /* ---------------------------------------------------------------- 渲染入口 */
  function render(age, opts) {
    opts = opts || {};
    if (!age || typeof age !== "object") return;
    var p = age.profile || {};
    var en = age.enrollment || {};
    var es = age.estimate || {};
    var c = age.comparison || {};
    var pv = age.privacy || {};

    if (Number.isFinite(numOrNull(opts.serverTime))) state.lastServerTime = opts.serverTime;
    updateSourcePill();

    renderProfile(p, !!opts.demo);
    renderEstimate(es);
    renderEnrollment(en);
    renderComparison(c);

    setText(el.privacyNote, "隐私：" + (pv.note || "—") +
      "（stores_face_image=" + String(pv.stores_face_image === true) +
      " · signature_kind=" + (pv.signature_kind || "—") + "）");
  }

  /* ---------------------------------------------------------------- 动作 */
  function fetchJSON(url, options) {
    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        var body = null;
        try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
        if (!response.ok) {
          var err = new Error("HTTP " + response.status);
          err.status = response.status;
          err.body = body;
          throw err;
        }
        if (!body || body.ok !== true) throw new Error("响应缺少 ok:true");
        return body;
      });
    });
  }

  function postAction(payload, okLabel) {
    if (!isHttp() || state.mode === "offline") {
      // 离线演示**绝不**假装提交成功：没有后端就没有结果
      setAction("离线演示：没有可用的后端 /api/age，本次动作未提交（不会伪造结果）", true);
      return Promise.resolve(null);
    }
    return fetchJSON("/api/age", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (j) {
      var M = mockApi();
      var errs = M ? M.validateAgeState(j.age) : [];
      if (errs.length) {
        setAction("后端返回的档案件未过旁路 schema：" + errs[0], true);
        return null;
      }
      render(j.age, { demo: false });
      setAction(okLabel + "（事件：" + ((j.event && j.event.kind) || "—") + "）", false);
      fetchAux();
      return j;
    }).catch(function (err) {
      var detail = "";
      if (err && err.body && err.body.errors) detail = "：" + err.body.errors.join("；");
      setAction("提交失败（" + (err && err.message ? err.message : "未知错误") + "）" + detail, true);
      return null;
    });
  }

  /* 客户端先做范围校验：明显越界的输入不该占用一次往返，也要让用户当场看到原因 */
  function submitManual() {
    var raw = el.manualInput ? String(el.manualInput.value).trim() : "";
    if (raw === "") { setManualError("请先填年龄（6~100 的整数）"); return; }
    var n = Number(raw);
    if (!isFinite(n)) { setManualError("年龄必须是数字（当前输入「" + raw + "」）"); return; }
    if (Math.trunc(n) !== n) { setManualError("年龄必须是整数（当前输入「" + raw + "」）"); return; }
    if (n < 6 || n > 100) {
      setManualError("年龄必须在 6~100 之间（当前 " + n + "）：超出范围的年龄没有可用的同龄基线，" +
                     "系统不会硬塞进某一组");
      return;
    }
    setManualError("");
    postAction({ action: "set_manual", age_years: n }, "已确认年龄 " + n + " 岁");
  }

  /* ---------------------------------------------------------------- 轮询 */
  function schedulePoll(delay) {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(fetchAge, delay);
  }

  function onAgeOk(j) {
    var M = mockApi();
    var errs = M ? M.validateAgeState(j.age) : [];
    if (errs.length) { onAgeFail(new Error("年龄档案件未过旁路 schema：" + errs[0])); return; }
    state.failCount = 0;
    state.lastError = "";
    if (state.mode !== "live") enterLive();
    render(j.age, { demo: false, serverTime: j.server_time });
    schedulePoll(POLL_MS);
  }

  function onAgeFail(err) {
    state.failCount++;
    state.lastError = (err && err.message) ? err.message : String(err);
    if (state.failCount >= OFFLINE_AFTER) enterOffline(state.lastError);
    var delay = state.mode === "offline"
      ? MAX_POLL_MS                                                     // 离线：慢速探测，等后端回来
      : Math.min(MAX_POLL_MS, POLL_MS * Math.pow(2, state.failCount - 1)); // 在线：指数退避
    updateSourcePill();
    schedulePoll(delay);
  }

  function fetchAge() {
    if (state.mode === "stopped" || !isHttp()) return;
    if (state.busy) { schedulePoll(POLL_MS); return; }
    state.busy = true;
    fetchJSON("/api/age")
      .then(onAgeOk)
      .catch(onAgeFail)
      .then(function () { state.busy = false; });
  }

  function fetchAux() {
    if (state.mode !== "live") return;
    // 事件日志与数据库统计是"有更好"，失败不影响主面板，也不写控制台错误
    fetchJSON("/api/age/events?limit=" + EVENT_LIMIT).then(function (j) {
      renderEvents(j.count, j.events, false);
    }).catch(function () { /* 静默：事件列表缺失不该让整块面板显示成故障 */ });
    fetchJSON("/api/fatigue_db/summary").then(function (j) {
      renderDb(j.db || null, false);
    }).catch(function () { });
  }

  function startAuxTimer() {
    if (state.auxTimer) return;
    state.auxTimer = setInterval(fetchAux, AUX_MS);
    fetchAux();
  }

  function stopAuxTimer() {
    if (state.auxTimer) { clearInterval(state.auxTimer); state.auxTimer = null; }
  }

  /* ---------------------------------------------------------------- 模式切换 */
  function enterLive() {
    state.mode = "live";
    stopOfflineTicker();
    startAuxTimer();
    updateSourcePill();
  }

  function enterOffline(reason) {
    if (state.mode === "offline") return;
    state.mode = "offline";
    stopAuxTimer();
    startOfflineTicker();
    updateSourcePill();
    if (isHttp()) schedulePoll(MAX_POLL_MS);   // 慢速探测：后端回来就自动切回去
    if (reason) setAction("已切到离线演示（后端不可用：" + reason + "）", true);
  }

  function startOfflineTicker() {
    if (state.offlineTimer) return;
    tickOffline();
    state.offlineTimer = setInterval(tickOffline, OFFLINE_TICK_MS);
  }

  function stopOfflineTicker() {
    if (state.offlineTimer) { clearInterval(state.offlineTimer); state.offlineTimer = null; }
  }

  /* 离线演示：用 mock.js 的确定性演示器走完整生命周期（未确定 → 观察 → 不收录 → 已收录
     → 图像推断 → 已确认锁定），并**每一处都标成"离线演示"**。 */
  function tickOffline() {
    var M = mockApi();
    if (!M || typeof M.mockAgeState !== "function") return;
    var cycle = (M.AGE_LIFECYCLE && M.AGE_LIFECYCLE.cycle_frames) ? M.AGE_LIFECYCLE.cycle_frames : 34;
    var fid = state.offlineFrameId % cycle;
    state.offlineFrameId++;
    var ts = Date.now() / 1000;                        // 界面上的时间用墙钟，便于阅读
    var age = M.mockAgeState(fid, { seed: SEED, ts: ts });
    render(age, { demo: true });
    var events = (typeof M.mockAgeEvents === "function") ? M.mockAgeEvents(fid, { seed: SEED, ts: ts }) : [];
    renderEvents(events.length, events, true);
    renderDb(null, true);
  }

  /* ---------------------------------------------------------------- 初始化 */
  function collectEls() {
    el = {
      demoBadge: $("ageDemoBadge"),
      sourcePill: $("ageSourcePill"), sourceText: $("ageSourceText"),
      stateText: $("ageStateText"), sourceBadge: $("ageSourceBadge"),
      lockBadge: $("ageLockBadge"), subjectText: $("ageSubjectText"),
      privacyNote: $("agePrivacyNote"),

      estimateValue: $("ageEstimateValue"), estimateMeta: $("ageEstimateMeta"),
      estimateWarnBadge: $("ageEstimateWarnBadge"), estimateNote: $("ageEstimateNote"),

      enrollBadge: $("ageEnrollBadge"), enrollText: $("ageEnrollText"),
      enrollFill: $("ageEnrollFill"), enrollReason: $("ageEnrollReason"),
      btnEnrollAccept: $("ageBtnEnrollAccept"), btnEnrollReject: $("ageBtnEnrollReject"),

      compareVerdict: $("ageCompareVerdict"), compareNumbers: $("ageCompareNumbers"),
      compareLevel: $("ageCompareLevel"), compareValue: $("ageCompareValue"),
      compareFill: $("ageCompareFill"), compareBand: $("ageCompareBand"),
      compareContrib: $("ageCompareContrib"), compareInteractions: $("ageCompareInteractions"),
      compareReason: $("ageCompareReason"), compareSources: $("ageCompareSources"),
      compareIncomparable: $("ageCompareIncomparable"),
      compareIncomparableText: $("ageCompareIncomparableText"),

      dbText: $("ageDbText"), dbBands: $("ageDbBands"),
      eventCount: $("ageEventCount"), eventList: $("ageEventList"),
      eventDemoBadge: $("ageEventDemoBadge"),

      manualInput: $("ageManualInput"), manualError: $("ageManualError"),
      actionMsg: $("ageActionMsg"),
      btnManual: $("ageBtnManual"), btnAcceptEstimate: $("ageBtnAcceptEstimate"),
      btnRejectEstimate: $("ageBtnRejectEstimate"), btnReset: $("ageBtnReset")
    };
  }

  function bindControls() {
    if (el.btnManual) el.btnManual.onclick = submitManual;
    if (el.manualInput) {
      el.manualInput.onkeydown = function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); submitManual(); }
      };
      el.manualInput.oninput = function () { setManualError(""); };
    }
    if (el.btnAcceptEstimate) {
      el.btnAcceptEstimate.onclick = function () {
        postAction({ action: "accept_estimate" }, "已接受图像推断（确认动作由人做出，已留痕）");
      };
    }
    if (el.btnRejectEstimate) {
      el.btnRejectEstimate.onclick = function () {
        postAction({ action: "reject_estimate" }, "已忽略本次推断（本段运行不再重复提议）");
      };
    }
    if (el.btnReset) {
      el.btnReset.onclick = function () {
        postAction({ action: "reset" }, "已重置档案（解锁，图像推测重新启用）");
      };
    }
    if (el.btnEnrollAccept) {
      el.btnEnrollAccept.onclick = function () {
        postAction({ action: "enroll_decision", accept: true }, "已提交：收录这张脸");
      };
    }
    if (el.btnEnrollReject) {
      el.btnEnrollReject.onclick = function () {
        postAction({ action: "enroll_decision", accept: false }, "已提交：不收录");
      };
    }
  }

  function start() {
    if (state.started || typeof document === "undefined") return;
    state.started = true;
    collectEls();
    bindControls();

    if (!isHttp()) {
      // file:// 双击打开：直接进离线演示，一次 fetch 都不发
      state.mode = "offline";
      startOfflineTicker();
      updateSourcePill();
      setAction("本页以 file:// 打开，没有同源后端：已进入离线演示（不发送任何网络请求）", false);
      return;
    }
    state.mode = "probing";
    updateSourcePill();
    schedulePoll(0);
  }

  function stop() {
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    if (state.actionTimer) { clearTimeout(state.actionTimer); state.actionTimer = null; }
    stopAuxTimer();
    stopOfflineTicker();
    state.mode = "stopped";
    state.busy = false;
  }

  return {
    start: start,
    stop: stop,
    render: render,
    postAction: postAction,
    submitManual: submitManual,
    // 只读常量：给别的脚本/自检用，避免各处再抄一份
    POLL_MS: POLL_MS, MAX_POLL_MS: MAX_POLL_MS, OFFLINE_AFTER: OFFLINE_AFTER,
    EVENT_ZH: EVENT_ZH, ENROLL_ZH: ENROLL_ZH, RISK_ZH: RISK_ZH
  };
});
