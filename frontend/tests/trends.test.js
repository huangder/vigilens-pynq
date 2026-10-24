/* Synthetic fixtures verify rendering semantics; these are not measurement evidence. */
const test = require("node:test");
const assert = require("node:assert/strict");
const Trends = require("../trends.js");
function frame(ts, value, status = "normal", visible = 0.95) {
  return { ts, frame_id: ts, status, face: { visible }, value };
}
const get = f => f.value;
const append = (history, f) => Trends.append(history, f, 120, 120);

test("history and latest sample share one timestamp-based series", () => {
  const history = [];
  append(history, frame(10, 1)); append(history, frame(11, 2));
  assert.deepEqual(Trends.series(history, get, 2), [[{ ts: 10, value: 1 }, { ts: 11, value: 2 }]]);
});
test("one sample is retained and genuine zero remains a number", () => {
  assert.deepEqual(Trends.series([frame(0, 0)], get, 2), [[{ ts: 0, value: 0 }]]);
});
test("null, no face, and disconnected samples break rather than zero-fill curves", () => {
  for (const gap of [frame(2, null), frame(2, 0, "unreliable", 0), frame(2, 0, "disconnected")]) {
    assert.deepEqual(Trends.series([frame(1, 4), gap, frame(3, 5)], get, 2), [
      [{ ts: 1, value: 4 }], [{ ts: 3, value: 5 }]
    ]);
  }
});
test("missing frames create a time gap instead of bridging a stale value", () => {
  assert.equal(Trends.series([frame(1, 4), frame(10, 5)], get, 2).length, 2);
});
test("invalid numeric values never reach the chart", () => {
  for (const value of [NaN, Infinity, undefined, "5"]) assert.deepEqual(Trends.series([frame(1, value)], get, 2), []);
});
test("disconnect is anchored to the last sample and never mutates the contract frame", () => {
  const history = [frame(10, 4)], gap = frame(999999, 0, "disconnected");
  append(history, gap); append(history, gap);
  assert.equal(history.length, 2);
  assert.equal(history[1].ts, 10);
  assert.equal(history[1].frame_id, 10);
  assert.equal(gap.ts, 999999);
  assert.deepEqual(Trends.series(history, get, 2), [[{ ts: 10, value: 4 }]]);
});
test("a disconnect before the first measurement does not create history", () => {
  const history = [];
  assert.equal(append(history, frame(0, 0, "disconnected")), false);
  assert.equal(history.length, 0);
});
test("duplicate frames do not accumulate and recovery is a new segment", () => {
  const history = [], f = frame(10, 4);
  append(history, f); append(history, f);
  assert.equal(history.length, 1);
  append(history, frame(10, 0, "disconnected")); append(history, f);
  assert.equal(Trends.series(history, get, 2).length, 2);
});
test("a producer timestamp or frame-id reset starts a fresh history", () => {
  for (const restarted of [frame(1, 2), { ...frame(11, 2), frame_id: 1 }]) {
    const history = [frame(10, 4)];
    append(history, restarted);
    assert.deepEqual(history, [restarted]);
  }
});
test("both the 120-second time window and the point limit are enforced", () => {
  const sparse = [];
  [0, 60, 120, 121].forEach(ts => append(sparse, frame(ts, 1)));
  assert.deepEqual(sparse.map(f => f.ts), [60, 120, 121]);
  const dense = [];
  for (let i = 0; i < 140; i++) append(dense, frame(i / 10, 1));
  assert.equal(dense.length, 120);
});
test("ranges preserve units, cover all samples, and handle constant/zero values", () => {
  for (const value of [0, 0.3, 75, -15]) {
    const range = Trends.range([[[{ ts: 1, value }]]], value >= 0);
    assert.ok(range.min <= value && range.max > value && range.max > range.min);
    if (value >= 0) assert.ok(range.min >= 0);
  }
  assert.equal(Trends.range([[]], true), null);
});
test("both yaw and pitch contribute to a shared degree scale", () => {
  const range = Trends.range([[[{ ts: 1, value: -20 }]], [[{ ts: 1, value: 40 }]]], false);
  assert.ok(range.min < -20 && range.max > 40);
});
