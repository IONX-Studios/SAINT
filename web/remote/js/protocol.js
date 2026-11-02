// Halo Remote protocol v1 for the browser (docs/REMOTE_DESKTOP.md, modules/remote_desktop/protocol.py).
// WebCrypto only: X25519, HKDF, HMAC, PBKDF2, SHA-256. Checked against tests/data/remote_vectors.json
// (web/remote/test/protocol.test.mjs), the same file the Python and Swift implementations use.

export const VERSION = 1;
const enc = new TextEncoder();
const SALT = enc.encode("halo-remote/v1");
const subtle = globalThis.crypto.subtle;

export class ProtocolError extends Error {
  constructor(reason) { super(reason); this.reason = reason; }
}

// ---------------------------------------------------------------- bytes
export function b64(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s);
}
export function unb64(text, size) {
  let raw;
  try { raw = Uint8Array.from(atob(text), (c) => c.charCodeAt(0)); } catch { raw = null; }
  if (!raw || (size !== undefined && raw.length !== size)) throw new ProtocolError("bad_base64");
  return raw;
}
export const hex = (bytes) => Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
export const unhex = (h) => Uint8Array.from(h.match(/../g) || [], (x) => parseInt(x, 16));
const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let i = 0;
  for (const p of parts) { out.set(p, i); i += p.length; }
  return out;
};
const cmp = (a, b) => { for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i]; return 0; };
function equal(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export async function sha256(bytes) { return new Uint8Array(await subtle.digest("SHA-256", bytes)); }
export async function deviceIdFor(pub) { return hex(await sha256(pub)).slice(0, 16); }
export async function accountIdFor(hostPub) { return hex(await sha256(hostPub)); }

// ---------------------------------------------------------------- canonical JSON
// Sorted keys, no whitespace, UTF-8, JSON.stringify's escapes. Integers only (no floats), ASCII keys.
export function canonical(value) {
  if (value === null || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new ProtocolError("float_in_signed_message");
    return String(value);
  }
  if (typeof value === "string") {
    if (/[\uD800-\uDFFF]/.test(value.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, ""))) {
      throw new ProtocolError("surrogate_in_string");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    for (const k of keys) if (!/^[\x00-\x7f]*$/.test(k)) throw new ProtocolError("non_ascii_key");
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonical(value[k])).join(",") + "}";
  }
  throw new ProtocolError("unsupported_value");
}

// ---------------------------------------------------------------- primitives
async function hkdf(ikm, salt, info) {
  const key = await subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info }, key, 256));
}
async function hmac(key, data) {
  const k = await subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await subtle.sign("HMAC", k, data));
}
// An identity holds a WebCrypto key (browser), or does the X25519 itself: SAINT's desktop app keeps its Link
// key in Python and lends the page `sharedSecret(peerPub)` instead.
async function x25519(me, peerPub) {
  if (me.sharedSecret) return new Uint8Array(await me.sharedSecret(peerPub));
  const pub = await subtle.importKey("raw", peerPub, { name: "X25519" }, true, []);
  return new Uint8Array(await subtle.deriveBits({ name: "X25519", public: pub }, me.privateKey, 256));
}
async function mac(key, label, obj) {
  return b64(await hmac(key, concat(enc.encode("halo-remote/v1 " + label + "\0"), enc.encode(canonical(obj)))));
}
const without = (obj, field) => Object.fromEntries(Object.entries(obj).filter(([k]) => k !== field));

// ---------------------------------------------------------------- identity
// identity = { privateKey: CryptoKey (X25519, non-extractable in the browser), publicKey: Uint8Array(32), deviceId }
export async function newIdentity(extractable = false) {
  const pair = await subtle.generateKey({ name: "X25519" }, extractable, ["deriveBits"]);
  const publicKey = new Uint8Array(await subtle.exportKey("raw", pair.publicKey));
  return { privateKey: pair.privateKey, publicKey, deviceId: await deviceIdFor(publicKey) };
}

// For tests and the vectors: a raw 32-byte private key.
export async function identityFromRaw(priv, pub) {
  const b64url = (b) => b64(b).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const privateKey = await subtle.importKey("jwk", { kty: "OKP", crv: "X25519", d: b64url(priv), x: b64url(pub) },
    { name: "X25519" }, false, ["deriveBits"]);
  return { privateKey, publicKey: pub, deviceId: await deviceIdFor(pub) };
}

export function supported() {
  return !!(globalThis.crypto && globalThis.crypto.subtle && globalThis.RTCPeerConnection !== undefined);
}

// ---------------------------------------------------------------- session MACs
export async function sessionKey(me, peerPub) {
  const [low, high] = cmp(me.publicKey, peerPub) <= 0 ? [me.publicKey, peerPub] : [peerPub, me.publicKey];
  return hkdf(await x25519(me, peerPub), SALT, concat(enc.encode("session-mac"), low, high));
}

export const SIGNED_TYPES = new Set(["session.request", "session.accept", "session.deny", "sdp.offer", "sdp.answer",
  "session.end"]);

export async function sign(msg, me, peerPub) {
  const body = without(msg, "sig");
  if (!body.from) body.from = me.deviceId;
  body.sig = await mac(await sessionKey(me, peerPub), "sig", body);
  return body;
}

export const MAX_SKEW = 300;
export async function verify(msg, me, peerPub, now = Date.now() / 1000) {
  if (!SIGNED_TYPES.has(msg.type)) throw new ProtocolError("not_signed_type");
  if (typeof msg.sig !== "string") throw new ProtocolError("missing:sig");
  if (msg.from !== await deviceIdFor(peerPub)) throw new ProtocolError("wrong_sender");
  if (msg.to !== me.deviceId) throw new ProtocolError("wrong_recipient");
  const want = await mac(await sessionKey(me, peerPub), "sig", without(msg, "sig"));
  if (!equal(want, msg.sig)) throw new ProtocolError("bad_signature");
  if (!Number.isInteger(msg.ts) || Math.abs(now - msg.ts) > MAX_SKEW) throw new ProtocolError("stale");
  return msg;
}

export function routed(type, from, to, fields = {}, now = Date.now() / 1000) {
  const msg = { type, from, to };
  if (!["ice", "pair.deny"].includes(type)) msg.ts = Math.floor(now);
  return Object.assign(msg, fields);
}

// ---------------------------------------------------------------- relay login
export async function relayProof(me, challenge, role) {
  const key = await hkdf(await x25519(me, unb64(challenge.eph, 32)), SALT, enc.encode("relay-login"));
  const input = concat(unb64(challenge.nonce, 32), enc.encode(me.deviceId), new Uint8Array([0]), enc.encode(role));
  return { type: "relay.proof", sig: b64(await hmac(key, input)) };
}

// ---------------------------------------------------------------- pairing codes
const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
export function decodeCode(code) {
  let s = [...(code || "").toUpperCase()].filter((c) => !" -_.".includes(c)).join("");
  s = s.replace(/0/g, "O").replace(/1/g, "I").replace(/8/g, "B");
  if (!s || [...s].some((c) => !B32.includes(c))) throw new ProtocolError("bad_code");
  let bits = 0, value = 0;
  const out = [];
  for (const c of s) {
    value = (value << 5) | B32.indexOf(c);
    bits += 5;
    if (bits >= 8) { out.push((value >>> (bits - 8)) & 0xff); bits -= 8; }
  }
  if (out.length !== 5 && out.length !== 16) throw new ProtocolError("bad_code");
  return new Uint8Array(out);
}

export async function pskFor(token) {
  if (token.length === 16) return hkdf(token, enc.encode("SAINT-LINK-PAIRING"), enc.encode("psk"));
  const key = await subtle.importKey("raw", token, "PBKDF2", false, ["deriveBits"]);
  return new Uint8Array(await subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: enc.encode("SAINT-LINK-PAIRING-SHORT"), iterations: 20000 }, key, 256));
}

export async function pairMac(msg, psk) {
  const body = without(msg, "mac");
  body.mac = await mac(await hkdf(psk, SALT, enc.encode("pair")), "pair", body);
  return body;
}

export async function checkPairMac(msg, psk, now = Date.now() / 1000) {
  const want = await mac(await hkdf(psk, SALT, enc.encode("pair")), "pair", without(msg, "mac"));
  if (typeof msg.mac !== "string" || !equal(want, msg.mac)) throw new ProtocolError("bad_code");
  if (!Number.isInteger(msg.ts) || Math.abs(now - msg.ts) > MAX_SKEW) throw new ProtocolError("stale");
  return msg;
}
