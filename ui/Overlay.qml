// Zade overlay: a top-center island that shows what Zade hears and says.
// A Scope so it can run standalone (ui/shell.qml) or inside a desktop shell (e.g. inir via LazyLoader).
// Reads Zade's state from $XDG_RUNTIME_DIR/zade/state.json and the wallpaper colors
// generated for the desktop shell, so it follows the current theme.
import QtQuick
import QtQuick.Effects
import QtQuick.Shapes
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

Scope {
    id: root

    property string mode: "idle"      // listening | thinking | speaking | done | idle
    property string heard: ""
    property string reply: ""
    property real level: 0
    property bool shown: false
    // Style from Zade's settings (sent with every state update, so changes apply live).
    property var style: ({ position: "top", size: "medium", accent: "", linger_s: 0.5, reveal_cps: 18, show_heard: true })
    readonly property bool fromTop: !style.position.startsWith("bottom")
    readonly property string side: style.position.endsWith("left") ? "left" : style.position.endsWith("right") ? "right" : "center"
    readonly property real sizeFactor: ({ small: 0.85, medium: 1.0, large: 1.2 })[style.size] || 1.0
    readonly property color accent: style.accent ? style.accent : c.primary
    readonly property bool expanded: reply.length > 0 || (heard.length > 0 && style.show_heard && mode !== "listening")

    // Material You palette; defaults are replaced by the generated colors.json.
    property var c: ({
        primary: "#A5D0BB", primary_container: "#749D8A", tertiary: "#EABBB8",
        on_surface: "#E2E2E0", on_surface_variant: "#C1C8C3",
        surface_container_low: "#1A1C1B", outline_variant: "#434844"
    })

    function applyState(text) {
        let s
        try { s = JSON.parse(text) } catch (e) { return }   // half-written file: keep the last state
        const newReply = s.reply || ""
        if (newReply !== reply) revealed = 0
        mode = s.state || "idle"
        heard = s.heard || ""
        reply = newReply
        level = s.level || 0
        if (s.style) style = s.style
        if (mode !== "speaking") revealed = reply.length   // only the spoken part types itself out
        if (mode === "idle") { hideTimer.interval = 120; hideTimer.restart() }
        else if (mode === "done") { hideTimer.interval = Math.max(100, style.linger_s * 1000); hideTimer.restart() }
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

    // Reply text is revealed at roughly speaking pace (~17 characters a second at speed 1.2).
    property int revealed: 0
    Timer {
        interval: Math.max(10, 1000 / (root.style.reveal_cps || 18)); repeat: true
        running: root.mode === "speaking" && root.revealed < root.reply.length
        onTriggered: root.revealed = Math.min(root.reply.length, root.revealed + 1)
    }

    // Shared clock for the living parts of the orb and ribbon.
    property real phase: 0
    NumberAnimation on phase {
        from: 0; to: Math.PI * 2; duration: 1600
        loops: Animation.Infinite; running: root.shown
    }

    PanelWindow {
        anchors.top: root.fromTop
        anchors.bottom: !root.fromTop
        anchors.left: root.side === "left"
        anchors.right: root.side === "right"
        exclusionMode: ExclusionMode.Ignore   // start at the very screen edge, in line with the bar
        margins.left: root.side === "left" ? 8 : 0
        margins.right: root.side === "right" ? 8 : 0
        implicitWidth: 720 * root.sizeFactor
        implicitHeight: 280 * root.sizeFactor
        color: "transparent"
        visible: island.opacity > 0.01
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "zade"
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        mask: Region { item: island }         // clicks outside the island pass through

        // Soft depth under the island.
        MultiEffect {
            source: island
            anchors.fill: island
            opacity: island.opacity
            shadowEnabled: true
            shadowColor: "#000000"
            shadowOpacity: 0.45
            shadowBlur: 1.0
            shadowVerticalOffset: 6
            blurMax: 32
        }

        Rectangle {
            id: island
            // Slides in from the nearest screen edge.
            x: root.side === "left" ? 0 : root.side === "right" ? parent.width - width : (parent.width - width) / 2
            y: root.fromTop ? (root.shown ? 8 : -height - 12) : (root.shown ? parent.height - height - 8 : parent.height + 12)
            transformOrigin: root.fromTop ? (root.side === "left" ? Item.TopLeft : root.side === "right" ? Item.TopRight : Item.Top)
                                          : (root.side === "left" ? Item.BottomLeft : root.side === "right" ? Item.BottomRight : Item.Bottom)
            width: root.expanded ? Math.min(560, Math.max(300, textCol.implicitWidth + 84)) : 128
            height: root.expanded ? Math.max(62, textCol.implicitHeight + 34) : 44
            radius: root.expanded ? 26 : 22
            color: Qt.alpha(root.c.surface_container_low, 0.96)
            border.width: 1
            border.color: Qt.alpha(root.c.outline_variant, 0.55)
            opacity: root.shown ? 1 : 0
            scale: root.sizeFactor
            clip: true

            Behavior on width { SpringAnimation { spring: 3.2; damping: 0.32; epsilon: 0.3 } }
            Behavior on height { SpringAnimation { spring: 3.2; damping: 0.32; epsilon: 0.3 } }
            Behavior on radius { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
            Behavior on opacity { NumberAnimation { duration: 200 } }
            Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack } }
            Behavior on y { NumberAnimation { duration: 380; easing.type: Easing.OutBack; easing.overshoot: 0.9 } }

            // The orb: breathes, swells with your voice, orbits while thinking, ripples while speaking.
            Item {
                id: orb
                width: 22; height: 22
                x: root.expanded ? 20 : 16
                y: root.expanded ? 20 : (island.height - height) / 2
                Behavior on x { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }

                readonly property color tint: root.mode === "thinking" ? root.c.tertiary : root.accent

                // Voice glow: grows with mic level while listening.
                Rectangle {
                    anchors.centerIn: parent
                    width: parent.width; height: width; radius: width / 2
                    color: Qt.alpha(orb.tint, 0.22)
                    scale: root.mode === "listening" ? 1.05 + root.level * 0.55 : 1.1 + 0.06 * Math.sin(root.phase)
                    Behavior on scale { NumberAnimation { duration: 110 } }
                    Behavior on color { ColorAnimation { duration: 300 } }
                }

                // Speaking ripples.
                Repeater {
                    model: 2
                    Rectangle {
                        required property int index
                        anchors.centerIn: parent
                        width: orb.width; height: width; radius: width / 2
                        color: "transparent"
                        border.width: 1.5
                        border.color: orb.tint
                        visible: root.mode === "speaking"
                        property real t: ((root.phase / (Math.PI * 2)) + index * 0.5) % 1
                        scale: 1 + t * 1.2
                        opacity: (1 - t) * 0.55
                    }
                }

                // Thinking arc orbiting the core.
                Shape {
                    anchors.centerIn: parent
                    width: 30; height: 30
                    visible: root.mode === "thinking"
                    rotation: root.phase * 180 / Math.PI * 2
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: "transparent"
                        strokeColor: root.c.tertiary
                        strokeWidth: 2
                        capStyle: ShapePath.RoundCap
                        PathAngleArc { centerX: 15; centerY: 15; radiusX: 13; radiusY: 13; startAngle: 0; sweepAngle: 110 }
                    }
                }

                // Core.
                Rectangle {
                    anchors.centerIn: parent
                    width: 12; height: 12; radius: 6
                    scale: root.mode === "listening" ? 1 + root.level * 0.35 : 1
                    gradient: Gradient {
                        GradientStop { position: 0; color: Qt.lighter(orb.tint, 1.15) }
                        GradientStop { position: 1; color: root.mode === "thinking" ? root.c.tertiary
                                                            : (root.style.accent ? Qt.darker(root.accent, 1.35) : root.c.primary_container) }
                    }
                    Behavior on scale { NumberAnimation { duration: 110 } }
                }
            }

            // Voice ribbon in the pill: your mic level while listening, a slow wave while thinking.
            Row {
                anchors.verticalCenter: parent.verticalCenter
                x: 50
                spacing: 4
                opacity: root.expanded ? 0 : 1
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 150 } }
                Repeater {
                    model: 7
                    Rectangle {
                        required property int index
                        readonly property real centre: 1 - Math.abs(index - 3) / 4        // taller in the middle
                        // Each bar flickers a little differently so a steady voice still looks alive.
                        readonly property real jitter: 0.6 + 0.4 * Math.sin(root.phase * 5 + index * 2.1)
                        readonly property real wave: 0.5 + 0.5 * Math.sin(root.phase * 2 - index * 0.7)
                        // Listening: driven by your voice (flat when you're quiet). Thinking: a slow wave.
                        readonly property real amount: root.mode === "listening"
                            ? Math.min(1, root.level * (0.45 + 0.55 * centre) * jitter * 1.4)
                            : 0.35 * (0.4 + 0.6 * centre) * wave
                        width: 3; radius: 1.5
                        anchors.verticalCenter: parent.verticalCenter
                        // No Behavior here: the height changes every frame, and a restarting animation would freeze it.
                        height: 4 + 24 * amount
                        color: root.mode === "thinking" ? root.c.tertiary : root.accent
                        opacity: 0.5 + 0.5 * Math.max(centre, amount)
                    }
                }
            }

            // What you said (quiet context) and Zade's reply.
            Column {
                id: textCol
                x: 58
                y: 17
                spacing: 4
                opacity: root.expanded ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 200 } }

                Text {
                    visible: root.heard.length > 0 && root.style.show_heard
                    width: Math.min(implicitWidth, 476)
                    text: root.heard
                    color: Qt.alpha(root.c.on_surface_variant, 0.75)
                    font.family: "Readex Pro"
                    font.pixelSize: 13
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }

                // The full reply is laid out invisibly to size the card up front; the visible copy
                // reveals over it at speaking pace, so the card never jumps while text appears.
                Item {
                    visible: root.reply.length > 0
                    width: fullReply.width
                    height: fullReply.height

                    Text {
                        id: fullReply
                        opacity: 0
                        width: Math.min(implicitWidth, 476)
                        text: root.reply
                        font.family: "Readex Pro"
                        font.pixelSize: 15
                        lineHeight: 1.25
                        wrapMode: Text.WordWrap
                        maximumLineCount: 6
                        elide: Text.ElideRight
                    }

                    Text {
                        anchors.fill: parent
                        text: root.reply.slice(0, root.revealed)
                        color: root.c.on_surface
                        font: fullReply.font
                        lineHeight: 1.25
                        wrapMode: Text.WordWrap
                        maximumLineCount: 6
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }
}
