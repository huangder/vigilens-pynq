/* Explicitly synthetic fixtures test UI behavior, not measurement accuracy. */
const test = require("node:test");
const assert = require("node:assert/strict");
const Alerts = require("../alerts.js");
const thresholds = {
  alert_duration_seconds: 10, hr_alert_high_bpm: 100, ws_disconnect_timeout_s: 3,
  vital_require_quality: 0.75, quality_min_score: 0.6, face_visible_min: 0.7,
  light_score_min: 0.5, motion_score_max: 0.35
};
function frame(ts, options = {}) {
  return {
    ts, frame_id: options.id === undefined ? ts : options.id,
    status: options.status || "normal",
    face: { visible: options.visible === undefined ? 0.95 : options.visible },
    quality: { overall: options.quality === undefined ? 0.9 : options.quality,
      light_score: options.light === undefined ? 0.8 : options.light,
      motion_score: options.motion === undefined ? 0.02 : options.motion },
    vital: { hr_bpm: Object.hasOwn(options, "hr") ? options.hr : 72 }
  };
}
function feed(tracker, start, end, options, policy = thresholds) {
  const events = [];
  for (let ts = start; ts <= end; ts++) {
    const event = Alerts.update(tracker, frame(ts, options), policy);
    if (event) events.push(event);
  }
  return events;
}

test("exactly ten seconds does not alert; first sample beyond ten does, once", () => {
  const tracker = Alerts.createTracker();
  assert.deepEqual(feed(tracker, 0, 10, { hr: 110 }), []);
  assert.equal(feed(tracker, 11, 11, { hr: 110 })[0].kind, "heart_rate");
  assert.deepEqual(feed(tracker, 12, 20, { hr: 110 }), []);
});
test("heart rate must exceed the configured line, not equal it", () => {
  assert.deepEqual(feed(Alerts.createTracker(), 0, 15, { hr: 100 }), []);
});
test("fatigue status produces its own warning and practical advice", () => {
  const events = feed(Alerts.createTracker(), 0, 11, { status: "fatigue_risk" });
  assert.equal(events[0].kind, "fatigue");
  assert.match(events[0].advice, /休息/);
});
test("simultaneous sustained conditions produce one combined warning", () => {
  const events = feed(Alerts.createTracker(), 0, 11, { status: "fatigue_risk", hr: 110 });
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "combined");
});
test("normal readings reset timing instead of accumulating intermittent anomalies", () => {
  const tracker = Alerts.createTracker();
  feed(tracker, 0, 7, { status: "fatigue_risk" });
  feed(tracker, 8, 8, {});
  assert.deepEqual(feed(tracker, 9, 19, { status: "fatigue_risk" }), []);
  assert.equal(feed(tracker, 20, 20, { status: "fatigue_risk" }).length, 1);
});
test("each condition keeps its timer when another condition starts or resolves", () => {
  const tracker = Alerts.createTracker();
  feed(tracker, 0, 7, { status: "fatigue_risk" });
  assert.equal(feed(tracker, 8, 11, { status: "fatigue_risk", hr: 110 })[0].kind, "fatigue");
  assert.equal(feed(tracker, 12, 19, { status: "fatigue_risk", hr: 110 })[0].kind, "combined");
  feed(tracker, 20, 20, { hr: 110 });
  assert.equal(Alerts.current(tracker).kind, "heart_rate");
});
test("acknowledged episodes stay quiet until recovery and a fresh sustained episode", () => {
  const tracker = Alerts.createTracker();
  feed(tracker, 0, 11, { hr: 110 });
  Alerts.dismiss(tracker);
  assert.equal(Alerts.current(tracker), null);
  assert.deepEqual(feed(tracker, 12, 20, { hr: 110 }), []);
  feed(tracker, 21, 21, {});
  assert.equal(feed(tracker, 22, 33, { hr: 110 }).length, 1);
});
test("a new warning still appears when a different warning was acknowledged", () => {
  const tracker = Alerts.createTracker();
  feed(tracker, 0, 11, { status: "fatigue_risk" });
  Alerts.dismiss(tracker);
  const events = feed(tracker, 12, 23, { status: "fatigue_risk", hr: 110 });
  assert.equal(events[0].kind, "heart_rate");
});
test("invalid quality, light, motion or visibility cannot sustain warnings", () => {
  for (const options of [
    { quality: 0.5 }, { light: 0.2 }, { motion: 0.5 }, { visible: 0 }, { visible: 0.4 },
    { status: "unreliable" }, { status: "disconnected" }, { status: "adjust_posture" }, { status: "done" }
  ]) {
    const tracker = Alerts.createTracker();
    feed(tracker, 0, 8, { status: "fatigue_risk", hr: 110 });
    feed(tracker, 9, 9, { status: "fatigue_risk", hr: 110, ...options });
    assert.deepEqual(feed(tracker, 10, 20, { status: "fatigue_risk", hr: 110 }), []);
    assert.equal(feed(tracker, 21, 21, { status: "fatigue_risk", hr: 110 }).length, 1);
  }
});
test("missing or gated heart rate cannot alert while reliable fatigue still can", () => {
  for (const hr of [null, undefined, NaN, Infinity, "110"]) {
    assert.deepEqual(feed(Alerts.createTracker(), 0, 11, { hr }), []);
  }
  assert.deepEqual(feed(Alerts.createTracker(), 0, 11, { quality: 0.7, hr: 110 }), []);
  const events = feed(Alerts.createTracker(), 0, 11, { status: "fatigue_risk", quality: 0.7, hr: null });
  assert.equal(events[0].kind, "fatigue");
});
test("gaps longer than the configured disconnect timeout reset the clock", () => {
  const tracker = Alerts.createTracker();
  feed(tracker, 0, 8, { hr: 110 });
  assert.equal(Alerts.update(tracker, frame(20, { hr: 110 }), thresholds), null);
  assert.deepEqual(feed(tracker, 21, 30, { hr: 110 }), []);
  assert.equal(feed(tracker, 31, 31, { hr: 110 }).length, 1);
});
test("timestamps and frame IDs must advance; replays start fresh", () => {
  for (const replay of [{ ts: 1, id: 1 }, { ts: 9, id: 1 }]) {
    const tracker = Alerts.createTracker();
    feed(tracker, 0, 8, { hr: 110 });
    assert.equal(Alerts.update(tracker, frame(replay.ts, { hr: 110, id: replay.id }), thresholds), null);
    assert.equal(tracker.heart_rate.startedTs, replay.ts);
  }
  const tracker = Alerts.createTracker();
  feed(tracker, 0, 9, { hr: 110 });
  for (let n = 0; n < 20; n++) assert.equal(Alerts.update(tracker, frame(9, { hr: 110 }), thresholds), null);
  assert.equal(Alerts.current(tracker), null);
});
test("disconnect with the same timestamp clears an already visible warning", () => {
  const tracker = Alerts.createTracker();
  feed(tracker, 0, 11, { hr: 110 });
  Alerts.update(tracker, frame(11, { status: "disconnected" }), thresholds);
  assert.equal(Alerts.current(tracker), null);
});
test("policy changes, malformed frames and source tracker replacement reset timing", () => {
  const tracker = Alerts.createTracker();
  feed(tracker, 0, 9, { hr: 110 });
  assert.equal(Alerts.update(tracker, frame(10, { hr: 110 }), { ...thresholds, hr_alert_high_bpm: 105 }), null);
  assert.equal(tracker.heart_rate.startedTs, 10);
  Alerts.update(tracker, null, thresholds);
  assert.equal(Alerts.current(tracker), null);
  assert.equal(Alerts.createTracker().heart_rate.startedTs, null);
});
test("duration and gap settings must be valid and are respected", () => {
  for (const alert_duration_seconds of [null, 0, -1, NaN]) {
    assert.deepEqual(feed(Alerts.createTracker(), 0, 11, { hr: 110 }, { ...thresholds, alert_duration_seconds }), []);
  }
  const tracker = Alerts.createTracker();
  const policy = { ...thresholds, alert_duration_seconds: 3 };
  assert.deepEqual(feed(tracker, 0, 3, { hr: 110 }, policy), []);
  assert.equal(feed(tracker, 4, 4, { hr: 110 }, policy).length, 1);
});
