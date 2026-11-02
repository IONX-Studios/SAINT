# Halo Remote — remote desktop design (ADR + protocol v1)

Status: accepted 2026-11-02 · Workstream B · applies to the Halo host, the browser client, the desktop
client and the iPhone app.

## Decision

**One protocol for every client: WebRTC media + a JSON control channel, set up through an authenticated
signaling service, with Halo devices identified by their existing SAINT Link keys.**

| Need | Choice | Why |
|---|---|---|
| Capture + input on Plasma Wayland | `xdg-desktop-portal` **ScreenCast + RemoteDesktop** (KDE backend), PipeWire stream | The only supported way on Wayland; the user approves once and the portal can restore the session token |
| Encode / transport | **GStreamer `webrtcbin`** (`pipewiresrc` → VA-API H.264, x264 fallback), host process on system Python (`python-gobject`, `gst-python`) — separate from SAINT's venv | Browsers, iOS (`WebRTC.framework`) and desktop clients all speak WebRTC; DTLS-SRTP encryption is mandatory in WebRTC |
| Across networks | ICE: direct/STUN first (peer-to-peer), **TURN relay fallback** (coturn); Tailscale addresses used as host candidates when present | Works behind NAT/CGNAT without opening ports; no VNC/RDP/SSH exposed |
| Finding the PC | **Halo Relay**: a small WebSocket signaling service (self-hostable; one container + coturn) | The PC dials *out* to the relay, so nothing listens on the internet |
| Identity / pairing | SAINT Link's device keys and pairing (Noise XXpsk3, one-time code, `modules/link`) | Already implemented and tested on PC and iPhone; no second account system |
| Native RDP | Keep **KRdp** for LAN use with any RDP app | Free fallback; not the primary path |

Rejected: Guacamole (Java server, RDP-to-HTML adds a hop), raw VNC (weak auth, no Wayland input), TeamViewer as
the core (closed, account-bound — remains an optional Store app).

## Components

```
 iPhone app ─┐                       ┌──────────── Halo PC ─────────────┐
 browser   ──┼── WSS ── Halo Relay ──┤ halo-remote (host)               │
 desktop   ──┘   (signaling only)    │  portal ScreenCast/RemoteDesktop │
      ▲                              │  GStreamer webrtcbin             │
      └──── WebRTC (DTLS-SRTP), P2P or via TURN ───────────────────────┘
```

- `halo-remote` — user systemd service on the PC (`packaging/linux/systemd/halo-remote.service.in`;
  `python3 -m modules.remote_desktop.host enable|disable|status|pair|set-relay URL`), off until enabled. Dials the
  relay, runs one session at a time (v1; the same device reconnecting replaces its own session). The portal shows
  KDE's "Allow remote control?" dialog the **first** time; the restore token it returns (persist mode 2) makes
  later sessions start without anyone at the PC — so approve it once while sitting at the PC.
- Halo Relay — `server/relay/` (Workstream B). In-memory state only (presence, each PC's device list; PCs re-send
  it on reconnect); never sees media or keystrokes.
- Clients — `web/remote/` (static, served by the relay), the desktop client (same web client in a Tauri
  shell), `mobile/ios` (native SwiftUI + WebRTC).

## Authentication

1. **Pairing** (once per client).
   - *iPhone and other SAINT devices*: the existing SAINT Link pairing (code/QR, Noise XXpsk3 over Link's TCP
     socket). The phone keeps using its Link key; nothing new to pair.
   - *Browsers*: they can't open Link's TCP socket, so they pair **through the relay** with a one-time code from
     `halo-remote pair` on the PC (the same 5-byte code and PBKDF2-stretched PSK as Link pairing). The browser sends
     `pair.request {pubkey, name}` MACed with the code's PSK; the PC checks it, records the key as an own device
     (permission `remote_desktop: ask`) and answers `pair.accept {pubkey}` MACed the same way, so a relay without the
     code can't substitute either key. The browser keeps a non-extractable WebCrypto X25519 key in IndexedDB.
   The PC's Link permission **`remote_desktop`** (own devices: *ask*, friends: *deny*) decides who may ask.
2. **Relay login**: `hello` -> `relay.challenge {nonce, eph}` (a one-time relay X25519 key) -> `relay.proof
   {sig = HMAC(HKDF(X25519(device, eph), "relay-login"), nonce || device_id || 0 || role)}`. The relay routes only
   between a PC and the devices that PC listed (`devices`); an account id is SHA-256 of the PC's public key.
   No passwords on the relay.
3. **Session**: `session.*` and `sdp.*` carry `sig` = HMAC-SHA256 under
   `HKDF(X25519(mine, theirs), "halo-remote/v1", "session-mac" || min(pub) || max(pub))` over the canonical JSON of
   the message, plus `from`/`to`/`ts` (+-300 s) and a replay guard. Only the two paired devices can derive that key,
   so the relay can't forge or alter a message, and the DTLS fingerprints inside the SDP are covered: it can't sit in
   the middle of the media either. The host checks the key is paired, not revoked and allowed `remote_desktop`, then
   asks "Allow <device>?" on the PC (kdialog, 30 s) unless that device is set to *allow*.
4. **Revocation**: removing a device in SAINT deletes its key and records it in `data/link/revoked.json`; the host
   refuses it as `revoked`, ends a running session within a second, and re-sends its `devices` list so the relay
   drops the route at once. A session ends after 12 h without input.

**Changed from the first draft (and why):** the draft said messages would be *signed* with "Ed25519/X25519-derived"
signatures. Link keys are X25519, which can't sign; Ed25519 would mean a second key per device and pairing every
device again, and XEdDSA (signing with an X25519 key) isn't available in CryptoKit, WebCrypto or `cryptography`.
A MAC from the pairwise static-static X25519 secret gives the same guarantee here (only the two paired devices can
produce or check it; the relay can do neither) with primitives all three platforms have. It is deniable between the
two devices, which doesn't matter for this use. The draft also assumed browsers pair through Link, which they can't
(no raw TCP), hence pairing through the relay.

## Protocol v1 (JSON over WSS to the relay; `type` + fields)

Implemented by `modules/remote_desktop/protocol.py` (PC + relay), `web/remote/js/protocol.js` (browser) and
`mobile/ios/Sources/SaintCore/Remote` (iPhone); all three check the known answers in `tests/data/remote_vectors.json`.
Signed messages may only hold strings, integers, booleans, null, lists and objects (no floats), so the canonical JSON
(sorted keys, no whitespace, UTF-8, JSON.stringify escapes) is byte-identical everywhere.

| Message | Direction | Fields |
|---|---|---|
| `hello` | device -> relay | `v:1`, `device_id`, `role: host\|client`, `pubkey`, `name`; clients: `accounts: [...]`, `pair?: account` |
| `relay.challenge` / `relay.proof` | relay <-> device | `nonce`, `eph` / `sig` |
| `welcome` | relay -> device | `device_id`, `ice_servers` (STUN + 12-h coturn REST credentials), `account` (host) |
| `devices` | host -> relay | `keys: [{device_id, pubkey, name}]` — who may reach this PC |
| `presence` | relay -> client | `hosts: [{account, device_id, name, online, last_seen}]` |
| `error` | relay -> device | `reason` (`login_failed`, `not_allowed`, `offline`, `revoked`, `rate_limited`, ...) |
| `pair.request` / `pair.accept` / `pair.deny` | client <-> host | `from`, `to`, `pubkey`, `name`, `platform`, `ts`, `mac` / `reason` |
| `session.request` | client -> host | `from`, `to`, `session_id`, `ts`, `client`, `want: {max_w, max_h, fps, audio}`, `sig` |
| `session.accept` / `session.deny` | host -> client | `from`, `to`, `session_id`, `ts`, `ice_servers` / `reason`, `sig` |
| `sdp.offer` / `sdp.answer` | host -> client / client -> host | `from`, `to`, `session_id`, `ts`, `sdp`, `sig` |
| `ice` | both | `from`, `to`, `session_id`, `candidate: {candidate, sdpMLineIndex}` (not signed; can't break DTLS) |
| `session.end` | both | `from`, `to`, `session_id`, `ts`, `reason`, `sig` |

The host sends the offer (video track + `control` and `files` data channels); clients answer.

**Data channel `control`** (reliable, ordered) after connect — JSON:

| Message | Meaning |
|---|---|
| `pointer.move {x, y}` | absolute, normalised 0..1 of the stream |
| `pointer.button {button: 1-3, down}` · `pointer.scroll {dx, dy}` | mouse |
| `key {code, down}` | Linux evdev keycode (clients map from their layout) · `text {utf8}` for IME/emoji |
| `clipboard {mime, data}` | both ways; v1 implements text (≤ 4 MB), images later |
| `display {w, h, scale}` | client viewport → host may switch stream size |
| `stats {rtt_ms, fps, kbps, path: p2p\|relay}` | host → client, once per second |

**Data channel `files`** (reliable): `file.offer {id, name, size, sha256}` → `file.accept` → binary chunks
(64 KiB) → `file.done {id, ok}`; received files land in `~/Downloads/Halo Remote/`.

## Hosting

- Self-hosted: relay + coturn in one `docker compose` on any VPS (≈ 1 vCPU / 1 GB, ~$5/month); TURN traffic
  only when P2P fails (typically 10–20 % of sessions; budget ~2–5 GB per relayed hour at 1080p).
- Tailscale users can skip the relay: host and client find each other by Tailscale address.

## Test plan (Workstream B)

Unit: message schema + signature checks (Python and Swift share vectors in `tests/data/remote_vectors.json`).
Integration: host ↔ browser on the Halo VM; unauthorised key refused; revoked key refused; reconnect after
network drop; file transfer checksum. Cross-network: phone on cellular ↔ PC behind NAT via TURN.

## Status (2026-11-02, Workstream B)

| Part | Where | State |
|---|---|---|
| Protocol v1 + vectors | `modules/remote_desktop/protocol.py`, `tests/data/remote_vectors.json` | tested (pytest) |
| Halo Relay | `server/relay/` | tested on localhost with real WebSockets (pytest, and from the browser client in Node); Docker image not built here |
| Host logic (permission decision, session state machine, input mapping, files, pairing, pipeline/ICE config) | `modules/remote_desktop/host.py` | tested (pytest) |
| Host media path (portal, PipeWire, GStreamer webrtcbin, libsoup, wl-clipboard) | `modules/remote_desktop/media.py` | **implemented, untested** — needs a Plasma Wayland session |
| Browser client | `web/remote/` | tested: Node unit + relay integration tests; real WebRTC checked browser-to-browser in Chromium against `test/fake_host.html` (video, mouse, keys incl. Ctrl combos, clipboard, file + SHA-256, stats, disconnect). Not yet tried on Safari/iPad or a Chromebook; touch gestures only unit-tested |
| iPhone | `mobile/ios` (`SaintCore/Remote`, `SaintMobile/Remote`) | **written, not compiled here** (no Swift toolchain on the dev host); XCTests run in CI |
| Desktop client (Tauri shell) | — | not started; the browser client works on Windows/Linux meanwhile |
| SAINT -> Devices -> Remote access UI on the PC | — | not started; the `halo-remote` CLI does it |
| Halo package | `packaging/arch/pkgbuilds/saint` | not changed (Workstream A owns it): needs the unit installed and `gst-plugins-bad libnice gst-plugin-pipewire gst-python python-gobject libsoup3 wl-clipboard kdialog python-keyring` |
