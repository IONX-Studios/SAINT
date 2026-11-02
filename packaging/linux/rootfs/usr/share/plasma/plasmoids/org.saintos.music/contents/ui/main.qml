// SAINT Music: the track playing in any MPRIS app (Spotify, Firefox, VLC, Elisa, …) through playerctl
// (a SAINT dependency), with artwork, progress, volume and controls. Reads once a second while visible,
// nothing while hidden. Drag and resize it like any desktop widget.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root
    readonly property string sep: "\u001f"
    readonly property string query: "playerctl metadata --format '{{status}}\u001f{{title}}\u001f{{artist}}\u001f"
                                    + "{{album}}\u001f{{mpris:artUrl}}\u001f{{position}}\u001f{{mpris:length}}\u001f"
                                    + "{{volume}}\u001f{{playerName}}'"
    property string status: ""            // Playing / Paused / Stopped / "" = no player
    property string title: ""
    property string artist: ""
    property string album: ""
    property string artUrl: ""
    property real position: 0             // seconds
    property real length: 0
    property real volume: -1              // 0..1, -1 = the player doesn't report it
    property string player: ""
    readonly property bool playing: status === "Playing"
    readonly property bool hasPlayer: status !== ""

    preferredRepresentation: fullRepresentation
    Plasmoid.icon: playing ? "media-playback-start" : "media-playback-pause"
    toolTipMainText: hasPlayer ? title : "Nothing playing"
    toolTipSubText: artist

    function control(args) { ctl.connectSource("playerctl " + args); }
    function clock(s) {
        s = Math.max(0, Math.floor(s));
        return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
    }

    P5Support.DataSource {
        id: meta
        engine: "executable"
        interval: root.visible ? 1000 : 0
        connectedSources: [root.query]
        onNewData: function (source, data) {
            var out = (data["stdout"] || "").trim();
            var f = out.split(root.sep);
            if (data["exit code"] !== 0 || f.length < 9) {
                root.status = "";
                return;
            }
            root.status = f[0]; root.title = f[1]; root.artist = f[2]; root.album = f[3]; root.artUrl = f[4];
            root.position = (parseInt(f[5]) || 0) / 1e6;
            root.length = (parseInt(f[6]) || 0) / 1e6;
            root.volume = f[7] === "" ? -1 : parseFloat(f[7]);
            root.player = f[8];
        }
    }
    P5Support.DataSource {
        id: ctl
        engine: "executable"
        onNewData: function (source) { disconnectSource(source); meta.connectSource(root.query); }
    }

    fullRepresentation: RowLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 14
        Layout.minimumHeight: Kirigami.Units.gridUnit * 5
        Layout.preferredWidth: Kirigami.Units.gridUnit * 22
        Layout.preferredHeight: Kirigami.Units.gridUnit * 7
        spacing: Kirigami.Units.largeSpacing

        Rectangle {
            id: art
            Layout.fillHeight: true
            Layout.preferredWidth: height
            Layout.maximumWidth: Kirigami.Units.gridUnit * 10
            radius: Kirigami.Units.cornerRadius
            color: Kirigami.Theme.alternateBackgroundColor
            clip: true
            Image {
                anchors.fill: parent
                source: root.artUrl
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                visible: status === Image.Ready
            }
            Kirigami.Icon {
                anchors.centerIn: parent
                width: parent.width * 0.4
                height: width
                source: "media-album-cover"
                visible: !root.artUrl
                opacity: 0.5
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 2

            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: root.hasPlayer ? (root.title || "Unknown track") : "Nothing playing"
                font.bold: true
                font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.15
                elide: Text.ElideRight
            }
            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: root.hasPlayer ? [root.artist, root.album].filter(Boolean).join(" · ")
                                     : "Play something in Spotify, Firefox or any music app"
                opacity: 0.7
                elide: Text.ElideRight
            }

            RowLayout {
                Layout.fillWidth: true
                visible: root.hasPlayer && root.length > 0
                PlasmaComponents.Label { text: root.clock(root.position); opacity: 0.6; font: Kirigami.Theme.smallFont }
                QQC2.Slider {
                    Layout.fillWidth: true
                    from: 0
                    to: Math.max(1, root.length)
                    value: root.position
                    onMoved: root.control("position " + Math.round(value))
                }
                PlasmaComponents.Label { text: root.clock(root.length); opacity: 0.6; font: Kirigami.Theme.smallFont }
            }

            RowLayout {
                Layout.fillWidth: true
                enabled: root.hasPlayer
                PlasmaComponents.ToolButton {
                    icon.name: "media-skip-backward"
                    onClicked: root.control("previous")
                    PlasmaComponents.ToolTip { text: "Previous" }
                }
                PlasmaComponents.ToolButton {
                    icon.name: root.playing ? "media-playback-pause" : "media-playback-start"
                    onClicked: root.control("play-pause")
                    PlasmaComponents.ToolTip { text: root.playing ? "Pause" : "Play" }
                }
                PlasmaComponents.ToolButton {
                    icon.name: "media-skip-forward"
                    onClicked: root.control("next")
                    PlasmaComponents.ToolTip { text: "Next" }
                }
                Item { Layout.fillWidth: true }
                Kirigami.Icon {
                    source: root.volume === 0 ? "audio-volume-muted" : "audio-volume-medium"
                    Layout.preferredWidth: Kirigami.Units.iconSizes.small
                    Layout.preferredHeight: Kirigami.Units.iconSizes.small
                    visible: root.volume >= 0
                }
                QQC2.Slider {
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 5
                    visible: root.volume >= 0
                    from: 0; to: 1; stepSize: 0.05
                    value: root.volume
                    onMoved: root.control("volume " + value.toFixed(2))
                }
            }
            PlasmaComponents.Label {
                text: root.hasPlayer ? (root.player + " · " + root.status) : ""
                opacity: 0.45
                font: Kirigami.Theme.smallFont
            }
        }
    }
}
