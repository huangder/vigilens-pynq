/* Synthetic playback tests; these do not validate physiological accuracy. */
const test = require("node:test");
const assert = require("node:assert/strict");
const R = require("../recording.js");
const M = require("../mock.js");
const A = require("../alerts.js");
const policy = { alert_duration_seconds: 10, hr_alert_high_bpm: 100, ws_disconnect_timeout_s: 3,
  vital_require_quality: 0.75, quality_min_score: 0.6, face_visible_min: 0.7,
  light_score_min: 0.5, motion_score_max: 0.35 };

test("every sample of all three groups passes the existing contract and is reproducible", () => {
  for (const scene of R.SCENES) {
    let last;
    for (let t = -119; t < 60; t++) {
      const opts = { frameId: t + 119, ts: t + 119, seconds: 60 };
      const frame = R.makeFrame(scene, t, opts);
      assert.deepEqual(frame, R.makeFrame(scene, t, opts));
      assert.deepEqual(M.validateFrame(frame), []);
      assert.equal(frame.vital.rr_per_min, null);
      assert.equal(frame.vital.rr_conf, null);
      if (last) {
        assert.ok(frame.ts > last.ts && frame.frame_id > last.frame_id);
        assert.ok(frame.behavior.blink_count >= last.behavior.blink_count);
        assert.ok(frame.behavior.yawn_count >= last.behavior.yawn_count);
        assert.ok(frame.behavior.long_close_count >= last.behavior.long_close_count);
        assert.ok(Math.abs(frame.vital.hr_bpm - last.vital.hr_bpm) < 1);
      }
      last = frame;
    }
  }
});
test("exercise recovers without inventing a seventh status or implying fatigue", () => {
  const start = R.makeFrame("exercise", 0), end = R.makeFrame("exercise", 59);
  assert.ok(start.vital.hr_bpm > R.makeFrame("rest", 0).vital.hr_bpm);
  assert.ok(start.vital.hr_bpm > end.vital.hr_bpm);
  assert.equal(start.status, "normal");
  assert.equal(end.behavior.long_close_count, 0);
});
test("rest and fatigue heart rates rise and fall inside a bounded band, while exercise recovers", () => {
  for (const [scene, center] of [["rest", 72], ["fatigue", 69]]) {
    const values = Array.from({ length: 15 }, (_, t) => R.makeFrame(scene, t).vital.hr_bpm);
    const changes = values.slice(1).map((value, i) => value - values[i]);
    assert.ok(changes.some(value => value > 0) && changes.some(value => value < 0));
    assert.ok(values.every(value => Math.abs(value - center) <= 4.1));
  }
  const values = Array.from({ length: 60 }, (_, t) => R.makeFrame("exercise", t, { seconds: 60 }).vital.hr_bpm);
  assert.ok(values[0] > values.at(-1));
  assert.ok(values.slice(1).some((value, i) => value > values[i]));
});
test("prepared histories do not reuse a repeating wave or synchronize unrelated signals", () => {
  function correlation(a, b) {
    const mean = values => values.reduce((sum, value) => sum + value) / values.length;
    const ma = mean(a), mb = mean(b);
    let xy = 0, xx = 0, yy = 0;
    for (let i = 0; i < a.length; i++) { xy += (a[i] - ma) * (b[i] - mb); xx += (a[i] - ma) ** 2; yy += (b[i] - mb) ** 2; }
    return xy / Math.sqrt(xx * yy);
  }
  for (const scene of R.SCENES) {
    const frames = Array.from({ length: 120 }, (_, i) => R.makeFrame(scene, i - 119));
    // The old shared sine repeated roughly every 44 s and aligned light/quality.
    assert.ok(Math.abs(correlation(frames.map(f => f.quality.light_score), frames.map(f => f.quality.overall))) < 0.9);
    if (scene !== "exercise") {
      const hr = frames.map(f => f.vital.hr_bpm);
      assert.ok(Math.abs(correlation(hr.slice(0, -44), hr.slice(44))) < 0.9);
    }
    assert.notDeepEqual(frames.slice(0, 15).map(f => f.behavior.ear_left), frames.slice(15, 30).map(f => f.behavior.ear_left));
  }
});
test("fatigue playback has one full yawn with a single count increment at every supported duration", () => {
  for (const seconds of [15, 60, 300]) {
    const frames = Array.from({ length: seconds }, (_, t) => R.makeFrame("fatigue", t, { seconds }));
    const mouth = frames.map(f => f.behavior.mar);
    assert.ok(mouth[0] < 0.25 && mouth.at(-1) < 0.25);
    assert.ok(Math.max(...mouth) > 0.7);
    let episodes = 0, open = false;
    for (const value of mouth) { if (value > 0.6 && !open) episodes++; open = value > 0.6; }
    assert.equal(episodes, 1);
    assert.equal(frames.at(-1).behavior.yawn_count - frames[0].behavior.yawn_count, 1);
  }
});
test("samples do not depend on generation order or the transport frame ID", () => {
  for (const scene of R.SCENES) {
    const expected = Array.from({ length: 15 }, (_, t) => R.makeFrame(scene, t));
    for (let t = 14; t >= 0; t--) {
      const frame = R.makeFrame(scene, t, { frameId: 10000 + t, ts: 20000 + t });
      assert.deepEqual(frame.vital, expected[t].vital);
      assert.deepEqual(frame.behavior, expected[t].behavior);
      assert.deepEqual(frame.quality, expected[t].quality);
    }
  }
});
test("only fatigue fixtures contain sustained eye and yawn evidence", () => {
  for (const scene of R.SCENES) {
    const frame = R.makeFrame(scene, 17);
    assert.equal(frame.status === "fatigue_risk", scene === "fatigue");
    assert.equal(frame.behavior.perclos > 0.25, scene === "fatigue");
    assert.equal(frame.behavior.long_close_count > 0, scene === "fatigue");
    assert.equal(frame.behavior.yawn_count > 0, scene === "fatigue");
  }
});
test("rest stays quiet; exercise and fatigue exercise the latest GitHub alert logic", () => {
  for (const scene of R.SCENES) {
    const tracker = A.createTracker(), events = [];
    for (let t = 0; t < 60; t++) {
      const event = A.update(tracker, R.makeFrame(scene, t, { seconds: 60 }), policy);
      if (event) events.push([t, event.kind]);
    }
    assert.deepEqual(events, scene === "rest" ? [] : [[11, scene === "exercise" ? "heart_rate" : "fatigue"]]);
    if (scene === "exercise") assert.equal(A.current(tracker), null);
  }
});
test("default round plays each group for 15 seconds, raises fatigue prompt and holds its last sample", () => {
  const player = R.createPlayer("all"), counts = { rest: 0, exercise: 0, fatigue: 0 };
  const fatigueTracker = A.createTracker(), fatigueEvents = [];
  while (!player.current().finished) {
    const current = player.current();
    counts[current.scene]++;
    if (current.scene === "fatigue") {
      const event = A.update(fatigueTracker, R.makeFrame(current.scene, current.second), policy);
      if (event) fatigueEvents.push([current.second, event.kind]);
    }
    player.advance();
  }
  assert.deepEqual(counts, { rest: 15, exercise: 15, fatigue: 15 });
  assert.deepEqual(fatigueEvents, [[11, "fatigue"]]);
  const last = player.current();
  assert.equal(last.second, 14);
  player.advance(); player.togglePause();
  assert.deepEqual(player.current(), last);
});
test("pause consumes no samples, resume continues, and looping returns to the first sample", () => {
  const player = R.createPlayer("exercise", 15, true);
  player.advance(); player.togglePause();
  const paused = player.current();
  for (let n = 0; n < 20; n++) player.advance();
  assert.deepEqual(player.current(), paused);
  player.togglePause(); player.advance();
  assert.equal(player.current().second, 2);
  for (let n = 0; n < 13; n++) player.advance();
  assert.equal(player.current().second, 0);
  assert.equal(player.current().finished, false);
});
test("invalid groups and duration cannot produce a broken or runaway player", () => {
  for (const scene of ["", "bad", null]) assert.throws(() => R.createPlayer(scene));
  for (const seconds of [0, -1, 14, 301, NaN, Infinity, 1.5, "bad"]) {
    assert.throws(() => R.createPlayer("all", seconds));
    assert.throws(() => R.makeFrame("rest", 0, { seconds }));
  }
  assert.throws(() => R.makeFrame("rest", NaN));
});
