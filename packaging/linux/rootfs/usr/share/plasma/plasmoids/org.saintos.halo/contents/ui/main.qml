// The Halo widget: an animated ring you can drop anywhere on the desktop and resize like any widget.
// It runs inside Plasma, so it needs no SAINT window; when SAINT runs it follows SAINT's state from
// ws://127.0.0.1:47811 (core/state_socket.py) — the same motion as the screen-edge Halo (ui/halo.py):
// a slow orbit while idle, a bright fast sweep while listening, twin comets while thinking, a pulse while
// speaking. Still when Plasma's animations are off (reduced motion) or the widget is hidden.
import QtQuick
import QtQuick.Layouts
import QtWebSockets
import org.kde.kirigami as Kirigami
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root
    property string saintState: "offline"
    property real level: 0
    property real head: 0
    property real pulse: 0
    property int frame: 0                 // bumped to repaint the ring (it lives in fullRepresentation)

    // state -> [base glow, comet brightness, laps per second, tail (fraction of the ring), comets]
    readonly property var profiles: ({
        "offline": [0.10, 0.45, 1 / 40, 0.18, 1], "idle": [0.14, 0.60, 1 / 30, 0.20, 1],
        "wake_listening": [0.16, 0.80, 1 / 14, 0.22, 1], "wake_detected": [0.35, 1.0, 1 / 3, 0.25, 1],
        "command_listening": [0.25, 1.0, 1 / 4, 0.22, 1], "listening": [0.25, 1.0, 1 / 4, 0.22, 1],
        "processing": [0.20, 1.0, 1 / 2.2, 0.16, 2], "observing": [0.20, 1.0, 1 / 2.2, 0.16, 2],
        "executing": [0.22, 1.0, 1 / 1.8, 0.16, 2], "speaking": [0.20, 0.9, 1 / 6, 0.28, 1],
        "error": [0.35, 0.0, 1 / 20, 0.18, 1]
    })
    readonly property var profile: profiles[cfgFollow ? saintState : "idle"] || profiles.idle
    readonly property bool cfgFollow: Plasmoid.configuration.followSaint
    readonly property bool moving: Plasmoid.configuration.animate && Kirigami.Units.longDuration > 0
                                   && root.visible
    readonly property color ringColor: {
        if (Plasmoid.configuration.useThemeColor) return Kirigami.Theme.highlightColor;
        if (!cfgFollow) return "#feaa34";
        if (saintState === "error") return "#f06a6a";
        if (saintState === "offline") return "#8a8f98";
        if (saintState === "processing" || saintState === "executing" || saintState === "observing") return "#ffc36b";
        return "#feaa34";
    }

    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground | PlasmaCore.Types.ConfigurableBackground
    preferredRepresentation: fullRepresentation
    toolTipMainText: "Halo"
    toolTipSubText: cfgFollow ? (saintState === "offline" ? "SAINT isn't running" : "SAINT: " + saintState) : ""

    fullRepresentation: Item {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 4
        Layout.minimumHeight: Kirigami.Units.gridUnit * 4
        Layout.preferredWidth: Kirigami.Units.gridUnit * 12
        Layout.preferredHeight: Kirigami.Units.gridUnit * 12
        opacity: Plasmoid.configuration.opacity / 100

        Canvas {
            id: ring
            anchors.fill: parent
            renderStrategy: Canvas.Cooperative
            property int frame: root.frame
            onFrameChanged: requestPaint()
            onPaint: {
                var ctx = getContext("2d");
                ctx.reset();
                var t = Plasmoid.configuration.thickness;
                var size = Math.min(width, height);
                var r = size / 2 - t * 2.5;
                if (r <= 0) return;
                var cx = width / 2, cy = height / 2;
                var p = root.profile;
                var base = Math.min(1, p[0] + 0.5 * root.level + 0.15 * root.pulse);
                var c = root.ringColor;
                function rgba(a) { return Qt.rgba(c.r, c.g, c.b, Math.max(0, Math.min(1, a))); }
                // soft outer glow, then the base ring
                ctx.lineCap = "round";
                ctx.lineWidth = t * 3;
                ctx.strokeStyle = rgba(base * 0.25);
                ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke();
                ctx.lineWidth = t;
                ctx.strokeStyle = rgba(base);
                ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke();
                // comets: short arcs fading behind each head
                var steps = 48, tail = p[3] * 2 * Math.PI;
                for (var k = 0; k < p[4]; ++k) {
                    var h = root.head * 2 * Math.PI + k * 2 * Math.PI / p[4] - Math.PI / 2;
                    for (var i = 0; i < steps; ++i) {
                        var f = i / steps;
                        var a0 = h - f * tail, a1 = h - (f + 1 / steps) * tail;
                        var bright = p[1] * Math.exp(-f * 3.2);
                        ctx.strokeStyle = f < 0.06 ? Qt.rgba(1, 1, 1, bright * 0.8) : rgba(bright);
                        ctx.lineWidth = t * (1 + (1 - f) * 0.6);
                        ctx.beginPath(); ctx.arc(cx, cy, r, a1, a0); ctx.stroke();
                    }
                }
            }
        }

        MouseArea {
            anchors.fill: parent
            enabled: Plasmoid.configuration.clickOpensSaint
            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: exec.connectSource("/usr/bin/saint --toggle-overlay")
        }
    }

    Timer {
        // ~30 fps while something moves; a repaint only when the state changes otherwise.
        interval: 33
        repeat: true
        running: root.moving
        property double last: 0
        onTriggered: {
            var now = Date.now();
            var dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
            last = now;
            root.head = (root.head + root.profile[2] * Plasmoid.configuration.speed * dt) % 1;
            if (root.saintState === "speaking") root.pulse = Math.abs(Math.sin(now / 160));
            else root.pulse *= 0.9;
            root.frame++;
        }
        onRunningChanged: last = 0
    }
    onSaintStateChanged: frame++
    onRingColorChanged: frame++
    Connections {
        target: Plasmoid.configuration
        function onValueChanged() { root.frame++; }
    }

    P5Support.DataSource {
        id: exec
        engine: "executable"
        onNewData: function (source) { disconnectSource(source); }
    }

    WebSocket {
        id: feed
        url: "ws://127.0.0.1:47811/"
        active: root.cfgFollow
        onTextMessageReceived: function (message) {
            try {
                var m = JSON.parse(message);
                root.saintState = m.state || "idle";
                root.level = Math.max(0, Math.min(1, m.level || 0));
            } catch (e) {}
        }
        onStatusChanged: {
            if (status === WebSocket.Error || status === WebSocket.Closed) {
                root.saintState = "offline";
                root.level = 0;
                active = false;
                retry.start();
            }
        }
    }
    Timer { id: retry; interval: 5000; onTriggered: feed.active = root.cfgFollow }
}
