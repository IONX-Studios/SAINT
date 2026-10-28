// SAINTOS login splash: the SAINT mark inside the Halo on near-black, then the desktop.
// KSplash advances `stage` as Plasma starts (1..6); the ring fills with it.
import QtQuick

Rectangle {
    id: root
    color: "#0a0b0d"
    property int stage

    Item {
        anchors.centerIn: parent
        width: Math.min(root.width, root.height) * 0.22
        height: width

        Canvas {
            id: ring
            anchors.fill: parent
            property real progress: Math.min(1, root.stage / 6)
            onProgressChanged: requestPaint()
            onPaint: {
                var ctx = getContext("2d");
                ctx.reset();
                var r = width / 2 - 6;
                ctx.lineWidth = Math.max(3, width / 60);
                ctx.strokeStyle = "rgba(254,170,52,0.18)";
                ctx.beginPath(); ctx.arc(width / 2, height / 2, r, 0, 2 * Math.PI); ctx.stroke();
                ctx.strokeStyle = "#feaa34";
                ctx.beginPath();
                ctx.arc(width / 2, height / 2, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * Math.max(0.02, progress));
                ctx.stroke();
            }
        }
        Image {
            anchors.centerIn: parent
            width: parent.width * 0.55
            height: width
            source: "file:///usr/share/icons/hicolor/512x512/apps/saint.png"
            sourceSize.width: 512
            sourceSize.height: 512
            fillMode: Image.PreserveAspectFit
            smooth: true
        }
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: root.height * 0.12
        text: "SAINTOS"
        color: "#eceef2"
        font.pixelSize: Math.max(18, root.height / 28)
        font.letterSpacing: 6
        font.weight: Font.Light
    }
}
