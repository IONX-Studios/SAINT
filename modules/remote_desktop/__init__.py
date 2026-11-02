"""
Halo Remote: remote desktop for Halo OS (docs/REMOTE_DESKTOP.md).

    protocol.py   protocol v1 messages, checks, Link-key signatures, who may connect
    host.py       the PC side: portal session, GStreamer webrtcbin, input/clipboard/files (user service)

The relay lives in server/relay/, the browser client in web/remote/, the iPhone client in mobile/ios.
"""
