/* Deterministic synthetic filming fixtures. Never use these as measurement evidence.
 * The numbers below are presentation presets, not physiological reference ranges
 * or decision thresholds. The real pipeline and contract validators are unchanged.
 */
(function (root) {
  "use strict";
  var M = typeof module !== "undefined" && module.exports ? require("./mock.js") : root.VigiLensMock;
  var SCENES = ["rest", "exercise", "fatigue"];
  var LABELS = { rest: "静息状态", exercise: "短时运动后", fatigue: "疲劳状态" };
  var DEFAULT_SECONDS = 15;
  var MIN_SECONDS = 15;
  var MAX_SECONDS = 300;
  var PRESETS = {
    rest: { hrStart: 72, hrEnd: 72, blink: 16, perclos: 0.055, ear: 0.28, motion: 0.025 },
    exercise: { hrStart: 114, hrEnd: 92, blink: 18, perclos: 0.065, ear: 0.28, motion: 0.07 },
    fatigue: { hrStart: 69, hrEnd: 69, blink: 8, perclos: 0.34, ear: 0.24, motion: 0.035 }
  };
  function round(n, digits) { return Number(n.toFixed(digits)); }
  function checkScene(scene) {
    if (SCENES.indexOf(scene) < 0) throw new Error("未知拍摄分组：" + scene);
  }
  function duration(value) {
    var seconds = Number(value);
    if (!Number.isInteger(seconds) || seconds < MIN_SECONDS || seconds > MAX_SECONDS) {
      throw new Error("每组时长须为 " + MIN_SECONDS + "～" + MAX_SECONDS + " 秒的整数");
    }
    return seconds;
  }
  // Independent deterministic noise bands replace the shared repeating sine.
  // These are presentation fixtures only, not recorded or validated physiology.
  function randomAt(seed, index) {
    var value = seed ^ Math.imul(index, 0x9e3779b1);
    value = Math.imul(value ^ (value >>> 16), 0x21f0aaad);
    value = Math.imul(value ^ (value >>> 15), 0x735a2d97);
    return ((value ^ (value >>> 15)) >>> 0) / 4294967296;
  }
  function ease(value) { return value * value * (3 - 2 * value); }
  function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }
  function noise(second, interval, seed) {
    var position = second / interval, index = Math.floor(position), mix = ease(position - index);
    return 2 * (randomAt(seed, index) * (1 - mix) + randomAt(seed, index + 1) * mix) - 1;
  }
  function drift(second, seed) {
    return 0.5 * noise(second, 13.7, seed) + 0.34 * noise(second, 4.3, seed + 1) +
      0.16 * noise(second, 1.9, seed + 2);
  }
  function eventTrack(seed, gap, width, anchor) {
    var events = [];
    function add(at, index) {
      events.push({ at: at, width: width[0] + (width[1] - width[0]) * randomAt(seed + 1, index),
        strength: 0.75 + 0.25 * randomAt(seed + 2, index), direction: randomAt(seed + 3, index) < 0.5 ? -1 : 1 });
    }
    var at = anchor, index = 0;
    while (at <= MAX_SECONDS + 10) { add(at, index); at += gap * (0.65 + 0.7 * randomAt(seed, ++index)); }
    at = anchor; index = -1;
    while (at > -160) { at -= gap * (0.65 + 0.7 * randomAt(seed, index)); add(at, index--); }
    return events.sort(function (a, b) { return a.at - b.at; });
  }
  function pulse(second, event, blur) {
    var distance = Math.abs(second - event.at) / (event.width / 2 + blur);
    return distance < 1 ? Math.pow(1 - distance * distance, 2) * event.strength : 0;
  }
  function activity(second, events, blur, signed) {
    return events.reduce(function (total, event) {
      return total + pulse(second, event, blur) * (signed ? event.direction : 1);
    }, 0);
  }
  function finishedCount(second, events) {
    return events.filter(function (event) { return event.at + event.width / 2 <= second; }).length;
  }
  function recentCount(second, events) {
    return events.filter(function (event) { return event.at <= second && event.at > second - 30; }).length;
  }
  function heartTrace(seed) {
    var samples = [], value = 0;
    for (var second = -180; second <= MAX_SECONDS; second++) {
      var target = 2.5 * noise(second, 7.1, seed) + noise(second, 2.3, seed + 1) +
        0.5 * noise(second, 19.1, seed + 2);
      value += clamp((target - value) * 0.5, -0.28, 0.28);
      samples.push(value);
    }
    return samples;
  }
  var TRACKS = {};
  SCENES.forEach(function (scene, index) {
    var seed = 31171 + index * 17041;
    TRACKS[scene] = { seed: seed, heart: heartTrace(seed + 300),
      blinks: eventTrack(seed + 10, 60 / PRESETS[scene].blink, [0.2, 0.45], 1.7),
      closes: scene === "fatigue" ? eventTrack(seed + 20, 19, [1.5, 2.8], 3.7) : [],
      head: eventTrack(seed + 30, 17, [1.2, 3.8], 5.2),
      mouth: eventTrack(seed + 40, 23, [1.0, 2.0], 2.2)
    };
  });
  function makeFrame(scene, second, options) {
    checkScene(scene);
    options = options || {};
    if (!Number.isInteger(second)) throw new Error("采样位置须为整数秒");
    var seconds = duration(options.seconds === undefined ? DEFAULT_SECONDS : options.seconds);
    var preset = PRESETS[scene];
    var progress = Math.max(0, Math.min(1, second / (seconds - 1)));
    var track = TRACKS[scene], seed = track.seed;
    var head = activity(second, track.head, 0.3, true);
    var blink = activity(second, track.blinks, 0.4, false);
    var longClose = activity(second, track.closes, 0.2, false);
    // One complete, asymmetric yawn during the final group, including at 15 s.
    var yawnStart = Math.min(5, seconds * 0.3), yawnEnd = yawnStart + 5.5;
    var yawnProgress = (second - yawnStart) / (yawnEnd - yawnStart);
    var yawn = 0;
    if (scene === "fatigue" && yawnProgress > 0 && yawnProgress < 1) {
      yawn = yawnProgress < 0.35 ? ease(yawnProgress / 0.35) :
        (yawnProgress < 0.65 ? 1 : 1 - ease((yawnProgress - 0.65) / 0.35));
    }
    var frameId = options.frameId === undefined ? Math.max(0, second) : options.frameId;
    var frame = M.mockFrame(frameId, { status: scene === "fatigue" ? "fatigue_risk" : "normal",
      ts: options.ts === undefined ? frameId : options.ts });
    frame.face = { visible: round(0.97 + 0.015 * drift(second, seed + 100), 3), bbox: [230, 90, 180, 240], pose: {
      yaw: round(1.8 * drift(second, seed + 110) + 1.4 * head, 2),
      pitch: round((scene === "fatigue" ? 5 : 1) + 0.9 * drift(second, seed + 120) + 0.6 * longClose, 2),
      roll: round(0.7 * drift(second, seed + 130) + 0.3 * head, 2)
    } };
    var eyeDrop = Math.max(0.14 * blink, 0.13 * longClose);
    frame.behavior = {
      ear_left: round(preset.ear + 0.008 * drift(second, seed + 200) - eyeDrop, 4),
      ear_right: round(preset.ear + 0.003 + 0.007 * drift(second, seed + 210) - eyeDrop * 0.98, 4),
      blink_state: eyeDrop > 0.08 ? "CLOSED" : "OPEN",
      blink_count: finishedCount(second, track.blinks),
      blink_rate_per_min: round(0.7 * 2 * recentCount(second, track.blinks) + 0.3 * preset.blink + 0.3 * drift(second, seed + 220), 2),
      perclos: round(preset.perclos + 0.018 * noise(second, 21.3, seed + 230) + 0.006 * noise(second, 5.7, seed + 231), 4),
      long_close_count: finishedCount(second, track.closes),
      mar: round(0.13 + 0.014 * drift(second, seed + 240) + 0.05 * activity(second, track.mouth, 0.2, false) + 0.61 * yawn, 4),
      yawn_count: scene === "fatigue" ? 2 + (second >= yawnEnd ? 1 : 0) : 0
    };
    var hrBase = preset.hrStart + (preset.hrEnd - preset.hrStart) * ease(progress);
    if (scene === "exercise" && second < 0) {
      hrBase = PRESETS.rest.hrStart + (preset.hrStart - PRESETS.rest.hrStart) * ease(clamp((second + 105) / 105, 0, 1));
    }
    var hrNoise = track.heart[clamp(second + 180, 0, track.heart.length - 1)] + 0.03 * noise(second, 1.3, seed + 303);
    frame.vital = {
      hr_bpm: round(hrBase + hrNoise, 1),
      hr_conf: round(0.93 + 0.025 * drift(second, seed + 310), 3),
      // Keep required contract keys, but this filming preset has no respiration data.
      rr_per_min: null,
      rr_conf: null
    };
    var motion = preset.motion + 0.012 * drift(second, seed + 400) + 0.018 * Math.abs(head);
    frame.quality = { light_score: round(0.88 + 0.025 * drift(second, seed + 410), 4),
      motion_score: round(motion, 4),
      overall: round(0.93 + 0.014 * drift(second, seed + 420) - 0.2 * (motion - preset.motion), 4) };
    frame.advice = scene === "fatigue" ? "疲劳风险升高，建议暂停当前任务并休息放松" :
      (scene === "exercise" ? "保持自然坐姿，观察运动后的恢复趋势" : "保持自然坐姿与均匀光照，继续观察状态趋势");
    frame.reason = scene === "fatigue" ? "闭眼比例偏高，伴随长闭眼与哈欠增加" :
      (scene === "exercise" ? "运动后心率呈回落趋势，当前画面质量稳定" : "闭眼比例较低，心率趋势平稳");
    var errors = M.validateFrame(frame);
    if (errors.length) throw new Error(errors.join("；"));
    return frame;
  }
  function createPlayer(selection, seconds, loop) {
    if (selection !== "all") checkScene(selection);
    var order = selection === "all" ? SCENES.slice() : [selection];
    var length = duration(seconds === undefined ? DEFAULT_SECONDS : seconds);
    var index = 0, second = 0, paused = false, finished = false;
    function current() { return { scene: order[index], group: index + 1, groups: order.length,
      second: second, seconds: length, paused: paused, finished: finished }; }
    return {
      current: current,
      togglePause: function () { if (!finished) paused = !paused; return current(); },
      advance: function () {
        if (paused || finished) return current();
        if (second + 1 < length) second++;
        else if (index + 1 < order.length) { index++; second = 0; }
        else if (loop) { index = 0; second = 0; }
        else finished = true;
        return current();
      }
    };
  }
  var API = { SCENES: SCENES, LABELS: LABELS, DEFAULT_SECONDS: DEFAULT_SECONDS,
    MIN_SECONDS: MIN_SECONDS, MAX_SECONDS: MAX_SECONDS, makeFrame: makeFrame, createPlayer: createPlayer };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.VigiLensRecording = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
