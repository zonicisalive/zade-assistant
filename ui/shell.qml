// Zade overlay: a top-center island that shows what Zade hears and says.
// Reads Zade's state from $XDG_RUNTIME_DIR/zade/state.json and the wallpaper colors
// generated for the desktop shell, so it follows the current theme.
import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root

    property string mode: "idle"      // listening | thinking | speaking | done | idle
    property string heard: ""
    property string reply: ""
    property real level: 0
    property bool shown: false
    readonly property bool expanded: heard.length > 0 || reply.length > 0

    // Material You palette; defaults are replaced by the generated colors.json.
    property var c: ({
        primary: "#A5D0BB", tertiary: "#EABBB8", on_surface: "#E2E2E0",
        on_surface_variant: "#C1C8C3", surface_container: "#1E201F", outline_variant: "#434844"
    })

    function applyState(text) {
        let s
        try { s = JSON.parse(text) } catch (e) { return }   // half-written file: keep the last state
        mode = s.state || "idle"
        heard = s.heard || ""
        reply = s.reply || ""
        level = s.level || 0
        if (mode === "idle") { hideTimer.interval = 120; hideTimer.restart() }
        else if (mode === "done") { hideTimer.interval = 4500; hideTimer.restart() }
        else { hideTimer.stop(); shown = true }
    }

    FileView {
        path: Quickshell.env("XDG_RUNTIME_DIR") + "/zade/state.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: root.applyState(text())
    }

    FileView {
        path: Quickshell.env("HOME") + "/.local/state/quickshell/user/generated/colors.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            try { root.c = Object.assign({}, root.c, JSON.parse(text())) } catch (e) {}
        }
    }

    Timer { id: hideTimer; onTriggered: root.shown = false }

    // Drives the waveform animation.
    property real phase: 0
    NumberAnimation on phase {
        from: 0; to: Math.PI * 2; duration: root.mode === "thinking" ? 900 : 1400
        loops: Animation.Infinite; running: island.opacity > 0
    }

    PanelWindow {
        id: win
        anchors.top: true
        exclusiveZone: 0                      // sit just below the bar, reserve nothing
        margins.top: 6
        implicitWidth: 680
        implicitHeight: 260
        color: "transparent"
        visible: island.opacity > 0.01
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "zade"
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        mask: Region { item: island }         // clicks outside the island pass through

        Rectangle {
            id: island
            anchors.horizontalCenter: parent.horizontalCenter
            y: root.shown ? 4 : -12
            width: root.expanded ? Math.min(640, Math.max(340, body.implicitWidth + 40)) : header.implicitWidth + 36
            height: root.expanded ? body.implicitHeight + 28 : 46
            radius: root.expanded ? 24 : height / 2
            color: Qt.alpha(root.c.surface_container, 0.97)
            border.width: 1
            border.color: Qt.alpha(root.c.outline_variant, 0.7)
            opacity: root.shown ? 1 : 0
            scale: root.shown ? 1 : 0.92
            clip: true

            Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
            Behavior on height { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
            Behavior on radius { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
            Behavior on opacity { NumberAnimation { duration: 220 } }
            Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }
            Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

            ColumnLayout {
                id: body
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: root.expanded ? 16 : 0
                anchors.leftMargin: root.expanded ? 20 : 18
                anchors.topMargin: root.expanded ? 14 : 0
                spacing: 8

                RowLayout {
                    id: header
                    Layout.preferredHeight: root.expanded ? 28 : 46
                    spacing: 12

                    // Orb: breathing dot, tertiary while thinking.
                    Rectangle {
                        id: orb
                        Layout.alignment: Qt.AlignVCenter
                        width: 12; height: 12; radius: 6
                        color: root.mode === "thinking" ? root.c.tertiary : root.c.primary
                        scale: root.mode === "listening" ? 1 + root.level * 0.6 : 0.9 + 0.15 * Math.sin(root.phase)
                        Behavior on color { ColorAnimation { duration: 250 } }
                        Rectangle {
                            anchors.centerIn: parent
                            width: parent.width * 2; height: width; radius: width / 2
                            color: "transparent"
                            border.width: 2
                            border.color: Qt.alpha(parent.color, 0.12 + 0.1 * Math.sin(root.phase))
                        }
                    }

                    // Waveform: mic level while listening, a travelling wave otherwise.
                    Row {
                        Layout.alignment: Qt.AlignVCenter
                        spacing: 3
                        height: 26
                        Repeater {
                            model: 5
                            Rectangle {
                                required property int index
                                width: 4; radius: 2
                                anchors.verticalCenter: parent.verticalCenter
                                color: root.mode === "thinking" ? root.c.tertiary : root.c.primary
                                opacity: root.mode === "done" ? 0.35 : 0.9
                                readonly property real wave: 0.5 + 0.5 * Math.sin(root.phase * 2 + index * 0.9)
                                readonly property real amp: root.mode === "listening" ? 0.25 + root.level * 0.75
                                                          : root.mode === "speaking" ? 0.7
                                                          : root.mode === "thinking" ? 0.4 : 0.15
                                height: 5 + 21 * amp * wave
                                Behavior on height { NumberAnimation { duration: 90 } }
                            }
                        }
                    }

                    Text {
                        Layout.alignment: Qt.AlignVCenter
                        text: ({ listening: "Listening", thinking: "Thinking", speaking: "Zade", done: "Zade" })[root.mode] || "Zade"
                        color: root.c.on_surface_variant
                        font.family: "Readex Pro"
                        font.pixelSize: 13
                        font.weight: Font.Medium
                    }
                }

                Text {
                    visible: root.heard.length > 0
                    Layout.fillWidth: true
                    Layout.maximumWidth: 600
                    text: "“" + root.heard + "”"
                    color: Qt.alpha(root.c.on_surface_variant, 0.85)
                    font.family: "Readex Pro"
                    font.pixelSize: 13
                    font.italic: true
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }

                Text {
                    visible: root.reply.length > 0
                    Layout.fillWidth: true
                    Layout.maximumWidth: 600
                    Layout.bottomMargin: 2
                    text: root.reply
                    color: root.c.on_surface
                    font.family: "Readex Pro"
                    font.pixelSize: 15
                    lineHeight: 1.15
                    wrapMode: Text.WordWrap
                    maximumLineCount: 7
                    elide: Text.ElideRight
                }
            }
        }
    }
}
