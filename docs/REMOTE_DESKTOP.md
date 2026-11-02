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

- `halo-remote` — user systemd service on the PC (`packaging/.../halo-remote.service`), off until enabled in
  SAINT → Devices → Remote access. Dials the relay, waits for offers, runs one session at a time (v1).
- Halo Relay — `server/relay/` (Workstream B). Stateless except presence; never sees media or keystrokes.
- Clients — `web/remote/` (static, served by the relay), the desktop client (same web client in a Tauri
  shell), `mobile/ios` (native SwiftUI + WebRTC).

## Authentication

1. **Pairing** (once per client): SAINT Link pairing code/QR (`saint://pair?...`). The client stores its
   Curve25519 key pair (iOS Keychain / browser: non-extractable WebCrypto key in IndexedDB) and the PC's
   public key; the PC records the client's key with role `own` and permission `remote_desktop`.
2. **Relay login**: the PC and each client authenticate to the relay with a signed challenge
   (`relay.challenge` → Ed25519/X25519-derived signature); the relay only routes between devices registered
   to the same *account id* = SHA-256 of the PC's public key. No passwords on the relay.
3. **Session**: the client's `session.request` is signed with its device key; the host checks the key is
   paired, not revoked and allowed `remote_desktop`, then shows "Allow <device>?" on the PC unless the user
   set that device to *always allow*. The WebRTC DTLS fingerprints are carried inside the signed messages,
   so the relay cannot man-in-the-middle the media.
4. **Revocation**: removing a device in SAINT deletes its key; the host refuses it immediately and tells the
   relay to drop its routes. Session tokens expire after 12 h of inactivity.

## Protocol v1 (JSON over WSS to the relay; `type` + fields; all host-bound requests signed)

| Message | Direction | Fields |
|---|---|---|
| `hello` | any → relay | `v:1`, `device_id`, `role: host\|client`, `pubkey` |
| `relay.challenge` / `relay.proof` | relay ↔ device | `nonce` / `sig` |
| `presence` | relay → client | `hosts: [{device_id, name, online, last_seen}]` |
| `session.request` | client → host | `session_id`, `client`, `want: {max_w, max_h, fps, audio}`, `sig` |
| `session.accept` / `session.deny` | host → client | `session_id`, `reason?`, `ice_servers` |
| `sdp.offer` / `sdp.answer` | both | `session_id`, `sdp`, `sig` |
| `ice` | both | `session_id`, `candidate` |
| `session.end` | both | `session_id`, `reason` |

**Data channel `control`** (reliable, ordered) after connect — JSON:

| Message | Meaning |
|---|---|
| `pointer.move {x, y}` | absolute, normalised 0..1 of the stream |
| `pointer.button {button: 1-3, down}` · `pointer.scroll {dx, dy}` | mouse |
| `key {code, down}` | Linux evdev keycode (clients map from their layout) · `text {utf8}` for IME/emoji |
| `clipboard {mime, data}` | both ways, text first; images ≤ 4 MB |
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
