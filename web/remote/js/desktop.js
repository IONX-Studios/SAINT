// Halo Remote in SAINT's desktop app. The page gets its identity, the PC to reach and the relay from the app
// (ui/remote_desktop.py) over Qt WebChannel; the Link private key never enters the page — the app computes the
// X25519 shared secrets for it. Clipboard follows automatically both ways.
import * as P from "./protocol.js";
import { RelayClient } from "./client.js";
import { initViewer, say } from "./viewer.js";

const $ = (id) => document.getElementById(id);
const status = (text) => { $("status").textContent = text; };

function bridge() {
  return new Promise((resolve, reject) => {
    if (!globalThis.qt?.webChannelTransport) return reject(new Error("no Qt WebChannel"));
    new QWebChannel(qt.webChannelTransport, (channel) => resolve(channel.objects.saint));
  });
}
const call = (fn, ...args) => new Promise((resolve) => fn(...args, resolve));

async function main() {
  let app;
  try { app = await bridge(); } catch { status("Open this from SAINT → Devices → Remote desktop."); return; }
  const cfg = JSON.parse(await call(app.config));
  const host = cfg.host;
  $("host-name").textContent = host.name;
  document.title = `Halo Remote — ${host.name}`;
  const me = {
    deviceId: cfg.me.device_id,
    publicKey: P.unb64(cfg.me.pubkey, 32),
    sharedSecret: async (peerPub) => P.unhex(await call(app.sharedSecret, P.b64(peerPub))),
  };
  const relay = new RelayClient({ url: cfg.relay_url, me, name: cfg.me.name, accounts: [host.account] });
  let wantConnect = true, connected = false;

  const viewer = initViewer({
    relay: () => relay, me, name: () => cfg.me.name,
    clipboard: { read: () => call(app.clipboardText), write: (t) => app.setClipboardText(t), auto: true },
    onLeave: () => {
      connected = false;
      $("home").classList.remove("hidden");
      $("btn-retry").classList.remove("hidden");
      status("Disconnected.");
    },
  });
  $("btn-retry").onclick = () => { wantConnect = true; $("btn-retry").classList.add("hidden"); tryConnect(); };

  let online = false, presenceSeen = false;
  function tryConnect() {
    if (!wantConnect || connected) return;
    if (relay.status !== "online") return status("Connecting to the relay…");
    if (!presenceSeen) return status(`Looking for ${host.name}…`);
    if (!online) return status(`${host.name} isn't online on the relay. Is Halo Remote enabled on it (halo-remote enable)?`);
    wantConnect = false;
    connected = true;
    $("home").classList.add("hidden");
    viewer.connect(host);
  }

  relay.on("status", ({ status: s, detail }) => {
    if (s === "reconnecting") status(`Relay unreachable, retrying in ${detail}s…`);
    tryConnect();
  });
  relay.on("message", (m) => {
    if (m.type === "presence") {
      const h = m.hosts.find((x) => x.device_id === host.device_id);
      presenceSeen = true;
      online = !!h?.online;
      if (!h) status(`${host.name} doesn't list this computer yet — on that PC, check SAINT → Devices → Permissions → remote desktop.`);
      tryConnect();
    } else if (m.type === "error") {
      status(say(m.reason));
    }
  });
  relay.connect();
}

main();
