// Halo widget settings. Size and position: drag the widget's edges and title like any desktop widget.
import QtQuick
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    property alias cfg_opacity: opacitySlider.value
    property alias cfg_thickness: thicknessSlider.value
    property alias cfg_speed: speedSlider.value
    property alias cfg_animate: animateBox.checked
    property alias cfg_followSaint: followBox.checked
    property alias cfg_clickOpensSaint: clickBox.checked
    property alias cfg_useThemeColor: themeBox.checked

    Kirigami.FormLayout {
        QQC2.Slider {
            id: opacitySlider
            Kirigami.FormData.label: i18n("Opacity:")
            from: 10; to: 100; stepSize: 5
        }
        QQC2.Slider {
            id: thicknessSlider
            Kirigami.FormData.label: i18n("Ring thickness:")
            from: 2; to: 24; stepSize: 1
        }
        QQC2.Slider {
            id: speedSlider
            Kirigami.FormData.label: i18n("Animation speed:")
            from: 0.25; to: 3.0; stepSize: 0.25
        }
        QQC2.CheckBox {
            id: animateBox
            Kirigami.FormData.label: i18n("Animation:")
            text: i18n("Animate (always still when Plasma's animations are off)")
        }
        QQC2.CheckBox {
            id: followBox
            text: i18n("Follow SAINT (listening, thinking, speaking)")
        }
        QQC2.CheckBox {
            id: clickBox
            text: i18n("Click the Halo to talk to SAINT")
        }
        QQC2.CheckBox {
            id: themeBox
            text: i18n("Use the desktop accent colour instead of SAINT's")
        }
    }
}
