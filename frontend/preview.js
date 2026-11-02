/* 同源预览的纯函数：浏览器用全局对象，Node 用 module.exports 自检。 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VigiLensPreview = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var HEADERS = {
    frameId: "x-vigilens-frame-id",
    ts: "x-vigilens-ts",
    bboxX: "x-vigilens-bbox-x",
    bboxY: "x-vigilens-bbox-y",
    bboxW: "x-vigilens-bbox-w",
    bboxH: "x-vigilens-bbox-h",
    sourceWidth: "x-vigilens-source-width",
    sourceHeight: "x-vigilens-source-height",
    faceVisible: "x-vigilens-face-visible",
    status: "x-vigilens-status",
    detector: "x-vigilens-detector"
  };

  function finiteNumber(value, label) {
    var n = Number(value);
    if (!isFinite(n)) throw new Error(label + " 不是有限数值");
    return n;
  }

  function containLayout(viewWidth, viewHeight, sourceWidth, sourceHeight) {
    var vw = finiteNumber(viewWidth, "viewWidth");
    var vh = finiteNumber(viewHeight, "viewHeight");
    var sw = finiteNumber(sourceWidth, "sourceWidth");
    var sh = finiteNumber(sourceHeight, "sourceHeight");
    if (vw <= 0 || vh <= 0 || sw <= 0 || sh <= 0) throw new Error("尺寸必须为正数");
    var scale = Math.min(vw / sw, vh / sh);
    var displayWidth = sw * scale;
    var displayHeight = sh * scale;
    return {
      scale: scale,
      offsetX: (vw - displayWidth) / 2,
      offsetY: (vh - displayHeight) / 2,
      displayWidth: displayWidth,
      displayHeight: displayHeight
    };
  }

  function mapBbox(bbox, viewWidth, viewHeight, sourceWidth, sourceHeight) {
    if (!Array.isArray(bbox) || bbox.length !== 4) return null;
    var x = finiteNumber(bbox[0], "bbox.x");
    var y = finiteNumber(bbox[1], "bbox.y");
    var w = finiteNumber(bbox[2], "bbox.w");
    var h = finiteNumber(bbox[3], "bbox.h");
    if (w <= 0 || h <= 0) return null;
    var layout = containLayout(viewWidth, viewHeight, sourceWidth, sourceHeight);
    return {
      x: layout.offsetX + x * layout.scale,
      y: layout.offsetY + y * layout.scale,
      width: w * layout.scale,
      height: h * layout.scale,
      scale: layout.scale,
      offsetX: layout.offsetX,
      offsetY: layout.offsetY
    };
  }

  function parseMeta(headers) {
    function get(name) {
      var value = headers && typeof headers.get === "function" ? headers.get(name) : null;
      if (value === null || value === undefined || value === "") throw new Error("缺少响应头 " + name);
      return value;
    }
    return {
      frameId: Math.trunc(finiteNumber(get(HEADERS.frameId), "frameId")),
      ts: finiteNumber(get(HEADERS.ts), "ts"),
      bbox: [
        Math.trunc(finiteNumber(get(HEADERS.bboxX), "bbox.x")),
        Math.trunc(finiteNumber(get(HEADERS.bboxY), "bbox.y")),
        Math.trunc(finiteNumber(get(HEADERS.bboxW), "bbox.w")),
        Math.trunc(finiteNumber(get(HEADERS.bboxH), "bbox.h"))
      ],
      sourceWidth: Math.trunc(finiteNumber(get(HEADERS.sourceWidth), "sourceWidth")),
      sourceHeight: Math.trunc(finiteNumber(get(HEADERS.sourceHeight), "sourceHeight")),
      faceVisible: finiteNumber(get(HEADERS.faceVisible), "faceVisible"),
      status: get(HEADERS.status),
      detector: headers.get(HEADERS.detector) || "unknown"
    };
  }

  function selfTest() {
    var failures = [];
    function near(actual, expected, label) {
      if (Math.abs(actual - expected) > 1e-9) failures.push(label + ": " + actual + " != " + expected);
    }
    var same = mapBbox([64, 48, 128, 96], 640, 480, 640, 480);
    near(same.x, 64, "same.x"); near(same.y, 48, "same.y");
    near(same.width, 128, "same.width"); near(same.height, 96, "same.height");

    var pillar = mapBbox([0, 0, 640, 480], 1000, 480, 640, 480);
    near(pillar.x, 180, "pillar.x"); near(pillar.y, 0, "pillar.y");
    near(pillar.width, 640, "pillar.width"); near(pillar.height, 480, "pillar.height");

    var letter = mapBbox([0, 0, 640, 480], 640, 640, 640, 480);
    near(letter.x, 0, "letter.x"); near(letter.y, 80, "letter.y");
    near(letter.width, 640, "letter.width"); near(letter.height, 480, "letter.height");

    var scaled = mapBbox([10, 20, 100, 120], 320, 240, 640, 480);
    near(scaled.x, 5, "scaled.x"); near(scaled.y, 10, "scaled.y");
    near(scaled.width, 50, "scaled.width"); near(scaled.height, 60, "scaled.height");
    if (mapBbox([0, 0, 0, 10], 320, 240, 640, 480) !== null) failures.push("空 bbox 应返回 null");
    var moving = [
      mapBbox([100, 80, 180, 220], 640, 480, 640, 480),
      mapBbox([260, 80, 180, 220], 640, 480, 640, 480)
    ];
    if (moving[0].x === moving[1].x) failures.push("连续帧 bbox 改变时屏幕坐标必须同步改变");

    /* ---- 1280×720（v1.5/v1.6 草案的 720p 档）：换算里不许留 640×480 的印子 ---- */
    var hd = mapBbox([100, 100, 200, 200], 1280, 720, 1280, 720);   // 同尺寸 ⇒ 恒等
    near(hd.x, 100, "hd.x"); near(hd.y, 100, "hd.y");
    near(hd.width, 200, "hd.width"); near(hd.height, 200, "hd.height");

    var hdLetter = mapBbox([0, 0, 1280, 720], 640, 480, 1280, 720);  // 4:3 视口 ⇒ 上下留黑 60
    near(hdLetter.x, 0, "hdLetter.x"); near(hdLetter.y, 60, "hdLetter.y");
    near(hdLetter.width, 640, "hdLetter.width"); near(hdLetter.height, 360, "hdLetter.height");

    var hdPillar = containLayout(1000, 480, 1280, 720);              // 宽视口 ⇒ 左右留黑
    near(hdPillar.scale, 480 / 720, "hdPillar.scale");
    near(hdPillar.offsetX, (1000 - 1280 * (480 / 720)) / 2, "hdPillar.offsetX");
    near(hdPillar.offsetY, 0, "hdPillar.offsetY");

    /* 源尺寸必须来自服务端响应头，前端不能自己假设 640×480 */
    var table = {};
    table[HEADERS.frameId] = "42";
    table[HEADERS.ts] = "1729000000.5";
    table[HEADERS.bboxX] = "100"; table[HEADERS.bboxY] = "120";
    table[HEADERS.bboxW] = "300"; table[HEADERS.bboxH] = "240";
    table[HEADERS.sourceWidth] = "1280"; table[HEADERS.sourceHeight] = "720";
    table[HEADERS.faceVisible] = "1"; table[HEADERS.status] = "normal";
    table[HEADERS.detector] = "mediapipe";
    var meta = parseMeta({ get: function (name) { return name in table ? table[name] : null; } });
    if (meta.sourceWidth !== 1280 || meta.sourceHeight !== 720) {
      failures.push("parseMeta 必须原样保留 1280×720，实际 " +
                    meta.sourceWidth + "×" + meta.sourceHeight);
    }
    if (meta.frameId !== 42) failures.push("parseMeta.frameId: " + meta.frameId);

    return { checked: 9, failures: failures, ok: failures.length === 0 };
  }

  return { HEADERS: HEADERS, containLayout: containLayout, mapBbox: mapBbox, parseMeta: parseMeta, selfTest: selfTest };
});

if (typeof module !== "undefined" && module.exports && require.main === module) {
  var result = module.exports.selfTest();
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  process.exitCode = result.ok ? 0 : 1;
}
