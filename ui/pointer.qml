// Zade's screen-guide pointer: a pulsing ring (and the spoken instruction) where to click.
// Full-screen, click-through overlay; Zade writes $XDG_RUNTIME_DIR/zade/pointer.json.
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root
    property var p: ({ visible: false, x: 0, y: 0, text: "" })
    property color accent: "#A5D0BB"

    FileView {
        path: Quickshell.env("XDG_RUNTIME_DIR") + "/zade/pointer.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { try { root.p = JSON.parse(text()) } catch (e) {} }
    }
    FileView {  // the wallpaper accent, like the overlay
        path: Quickshell.env("HOME") + "/.local/state/quickshell/user/generated/colors.json"
        onLoaded: { try { root.accent = JSON.parse(text()).primary } catch (e) {} }
    }

    PanelWindow {
        anchors { top: true; left: true; right: true; bottom: true }
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "zade-pointer"
        mask: Region {}  // every click goes through to the app below
        visible: root.p.visible === true

        Item {
            x: root.p.x; y: root.p.y
            Behavior on x { NumberAnimation { duration: 350; easing.type: Easing.OutCubic } }
            Behavior on y { NumberAnimation { duration: 350; easing.type: Easing.OutCubic } }

            Repeater {  // two rings pulsing outwards
                model: 2
                Rectangle {
                    required property int index
                    property real t: 0
                    width: 36 + t * 40; height: width; radius: width / 2
                    x: -width / 2; y: -height / 2
                    color: "transparent"; border.width: 3; border.color: root.accent
                    opacity: 1 - t
                    NumberAnimation on t { from: 0; to: 1; duration: 1200; loops: Animation.Infinite; running: true }
                    Component.onCompleted: if (index === 1) t = 0.5
                }
            }
            Rectangle { width: 14; height: 14; radius: 7; x: -7; y: -7; color: root.accent }

            Rectangle {  // the instruction, next to the ring
                visible: root.p.text.length > 0
                x: 34; y: -height / 2
                width: label.implicitWidth + 24; height: label.implicitHeight + 14; radius: height / 2
                color: Qt.rgba(0.08, 0.09, 0.09, 0.92)
                border.width: 1; border.color: root.accent
                Text {
                    id: label
                    anchors.centerIn: parent
                    text: root.p.text
                    color: "white"
                    font.family: "Readex Pro"; font.pixelSize: 15
                }
            }
        }
    }
}
