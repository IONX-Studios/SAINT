// This browser's Halo Remote identity and paired PCs, in IndexedDB. The private key is a non-extractable
// WebCrypto key: scripts on this page can use it, nothing can read it out.
import * as P from "./protocol.js";

function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("halo-remote", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("kv");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function op(mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const tx = d.transaction("kv", mode);
    const req = fn(tx.objectStore("kv"));
    tx.oncomplete = () => resolve(req.result);
    tx.onerror = () => reject(tx.error);
  });
}

const get = (k) => op("readonly", (s) => s.get(k));
const put = (k, v) => op("readwrite", (s) => s.put(v, k));

export async function identity() {
  let me = await get("identity");
  if (!me) {
    me = await P.newIdentity(false);
    await put("identity", me);
  }
  return me;
}

export async function hosts() { return (await get("hosts")) || []; }

export async function saveHost(h) {
  const list = (await hosts()).filter((x) => x.device_id !== h.device_id);
  list.push(h);
  await put("hosts", list);
  return list;
}

export async function forgetHost(deviceId) {
  const list = (await hosts()).filter((x) => x.device_id !== deviceId);
  await put("hosts", list);
  return list;
}

export async function setting(name, value) {
  if (value === undefined) return get("setting:" + name);
  return put("setting:" + name, value);
}
