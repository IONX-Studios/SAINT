// Relay connection, pairing and one remote-desktop session (WebRTC) for the browser client.
// WebSocket and RTCPeerConnection are injectable so this runs under node --test against the real relay.

import * as P from "./protocol.js";

const randomHex = (n) => P.hex(globalThis.crypto.getRandomValues(new Uint8Array(n)));

// ---------------------------------------------------------------- relay
export class RelayClient {
  // opts: { url, me, name, accounts: [account ids], pair?: account id, WebSocket? }
  constructor(opts) {
    this.opts = opts;
    this.handlers = { message: [], status: [] };
    this.ws = null;
    this.status = "offline";
    this.iceServers = [];
    this.backoff = 1000;
    this.closed = false;
  }
  on(kind, fn) { this.handlers[kind].push(fn); return this; }
  _emit(kind, value) { for (const fn of this.handlers[kind]) fn(value); }
  _status(s, detail) { this.status = s; this._emit("status", { status: s, detail }); }

  connect() {
    this.closed = false;
    const WS = this.opts.WebSocket || globalThis.WebSocket;
    const ws = (this.ws = new WS(this.opts.url));
    this._status("connecting");
    ws.onopen = () => {
      const hello = { type: "hello", v: P.VERSION, device_id: this.opts.me.deviceId, role: "client",
        pubkey: P.b64(this.opts.me.publicKey), name: this.opts.name || "Browser", accounts: this.opts.accounts || [] };
      if (this.opts.pair) hello.pair = this.opts.pair;
      ws.send(JSON.stringify(hello));
    };
    ws.onmessage = async (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "relay.challenge") {
        ws.send(JSON.stringify(await P.relayProof(this.opts.me, msg, "client")));
      } else if (msg.type === "welcome") {
        this.backoff = 1000;
        this.iceServers = msg.ice_servers || [];
        this._status("online");
      } else {
        this._emit("message", msg);
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.closed) return this._status("offline");
      this._status("reconnecting", Math.round(this.backoff / 1000));
      this.timer = setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 30000);
    };
    return this;
  }

  // Wait for the first message matching pred (or a timeout).
  next(pred, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const fn = (m) => { if (pred(m)) { clearTimeout(t); this.handlers.message = this.handlers.message.filter((f) => f !== fn); resolve(m); } };
      const t = setTimeout(() => { this.handlers.message = this.handlers.message.filter((f) => f !== fn); reject(new P.ProtocolError("timeout")); }, timeoutMs);
      this.on("message", fn);
    });
  }

  send(msg) {
    if (!this.ws || this.ws.readyState !== 1) throw new P.ProtocolError("offline");
    this.ws.send(JSON.stringify(msg));
  }

  close() { this.closed = true; clearTimeout(this.timer); if (this.ws) this.ws.close(); }
}

// ---------------------------------------------------------------- pairing
// With the code from `halo-remote pair` on the PC. Resolves with the host record to store.
export async function pairWithHost(relay, me, { code, hostId, account, name }) {
  const psk = await P.pskFor(P.decodeCode(code));
  const req = await P.pairMac(P.routed("pair.request", me.deviceId, hostId,
    { pubkey: P.b64(me.publicKey), name: name || "Browser", platform: "web" }), psk);
  const answer = relay.next((m) => (m.type === "pair.accept" || m.type === "pair.deny" ||
    (m.type === "error" && ["offline", "not_allowed", "rate_limited"].includes(m.reason))) , 20000);
  relay.send(req);
  const msg = await answer;
  if (msg.type !== "pair.accept") throw new P.ProtocolError(msg.reason || "pairing_refused");
  await P.checkPairMac(msg, psk);
  const hostPub = P.unb64(msg.pubkey, 32);
  if (msg.from !== hostId || await P.deviceIdFor(hostPub) !== hostId || await P.accountIdFor(hostPub) !== account) {
    throw new P.ProtocolError("wrong_host");
  }
  return { device_id: hostId, account, pubkey: msg.pubkey, name: msg.name };
}

// ---------------------------------------------------------------- session
export class RemoteSession {
  // opts: { relay, me, host: {device_id, pubkey (b64)}, want, name, RTCPeerConnection?, onStatus, onTrack,
  //         onControl, onStats }
  constructor(opts) {
    this.opts = opts;
    this.id = randomHex(16);
    this.hostPub = P.unb64(opts.host.pubkey, 32);
    this.pc = null;
    this.control = null;
    this.files = null;
    this.pendingIce = [];
    this.state = "new";
    this._onMessage = (m) => this.handle(m).catch((e) => this._fail(e.reason || String(e)));
    opts.relay.on("message", this._onMessage);
  }
  _status(state, detail) { this.state = state; this.opts.onStatus?.(state, detail); }
  _fail(reason) { if (this.state !== "ended") { this._status("ended", reason); this._cleanup(); } }

  async start() {
    const { me, host, want, name, relay } = this.opts;
    const req = await P.sign(P.routed("session.request", me.deviceId, host.device_id,
      { session_id: this.id, client: name || "Browser", want: want || {} }), me, this.hostPub);
    relay.send(req);
    this._status("waiting");
  }

  async _signed(type, fields) {
    const { me, host } = this.opts;
    return P.sign(P.routed(type, me.deviceId, host.device_id, Object.assign({ session_id: this.id }, fields)),
      me, this.hostPub);
  }

  async handle(msg) {
    if (msg.session_id !== this.id || msg.from !== this.opts.host.device_id || this.state === "ended") return;
    if (P.SIGNED_TYPES.has(msg.type)) await P.verify(msg, this.opts.me, this.hostPub);
    else if (msg.type !== "ice") return;
    switch (msg.type) {
      case "session.deny": return this._fail(msg.reason);
      case "session.end": return this._fail(msg.reason);
      case "session.accept": return this._accepted(msg);
      case "sdp.offer": return this._offer(msg);
      case "ice":
        if (!this.pc || !this.pc.remoteDescription) this.pendingIce.push(msg.candidate);
        else await this.pc.addIceCandidate(msg.candidate);
    }
  }

  _accepted(msg) {
    const RTC = this.opts.RTCPeerConnection || globalThis.RTCPeerConnection;
    const iceServers = (msg.ice_servers && msg.ice_servers.length) ? msg.ice_servers : this.opts.relay.iceServers;
    const pc = (this.pc = new RTC({ iceServers, bundlePolicy: "max-bundle" }));
    pc.ontrack = (ev) => this.opts.onTrack?.(ev.streams[0] || new MediaStream([ev.track]));
    pc.onicecandidate = (ev) => {
      if (!ev.candidate) return;
      const c = ev.candidate.toJSON ? ev.candidate.toJSON() : ev.candidate;
      try {
        this.opts.relay.send(P.routed("ice", this.opts.me.deviceId, this.opts.host.device_id,
          { session_id: this.id, candidate: { candidate: c.candidate, sdpMLineIndex: c.sdpMLineIndex ?? 0 } }));
      } catch { /* relay reconnecting: ICE will retry */ }
    };
    pc.ondatachannel = (ev) => {
      const ch = ev.channel;
      if (ch.label === "control") {
        this.control = ch;
        ch.onmessage = (e) => {
          let m;
          try { m = JSON.parse(e.data); } catch { return; }
          if (m.type === "stats") this.opts.onStats?.(m);
          else this.opts.onControl?.(m);
        };
      } else if (ch.label === "files") {
        this.files = ch;
        ch.binaryType = "arraybuffer";
        ch.bufferedAmountLowThreshold = 256 * 1024;
      }
    };
    pc.onconnectionstatechange = () => {
      const s = pc.connectionState;
      if (s === "connected") this._status("connected");
      else if (s === "failed") this._fail("connection_lost");
      else if (s === "disconnected") {
        this._status("unstable");
        clearTimeout(this.dropTimer);
        this.dropTimer = setTimeout(() => { if (pc.connectionState !== "connected") this._fail("connection_lost"); }, 5000);
      }
    };
    this._status("negotiating");
  }

  async _offer(msg) {
    if (!this.pc) return;
    await this.pc.setRemoteDescription({ type: "offer", sdp: msg.sdp });
    for (const c of this.pendingIce.splice(0)) await this.pc.addIceCandidate(c);
    const answer = await this.pc.createAnswer();
    await this.pc.setLocalDescription(answer);
    this.opts.relay.send(await this._signed("sdp.answer", { sdp: this.pc.localDescription.sdp }));
  }

  sendControl(msg) {
    if (this.control && this.control.readyState === "open") this.control.send(JSON.stringify(msg));
  }

  // Send a file over the "files" channel; resolves true when the PC confirmed the checksum.
  async sendFile(file, onProgress) {
    const ch = this.files;
    if (!ch || ch.readyState !== "open") throw new P.ProtocolError("not_connected");
    if (file.size > 1024 ** 3) throw new P.ProtocolError("too_large");
    const buf = new Uint8Array(await file.arrayBuffer());
    const id = randomHex(8);
    const reply = (want) => new Promise((resolve) => {
      const fn = (e) => {
        if (typeof e.data !== "string") return;
        const m = JSON.parse(e.data);
        if (m.id === id && want.includes(m.type)) { ch.removeEventListener("message", fn); resolve(m); }
      };
      ch.addEventListener("message", fn);
    });
    const first = reply(["file.accept", "file.done"]);
    ch.send(JSON.stringify({ type: "file.offer", id, name: file.name, size: buf.length,
      sha256: P.hex(await P.sha256(buf)) }));
    if ((await first).type !== "file.accept") return false;
    const done = reply(["file.done"]);
    for (let off = 0; off < buf.length; off += 65536) {
      while (ch.bufferedAmount > 1024 * 1024) await new Promise((r) => ch.addEventListener("bufferedamountlow", r, { once: true }));
      ch.send(buf.subarray(off, off + 65536));
      onProgress?.(Math.min(off + 65536, buf.length) / buf.length);
    }
    return (await done).ok === true;
  }

  async getClientStats() {
    if (!this.pc) return null;
    const out = { fps: 0, path: "p2p", rtt_ms: 0 };
    const report = await this.pc.getStats();
    const byId = new Map();
    report.forEach((s) => byId.set(s.id, s));
    report.forEach((s) => {
      if (s.type === "inbound-rtp" && s.kind === "video") out.fps = Math.round(s.framesPerSecond || 0);
      if (s.type === "candidate-pair" && (s.nominated || s.selected) && s.state === "succeeded") {
        out.rtt_ms = Math.round((s.currentRoundTripTime || 0) * 1000);
        const local = byId.get(s.localCandidateId), remote = byId.get(s.remoteCandidateId);
        if (local?.candidateType === "relay" || remote?.candidateType === "relay") out.path = "relay";
      }
    });
    return out;
  }

  async end(reason = "closed") {
    if (this.state === "ended") return;
    try { this.opts.relay.send(await this._signed("session.end", { reason })); } catch { /* offline */ }
    this._fail(reason);
  }

  _cleanup() {
    clearTimeout(this.dropTimer);
    this.opts.relay.handlers.message = this.opts.relay.handlers.message.filter((f) => f !== this._onMessage);
    if (this.pc) this.pc.close();
    this.pc = null;
  }
}
