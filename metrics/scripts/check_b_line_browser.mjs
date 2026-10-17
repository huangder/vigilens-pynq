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
const expectedSource = source === "mock" ? "服务 Mock" : (source === "standalone" ? "来源未声明" : "A 线 ingest");
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
    noface: "unreliable"
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
  if (!["ingest", "mock", "standalone"].includes(source)) {
    throw new Error("--source must be ingest, mock or standalone");
  }
  if (source === "standalone" && !wsUrl) throw new Error("--source standalone requires --ws-url");
  await waitForJson(`${base}/api/status`);

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
      `http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(base + "/")}`,
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
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
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
    await send("Page.navigate", { url: base + "/" });
    await delay(800);
    if (wsUrl) {
      await send("Runtime.evaluate", {
        expression: `document.getElementById("wsUrl").value = ${JSON.stringify(wsUrl)}`
      });
    }
    await send("Runtime.evaluate", {
      expression: `document.getElementById(${JSON.stringify(wsUrl ? "btnConnect" : "btnStart")}).click()`
    });
    await delay(500);
    if (source === "ingest") await postFrame(makeFrame(scenario));
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
        uiModuleLoaded: !!window.VigiLensUIState
      }))()`
    });
    const data = inspection.result.value;
    const failures = [];
    if (data.source !== expectedSource) failures.push(`source=${data.source}, expected=${expectedSource}`);
    if (data.connection !== "已连接") failures.push(`connection=${data.connection}`);
    if (data.horizontalOverflow) failures.push("horizontal overflow");
    if (!data.uiModuleLoaded) failures.push("ui_state.js missing");

    if (source === "ingest") {
      const expectedStates = {
        normal: "正常",
        unreliable: "信号不可靠",
        disconnected: "连接中断",
        done: "测量完成",
        noface: "信号不可靠"
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
    }

    if (screenshotPath) {
      const layout = await send("Page.getLayoutMetrics");
      const size = layout.cssContentSize;
      const screenshot = await send("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: true,
        clip: { x: 0, y: 0, width: Math.min(size.width, width), height: size.height, scale: 1 }
      });
      fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, "base64"));
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
