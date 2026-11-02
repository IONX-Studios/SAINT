// The browser client's relay, pairing and signaling code against the real Halo Relay (server/relay/relay.py),
// started here as a subprocess. A small "PC" built from protocol.js plays the host side. WebRTC itself is
// replaced by a stand-in RTCPeerConnection (Node has none), so this checks everything up to the media.
//
// Needs Python with aiohttp: HALO_PYTHON=/path/to/python (default: the repo's .venv, then python3). Skipped
// when that can't start the relay.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import * as P from "../js/protocol.js";
import { pairWithHost, RelayClient, RemoteSession } from "../js/client.js";

const ROOT = fileURLToPath(new URL("../../../", import.meta.url));
const PORT = 18000 + Math.floor(Math.random() * 2000);
const URL_WS = `ws://127.0.0.1:${PORT}/ws`;
let relay = null, skip = false;

function python() {
  if (process.env.HALO_PYTHON) return process.env.HALO_PYTHON;
  for (const p of [".venv/Scripts/python.exe", ".venv/bin/python"]) if (existsSync(ROOT + p)) return ROOT + p;
  return "python3";
}

before(async () => {
  relay = spawn(python(), ["-m", "server.relay.relay", "--host", "127.0.0.1", "--port", String(PORT)],
    { cwd: ROOT, stdio: "ignore" });
  relay.on("error", () => { skip = true; });
  for (let i = 0; i < 60 && !skip; i++) {
    try { if ((await fetch(`http://127.0.0.1:${PORT}/healthz`)).ok) return; } catch { /* starting */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  skip = true;
});
after(() => relay?.kill());

// The PC side, straight on a WebSocket.
async function hostLogin(me) {
  const ws = new WebSocket(URL_WS);
  const inbox = [];
  let waiter = null;
  ws.onmessage = (e) => { const m = JSON.parse(e.data); inbox.push(m); waiter?.(); };
  await new Promise((r) => (ws.onopen = r));
  const next = async (type) => {
    for (;;) {
      const i = inbox.findIndex((m) => m.type === type);
      if (i >= 0) return inbox.splice(i, 1)[0];
      await new Promise((r) => (waiter = r));
    }
  };
  ws.send(JSON.stringify({ type: "hello", v: 1, device_id: me.deviceId, role: "host", pubkey: P.b64(me.publicKey), name: "Halo PC" }));
  ws.send(JSON.stringify(await P.relayProof(me, await next("relay.challenge"), "host")));
  await next("welcome");
  return { ws, next, send: (m) => ws.send(JSON.stringify(m)) };
}

class FakePC {
  constructor(cfg) { this.cfg = cfg; this.remoteDescription = null; this.localDescription = null; this.ice = []; }
  async setRemoteDescription(d) { this.remoteDescription = d; }
  async createAnswer() { return { type: "answer", sdp: "v=0\r\na=answer\r\n" }; }
  async setLocalDescription(d) { this.localDescription = d; }
  async addIceCandidate(c) { this.ice.push(c); }
  close() { this.closed = true; }
}

test("pair a browser through the relay, see the PC, and negotiate a session", async (t) => {
  if (skip) return t.skip("relay could not start (needs Python + aiohttp)");
  const pc = await P.newIdentity(true);
  const account = await P.accountIdFor(pc.publicKey);
  const host = await hostLogin(pc);
  host.send({ type: "devices", keys: [] });

  const me = await P.newIdentity();
  const client = new RelayClient({ url: URL_WS, me, name: "Chromebook", accounts: [account], pair: account });
  const online = new Promise((r) => client.on("status", (s) => s.status === "online" && r()));
  client.connect();
  await online;

  // the PC shows code 0102030405 (ABCD-style) and checks the request with it
  const code = "AEBA-GBAF";
  assert.equal(P.hex(P.decodeCode(code)), "0102030405");
  const psk = await P.pskFor(P.decodeCode(code));
  const presence = client.next((m) => m.type === "presence" && m.hosts.length === 1);
  const paired = pairWithHost(client, me, { code, hostId: pc.deviceId, account, name: "Chromebook" });
  const req = await host.next("pair.request");
  await P.checkPairMac(req, psk);
  host.send(await P.pairMac(P.routed("pair.accept", pc.deviceId, me.deviceId, { pubkey: P.b64(pc.publicKey), name: "Halo PC" }), psk));
  const record = await paired;
  assert.deepEqual(record, { device_id: pc.deviceId, account, pubkey: P.b64(pc.publicKey), name: "Halo PC" });
  host.send({ type: "devices", keys: [{ device_id: me.deviceId, pubkey: P.b64(me.publicKey), name: "Chromebook" }] });
  const hosts = (await presence).hosts;
  assert.equal(hosts[0].device_id, pc.deviceId);
  assert.equal(hosts[0].online, true);

  // a session: request -> accept -> offer -> answer, every step signed and checked on the other side
  const states = [];
  const session = new RemoteSession({ relay: client, me, host: record, want: { max_w: 1280, max_h: 800, fps: 30, audio: false },
    name: "Chromebook", RTCPeerConnection: FakePC, onStatus: (s) => states.push(s) });
  await session.start();
  const sreq = await P.verify(await host.next("session.request"), pc, me.publicKey);
  assert.equal(sreq.client, "Chromebook");
  const sign = (type, fields) => P.sign(P.routed(type, pc.deviceId, me.deviceId, { session_id: sreq.session_id, ...fields }), pc, me.publicKey);
  host.send(await sign("session.accept", { ice_servers: [{ urls: ["stun:127.0.0.1:3478"] }] }));
  host.send(await sign("sdp.offer", { sdp: "v=0\r\na=offer\r\n" }));
  const answer = await P.verify(await host.next("sdp.answer"), pc, me.publicKey);
  assert.equal(answer.sdp, "v=0\r\na=answer\r\n");
  assert.deepEqual(session.pc.cfg.iceServers, [{ urls: ["stun:127.0.0.1:3478"] }]);
  assert.equal(session.pc.remoteDescription.sdp, "v=0\r\na=offer\r\n");
  host.send({ type: "ice", from: pc.deviceId, to: me.deviceId, session_id: sreq.session_id, candidate: { candidate: "candidate:1", sdpMLineIndex: 0 } });
  await new Promise((r) => setTimeout(r, 200));
  assert.deepEqual(session.pc.ice, [{ candidate: "candidate:1", sdpMLineIndex: 0 }]);

  // a forged accept for another session, or one signed by someone else, changes nothing
  const stranger = await P.newIdentity(true);
  const forged = await P.sign(P.routed("session.end", pc.deviceId, me.deviceId, { session_id: session.id, reason: "x" }), stranger, me.publicKey);
  await session.handle(forged).catch((e) => assert.equal(e.reason, "bad_signature"));
  assert.notEqual(session.state, "ended");

  // the PC ends it
  host.send(await sign("session.end", { reason: "bye" }));
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(session.state, "ended");
  assert.deepEqual(states.slice(0, 2), ["waiting", "negotiating"]);

  // revoked: the relay drops the route
  host.send({ type: "devices", keys: [] });
  const err = await client.next((m) => m.type === "error" && m.reason === "revoked");
  assert.equal(err.reason, "revoked");
  client.close();
  host.ws.close();
});
