/* Presentation alerts only; config.yaml owns thresholds. No identity or diagnosis is inferred. */
(function (root) {
  "use strict";
  function number(value) { return typeof value === "number" && isFinite(value) ? value : null; }
  function emptyCondition() { return { startedTs: null, raised: false, acknowledged: false }; }
  function createTracker() {
    return { fatigue: emptyCondition(), heart_rate: emptyCondition(), lastTs: null, lastFrameId: null, policy: "" };
  }
  function reset(tracker) { Object.assign(tracker, createTracker()); }

  function candidates(frame, thresholds) {
    var none = { fatigue: false, heart_rate: false };
    if (!frame || ["normal", "fatigue_risk"].indexOf(frame.status) < 0) return none;
    if (!frame.face || !frame.quality || !frame.vital) return none;
    var visible = number(frame.face.visible), quality = number(frame.quality.overall);
    var light = number(frame.quality.light_score), motion = number(frame.quality.motion_score);
    if (visible === null || visible <= 0 || quality === null || light === null || motion === null ||
        visible < thresholds.face_visible_min || quality < thresholds.quality_min_score ||
        light < thresholds.light_score_min || motion > thresholds.motion_score_max) return none;
    var hr = number(frame.vital.hr_bpm), highLine = number(thresholds.hr_alert_high_bpm);
    return {
      fatigue: frame.status === "fatigue_risk",
      heart_rate: hr !== null && highLine !== null && highLine > 0 &&
        quality >= thresholds.vital_require_quality && hr > highLine
    };
  }

  function current(tracker) {
    var fatigue = tracker.fatigue.raised && !tracker.fatigue.acknowledged;
    var heart = tracker.heart_rate.raised && !tracker.heart_rate.acknowledged;
    if (!fatigue && !heart) return null;
    if (fatigue && heart) return {
      kind: "combined", title: "请暂停一下，留意当前状态",
      message: "疲劳风险与高于设定提示线的心率读数均已持续超过提示时长。你可能需要休息一下。",
      advice: "请先暂停当前任务，在安全的位置坐下休息，放松肩颈、远眺片刻；状态稳定后再重新测量。"
    };
    if (heart) return {
      kind: "heart_rate", title: "心率读数持续偏高，请先休息",
      message: "心率读数持续高于设定提示线。请先放慢节奏，留意自己的感受。",
      advice: "建议暂停当前任务，在安全的位置坐下休息，放松并保持自然呼吸；状态稳定后再重新测量。"
    };
    return {
      kind: "fatigue", title: "你可能有些疲劳，休息一下吧",
      message: "监测到疲劳风险状态持续超过提示时长，请给自己一段休息时间。",
      advice: "建议暂停当前任务，远眺或闭眼放松，起身活动前先确保周围安全；休息后再继续。"
    };
  }

  function update(tracker, frame, thresholds) {
    var duration = number(thresholds.alert_duration_seconds);
    var maxGap = number(thresholds.ws_disconnect_timeout_s);
    var ts = frame ? number(frame.ts) : null, id = frame ? number(frame.frame_id) : null;
    if (duration === null || duration <= 0 || maxGap === null || maxGap <= 0 || ts === null || id === null) {
      reset(tracker);
      return null;
    }
    var policy = JSON.stringify(thresholds);
    if (tracker.policy !== policy || tracker.lastTs !== null &&
        (ts < tracker.lastTs || id < tracker.lastFrameId || ts - tracker.lastTs > maxGap)) reset(tracker);
    tracker.policy = policy;
    // Missing/unreliable/finished frames reset even with a duplicate disconnect timestamp.
    var eligible = candidates(frame, thresholds);
    if (!eligible.fatigue && !eligible.heart_rate) {
      tracker.fatigue = emptyCondition();
      tracker.heart_rate = emptyCondition();
      tracker.lastTs = ts;
      tracker.lastFrameId = id;
      return null;
    }
    if (tracker.lastTs !== null && (ts === tracker.lastTs || id === tracker.lastFrameId)) return null;
    tracker.lastTs = ts;
    tracker.lastFrameId = id;
    var raised = false;
    ["fatigue", "heart_rate"].forEach(function (key) {
      if (!eligible[key]) { tracker[key] = emptyCondition(); return; }
      var condition = tracker[key];
      if (condition.startedTs === null) condition.startedTs = ts;
      // The user requested MORE than ten seconds; equality does not trigger.
      if (!condition.raised && ts - condition.startedTs > duration) {
        condition.raised = true;
        raised = true;
      }
    });
    return raised ? current(tracker) : null;
  }

  function dismiss(tracker) {
    ["fatigue", "heart_rate"].forEach(function (key) {
      if (tracker[key].raised) tracker[key].acknowledged = true;
    });
  }
  var API = { createTracker: createTracker, update: update, current: current, dismiss: dismiss };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.VigiLensAlerts = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
