/* Halo installer slideshow: what SAINT does, while the files copy. */
import QtQuick 2.15
import calamares.slideshow 1.0

Presentation {
    id: presentation

    function nextSlide() { presentation.goToNextSlide(); }

    Timer {
        interval: 8000
        running: presentation.activatedInCalamares
        repeat: true
        onTriggered: nextSlide()
    }

    Rectangle { anchors.fill: parent; color: "#0a0b0d"; z: -1 }

    Repeater {
        model: [
            ["Welcome to Halo", "Arch Linux and KDE Plasma, with SAINT built in."],
            ["Say \"SAINT\"", "Open apps, control music, find files and run your desktop by voice."],
            ["Your AI, locally", "Ollama runs language models on this PC; nothing leaves it unless you choose a cloud model."],
            ["Every app", "Search the Arch repositories, the AUR and Flathub from one place."]
        ]
        Slide {
            Column {
                anchors.centerIn: parent
                spacing: 18
                width: parent.width * 0.8
                Text {
                    text: modelData[0]
                    color: "#feaa34"
                    font.pixelSize: 34
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                }
                Text {
                    text: modelData[1]
                    color: "#eceef2"
                    font.pixelSize: 18
                    wrapMode: Text.WordWrap
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                }
            }
        }
    }

    function onActivate() { presentation.currentSlide = 0; }
    function onLeave() { }
}
