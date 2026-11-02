/* Browser QA for synthetic filming playback, using Edge/Chrome and native CDP. */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const root = path.resolve(import.meta.dirname, "../..");
const out = path.join(root, "metrics/logs");
const port = 9348;
const browserPath = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe"
].find(p => fs.existsSync(p));
if (!browserPath) throw new Error("Edge/Chrome not found");
const browser = spawn(browserPath, ["--headless=new", "--disable-gpu", "--no-first-run",
  "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
  `--remote-debugging-port=${port}`, `--user-data-dir=${path.join(out, "_recording_browser")}`,
  "about:blank"], { stdio: "ignore", windowsHide: true });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket, id = 0, budgetDone;
const pending = new Map(), errors = [], checks = [];
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const callId = ++id;
    const timeout = setTimeout(() => { pending.delete(callId); reject(new Error("CDP timeout: " + method)); }, 15000);
    pending.set(callId, { resolve: value => { clearTimeout(timeout); resolve(value); },
      reject: error => { clearTimeout(timeout); reject(error); } });
    socket.send(JSON.stringify({ id: callId, method, params }));
  });
}
async function evaluate(expression) {
  const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function advance(milliseconds) {
  const done = new Promise(resolve => { budgetDone = resolve; });
  await send("Emulation.setVirtualTimePolicy", { policy: "advance", budget: milliseconds });
  await done;
}
async function inspect() {
  return evaluate(`(() => ({ scene: document.getElementById('recordingScene').textContent,
    progress: document.getElementById('recordingProgress').textContent,
    heart: document.querySelector('#card_hr .num').textContent,
    heartCurve: document.querySelector('#card_hr canvas').getAttribute('aria-label'),
    respirationVisible: getComputedStyle(document.getElementById('card_rr')).display !== 'none',
    respirationTextVisible: /呼吸/.test(document.querySelector('main').innerText),
    state: document.getElementById('stateName').textContent,
    connection: document.getElementById('connName').textContent,
    alert: !document.getElementById('fatigueAlert').hidden,
    kind: document.getElementById('fatigueAlert').dataset.kind,
    samples: Number(document.querySelector('#card_hr canvas').dataset.samples),
    overflow: document.documentElement.scrollWidth > innerWidth + 1,
    invalid: document.getElementById('validName').textContent.startsWith('✗')
  }))()`);
}
async function select(scene, seconds = 15) {
  await evaluate(`document.getElementById('recordingSelection').value=${JSON.stringify(scene)};
    document.getElementById('recordingSeconds').value=${seconds};
    document.getElementById('recordingRestart').click()`);
}
async function screenshot(name) {
  await advance(180);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(path.join(out, `recording_${name}.png`), Buffer.from(shot.data, "base64"));
}
async function layout() {
  return evaluate(`(() => {
    const box = selector => { const b = document.querySelector(selector).getBoundingClientRect();
      return { x:b.x, y:b.y, width:b.width, height:b.height, right:b.right, bottom:b.bottom }; };
    const visible = element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
    return {
      viewport: [innerWidth, innerHeight], pageHeight: document.documentElement.scrollHeight,
      pageWidth: document.documentElement.scrollWidth,
      camera: box('.video-wrap'), cameraStage: box('.preview-stage'), gate: box('.gate-section'), metrics: box('#measurements'),
      insetPresent: !!document.querySelector('.recording-inset-space'),
      statusVisible:document.querySelector('.status-panel').getClientRects().length > 0,
      trendLinkPresent: !!document.querySelector('.section-nav a[href="#measurements"]'),
      blinkFooterVisible: document.querySelector('.measurement-footer').getClientRects().length > 0,
      cards: Array.from(document.querySelectorAll('.card')).filter(visible).map(card => ({
        id: card.id, bottom:card.getBoundingClientRect().bottom,
        height:card.getBoundingClientRect().height,
        chartWidth:card.querySelector('canvas').clientWidth, chartHeight:card.querySelector('canvas').clientHeight,
        overflow:card.scrollHeight > card.clientHeight + 1,
        readingClipped:card.querySelector('.card-reading').scrollWidth > card.querySelector('.card-reading').clientWidth + 1
      })),
      optionsHidden: document.getElementById('recordingOptions').hidden,
      expanded:document.getElementById('recordingToggle').getAttribute('aria-expanded'),
      offscreen: Array.from(document.querySelectorAll('main *')).filter(e => visible(e) && e.getBoundingClientRect().bottom > innerHeight + 1).map(e => ({tag:e.tagName, id:e.id, cls:e.className, bottom:e.getBoundingClientRect().bottom}))
    };
  })()`);
}
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/json/version`); ready = r.ok; } catch {}
    if (ready) break;
    await delay(100);
  }
  if (!ready) throw new Error("browser startup timed out");
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = event => {
    const m = JSON.parse(event.data);
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.text);
    if (m.method === "Emulation.virtualTimeBudgetExpired" && budgetDone) { budgetDone(); budgetDone = null; }
    if (!m.id || !pending.has(m.id)) return;
    const { resolve, reject } = pending.get(m.id); pending.delete(m.id);
    if (m.error) reject(new Error(JSON.stringify(m.error))); else resolve(m.result || {});
  };
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: pathToFileURL(path.join(root, "frontend/recording.html")).href });
  await delay(1000);
  await send("Emulation.setVirtualTimePolicy", { policy: "pause" });
  assert.equal(await evaluate("document.getElementById('recordingSeconds').value"), "15");
  await select("rest");
  let view = await inspect();
  assert.equal(view.scene, "静息状态");
  assert.equal(view.heart, await evaluate("VigiLensRecording.makeFrame('rest', 0).vital.hr_bpm.toFixed(1)"));
  assert.ok(view.heartCurve.includes('最新值 ' + view.heart + 'bpm'));
  assert.equal(view.samples, 120); assert.equal(view.alert, false); assert.equal(view.overflow, false);
  assert.equal(view.respirationVisible, false); assert.equal(view.respirationTextVisible, false);
  checks.push("independent entry, full prepared history and initial resting values");
  assert.equal((await layout()).optionsHidden, true);
  assert.equal((await layout()).expanded, 'false');
  const beforeControls = (await inspect()).progress;
  await evaluate("document.getElementById('recordingToggle').click()");
  assert.equal((await layout()).optionsHidden, false);
  assert.equal((await layout()).expanded, 'true');
  await screenshot('controls');
  await send('Input.dispatchKeyEvent', { type:'keyDown', key:'Escape', code:'Escape', windowsVirtualKeyCode:27 });
  await send('Input.dispatchKeyEvent', { type:'keyUp', key:'Escape', code:'Escape', windowsVirtualKeyCode:27 });
  assert.equal((await layout()).optionsHidden, true);
  assert.equal((await inspect()).progress, beforeControls);
  await evaluate("document.getElementById('recordingToggle').click()");
  await evaluate("document.querySelector('.page-heading h1').click()");
  assert.equal((await layout()).optionsHidden, true);
  const desktopLayouts = [];
  for (const [width, height] of [[1920, 1080], [1536, 864], [1440, 900], [1366, 768], [1366, 650], [1280, 720]]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor:1, mobile:false });
    await evaluate("window.dispatchEvent(new Event('resize'))");
    const measured = await layout();
    desktopLayouts.push(measured);
    if (measured.pageHeight > height + 1) await screenshot('overflow_' + width + 'x' + height);
    assert.ok(measured.pageHeight <= height + 1, 'page scrolls at ' + width + 'x' + height + ': ' + JSON.stringify(measured));
    assert.ok(measured.pageWidth <= width + 1, 'horizontal overflow');
    assert.equal(measured.cards.length, 13);
    assert.ok(measured.cards.every(card => card.bottom <= height && !card.overflow && !card.readingClipped && card.chartWidth > 0 && card.chartHeight > 20), JSON.stringify(measured));
    assert.ok(Math.abs(measured.camera.width / measured.camera.height - 4 / 3) < .01, 'camera should keep a 4:3 frame');
    assert.ok(Math.abs((measured.camera.x + measured.camera.width / 2) - (measured.cameraStage.x + measured.cameraStage.width / 2)) < 1, 'camera should be centered');
    assert.equal(measured.trendLinkPresent, false);
    assert.equal(measured.blinkFooterVisible, false);
    assert.equal(measured.insetPresent, false);
    assert.equal(measured.statusVisible, false);
    assert.ok(measured.gate.height < 100, 'quality panel should be compact');
    assert.ok(measured.metrics.x > measured.camera.right, 'desktop split changed');
    await screenshot('layout_' + width + 'x' + height);
  }
  fs.writeFileSync(path.join(out, 'recording_layouts.json'), JSON.stringify(desktopLayouts, null, 2));
  checks.push('collapsed controls; six desktop sizes fit all cards, proportional camera and compact quality panel; no status/inset');
  await send('Emulation.setDeviceMetricsOverride', { width:1440, height:900, deviceScaleFactor:1, mobile:false });
  await select('rest');
  await screenshot("rest");
  await advance(12000); assert.equal((await inspect()).alert, false);
  view = await inspect();
  assert.equal(view.heart, await evaluate("VigiLensRecording.makeFrame('rest', 12).vital.hr_bpm.toFixed(1)"));
  assert.ok(view.heartCurve.includes('最新值 ' + view.heart + 'bpm'));
  await evaluate("document.getElementById('recordingPause').click()");
  const paused = await inspect(); await advance(5000); assert.deepEqual(await inspect(), paused);
  await evaluate("document.getElementById('recordingPause').click()");
  await advance(1000); assert.notEqual((await inspect()).progress, paused.progress);
  checks.push("pause holds all values and resume advances one sample");
  await select("exercise", 60); view = await inspect();
  assert.equal(view.heart, await evaluate("VigiLensRecording.makeFrame('exercise', 0, {seconds:60}).vital.hr_bpm.toFixed(1)")); assert.equal(view.state, "正常");
  assert.ok(view.heartCurve.includes('最新值 ' + view.heart + 'bpm'));
  await screenshot("exercise");
  await advance(11000); view = await inspect(); assert.equal(view.kind, "heart_rate"); assert.equal(view.alert, true);
  await evaluate("document.getElementById('alertDismiss').click()");
  await advance(1000); assert.equal((await inspect()).alert, false);
  await evaluate("document.getElementById('recordingToggle').click()");
  await evaluate("document.getElementById('recordingDiagnostics').click()");
  assert.equal(await evaluate("document.getElementById('developerTools').open && getComputedStyle(document.getElementById('developerTools')).display !== 'none'"), true);
  await evaluate("document.getElementById('recordingToggle').click()");
  await evaluate("document.getElementById('recordingDiagnostics').click()");
  assert.equal(await evaluate("getComputedStyle(document.getElementById('developerTools')).display"), 'none');
  checks.push("exercise starts higher, raises sustained heart-rate prompt and acknowledges once");
  await select("fatigue"); assert.equal((await inspect()).alert, false);
  const initialYawnCount = await evaluate("Number(document.querySelector('#card_yawn .num').textContent)");
  await advance(6000);
  assert.ok(await evaluate("Number(document.querySelector('#card_mar .num').textContent) > 0.6"));
  await screenshot('fatigue_yawn');
  await advance(5000); view = await inspect();
  assert.ok(view.heartCurve.includes('最新值 ' + view.heart + 'bpm'));
  assert.ok(await evaluate("Number(document.querySelector('#card_mar .num').textContent) < 0.25"));
  assert.equal(await evaluate("Number(document.querySelector('#card_yawn .num').textContent)"), initialYawnCount + 1);
  assert.equal(view.state, "疲劳风险升高"); assert.equal(view.kind, "fatigue"); assert.equal(view.alert, true);
  assert.equal(view.respirationVisible, false); assert.equal(view.respirationTextVisible, false);
  await screenshot("fatigue_alert");
  await evaluate("document.getElementById('alertDismiss').click()"); await screenshot("fatigue");
  for (const [width, height] of [[1366, 650], [1280, 720]]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor:1, mobile:false });
    await evaluate("window.dispatchEvent(new Event('resize'))");
    const measured = await layout();
    assert.ok(measured.pageHeight <= height && measured.offscreen.length === 0, JSON.stringify(measured));
    assert.ok(measured.cards.every(card => !card.overflow && !card.readingClipped), JSON.stringify(measured));
    await screenshot('fatigue_' + width + 'x' + height);
  }
  await send('Emulation.setDeviceMetricsOverride', { width:1440, height:900, deviceScaleFactor:1, mobile:false });
  checks.push("group switch resets alert timer; sustained fatigue prompt appears");
  await select("all", 15);
  await advance(15000); assert.equal((await inspect()).scene, "短时运动后");
  await advance(15000); assert.equal((await inspect()).scene, "疲劳状态");
  await advance(15000); view = await inspect(); assert.equal(view.connection, "");
  assert.equal(await evaluate("document.body.innerText.includes('本轮播放结束')"), false);
  assert.equal(await evaluate("document.getElementById('measurementLive').hidden && document.getElementById('connPill').hidden"), true);
  await advance(5000); assert.deepEqual(await inspect(), view);
  checks.push("one complete yawn, sustained fatigue alert, then silently hold the last frame after all three groups");
  await select("rest");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal((await inspect()).overflow, false);
  checks.push("narrow viewport has no horizontal overflow");
  // Fake camera only: no person or external service is involved in this check.
  await evaluate("document.getElementById('recordingCamera').click()");
  await advance(1000);
  let camera = false;
  for (let attempt = 0; attempt < 30; attempt++) {
    await advance(1000);
    camera = await evaluate("!!document.getElementById('recordingVideo').srcObject");
    if (camera) break;
    await delay(100);
  }
  assert.equal(camera, true, await evaluate("document.getElementById('recordingHint').textContent + ' / busy=' + document.getElementById('recordingCamera').disabled"));
  await send('Emulation.setDeviceMetricsOverride', { width:1366, height:768, deviceScaleFactor:1, mobile:false });
  await advance(1000);
  const liveAspect = await evaluate(`(() => { const v=document.getElementById('recordingVideo'), b=document.querySelector('.video-wrap').getBoundingClientRect();
    return {source:v.videoWidth/v.videoHeight, frame:b.width/b.height, fit:getComputedStyle(v).objectFit}; })()`);
  assert.ok(Math.abs(liveAspect.source - liveAspect.frame) < .01, JSON.stringify(liveAspect));
  assert.equal(liveAspect.fit, 'contain');
  await screenshot('camera_proportions');
  await evaluate("document.getElementById('btnOffline').click()");
  assert.equal(await evaluate("document.getElementById('recordingVideo').srcObject === null"), true);
  assert.equal(await evaluate("document.getElementById('recordingControls').hidden"), true);
  assert.equal(await evaluate("document.getElementById('srcName').textContent"), "离线 Mock");
  checks.push("local camera preview opens and its tracks stop when leaving filming mode");
  assert.deepEqual(errors, []);
  const result = { checks, runtimeErrors: errors, ok: true };
  fs.writeFileSync(path.join(out, "recording_browser.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  await send("Browser.close");
} catch (error) {
  console.error(error.stack || error); process.exitCode = 1;
} finally {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (!browser.killed) browser.kill();
}
