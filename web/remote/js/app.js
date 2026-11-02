// Halo Remote browser client: pairing, the list of PCs, and the viewer (viewer.js).
import * as P from "./protocol.js";
import { pairWithHost, RelayClient } from "./client.js";
import { initViewer, say } from "./viewer.js";
import * as store from "./store.js";

const $ = (id) => document.getElementById(id);
const RELAY_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

let me, relay, hostList = [], online = new Map(), viewer;

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
    go.onclick = () => { $("home").classList.add("hidden"); viewer.connect(h); };
    const forget = document.createElement("button");
    forget.textContent = "Forget";
    forget.onclick = async () => { hostList = await store.forgetHost(h.device_id); connectRelay(); renderHosts(); };
    li.append(dot, name, go, forget);
    ul.append(li);
  }
}

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
  viewer = initViewer({ relay: () => relay, me, name: deviceName, onLeave: () => $("home").classList.remove("hidden") });
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
