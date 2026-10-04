/* =============================================================================
 * ui_state.js -- B line display semantics shared by the browser and Node tests.
 *
 * This module deliberately knows nothing about the DOM. It answers the small set
 * of questions that must stay consistent across metric cards, gates, charts and
 * the preview overlay.
 * ========================================================================== */
(function (root) {
  "use strict";

  var MEASUREMENT = {
    WAITING: "waiting",
    DISCONNECTED: "disconnected",
    NO_FACE: "no_face",
    MEASURED: "measured"
  };

  function measurementState(frame) {
    if (!frame) return MEASUREMENT.WAITING;
    if (frame.status === "disconnected") return MEASUREMENT.DISCONNECTED;
    if (frame.face && frame.face.visible === 0) return MEASUREMENT.NO_FACE;
    return MEASUREMENT.MEASURED;
  }

  function placeholder(frame) {
    var state = measurementState(frame);
    if (state === MEASUREMENT.WAITING) return "\u2014";
    if (state === MEASUREMENT.DISCONNECTED) return "\u65e0\u6570\u636e";
    if (state === MEASUREMENT.NO_FACE) return "\u65e0\u6d4b\u91cf";
    return null;
  }

  function measurementValue(frame, getter) {
    return measurementState(frame) === MEASUREMENT.MEASURED ? getter(frame) : null;
  }

  function shouldPlot(frame) {
    return measurementState(frame) === MEASUREMENT.MEASURED;
  }

  function shouldDrawBbox(frame, previewMeta) {
    if (measurementState(frame) !== MEASUREMENT.MEASURED || !previewMeta) return false;
    if (previewMeta.status === "disconnected" || previewMeta.faceVisible === 0) return false;
    return true;
  }

  function sourceView(mode, serverSource) {
    if (mode === "offline") return { label: "\u79bb\u7ebf Mock", kind: "mock" };
    if (mode === "ws" && serverSource === "mock") return { label: "\u670d\u52a1 Mock", kind: "mock" };
    if (mode === "ws" && serverSource === "ingest") return { label: "A \u7ebf ingest", kind: "live" };
    if (mode === "ws") return { label: "\u6765\u6e90\u672a\u58f0\u660e", kind: "" };
    return { label: "\u7b49\u5f85\u6570\u636e", kind: "" };
  }

  function selfTest() {
    var failures = [];
    var checked = 0;
    function check(name, ok) {
      checked++;
      if (!ok) failures.push(name);
    }

    var normal = { status: "normal", face: { visible: 0.95 } };
    var lowVisible = { status: "adjust_posture", face: { visible: 0.2 } };
    var noFace = { status: "unreliable", face: { visible: 0 } };
    var disconnected = { status: "disconnected", face: { visible: 0 } };
    var preview = { status: "normal", faceVisible: 0.95, bbox: [1, 2, 3, 4] };

    check("\u65e0\u5e27\u65f6\u7b49\u5f85\u6570\u636e", measurementState(null) === MEASUREMENT.WAITING && placeholder(null) === "\u2014");
    check("\u65ad\u6d41\u4f18\u5148\u663e\u793a\u65e0\u6570\u636e", measurementState(disconnected) === MEASUREMENT.DISCONNECTED && placeholder(disconnected) === "\u65e0\u6570\u636e");
    check("\u65e0\u4eba\u8138\u663e\u793a\u65e0\u6d4b\u91cf", measurementState(noFace) === MEASUREMENT.NO_FACE && placeholder(noFace) === "\u65e0\u6d4b\u91cf");
    check("\u4f4e\u53ef\u89c1\u7387\u4ecd\u662f\u6709\u4eba\u8138\u6d4b\u91cf", measurementState(lowVisible) === MEASUREMENT.MEASURED);
    check("\u65e0\u4eba\u8138\u7684\u6307\u6807\u503c\u88ab\u6e05\u7a7a", measurementValue(noFace, function () { return 42; }) === null);
    check("\u65e0\u4eba\u8138\u7684\u8d8b\u52bf\u7559\u7a7a", !shouldPlot(noFace));
    check("\u65e0\u4eba\u8138\u4e0d\u753b\u6846", !shouldDrawBbox(noFace, preview));
    check("\u9884\u89c8\u5143\u6570\u636e\u65e0\u4eba\u8138\u65f6\u4e0d\u753b\u6846", !shouldDrawBbox(normal, { status: "normal", faceVisible: 0, bbox: [0, 0, 0, 0] }));
    check("\u4eba\u8138\u6062\u590d\u540e\u6062\u590d\u6570\u503c\u4e0e\u6846", measurementValue(normal, function () { return 42; }) === 42 && shouldPlot(normal) && shouldDrawBbox(normal, preview));
    check("\u79bb\u7ebf Mock \u6765\u6e90\u6807\u7b7e", sourceView("offline", "").label === "\u79bb\u7ebf Mock");
    check("\u670d\u52a1 Mock \u6765\u6e90\u6807\u7b7e", sourceView("ws", "mock").label === "\u670d\u52a1 Mock");
    check("ingest \u6765\u6e90\u6807\u7b7e", sourceView("ws", "ingest").label === "A \u7ebf ingest");
    check("\u72ec\u7acb WebSocket \u4e0d\u5192\u5145\u6570\u636e\u6765\u6e90", sourceView("ws", "").label === "\u6765\u6e90\u672a\u58f0\u660e");

    return { checked: checked, failures: failures, ok: failures.length === 0 };
  }

  var API = {
    MEASUREMENT: MEASUREMENT,
    measurementState: measurementState,
    placeholder: placeholder,
    measurementValue: measurementValue,
    shouldPlot: shouldPlot,
    shouldDrawBbox: shouldDrawBbox,
    sourceView: sourceView,
    selfTest: selfTest
  };

  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.VigiLensUIState = API;
})(typeof globalThis !== "undefined" ? globalThis : this);

if (typeof require !== "undefined" && typeof module !== "undefined" && require.main === module) {
  var result = module.exports.selfTest();
  console.log(JSON.stringify(result, null, 2));
  if (!result.ok) process.exitCode = 1;
}
