// The SAINT wallpaper (Halo): the SAINT artwork, with the Halo ring around the SAINT mark coming alive
// when SAINT hears you. State comes from SAINT over ws://127.0.0.1:47811 (core/state_socket.py):
//   listening   ripples leave the ring and it swells with your voice
//   thinking    a light runs around the ring
//   speaking    the ring pulses with SAINT's voice
//   idle / SAINT not running   the still artwork: nothing animates, nothing redraws (no CPU, no battery)
import QtQuick
import QtWebSockets
import org.kde.plasma.plasmoid

WallpaperItem {
    id: root

    property string saintState: "idle"
    property real level: 0
    readonly property string mode: {
        switch (saintState) {
        case "wake_detected": case "command_listening": case "listening": return "listening";
        case "processing": case "executing": case "observing": return "thinking";
        case "speaking": return "speaking";
        case "error": return "error";
        default: return "still";
        }
    }
    // The artwork is 16:9 and fills the screen (crop); find its ring on this screen's shape.
    readonly property real shownHeight: Math.max(height, width * 9 / 16)
    readonly property real ringX: width / 2
    readonly property real ringY: height / 2 + (0.42 - 0.5) * shownHeight
    readonly property real ringR: 200 / 1080 * shownHeight
    readonly property real px: shownHeight / 1080
    readonly property color orange: "#feaa34"

    Rectangle { anchors.fill: parent; color: "#0a0b0d" }

    Image {
        anchors.fill: parent
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        smooth: true
        source: "file:///usr/share/wallpapers/SAINTOS/contents/images/"
                + (root.width > 2600 ? "3840x2160" : root.width > 1920 ? "2560x1440" : "1920x1080") + ".png"
    }

    // The live ring, drawn over the artwork's ring.
    Rectangle {
        id: live
        x: root.ringX - width / 2
        y: root.ringY - height / 2
        width: 2 * root.ringR * scaleNow
        height: width
        radius: width / 2
        color: "transparent"
        border.width: Math.max(2, 6 * root.px)
        border.color: root.mode === "error" ? "#ff5a5a" : root.orange
        visible: root.mode !== "still"
        opacity: root.mode === "still" ? 0 : 0.95
        property real scaleNow: root.mode === "listening" ? 1 + root.level * 0.22
                              : root.mode === "speaking" ? 1 + root.level * 0.12 : 1
        Behavior on scaleNow { NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }
        Behavior on opacity { NumberAnimation { duration: 250 } }
    }

    // Listening: ripples leave the ring (one shared phase, three ripples a third apart).
    property real phase: 0
    NumberAnimation on phase {
        from: 0; to: 1; duration: 2100; loops: Animation.Infinite
        running: root.mode === "listening"
    }
    Repeater {
        model: 3
        Rectangle {
            readonly property real t: (root.phase + index / 3) % 1
            visible: root.mode === "listening"
            x: root.ringX - width / 2
            y: root.ringY - height / 2
            width: 2 * root.ringR * (1 + t * 0.9)
            height: width
            radius: width / 2
            color: "transparent"
            border.width: Math.max(1, 3 * root.px)
            border.color: root.orange
            opacity: (1 - t) * 0.6
        }
    }

    // Thinking: a light runs around the ring.
    Item {
        x: root.ringX
        y: root.ringY
        visible: root.mode === "thinking"
        RotationAnimator on rotation {
            from: 0; to: 360; duration: 1400; loops: Animation.Infinite
            running: root.mode === "thinking"
        }
        Rectangle {
            width: 18 * root.px
            height: width
            radius: width / 2
            x: root.ringR - width / 2
            y: -height / 2
            color: "#ffffff"
            border.color: root.orange
            border.width: 3 * root.px
        }
    }

    WebSocket {
        id: feed
        url: "ws://127.0.0.1:47811/"
        active: true
        onTextMessageReceived: function (message) {
            try {
                var m = JSON.parse(message);
                root.saintState = m.state || "idle";
                root.level = Math.max(0, Math.min(1, m.level || 0));
            } catch (e) {}
        }
        onStatusChanged: {
            if (status === WebSocket.Error || status === WebSocket.Closed) {
                root.saintState = "idle";        // SAINT isn't running: the still artwork
                active = false;
                retry.start();
            }
        }
    }
    Timer { id: retry; interval: 5000; onTriggered: feed.active = true }
}
