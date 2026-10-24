/* Per-card history helpers. All values come from received contract frames. */
(function (root) {
  "use strict";
  var U = typeof module !== "undefined" && module.exports
    ? require("./ui_state.js") : root.VigiLensUIState;

  function append(history, frame, windowSeconds, maxPoints) {
    var last = history.length ? history[history.length - 1] : null;
    if (frame.status === "disconnected") {
      // A disconnect is a gap, not a new measurement or a wall-clock sample.
      // Watchdog frames can carry synthetic timestamps; anchor to the last frame.
      if (!last || last.status === "disconnected") return false;
      frame = Object.assign({}, frame, { ts: last.ts, frame_id: last.frame_id });
    } else if (last && (frame.ts < last.ts || frame.frame_id < last.frame_id)) {
      // A replay/restarted producer starts a new timeline.
      history.length = 0;
      last = null;
    }
    if (last && frame.ts === last.ts && frame.frame_id === last.frame_id && frame.status === last.status) return false;
    history.push(frame);
    while (history.length > maxPoints || (history.length && history[0].ts < frame.ts - windowSeconds)) history.shift();
    return true;
  }

  function series(history, getter, maxGapSeconds) {
    var segments = [], segment = null, previousTs = null;
    history.forEach(function (frame) {
      var value = U.shouldPlot(frame) ? getter(frame) : null;
      if (typeof value !== "number" || !isFinite(value)) {
        segment = null;
        previousTs = null;
        return;
      }
      if (!segment || (previousTs !== null && frame.ts - previousTs > maxGapSeconds)) {
        segment = [];
        segments.push(segment);
      }
      segment.push({ ts: frame.ts, value: value });
      previousTs = frame.ts;
    });
    return segments;
  }

  function range(seriesList, nonnegative) {
    var min = Infinity, max = -Infinity;
    seriesList.forEach(function (segments) {
      segments.forEach(function (segment) {
        segment.forEach(function (point) { min = Math.min(min, point.value); max = Math.max(max, point.value); });
      });
    });
    if (min === Infinity) return null;
    // Padding is a drawing choice, not an algorithm/medical threshold.
    var padding = max === min ? (Math.abs(max) * 0.1 || 1) : (max - min) * 0.15;
    return { min: nonnegative ? Math.max(0, min - padding) : min - padding, max: max + padding };
  }

  var API = { append: append, series: series, range: range };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.VigiLensTrends = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
