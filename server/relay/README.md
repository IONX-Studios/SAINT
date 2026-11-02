# Halo Relay

The signaling service for **Halo Remote** (remote desktop for Halo OS — design in `docs/REMOTE_DESKTOP.md`).
Your Halo PC and your paired phone/tablet/Chromebook dial *out* to it, so nothing on your PC listens on the
internet. It only introduces devices to each other:

- **Login** — each device answers a one-time challenge with its SAINT Link key. No passwords, no accounts to
  create: an "account" is one PC (the SHA-256 of its public key).
- **Presence** — which of your PCs are online.
- **Routing** — passes `session.*`, `sdp.*`, `ice` and `pair.*` messages, and only between a PC and a device
  that PC has allowed (the PC sends the list; remove a device in SAINT → Devices and its route is gone at once).
  Session messages are signed with keys the relay doesn't have, so it can't forge or alter them.
- **TURN credentials** — short-lived (12 h) credentials for the coturn next to it, used only when the two
  devices can't reach each other directly. Media is encrypted end to end (DTLS-SRTP) either way.
- **The browser client** — serves `web/remote/` at `/`.

State is in memory; after a restart PCs reconnect within seconds and resend their device lists.

## Run it locally (development)

```bash
pip install -r server/relay/requirements.txt
python -m server.relay.relay --port 8080        # from the repository root
```

Open `http://localhost:8080/` (browsers allow WebCrypto on `localhost` without HTTPS). Tests:
`python -m pytest tests/test_relay.py` — they start the relay on a local port and use real WebSockets.

## Host it (one small VPS)

1. A VPS with a public IPv4 address, Docker and Docker Compose. Point a DNS name at it
   (`relay.example.com`).
2. Open TCP 80, 443 (HTTPS + WSS), TCP/UDP 3478 (STUN/TURN), TCP 5349 (TURN over TLS, optional) and
   UDP 49160–49200 (TURN media) in its firewall.
3. In this folder:
   ```bash
   cp relay.env.example .env        # set HALO_DOMAIN, HALO_PUBLIC_IP, HALO_TURN_SECRET (openssl rand -hex 32)
   docker compose up -d
   ```
   Caddy gets a Let's Encrypt certificate automatically. For `turns:` on 5349 put a certificate for the same
   name in `turn-certs/<domain>.crt` / `.key` (e.g. copy Caddy's); without it TURN still works on 3478.
4. On the PC: SAINT → Devices → Remote access → relay `wss://relay.example.com/ws`
   (or `halo-remote set-relay wss://relay.example.com/ws`).

`.env` is git-ignored — never commit the TURN secret.

## What it costs

| | Typical | Notes |
|---|---|---|
| VPS (1 vCPU, 1 GB RAM) | about **$4–6 / month** (Hetzner CX22, DigitalOcean/Vultr basic droplet) | Signaling is tiny: thousands of devices fit on this |
| Traffic | usually included (Hetzner: 20 TB/month) | Only TURN-relayed sessions use the VPS's bandwidth |
| TURN usage | **~2–4 GB per relayed hour** at 1080p/30 fps (4–8 Mbit/s), ~1 GB at 720p | Most sessions go peer-to-peer; TURN is the fallback for strict NAT/cellular CGNAT (roughly 10–20 % of sessions) |
| Domain | ~$10 / year | Any name you control; a free DuckDNS subdomain works too |

Prices are indicative (late 2026); check the provider. A free-tier ARM VM (e.g. Oracle Cloud) runs it too.
If all your devices are on Tailscale you can skip the relay's TURN entirely: Tailscale already connects them.

## Security notes

- The relay never sees private keys, screen contents, keystrokes or files.
- A login must finish within 10 s and each connection is capped at 200 messages a second; pairing requests are limited to 5 per minute per
  address, and the PC cancels a pairing code after repeated wrong guesses.
- A compromised relay can deny service or learn *which* devices talk to which PC and when; it cannot see or
  control your screen.
