// node --test web/remote/test   — the browser protocol against tests/data/remote_vectors.json
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import * as P from "../js/protocol.js";

const V = JSON.parse(readFileSync(new URL("../../../tests/data/remote_vectors.json", import.meta.url), "utf8"));
const id = (name) => P.identityFromRaw(P.unhex(V.keys[name].private), P.unb64(V.keys[name].public, 32));
const pub = (name) => P.unb64(V.keys[name].public, 32);

test("device ids, account id and the pairwise key", async () => {
  for (const name of Object.keys(V.keys)) assert.equal(await P.deviceIdFor(pub(name)), V.keys[name].device_id);
  assert.equal(await P.accountIdFor(pub("host")), V.account_id);
  assert.equal(P.hex(await P.sessionKey(await id("client"), pub("host"))), V.session_key);
  assert.equal(P.hex(await P.sessionKey(await id("host"), pub("client"))), V.session_key);
});

test("canonical JSON matches Python byte for byte", () => {
  for (const c of V.canonical) assert.equal(P.canonical(c.object), c.canonical);
  assert.throws(() => P.canonical({ x: 1.5 }), /float/);
  assert.throws(() => P.canonical({ "é": 1 }), /non_ascii/);
  assert.throws(() => P.canonical({ s: "\ud800" }), /surrogate/);
});

test("signs exactly like the PC, and verifies its messages", async () => {
  const req = V.signed["session.request"];
  const { sig, ...body } = req;
  assert.deepEqual(await P.sign(body, await id("client"), pub("host")), req);
  for (const kind of ["session.accept", "sdp.offer"]) {
    await P.verify(V.signed[kind], await id("client"), pub("host"), V.ts);
  }
});

test("rejects tampered, forged, reflected and stale messages", async () => {
  for (const c of V.rejected) {
    await assert.rejects(P.verify(c.message, await id(c.verifier), pub(c.peer), V.ts), (e) => e.reason === c.why);
  }
  const req = V.signed["session.request"];
  await assert.rejects(P.verify(req, await id("client"), pub("host"), V.ts), (e) => e.reason === "wrong_sender");
  await assert.rejects(P.verify(req, await id("host"), pub("client"), V.ts + 301), (e) => e.reason === "stale");
});

test("relay login proof", async () => {
  const rl = V.relay_login;
  assert.deepEqual(await P.relayProof(await id("client"), rl.challenge, rl.role), rl.proof);
  assert.notDeepEqual(await P.relayProof(await id("stranger"), rl.challenge, rl.role), rl.proof);
});

test("pairing code, psk and pair MAC", async () => {
  const pv = V.pairing;
  assert.equal(P.hex(P.decodeCode(pv.code)), pv.token);
  assert.equal(P.hex(P.decodeCode(pv.code.toLowerCase().replace("-", " "))), pv.token);
  assert.equal(P.hex(await P.pskFor(P.decodeCode(pv.code))), pv.psk);
  const { mac, ...body } = pv.request;
  assert.deepEqual(await P.pairMac(body, P.unhex(pv.psk)), pv.request);
  await P.checkPairMac(pv.request, P.unhex(pv.psk), V.ts);
  await assert.rejects(P.checkPairMac(pv.request, new Uint8Array(32), V.ts), (e) => e.reason === "bad_code");
  assert.throws(() => P.decodeCode("ABC"), (e) => e.reason === "bad_code");
});

test("a fresh browser identity signs messages the other side accepts", async () => {
  const me = await P.newIdentity();
  assert.equal(me.publicKey.length, 32);
  const host = await id("host");
  const msg = await P.sign(P.routed("session.request", me.deviceId, host.deviceId,
    { session_id: "x", client: "Chromebook", want: {} }), me, pub("host"));
  await P.verify(msg, host, me.publicKey);
});

test("an identity that lends sharedSecret (the desktop app) signs the same as one holding the key", async () => {
  const client = await id("client");
  const lent = { deviceId: client.deviceId, publicKey: client.publicKey,
    sharedSecret: async (peer) => new Uint8Array(await globalThis.crypto.subtle.deriveBits({ name: "X25519",
      public: await globalThis.crypto.subtle.importKey("raw", peer, { name: "X25519" }, true, []) }, client.privateKey, 256)) };
  const { sig, ...body } = V.signed["session.request"];
  assert.deepEqual(await P.sign(body, lent, pub("host")), V.signed["session.request"]);
  assert.deepEqual(await P.relayProof(lent, V.relay_login.challenge, "client"), V.relay_login.proof);
});
