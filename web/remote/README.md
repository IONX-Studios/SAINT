# Halo Remote — browser client

Static page served by the Halo Relay at `/` (`server/relay`). Works in Chrome 133+ (Chromebook, Windows, Linux),
Safari 17+ (iPad, iPhone, Mac) and Firefox 130+; needs https (or `localhost`) for WebCrypto.

- `js/protocol.js` — protocol v1: canonical JSON, X25519-derived message MACs, relay login, pairing codes
- `js/input.js` — keyboard (evdev codes), mouse, letterboxing, touch gestures (tap = click, long press /
  two-finger tap = right click, drag, two-finger scroll, pinch = local zoom), soft keyboard, modifier sync
- `js/client.js` — relay socket with reconnect, pairing, one WebRTC session (video, `control` + `files` channels,
  stats)
- `js/store.js` — this browser's non-extractable key and paired PCs (IndexedDB)
- `js/viewer.js` — the live screen (session, reconnect, input, clipboard, files, stats), shared by both pages
- `js/app.js` — the browser page: pairing and the list of PCs
- `desktop.html` + `js/desktop.js` — the same viewer inside SAINT's desktop app (`ui/remote_desktop.py`); identity
  and relay come from the app over Qt WebChannel

Pairing: run `halo-remote pair` on the PC and open the link it prints (the code travels in the `#fragment`,
never to a server), or type code, PC id and account.

## Tests

```bash
node --test "web/remote/test/*.test.mjs"
```

`protocol.test.mjs` checks against `tests/data/remote_vectors.json` (shared with Python and Swift);
`relay.integration.test.mjs` starts the real relay (Python + aiohttp; set `HALO_PYTHON` if not the repo's `.venv`).

`test/fake_host.html` is a stand-in PC in a browser tab (canvas video, data channels, file checksum) for trying
the real WebRTC path without a Linux desktop: run the relay locally, open `/test/fake_host.html`, then the link it
shows in another tab. It is not shipped in the relay image.
