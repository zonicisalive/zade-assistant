// Zade's character: a drawn face that blinks, listens, thinks, talks and shows emotions.
// Used by the overlay (Overlay.qml) and the app's character editor (app.qml).
import QtQuick
import QtQuick.Shapes

Item {
    id: face

    property real size: 40
    property string mode: "idle"          // listening | thinking | speaking | done | idle
    property string emotion: "neutral"    // neutral happy excited sad confused surprised annoyed curious
    property real level: 0                // mic level while listening (0..1)
    property var look: ({ shape: "round", eyes: "round", mouth: "smile", color: "", blush: true })
    property color accent: "#A5D0BB"      // body colour when look.color is empty
    property color ink: "#1E352B"         // eyes, mouth, brows
    property color cheek: "#ff8fa3"

    implicitWidth: size
    implicitHeight: size

    // ── Expression targets ─────────────────────────────────────────────────
    // Each emotion is a set of numbers; the face animates between them.
    readonly property var table: ({
        neutral:   { open: 1.0,  arc: 0, curve: 0.25,  mouth: 0.0,  brow: 0,    browShow: 0, lookX: 0,     lookY: 0,    tilt: 0,  blush: 0 },
        happy:     { open: 1.0,  arc: 1, curve: 0.8,   mouth: 0.15, brow: 0,    browShow: 0, lookX: 0,     lookY: 0,    tilt: 0,  blush: 1 },
        excited:   { open: 1.15, arc: 0, curve: 0.85,  mouth: 0.45, brow: -0.3, browShow: 1, lookX: 0,     lookY: -0.1, tilt: 0,  blush: 1 },
        sad:       { open: 0.75, arc: 0, curve: -0.55, mouth: 0.0,  brow: -1,   browShow: 1, lookX: 0,     lookY: 0.25, tilt: 0,  blush: 0 },
        confused:  { open: 0.95, arc: 0, curve: -0.1,  mouth: 0.0,  brow: 0,    browShow: 1, lookX: 0.25,  lookY: 0,    tilt: 9,  blush: 0 },
        surprised: { open: 1.35, arc: 0, curve: 0.0,   mouth: 0.9,  brow: -0.4, browShow: 1, lookX: 0,     lookY: 0,    tilt: 0,  blush: 0 },
        annoyed:   { open: 0.5,  arc: 0, curve: -0.3,  mouth: 0.0,  brow: 1,    browShow: 1, lookX: 0,     lookY: 0,    tilt: 0,  blush: 0 },
        curious:   { open: 1.12, arc: 0, curve: 0.2,   mouth: 0.05, brow: -0.3, browShow: 1, lookX: -0.2,  lookY: -0.1, tilt: -9, blush: 0 }
    })
    readonly property var target: {
        const e = table[emotion] || table.neutral
        const t = Object.assign({}, e)
        if (mode === "listening") { t.open = Math.max(e.open, 1.05) + level * 0.15; t.mouth = 0; t.curve = Math.max(0.1, e.curve) }
        if (mode === "thinking") { t.lookX = 0.35; t.lookY = -0.4; t.curve = 0.05; t.mouth = 0; t.tilt = 7; t.arc = 0 }
        return t
    }

    // Animated values
    property real open: target.open
    property real arc: target.arc
    property real curve: target.curve
    property real mouthOpen: target.mouth
    property real brow: target.brow
    property real browShow: target.browShow
    property real lookX: target.lookX
    property real lookY: target.lookY
    property real tilt: target.tilt
    property real blushOn: target.blush
    Behavior on open { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    Behavior on arc { NumberAnimation { duration: 180 } }
    Behavior on curve { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    Behavior on mouthOpen { NumberAnimation { duration: 160 } }
    Behavior on brow { NumberAnimation { duration: 220 } }
    Behavior on browShow { NumberAnimation { duration: 220 } }
    Behavior on lookX { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
    Behavior on lookY { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
    Behavior on tilt { NumberAnimation { duration: 350; easing.type: Easing.OutBack } }
    Behavior on blushOn { NumberAnimation { duration: 300 } }

    // Life: a clock for talking/bouncing, and random blinks.
    property real t: 0
    NumberAnimation on t { from: 0; to: 1000; duration: 1000000; loops: Animation.Infinite; running: face.visible }
    property real blink: 1
    Timer {
        interval: 2600 + Math.random() * 2600; repeat: true; running: face.visible
        onTriggered: { interval = 2600 + Math.random() * 2600; blinkAnim.restart() }
    }
    SequentialAnimation {
        id: blinkAnim
        NumberAnimation { target: face; property: "blink"; to: 0.08; duration: 70 }
        NumberAnimation { target: face; property: "blink"; to: 1; duration: 110 }
    }

    // Talking: a syllable-like rhythm on top of the emotion's mouth shape.
    readonly property real talk: mode === "speaking" ? 0.12 + 0.55 * Math.abs(Math.sin(t * 9.3) * Math.sin(t * 3.1 + 1)) : 0
    readonly property real bounce: mode === "listening" ? level * 0.06 * size
                                 : emotion === "excited" ? Math.abs(Math.sin(t * 7)) * 0.05 * size : 0

    readonly property color body: look.color ? look.color : accent
    readonly property real s: size

    // ── Drawing ────────────────────────────────────────────────────────────
    Item {
        id: head
        width: face.s; height: face.s
        y: -face.bounce
        rotation: face.tilt
        scale: face.look.shape === "blob" ? 1 + 0.025 * Math.sin(face.t * 2.2) : 1

        Rectangle {
            anchors.fill: parent
            radius: face.look.shape === "squircle" ? width * 0.3 : width / 2
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.lighter(face.body, 1.18) }
                GradientStop { position: 1; color: Qt.darker(face.body, 1.12) }
            }
        }

        // Cheeks
        Repeater {
            model: [0.2, 0.8]
            Rectangle {
                required property real modelData
                width: face.s * 0.16; height: face.s * 0.08; radius: height / 2
                x: face.s * modelData - width / 2; y: face.s * 0.6
                color: face.cheek
                opacity: face.blushOn * (face.look.blush ? 0.55 : 0)
            }
        }

        // Eyes
        Repeater {
            model: [0.32, 0.68]
            Item {
                id: eye
                required property real modelData
                required property int index
                readonly property real w: face.look.eyes === "anime" ? face.s * 0.15 : face.look.eyes === "oval" ? face.s * 0.1 : face.s * 0.13
                readonly property real h: face.look.eyes === "anime" ? face.s * 0.2 : face.look.eyes === "oval" ? face.s * 0.17
                                         : face.look.eyes === "line" ? face.s * 0.035 : face.s * 0.13
                x: face.s * modelData - w / 2 + face.lookX * face.s * 0.06
                y: face.s * 0.42 - h / 2 + face.lookY * face.s * 0.06
                width: w; height: h

                // Open eye (hidden while happy: then it's a ^ arc)
                Rectangle {
                    anchors.centerIn: parent
                    width: parent.w
                    height: Math.max(face.s * 0.02, parent.h * Math.min(face.open, 1.4) * face.blink)
                    radius: Math.min(width, height) / 2
                    color: face.ink
                    opacity: 1 - face.arc
                    Rectangle {  // highlight
                        visible: face.look.eyes !== "line" && parent.height > face.s * 0.06
                        width: parent.width * (face.look.eyes === "anime" ? 0.42 : 0.32); height: width; radius: width / 2
                        x: parent.width * 0.52; y: parent.height * 0.14
                        color: "white"; opacity: 0.85
                    }
                }
                // Happy eye: ^ arc
                Shape {
                    anchors.centerIn: parent
                    width: parent.w * 1.2; height: parent.w * 1.2
                    opacity: face.arc
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: face.ink; strokeWidth: Math.max(1.5, face.s * 0.035)
                        fillColor: "transparent"; capStyle: ShapePath.RoundCap
                        PathAngleArc { centerX: eye.w * 0.6; centerY: eye.w * 0.75; radiusX: eye.w * 0.45; radiusY: eye.w * 0.4
                                       startAngle: 200; sweepAngle: 140 }
                    }
                }
                // Brow
                Rectangle {
                    width: face.s * 0.14; height: Math.max(1.5, face.s * 0.03); radius: height / 2
                    x: (parent.w - width) / 2
                    // confused: one brow up; sad: raised inner ends; annoyed: lowered inner ends
                    y: -face.s * 0.09 - (face.emotion === "confused" && eye.index === 0 ? face.s * 0.05 : 0)
                    rotation: (eye.index === 0 ? 1 : -1) * face.brow * 18
                    color: face.ink
                    opacity: face.browShow
                }
            }
        }

        // Mouth: an upper and a lower curve; closed = one line, open = filled shape.
        Shape {
            id: mouth
            // Narrows as it opens wide, so surprise reads as a round "O".
            readonly property real w: (face.look.mouth === "small" ? face.s * 0.16 : face.s * 0.28) * (1 - 0.45 * Math.min(1, face.mouthOpen))
            readonly property real bend: face.curve * face.s * 0.09
            readonly property real gap: (face.mouthOpen + face.talk) * face.s * 0.13
            x: face.s * 0.5 - w / 2; y: face.s * 0.66
            width: w; height: face.s * 0.3
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: face.ink
                strokeWidth: Math.max(1.5, face.s * 0.035)
                fillColor: mouth.gap > face.s * 0.01 ? face.ink : "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                startX: 0; startY: 0
                // cat mouth: a small "w"; otherwise one smooth curve
                PathQuad { x: face.look.mouth === "cat" ? mouth.w / 2 : mouth.w; y: 0
                           controlX: face.look.mouth === "cat" ? mouth.w / 4 : mouth.w / 2
                           controlY: face.look.mouth === "cat" ? mouth.bend + face.s * 0.04 : mouth.bend }
                PathQuad { x: mouth.w; y: 0
                           controlX: face.look.mouth === "cat" ? mouth.w * 0.75 : mouth.w
                           controlY: face.look.mouth === "cat" ? mouth.bend + face.s * 0.04 : 0 }
                PathQuad { x: 0; y: 0; controlX: mouth.w / 2; controlY: mouth.bend + mouth.gap * 2 }
            }
        }
    }
}
