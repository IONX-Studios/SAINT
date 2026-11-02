// The live remote-desktop screen, shared by the browser page (app.js) and SAINT's desktop app (desktop.js):
// session lifecycle with reconnect, video, mouse/touch/keyboard, clipboard, files, stats.
// The DOM it drives is the #viewer block in index.html / desktop.html.
import * as I from "./input.js";
import { RemoteSession } from "./client.js";

const $ = (id) => document.getElementById(id);

export const REASONS = {
  declined: "The PC said no.", no_answer: "Nobody answered on the PC.", busy: "Someone else is connected to that PC.",
  not_paired: "This device isn't paired with that PC any more.", revoked: "This device was removed on the PC.",
  not_allowed: "Remote desktop is switched off for this device on the PC.", connection_lost: "The connection dropped.",
  connect_timeout: "Couldn't reach the PC (network blocked?).", idle: "Disconnected after a long idle time.",
  offline: "The PC is offline.", replaced: "Connected again from this device.", portal_start_refused:
    "The PC refused screen sharing (approve Halo Remote once at the PC).", timeout: "No answer from the relay.",
  bad_code: "Wrong or expired pairing code.", wrong_host: "That answer didn't come from the PC you named.",
};
export const say = (reason) => REASONS[reason] || `Stopped (${reason}).`;

// The video codecs this browser can decode, in the protocol's names (Qt WebEngine has no H.264).
export function decodableCodecs() {
  const caps = globalThis.RTCRtpReceiver?.getCapabilities?.("video")?.codecs || [];
  const names = new Set(caps.map((c) => c.mimeType.split("/")[1].toUpperCase()));
  return ["H264", "VP8"].filter((c) => names.has(c));
}

// opts: { relay: () => RelayClient, me, name: () => string, clipboard?: {read, write, auto}, onLeave?: () => void }
export function initViewer(opts) {
  const clipboard = opts.clipboard || {
    read: () => navigator.clipboard.readText(),
    write: (t) => navigator.clipboard.writeText(t),
    auto: false,
  };
  const gestures = new I.TouchGestures();
  const coalescer = new I.MoveCoalescer(16);
  const keysDown = new Set();
  let session = null, current = null, retries = 0, hostStats = null, lastRemoteClip = "", lastSentClip = "";

  const overlay = (text) => { $("overlay").textContent = text || ""; };
  const send = (msg) => { if (msg && session) session.sendControl(msg); };
  const sendDisplay = () =>
    send({ type: "display", w: Math.round(innerWidth), h: Math.round(innerHeight), scale: window.devicePixelRatio || 1 });

  // ------------------------------------------------------------ session
  function connect(host) {
    current = host;
    retries = 0;
    $("viewer").classList.remove("hidden");
    startSession();
  }

  async function startSession() {
    const video = $("video");
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const want = { max_w: Math.round(Math.min(screen.width * dpr, 2560)), max_h: Math.round(Math.min(screen.height * dpr, 1600)),
      fps: 30, audio: false, codecs: decodableCodecs() };
    session = new RemoteSession({
      relay: opts.relay(), me: opts.me, host: current, want, name: opts.name(),
      onTrack: (stream) => { video.srcObject = stream; video.play().catch(() => {}); },
      onStatus: (state, reason) => {
        if (state === "waiting") overlay("Waiting for the PC to allow this device…");
        else if (state === "negotiating") overlay("Connecting…");
        else if (state === "unstable") overlay("Connection unstable…");
        else if (state === "connected") { overlay(""); retries = 0; sendDisplay(); clipboardBaseline(); }
        else if (state === "ended") ended(reason);
      },
      onStats: (s) => { hostStats = s; },
      onControl: (m) => {
        if (m.type === "clipboard" && m.mime.startsWith("text/")) {
          lastRemoteClip = m.data;
          if (clipboard.auto) { lastSentClip = m.data; clipboard.write(m.data); } else $("btn-copy").disabled = false;
        }
      },
    });
    try { await session.start(); } catch (e) { ended(e.reason || "offline"); }
  }

  function ended(reason) {
    releaseAll();
    if (reason === "connection_lost" && current && retries < 5) {
      retries += 1;
      overlay(`${say(reason)} Reconnecting (${retries}/5)…`);
      setTimeout(() => current && startSession(), Math.min(1000 * 2 ** retries, 15000));
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
    opts.onLeave?.();
  }

  // Desktop app: what you copy on this computer while connected goes to the PC when you come back to the window.
  // Whatever was on the clipboard before connecting stays here.
  async function clipboardBaseline() {
    if (!clipboard.auto) return;
    try { lastSentClip = (await clipboard.read()) || ""; } catch { lastSentClip = ""; }
  }
  async function pushClipboard() {
    if (!clipboard.auto || !session) return;
    try {
      const text = await clipboard.read();
      if (text && text !== lastSentClip && text !== lastRemoteClip) {
        lastSentClip = text;
        send({ type: "clipboard", mime: "text/plain", data: text });
      }
    } catch { /* nothing readable */ }
  }

  // ------------------------------------------------------------ stats
  setInterval(async () => {
    if (!session || session.state !== "connected") { $("stats").textContent = ""; return; }
    const c = await session.getClientStats();
    const parts = [c.path === "relay" ? "via TURN" : "direct", `${c.rtt_ms || hostStats?.rtt_ms || 0} ms`, `${c.fps} fps`];
    if (hostStats) parts.push(`${hostStats.kbps} kbps`);
    $("stats").textContent = parts.join(" · ");
  }, 1000);

  // ------------------------------------------------------------ mouse / pen
  const point = (x, y) => {
    const v = $("video");
    return I.normalizedPoint(x, y, v.getBoundingClientRect(), v.videoWidth, v.videoHeight);
  };
  const s = $("stage");
  s.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch") return;
    const p = point(e.clientX, e.clientY);
    if (p) send(coalescer.push({ type: "pointer.move", ...p }, e.timeStamp));
  });
  s.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") return;
    const b = I.mouseButton(e.button), p = point(e.clientX, e.clientY);
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

  // ------------------------------------------------------------ touch
  const touches = (list) => Array.from(list, (t) => ({ id: t.identifier, px: t.clientX, py: t.clientY,
    ...(point(t.clientX, t.clientY) || { x: 0, y: 0 }) }));
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
  let timer = null;
  const touchHandler = (kind) => (e) => {
    e.preventDefault();
    applyGesture(gestures.feed({ kind, touches: touches(e.touches), t: e.timeStamp }));
    if (kind === "start" && !timer) timer = setInterval(() => applyGesture(gestures.tick(performance.now())), 100);
    if ((kind === "end" || kind === "cancel") && e.touches.length === 0) { clearInterval(timer); timer = null; }
  };
  for (const [ev, kind] of [["touchstart", "start"], ["touchmove", "move"], ["touchend", "end"], ["touchcancel", "cancel"]]) {
    s.addEventListener(ev, touchHandler(kind), { passive: false });
  }
  $("btn-zoom").onclick = () => applyGesture(gestures.resetView());

  // ------------------------------------------------------------ keyboard
  const keyEvent = (down) => (e) => {
    if ($("viewer").classList.contains("hidden") || e.target === $("soft-keyboard") || e.target.tagName === "INPUT") return;
    const msg = I.keyMessage(e.code, down);
    if (!msg) return;
    e.preventDefault();
    for (const m of I.syncModifiers(e, keysDown, e.code)) send(I.keyMessage(m.code, m.down));
    if (down && (e.ctrlKey || e.metaKey) && e.code === "KeyV" && !clipboard.auto) {
      // paste: hand the PC this device's clipboard first, then the key press
      clipboard.read().then((t) => t && send({ type: "clipboard", mime: "text/plain", data: t }))
        .catch(() => {}).finally(() => send(msg));
      keysDown.add(e.code);
      return;
    }
    if (down) keysDown.add(e.code); else keysDown.delete(e.code);
    send(msg);
  };
  function releaseAll() {
    for (const code of keysDown) send(I.keyMessage(code, false));
    keysDown.clear();
  }
  const pressCombo = (codes) => {
    for (const c of codes) send(I.keyMessage(c, true));
    for (const c of [...codes].reverse()) send(I.keyMessage(c, false));
  };
  document.addEventListener("keydown", keyEvent(true));
  document.addEventListener("keyup", keyEvent(false));
  window.addEventListener("blur", releaseAll);
  window.addEventListener("focus", pushClipboard);
  document.addEventListener("visibilitychange", () => document.hidden && releaseAll());
  const soft = $("soft-keyboard");
  $("btn-keyboard").onclick = () => (document.activeElement === soft ? soft.blur() : soft.focus());
  soft.addEventListener("beforeinput", (e) => {
    e.preventDefault();
    for (const m of I.softKeyboardMessages(e.inputType, e.data)) send(m);
  });
  soft.addEventListener("keydown", (e) => {
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Escape", "Tab"].includes(e.code)) {
      e.preventDefault();
      pressCombo([e.code]);
    }
  });
  $("btn-keys").onclick = () => $("special").classList.toggle("hidden");
  for (const b of $("special").querySelectorAll("button")) b.onclick = () => pressCombo(b.dataset.combo.split(","));

  // ------------------------------------------------------------ clipboard, files, misc
  const flash = (text, ms = 1500) => { overlay(text); setTimeout(() => overlay(""), ms); };
  $("btn-paste").onclick = async () => {
    try {
      const text = await clipboard.read();
      if (text) send({ type: "clipboard", mime: "text/plain", data: text });
      flash("Clipboard sent to the PC");
    } catch { flash("Couldn't read this device's clipboard."); }
  };
  $("btn-copy").onclick = async () => {
    try { await clipboard.write(lastRemoteClip); flash("Copied from the PC"); }
    catch { flash("Couldn't write this device's clipboard."); }
  };
  $("file").onchange = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file || !session) return;
    try {
      const ok = await session.sendFile(file, (f) => overlay(`Sending ${file.name}: ${Math.round(f * 100)}%`));
      flash(ok ? `${file.name} is in Downloads/Halo Remote on the PC` : `${file.name} didn't arrive intact`, 2500);
    } catch (err) { flash(err.reason === "too_large" ? "Files up to 1 GB" : "Couldn't send the file", 2500); }
  };
  $("btn-full").onclick = () => {
    const el = document.documentElement;
    if (document.fullscreenElement) document.exitFullscreen();
    else (el.requestFullscreen || el.webkitRequestFullscreen)?.call(el);
  };
  $("btn-end").onclick = leave;
  window.addEventListener("resize", () => session?.state === "connected" && sendDisplay());

  return { connect, leave, get session() { return session; } };
}
