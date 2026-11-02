// Halo Remote browser client: the page glue. Protocol, input and session logic live in the tested modules.
import * as P from "./protocol.js";
import * as I from "./input.js";
import { pairWithHost, RelayClient, RemoteSession } from "./client.js";
import * as store from "./store.js";

const $ = (id) => document.getElementById(id);
const RELAY_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
const REASONS = {
  declined: "The PC said no.", no_answer: "Nobody answered on the PC.", busy: "Someone else is connected to that PC.",
  not_paired: "This device isn't paired with that PC any more.", revoked: "This device was removed on the PC.",
  not_allowed: "Remote desktop is switched off for this device on the PC.", connection_lost: "The connection dropped.",
  connect_timeout: "Couldn't reach the PC (network blocked?).", idle: "Disconnected after a long idle time.",
  offline: "The PC is offline.", replaced: "Connected again from this device.", portal_start_refused:
    "The PC refused screen sharing (approve Halo Remote once at the PC).", timeout: "No answer from the relay.",
  bad_code: "Wrong or expired pairing code.", wrong_host: "That answer didn't come from the PC you named.",
};
const say = (reason) => REASONS[reason] || `Stopped (${reason}).`;

let me, relay, hostList = [], online = new Map(), session = null, retries = 0, current = null;
const gestures = new I.TouchGestures();
const coalescer = new I.MoveCoalescer(16);
const keysDown = new Set();
let lastRemoteClip = "";

// ---------------------------------------------------------------- relay + hosts
function connectRelay(pairAccount) {
  if (relay) relay.close();
  relay = new RelayClient({ url: RELAY_URL, me, name: deviceName(), accounts: hostList.map((h) => h.account),
    pair: pairAccount });
  relay.on("status", ({ status, detail }) => {
    const el = $("relay-status");
    el.textContent = status === "reconnecting" ? `reconnecting in ${detail}s` : status;
    el.className = "pill " + status;
  });
  relay.on("message", (m) => {
    if (m.type === "presence") {
      online = new Map(m.hosts.map((h) => [h.device_id, h]));
      renderHosts();
    } else if (m.type === "error" && m.reason === "revoked") {
      $("pair-result").textContent = say("revoked");
    }
  });
  relay.connect();
  return relay;
}

function deviceName() {
  const ua = navigator.userAgent;
  return localStorage.getItem("halo.name") || (/iPad|Macintosh.*Mobile/.test(ua) || (/Macintosh/.test(ua) &&
    navigator.maxTouchPoints > 1) ? "iPad" : /CrOS/.test(ua) ? "Chromebook" : /iPhone/.test(ua) ? "iPhone" : "Browser");
}

function renderHosts() {
  const ul = $("hosts");
  ul.textContent = "";
  $("no-hosts").classList.toggle("hidden", hostList.length > 0);
  for (const h of hostList) {
    const live = online.get(h.device_id);
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "dot" + (live?.online ? " on" : "");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = (live?.name || h.name || h.device_id) + (live ? (live.online ? "" : " · offline") : " · not available");
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = "Connect";
    go.disabled = !live?.online;
    go.onclick = () => connect(h);
    const forget = document.createElement("button");
    forget.textContent = "Forget";
    forget.onclick = async () => { hostList = await store.forgetHost(h.device_id); connectRelay(); renderHosts(); };
    li.append(dot, name, go, forget);
    ul.append(li);
  }
}

// ---------------------------------------------------------------- pairing
async function pair(code, hostId, account, name) {
  const out = $("pair-result");
  out.textContent = "Pairing…";
  if (name) localStorage.setItem("halo.name", name);
  const r = connectRelay(account);
  try {
    if (r.status !== "online") await new Promise((res) => r.on("status", (s) => s.status === "online" && res()));
    const record = await pairWithHost(r, me, { code, hostId, account, name: deviceName() });
    hostList = await store.saveHost(record);
    out.textContent = `Paired with ${record.name}. On the PC this device asks before each connection; you can set it to "allow" in SAINT → Devices.`;
    connectRelay();
    renderHosts();
  } catch (e) {
    out.textContent = say(e.reason || String(e));
  }
}

// ---------------------------------------------------------------- session
function overlay(text) { $("overlay").textContent = text || ""; }

function connect(host) {
  current = host;
  retries = 0;
  $("home").classList.add("hidden");
  $("viewer").classList.remove("hidden");
  startSession();
}

async function startSession() {
  const video = $("video");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const want = { max_w: Math.round(Math.min(screen.width * dpr, 2560)), max_h: Math.round(Math.min(screen.height * dpr, 1600)),
    fps: 30, audio: false };
  session = new RemoteSession({
    relay, me, host: current, want, name: deviceName(),
    onTrack: (stream) => { video.srcObject = stream; video.play().catch(() => {}); },
    onStatus: (state, reason) => {
      if (state === "waiting") overlay("Waiting for the PC to allow this device…");
      else if (state === "negotiating") overlay("Connecting…");
      else if (state === "unstable") overlay("Connection unstable…");
      else if (state === "connected") { overlay(""); retries = 0; sendDisplay(); }
      else if (state === "ended") ended(reason);
    },
    onStats: (s) => { hostStats = s; },
    onControl: (m) => {
      if (m.type === "clipboard" && m.mime.startsWith("text/")) {
        lastRemoteClip = m.data;
        $("btn-copy").disabled = false;
      }
    },
  });
  try { await session.start(); } catch (e) { ended(e.reason || "offline"); }
}

function ended(reason) {
  releaseAll();
  if (reason === "connection_lost" && current && retries < 5) {
    retries += 1;
    const wait = Math.min(1000 * 2 ** retries, 15000);
    overlay(`${say(reason)} Reconnecting (${retries}/5)…`);
    setTimeout(() => current && startSession(), wait);
    return;
  }
  if (reason === "closed") return;
  overlay(say(reason));
  setTimeout(leave, 3500);
}

function leave() {
  if (session) session.end("closed");
  session = null;
  current = null;
  $("video").srcObject = null;
  overlay("");
  $("viewer").classList.add("hidden");
  $("home").classList.remove("hidden");
}

function send(msg) { if (msg && session) session.sendControl(msg); }
function sendDisplay() {
  send({ type: "display", w: Math.round(innerWidth), h: Math.round(innerHeight), scale: window.devicePixelRatio || 1 });
}

// ---------------------------------------------------------------- stats
let hostStats = null;
setInterval(async () => {
  if (!session || session.state !== "connected") { $("stats").textContent = ""; return; }
  const c = await session.getClientStats();
  const parts = [c.path === "relay" ? "via TURN" : "direct", `${c.rtt_ms || hostStats?.rtt_ms || 0} ms`, `${c.fps} fps`];
  if (hostStats) parts.push(`${hostStats.kbps} kbps`);
  $("stats").textContent = parts.join(" · ");
}, 1000);

// ---------------------------------------------------------------- mouse / pen
function point(clientX, clientY) {
  const v = $("video");
  return I.normalizedPoint(clientX, clientY, v.getBoundingClientRect(), v.videoWidth, v.videoHeight);
}
const stage = () => $("stage");

function setupPointer() {
  const s = stage();
  s.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch") return;
    const p = point(e.clientX, e.clientY);
    if (p) send(coalescer.push({ type: "pointer.move", ...p }, e.timeStamp));
  });
  s.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") return;
    const b = I.mouseButton(e.button);
    const p = point(e.clientX, e.clientY);
    if (p) send({ type: "pointer.move", ...p });
    if (b) send({ type: "pointer.button", button: b, down: true });
    s.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  s.addEventListener("pointerup", (e) => {
    if (e.pointerType === "touch") return;
    const b = I.mouseButton(e.button);
    if (b) send({ type: "pointer.button", button: b, down: false });
  });
  s.addEventListener("contextmenu", (e) => e.preventDefault());
  s.addEventListener("wheel", (e) => { send(I.wheelMessage(e.deltaX, e.deltaY, e.deltaMode)); e.preventDefault(); },
    { passive: false });
  setInterval(() => send(coalescer.flush(performance.now())), 16);
}

// ---------------------------------------------------------------- touch
function touches(list) {
  return Array.from(list, (t) => ({ id: t.identifier, px: t.clientX, py: t.clientY, ...(point(t.clientX, t.clientY) || { x: 0, y: 0 }) }));
}
function applyGesture(outputs) {
  for (const o of outputs) {
    if (o.send) send(o.send);
    if (o.view) {
      const v = o.view;
      $("video").style.transform = v.zoom === 1 ? "" : `translate(${v.panX}px, ${v.panY}px) scale(${v.zoom})`;
      $("btn-zoom").textContent = `${Math.round(v.zoom * 10) / 10}×`;
    }
  }
}
function setupTouch() {
  const s = stage();
  let timer = null;
  const handler = (kind) => (e) => {
    e.preventDefault();
    applyGesture(gestures.feed({ kind, touches: touches(e.touches), t: e.timeStamp }));
    if (kind === "start" && !timer) timer = setInterval(() => applyGesture(gestures.tick(performance.now())), 100);
    if ((kind === "end" || kind === "cancel") && e.touches.length === 0) { clearInterval(timer); timer = null; }
  };
  s.addEventListener("touchstart", handler("start"), { passive: false });
  s.addEventListener("touchmove", handler("move"), { passive: false });
  s.addEventListener("touchend", handler("end"), { passive: false });
  s.addEventListener("touchcancel", handler("cancel"), { passive: false });
  $("btn-zoom").onclick = () => applyGesture(gestures.resetView());
}

// ---------------------------------------------------------------- keyboard
function keyEvent(down) {
  return (e) => {
    if ($("viewer").classList.contains("hidden") || e.target === $("soft-keyboard") || e.target.tagName === "INPUT") return;
    const msg = I.keyMessage(e.code, down);
    if (!msg) return;
    e.preventDefault();
    for (const m of I.syncModifiers(e, keysDown, e.code)) send(I.keyMessage(m.code, m.down));
    if (down && (e.ctrlKey || e.metaKey) && e.code === "KeyV" && navigator.clipboard?.readText) {
      // paste: hand the PC this device's clipboard first, then the key press
      navigator.clipboard.readText().then((t) => t && send({ type: "clipboard", mime: "text/plain", data: t }))
        .catch(() => {}).finally(() => send(msg));
      keysDown.add(e.code);
      return;
    }
    if (down) keysDown.add(e.code); else keysDown.delete(e.code);
    send(msg);
  };
}
function releaseAll() {
  for (const code of keysDown) send(I.keyMessage(code, false));
  keysDown.clear();
}
function pressCombo(codes) {
  for (const c of codes) send(I.keyMessage(c, true));
  for (const c of [...codes].reverse()) send(I.keyMessage(c, false));
}
function setupKeyboard() {
  document.addEventListener("keydown", keyEvent(true));
  document.addEventListener("keyup", keyEvent(false));
  window.addEventListener("blur", releaseAll);
  document.addEventListener("visibilitychange", () => document.hidden && releaseAll());
  const soft = $("soft-keyboard");
  $("btn-keyboard").onclick = () => (document.activeElement === soft ? soft.blur() : soft.focus());
  soft.addEventListener("beforeinput", (e) => {
    e.preventDefault();
    for (const m of I.softKeyboardMessages(e.inputType, e.data)) send(m);
  });
  soft.addEventListener("keydown", (e) => {
    // hardware keys typed while the soft field has focus (iPad with a keyboard case)
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Escape", "Tab"].includes(e.code)) {
      e.preventDefault();
      pressCombo([e.code]);
    }
  });
  $("btn-keys").onclick = () => $("special").classList.toggle("hidden");
  for (const b of $("special").querySelectorAll("button")) b.onclick = () => pressCombo(b.dataset.combo.split(","));
}

// ---------------------------------------------------------------- clipboard, files, misc
function setupTools() {
  $("btn-paste").onclick = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) send({ type: "clipboard", mime: "text/plain", data: text });
      overlay("Clipboard sent to the PC");
    } catch { overlay("This browser didn't allow reading the clipboard."); }
    setTimeout(() => overlay(""), 1500);
  };
  $("btn-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(lastRemoteClip); overlay("Copied from the PC"); }
    catch { overlay("This browser didn't allow writing the clipboard."); }
    setTimeout(() => overlay(""), 1500);
  };
  $("file").onchange = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file || !session) return;
    try {
      const ok = await session.sendFile(file, (f) => overlay(`Sending ${file.name}: ${Math.round(f * 100)}%`));
      overlay(ok ? `${file.name} is in Downloads/Halo Remote on the PC` : `${file.name} didn't arrive intact`);
    } catch (err) { overlay(err.reason === "too_large" ? "Files up to 1 GB" : "Couldn't send the file"); }
    setTimeout(() => overlay(""), 2500);
  };
  $("btn-full").onclick = () => {
    const el = document.documentElement;
    if (document.fullscreenElement) document.exitFullscreen();
    else (el.requestFullscreen || el.webkitRequestFullscreen)?.call(el);
  };
  $("btn-end").onclick = leave;
  window.addEventListener("resize", () => session?.state === "connected" && sendDisplay());
}

// ---------------------------------------------------------------- start
async function main() {
  try {
    if (!P.supported()) throw new Error("unsupported");
    me = await store.identity();
  } catch {
    $("unsupported").classList.remove("hidden");
    return;
  }
  hostList = await store.hosts();
  renderHosts();
  setupPointer(); setupTouch(); setupKeyboard(); setupTools();
  $("pair-name").value = deviceName();
  $("pair-form").onsubmit = (e) => {
    e.preventDefault();
    pair($("pair-code").value, $("pair-host").value.trim(), $("pair-account").value.trim(), $("pair-name").value.trim());
  };
  const frag = new URLSearchParams(location.hash.slice(1));
  if (frag.get("pair") && frag.get("host") && frag.get("account")) {
    history.replaceState(null, "", location.pathname);       // the code shouldn't stay in history
    $("pair-code").value = frag.get("pair");
    $("pair-host").value = frag.get("host");
    $("pair-account").value = frag.get("account");
    pair(frag.get("pair"), frag.get("host"), frag.get("account"), "");
  } else {
    connectRelay();
  }
}

main();
