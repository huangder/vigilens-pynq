/*
 * Headless browser regression for the B-line dashboard.
 *
 * No npm package is required: the script talks to an installed Edge/Chrome via
 * the Chrome DevTools Protocol and uses Node's built-in fetch/WebSocket APIs.
 * Start backend/api.py first; this script only drives the browser and /api/ingest.
 *
 * Examples (from the repository root):
 *   .venv\Scripts\python.exe backend\api.py --no-mock --port 8897
 *   node metrics\scripts\check_b_line_browser.mjs --scenario noface
 *   node metrics\scripts\check_b_line_browser.mjs --scenario normal --width 390 --height 844
 *
 * To check the default service Mock label, start api.py without --no-mock:
 *   node metrics\scripts\check_b_line_browser.mjs --source mock
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const Mock = require("../../frontend/mock.js");
const repoRoot = path.resolve(import.meta.dirname, "../..");

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && index + 1 < process.argv.length ? process.argv[index + 1] : fallback;
}

const scenario = option("--scenario", "normal");
const source = option("--source", "ingest");
const wsUrl = option("--ws-url", "");
const base = option("--base", "http://127.0.0.1:8897").replace(/\/$/, "");
const width = Number(option("--width", "1440"));
const height = Number(option("--height", "900"));
const debugPort = Number(option("--debug-port", "9334"));
const screenshotArg = option("--screenshot", "");
const screenshotPath = screenshotArg ? path.resolve(screenshotArg) : "";
const expectedSource = source === "offline" ? "离线 Mock" : (source === "mock" ? "服务 Mock" : (source === "standalone" ? "来源未声明" : "A 线 ingest"));
const pageUrl = source === "offline" ? pathToFileURL(path.join(repoRoot, "frontend", "index.html")).href : base + "/";
const profile = path.join(repoRoot, "metrics", "logs", `_edge_qa_profile_${debugPort}`);

const browserCandidates = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"
];

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForJson(url, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return response.json();
    } catch (_) {
      // Browser startup races the first probe.
    }
    await delay(100);
  }
  throw new Error(`timeout waiting for ${url}`);
}

function makeFrame(name) {
  const statuses = {
    normal: "normal",
    unreliable: "unreliable",
    disconnected: "disconnected",
    done: "done",
    noface: "unreliable",
    history: "normal",
    gated: "unreliable"
  };
  if (!(name in statuses)) throw new Error(`unsupported scenario: ${name}`);
  const index = Object.keys(statuses).indexOf(name);
  const frame = Mock.mockFrame(100 + index, {
    status: statuses[name],
    seed: 20260910,
    ts: 100 + index
  });
  if (name === "noface") {
    frame.face.visible = 0;
    frame.face.bbox = [0, 0, 0, 0];
    frame.face.pose = { yaw: 0, pitch: 0, roll: 0 };
    frame.reason = "Mock 无人脸帧：验证前端清除旧值";
  }
  if (name === "gated") {
    // Deliberately retain numeric vitals to verify that the UI gate masks them.
    const normal = Mock.mockFrame(100, { status: "normal" });
    frame.vital = normal.vital;
    frame.reason = "QA 合成 Mock：低质量帧携带数值时前端仍应门控";
  }
  return frame;
}

async function postFrame(frame) {
  const response = await fetch(`${base}/api/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ frame })
  });
  if (!response.ok) throw new Error(`ingest failed: ${response.status} ${await response.text()}`);
}

async function main() {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    throw new Error("--width and --height must be positive numbers");
  }
  if (!["ingest", "mock", "standalone", "offline"].includes(source)) {
    throw new Error("--source must be ingest, mock, standalone or offline");
  }
  if (source === "standalone" && !wsUrl) throw new Error("--source standalone requires --ws-url");
  if (source !== "offline") await waitForJson(`${base}/api/status`);

  const browserPath = browserCandidates.find((candidate) => fs.existsSync(candidate));
  if (!browserPath) throw new Error("Edge/Chrome not found in the standard Windows install paths");
  const browser = spawn(browserPath, [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--no-first-run",
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${profile}`,
    "about:blank"
  ], { stdio: "ignore", windowsHide: true });

  let ws;
  try {
    await waitForJson(`http://127.0.0.1:${debugPort}/json/version`);
    const targetResponse = await fetch(
      `http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(pageUrl)}`,
      { method: "PUT" }
    );
    if (!targetResponse.ok) throw new Error(`cannot create browser target: ${targetResponse.status}`);
    const target = await targetResponse.json();
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = reject;
    });

    let nextId = 1;
    const pending = new Map();
    const runtimeErrors = [];
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.method === "Runtime.exceptionThrown") runtimeErrors.push(message.params.exceptionDetails.text);
      if (!message.id || !pending.has(message.id)) return;
      const handlers = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) handlers.reject(new Error(JSON.stringify(message.error)));
      else handlers.resolve(message.result || {});
    };
    function send(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = nextId++;
        pending.set(id, { resolve, reject });
        ws.send(JSON.stringify({ id, method, params }));
      });
    }

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: width <= 500
    });
    await send("Page.navigate", { url: pageUrl });
    await delay(800);
    if (source === "offline") {
      await send("Runtime.evaluate", {
        expression: `document.getElementById("forceStatus").value = "normal"; document.getElementById("forceStatus").dispatchEvent(new Event("change"))`
      });
    }
    if (wsUrl) {
      await send("Runtime.evaluate", {
        expression: `document.getElementById("wsUrl").value = ${JSON.stringify(wsUrl)}`
      });
    }
    if (source !== "offline") {
      await send("Runtime.evaluate", {
        expression: `document.getElementById(${JSON.stringify(wsUrl ? "btnConnect" : "btnStart")}).click()`
      });
    }
    await delay(500);
    let finalFrame = null;
    if (source === "ingest" && scenario === "history") {
      // Accelerated synthetic fixtures exercise 120 seconds of the timestamp axis.
      // These are explicitly Mock data, not measurement/performance evidence.
      for (let i = 0; i < 120; i++) {
        const f = i === 40 ? makeFrame("noface") : Mock.mockFrame(i, {
          status: i === 80 ? "disconnected" : (i === 60 ? "unreliable" : "normal"),
          ts: 1000 + i
        });
        f.ts = 1000 + i;
        f.frame_id = i;
        await postFrame(f);
        finalFrame = f;
        await delay(25);
      }
    } else if (source === "ingest") {
      finalFrame = makeFrame(scenario);
      await postFrame(finalFrame);
    }
    await delay(1200);

    const inspection = await send("Runtime.evaluate", {
      returnByValue: true,
      expression: `(() => ({
        state: document.getElementById("stateName").textContent,
        source: document.getElementById("srcName").textContent,
        connection: document.getElementById("connName").textContent,
        gate: document.getElementById("gateVerdictText").textContent,
        gateValues: Array.from(document.querySelectorAll(".gate-row .val"), e => e.textContent),
        cards: Array.from(document.querySelectorAll(".card .num"), e => e.textContent),
        blinkActive: document.querySelectorAll(".blink-step.on").length,
        horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        viewport: [window.innerWidth, window.innerHeight],
        uiModuleLoaded: !!window.VigiLensUIState,
        trendsModuleLoaded: !!window.VigiLensTrends,
        metricCharts: Array.from(document.querySelectorAll(".metric-chart"), e => ({
          samples: Number(e.dataset.samples), segments: Number(e.dataset.segments),
          accessibleLabel: e.getAttribute("aria-label"),
          width: e.clientWidth, height: e.clientHeight
        })),
        valueSizes: Array.from(document.querySelectorAll(".card:not(.null) .num"), e => parseFloat(getComputedStyle(e).fontSize)),
        confidenceSize: parseFloat(getComputedStyle(document.querySelector("#card_hr .note")).fontSize),
        videoOverlayPresent: !!document.querySelector(".video-wrap canvas"),
        videoTrackingHintsPresent: /人脸定位|定位框|追踪|同帧框|过期框|stub 框/.test(document.querySelector(".video-panel").textContent),
        horizontalCards: Array.from(document.querySelectorAll(".card"), e => {
          const reading = e.querySelector(".card-reading").getBoundingClientRect();
          const chart = e.querySelector(".card-trend").getBoundingClientRect();
          return chart.left >= reading.right && Math.abs(chart.top - reading.top) < 2;
        }),
        workspaceIsSplit: document.getElementById("measurements").getBoundingClientRect().left > document.querySelector(".video-panel").getBoundingClientRect().right
      }))()`
    });
    const data = inspection.result.value;
    const failures = [];
    if (data.source !== expectedSource) failures.push(`source=${data.source}, expected=${expectedSource}`);
    if (data.connection !== (source === "offline" ? "离线模式" : "已连接")) failures.push(`connection=${data.connection}`);
    if (data.horizontalOverflow) failures.push("horizontal overflow");
    if (!data.uiModuleLoaded) failures.push("ui_state.js missing");
    if (!data.trendsModuleLoaded) failures.push("trends.js missing");
    if (data.videoOverlayPresent) failures.push("video still contains a face-tracking canvas");
    if (data.videoTrackingHintsPresent) failures.push("video still contains face-tracking hints");
    if (runtimeErrors.length) failures.push(`runtime errors: ${runtimeErrors.join(", ")}`);
    if (data.metricCharts.length !== data.cards.length || data.metricCharts.some(c => c.width <= 0 || c.height <= 0)) failures.push("a metric card has no visible chart");
    if (!data.horizontalCards.every(Boolean)) failures.push("a card does not place its value to the left of its chart");
    if (width > 760 && !data.workspaceIsSplit) failures.push("desktop does not place measurements to the right of the video");
    if (data.valueSizes.some(size => size < 29 || size <= data.confidenceSize * 2)) failures.push("current values are not prominent relative to confidence");

    if (source === "ingest") {
      const expectedStates = {
        normal: "正常",
        unreliable: "信号不可靠",
        disconnected: "连接中断",
        done: "测量完成",
        noface: "信号不可靠",
        history: "正常",
        gated: "信号不可靠"
      };
      const expectedPlaceholder = scenario === "noface"
        ? "无测量"
        : (scenario === "disconnected" ? "无数据" : null);
      if (data.state !== expectedStates[scenario]) failures.push(`state=${data.state}`);
      if (expectedPlaceholder && !data.cards.every((value) => value === expectedPlaceholder)) {
        failures.push(`cards did not all show ${expectedPlaceholder}`);
      }
      if (expectedPlaceholder && !data.gateValues.every((value) => value === "—")) {
        failures.push("gate values were not cleared");
      }
      if (expectedPlaceholder && data.blinkActive !== 0) failures.push("blink state remained active");
      if (!expectedPlaceholder && data.cards.every((value) => ["—", "无数据", "无测量"].includes(value))) {
        failures.push("measured frame showed no values");
      }
      // Existing history is intentionally retained. Missing current readings must
      // clear the latest marker, not erase measurements received before the gap.
      if (expectedPlaceholder && data.metricCharts.some(c => !c.accessibleLabel.includes("当前无有效值"))) failures.push("missing measurement produced a current chart value");
      if (scenario === "gated" && data.metricCharts.slice(0, 2).some(c => !c.accessibleLabel.includes("当前无有效值"))) failures.push("low-quality vitals reached the current chart marker");
      if (scenario === "gated" && data.cards.slice(0, 2).some(v => /^-?\d/.test(v))) failures.push("low-quality vitals were displayed as current values");
      if (scenario === "history") {
        if (data.metricCharts.some(c => c.samples < 90 || c.segments < 3)) failures.push("history samples or missing-data gaps were lost");
        if (data.cards[0] !== finalFrame.vital.hr_bpm.toFixed(1)) failures.push("latest heart rate does not match the received frame");
        if (!data.metricCharts[0].accessibleLabel.includes(finalFrame.vital.hr_bpm.toFixed(1))) failures.push("latest sample is absent from the heart-rate chart");
      }
    }

    if (screenshotPath) {
      await send("Runtime.evaluate", { expression: `document.querySelector(".page-context").textContent = "QA 合成 Mock · 仅验证界面，非测量结果"` });
      const layout = await send("Page.getLayoutMetrics");
      const size = layout.cssContentSize;
      const screenshot = await send("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: true,
        clip: { x: 0, y: 0, width: Math.min(size.width, width), height: size.height, scale: 1 }
      });
      fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, "base64"));
    }

    if (source === "ingest" && scenario === "history") {
      const stopped = await send("Runtime.evaluate", {
        returnByValue: true,
        expression: `(() => {
          document.getElementById("btnStop").click();
          return {
            label: document.getElementById("measurementLiveText").textContent,
            readings: Array.from(document.querySelectorAll(".card:not(.null) .card-meta"), e => e.textContent)
          };
        })()`
      });
      if (stopped.result.value.label !== "监测已停止" || !stopped.result.value.readings.every(v => v === "最后一次测量")) failures.push("stopped monitoring still claims to show live measurements");
      const switched = await send("Runtime.evaluate", {
        returnByValue: true,
        expression: `(() => {
          document.getElementById("btnOffline").click();
          return Array.from(document.querySelectorAll(".metric-chart"), e => Number(e.dataset.samples));
        })()`
      });
      if (switched.result.value.some(n => n > 1)) failures.push("switching to offline Mock retained the previous source history");
    }

    console.log(JSON.stringify({
      scenario,
      sourceMode: source,
      data,
      failures,
      ok: failures.length === 0,
      screenshot: screenshotPath || null
    }, null, 2));
    if (failures.length) process.exitCode = 1;
    await send("Browser.close");
  } finally {
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
    if (!browser.killed) browser.kill();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
