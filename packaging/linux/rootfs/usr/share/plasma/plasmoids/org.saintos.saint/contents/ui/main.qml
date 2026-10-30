// The SAINT widget (Halo): SAINT's state as the orange orb, "Ask SAINT…" (saint --ask) and a button
// that opens SAINT's overlay to talk (saint --toggle-overlay). State comes from ws://127.0.0.1:47811.
import QtQuick
import QtQuick.Layouts
import QtWebSockets
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root
    property string saintState: "offline"
    property real level: 0
    readonly property var labels: ({
        "offline": "SAINT isn't running", "idle": "Ready", "wake_listening": "Listening for “Hey SAINT”",
        "wake_detected": "Yes?", "command_listening": "Listening…", "listening": "Listening…",
        "processing": "Thinking…", "executing": "Working on it…", "observing": "Looking at your screen…",
        "speaking": "Speaking", "error": "Something went wrong"
    })

    preferredRepresentation: fullRepresentation
    Plasmoid.icon: "saint"
    toolTipMainText: "SAINT"
    toolTipSubText: labels[saintState] || saintState

    function shellQuote(text) { return "'" + String(text).replace(/'/g, "'\\''") + "'"; }
    function run(args) { exec.connectSource("/usr/bin/saint " + args); }

    P5Support.DataSource {
        id: exec
        engine: "executable"
        onNewData: function (source) { disconnectSource(source); }
    }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: 260
        Layout.preferredWidth: 320
        spacing: 8

        RowLayout {
            spacing: 10
            Rectangle {
                id: orb
                Layout.preferredWidth: 28
                Layout.preferredHeight: 28
                radius: 14
                color: root.saintState === "offline" ? "#5a5f68" : root.saintState === "error" ? "#ff5a5a" : "#feaa34"
                scale: 1 + root.level * 0.35
                Behavior on scale { NumberAnimation { duration: 90 } }
            }
            ColumnLayout {
                spacing: 0
                PlasmaComponents.Label { text: "SAINT"; font.bold: true }
                PlasmaComponents.Label {
                    text: root.labels[root.saintState] || root.saintState
                    opacity: 0.7
                }
            }
            Item { Layout.fillWidth: true }
            PlasmaComponents.ToolButton {
                icon.name: "audio-input-microphone"
                text: "Talk"
                onClicked: root.run("--toggle-overlay")
            }
        }
        PlasmaComponents.TextField {
            id: ask
            Layout.fillWidth: true
            placeholderText: "Ask SAINT…"
            onAccepted: {
                var t = text.replace(/[\r\n]+/g, " ").trim();
                if (t.length > 0) {
                    root.run("--ask " + root.shellQuote(t.slice(0, 500)));
                    text = "";
                }
            }
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
                root.saintState = "offline";
                root.level = 0;
                active = false;
                retry.start();
            }
        }
    }
    Timer { id: retry; interval: 5000; onTriggered: feed.active = true }
}
