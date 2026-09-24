// Zade's character: an expressive cartoon face that blinks, listens, thinks, talks and shows emotions.
// Used by the overlay (Overlay.qml) and the app's character editor (app.qml).
//
// The face is a small rig: eyes (white, iris, pupil, highlights, eyelids), brows, a mouth with teeth
// and tongue, cheeks, and effects (tears, sweat, hearts, sparkles, zzz, anger mark, question mark).
// Each emotion is a set of rig values; the face animates between them.
import QtQuick
import QtQuick.Effects
import QtQuick.Shapes

Item {
    id: face

    property real size: 40
    property string mode: "idle"          // listening | thinking | speaking | done | idle
    property string emotion: "neutral"
    property real level: 0                // mic level while listening (0..1)
    property var look: ({ type: "blob", shape: "round", eyes: "round", mouth: "smile", color: "", blush: true })
    property color accent: "#A5D0BB"      // body colour when look.color is empty
    property color ink: "#1E352B"         // brows and mouth outline (robot: glowing features instead)

    implicitWidth: size
    implicitHeight: size

    readonly property string kind: look.type || "blob"
    readonly property bool robot: kind === "robot"
    readonly property bool hasEars: kind === "cat" || kind === "bunny" || kind === "bear"
    readonly property color body: robot ? "#23272e" : (look.color ? look.color : accent)
    readonly property color glow: look.color ? look.color : accent                    // robot features
    readonly property color lineColor: robot ? glow : ink
    readonly property color lidColor: robot ? body : Qt.darker(body, 1.1)
    readonly property color irisColor: robot ? glow : Qt.darker(body, 2.6)
    readonly property color mouthFill: robot ? Qt.alpha(glow, 0.35) : Qt.darker(body, 4.2)
    readonly property real hs: size * (hasEars ? 0.8 : 1.0)                          // head size (ears need room)
    // Level of detail: in the 36-48 px overlay, bold lines, big dark eyes and one main effect read best.
    readonly property bool small: hs < 64
    readonly property real sw: Math.max(small ? 2 : 1.5, hs * (small ? 0.06 : 0.042))   // one stroke weight for all lines

    // Drawn effect icons (instead of font glyphs, so they match the face's line work)
    component Heart: Shape {
        id: heartShape
        property color fill: "#ff4d6d"
        property real d: 10
        width: d; height: d
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            fillColor: heartShape.fill; strokeColor: "transparent"
            startX: heartShape.d * 0.5; startY: heartShape.d * 0.27
            PathCubic { x: heartShape.d * 0.05; y: heartShape.d * 0.36; control1X: heartShape.d * 0.42; control1Y: heartShape.d * 0.02; control2X: heartShape.d * 0.05; control2Y: heartShape.d * 0.05 }
            PathCubic { x: heartShape.d * 0.5; y: heartShape.d * 0.95; control1X: heartShape.d * 0.05; control1Y: heartShape.d * 0.66; control2X: heartShape.d * 0.42; control2Y: heartShape.d * 0.8 }
            PathCubic { x: heartShape.d * 0.95; y: heartShape.d * 0.36; control1X: heartShape.d * 0.58; control1Y: heartShape.d * 0.8; control2X: heartShape.d * 0.95; control2Y: heartShape.d * 0.66 }
            PathCubic { x: heartShape.d * 0.5; y: heartShape.d * 0.27; control1X: heartShape.d * 0.95; control1Y: heartShape.d * 0.05; control2X: heartShape.d * 0.58; control2Y: heartShape.d * 0.02 }
        }
    }
    component Star: Shape {   // points: 5 = star, 4 = sparkle
        id: starShape
        property color fill: "#ffd23f"
        property real d: 10
        property int points: 5
        property real inner: 0.42
        width: d; height: d
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            fillColor: starShape.fill; strokeColor: "transparent"
            PathPolyline {
                path: {
                    const pts = [], n = starShape.points * 2, r = starShape.d / 2
                    for (let i = 0; i <= n; i++) {
                        const a = -Math.PI / 2 + i * Math.PI / starShape.points
                        const k = i % 2 === 0 ? 1 : starShape.inner
                        pts.push(Qt.point(r + Math.cos(a) * r * k, r + Math.sin(a) * r * k))
                    }
                    return pts
                }
            }
        }
    }

    // ── Expression rig ─────────────────────────────────────────────────────
    readonly property var base: ({ eye: "open", lidTop: 0.05, lidTilt: 0, lidBottom: 0, pupil: 1, lookX: 0, lookY: 0,
                                   browY: 0, browTilt: 0, browShow: 0.75, browAsym: 0, mouth: "smile", curve: 0.2,
                                   mopen: 0, mwidth: 1, smirk: 0, tilt: 0, bounce: 0, blush: 0, fx: "", tongue: false })
    readonly property var table: ({
        neutral:     {},
        happy:       { eye: "happy", browY: -0.3, mouth: "grin", curve: 0.75, mopen: 0.3, blush: 0.7 },
        excited:     { lidTop: 0, pupil: 1.2, browY: -0.8, mouth: "grin", curve: 0.8, mopen: 0.55, mwidth: 1.05, bounce: 1, blush: 0.8, fx: "sparkle" },
        laughing:    { eye: "happy", browY: -0.5, mouth: "grin", curve: 0.9, mopen: 0.8, mwidth: 1.15, bounce: 2, blush: 0.9, fx: "joy", tongue: true },
        love:        { eye: "heart", browY: -0.3, curve: 0.7, mopen: 0.15, blush: 1, fx: "hearts" },
        sad:         { lidTop: 0.32, lidTilt: -1, pupil: 1.1, lookY: 0.35, browY: 0.1, browTilt: -1, curve: -0.7, mwidth: 0.8 },
        crying:      { lidTop: 0.35, lidTilt: -1, lidBottom: 0.15, lookY: 0.3, browTilt: -1.2, curve: -0.8, mopen: 0.35, fx: "tears" },
        confused:    { pupil: 0.9, lookX: 0.3, lookY: -0.2, browAsym: 1, browTilt: 0.2, mouth: "wavy", tilt: 10, fx: "question" },
        surprised:   { lidTop: 0, pupil: 0.6, browY: -1.2, mouth: "o", mopen: 0.8 },
        amazed:      { eye: "star", browY: -1, mouth: "o", mopen: 0.55, blush: 0.3, fx: "sparkle" },
        annoyed:     { lidTop: 0.5, lidTilt: 0.3, lookX: 0.35, browY: 0.3, browTilt: 0.6, mouth: "flat", curve: -0.15, mwidth: 0.7 },
        angry:       { lidTop: 0.38, lidTilt: 1.2, pupil: 0.8, browY: 0.5, browTilt: 1.4, mouth: "grin", curve: -0.5, mopen: 0.3, fx: "anger" },
        curious:     { lidTop: 0.05, pupil: 1.1, lookX: -0.3, browAsym: -1, mouth: "smile", curve: 0.15, mwidth: 0.6, tilt: -10 },
        smug:        { lidTop: 0.45, lidBottom: 0.1, lookX: 0.25, browAsym: 1, browTilt: -0.2, mouth: "smirk", curve: 0.4, smirk: 1, mwidth: 0.8 },
        sleepy:      { eye: "calm", browY: 0.2, mouth: "o", mopen: 0.15, mwidth: 0.6, tilt: 10, fx: "zzz" },
        embarrassed: { lidTop: 0.2, lookX: -0.4, lookY: 0.35, browTilt: -0.6, mouth: "wavy", mwidth: 0.6, blush: 1, fx: "sweat" },
        nervous:     { lidTop: 0, pupil: 0.65, browTilt: -0.9, browY: -0.4, mouth: "grin", curve: -0.1, mopen: 0.3, mwidth: 1.1, fx: "sweat" },
        wink:        { eye: "wink", browAsym: 0.6, curve: 0.7, mopen: 0.1, smirk: 0.6, blush: 0.4 },
        playful:     { eye: "wink", mouth: "tongue", curve: 0.6, blush: 0.5, tilt: 6 }
    })
    readonly property var rig: {
        const r = Object.assign({}, base, table[emotion] || {})
        if (mode === "listening")
            Object.assign(r, { lidTop: 0, pupil: 1.1 + level * 0.15, browY: -0.3 - level * 0.4, mouth: "smile",
                               curve: Math.max(0.25, r.curve), mopen: 0 })
        if (mode === "thinking")
            Object.assign(r, { eye: "open", lookX: 0.45, lookY: -0.5, browAsym: 0.8, mouth: "smirk", curve: 0.1,
                               smirk: 0.7, mwidth: 0.6, mopen: 0, tilt: 8, fx: "think" })
        return r
    }

    // Animated rig values
    property real lidTop: rig.lidTop
    property real lidTilt: rig.lidTilt
    property real lidBottom: rig.lidBottom
    property real pupil: rig.pupil
    property real lookX: rig.lookX
    property real lookY: rig.lookY
    property real browY: rig.browY
    property real browTilt: rig.browTilt
    property real browShow: rig.browShow
    property real browAsym: rig.browAsym
    property real curve: rig.curve
    property real mopen: rig.mopen
    property real mwidth: rig.mwidth
    property real smirk: rig.smirk
    property real tilt: rig.tilt
    property real blushOn: rig.blush
    Behavior on lidTop { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    Behavior on lidTilt { NumberAnimation { duration: 200 } }
    Behavior on lidBottom { NumberAnimation { duration: 200 } }
    Behavior on pupil { NumberAnimation { duration: 180 } }
    Behavior on lookX { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
    Behavior on lookY { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
    Behavior on browY { NumberAnimation { duration: 200; easing.type: Easing.OutBack } }
    Behavior on browTilt { NumberAnimation { duration: 220 } }
    Behavior on browShow { NumberAnimation { duration: 200 } }
    Behavior on browAsym { NumberAnimation { duration: 220 } }
    Behavior on curve { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    Behavior on mopen { NumberAnimation { duration: 160 } }
    Behavior on mwidth { NumberAnimation { duration: 200 } }
    Behavior on smirk { NumberAnimation { duration: 220 } }
    Behavior on tilt { NumberAnimation { duration: 380; easing.type: Easing.OutBack } }
    Behavior on blushOn { NumberAnimation { duration: 300 } }

    // Life: a clock, random blinks, talking rhythm, bounce/shake.
    property real t: 0
    NumberAnimation on t { from: 0; to: 1000; duration: 1000000; loops: Animation.Infinite; running: face.visible }
    property real blink: 1
    Timer {
        interval: 2400 + Math.random() * 2800; repeat: true; running: face.visible
        onTriggered: { interval = 2400 + Math.random() * 2800; blinkAnim.restart() }
    }
    SequentialAnimation {
        id: blinkAnim
        NumberAnimation { target: face; property: "blink"; to: 0; duration: 70 }
        NumberAnimation { target: face; property: "blink"; to: 1; duration: 120 }
    }
    readonly property real talk: mode === "speaking" ? 0.15 + 0.6 * Math.abs(Math.sin(t * 9.3) * Math.sin(t * 3.1 + 1)) : 0
    readonly property real bob: mode === "listening" ? level * 0.06 * size
                              : rig.bounce === 1 ? Math.abs(Math.sin(t * 7)) * 0.06 * size
                              : kind === "ghost" ? (Math.sin(t * 2) * 0.03 + 0.03) * size : 0
    readonly property real shake: rig.bounce === 2 ? Math.sin(t * 16) * 4 : 0

    function eyeMode(index) {
        if (rig.eye === "wink") return index === 0 ? "happy" : "open"
        return rig.eye
    }

    // ── Ears / antenna (behind the head) ───────────────────────────────────
    Item {
        id: head
        width: face.hs; height: face.hs
        x: (face.size - width) / 2
        y: face.size - height - face.bob
        rotation: face.tilt + face.shake
        transformOrigin: Item.Bottom

        // Cat ears
        Repeater {
            model: face.kind === "cat" ? [-1, 1] : []
            Shape {
                required property real modelData
                width: face.hs * 0.34; height: face.hs * 0.36
                x: face.hs / 2 + modelData * face.hs * 0.3 - width / 2; y: -face.hs * 0.16
                rotation: modelData * 14
                preferredRendererType: Shape.CurveRenderer
                ShapePath { fillColor: Qt.darker(face.body, 1.05); strokeColor: "transparent"
                    startX: 0; startY: face.hs * 0.36
                    PathLine { x: face.hs * 0.17; y: 0 }
                    PathLine { x: face.hs * 0.34; y: face.hs * 0.36 } }
                ShapePath { fillColor: "#f4a6b8"; strokeColor: "transparent"
                    startX: face.hs * 0.08; startY: face.hs * 0.34
                    PathLine { x: face.hs * 0.17; y: face.hs * 0.1 }
                    PathLine { x: face.hs * 0.26; y: face.hs * 0.34 } }
            }
        }
        // Bunny ears
        Repeater {
            model: face.kind === "bunny" ? [-1, 1] : []
            Rectangle {
                required property real modelData
                width: face.hs * 0.2; height: face.hs * 0.55; radius: width / 2
                x: face.hs / 2 + modelData * face.hs * 0.18 - width / 2; y: -face.hs * 0.4
                rotation: modelData * (10 + (face.rig.bounce ? 6 * Math.sin(face.t * 6) : 0))
                transformOrigin: Item.Bottom
                color: Qt.darker(face.body, 1.04)
                Rectangle { anchors.centerIn: parent; width: parent.width * 0.5; height: parent.height * 0.75
                            radius: width / 2; color: "#f4a6b8"; opacity: 0.85 }
            }
        }
        // Bear ears
        Repeater {
            model: face.kind === "bear" ? [-1, 1] : []
            Rectangle {
                required property real modelData
                width: face.hs * 0.3; height: width; radius: width / 2
                x: face.hs / 2 + modelData * face.hs * 0.34 - width / 2; y: -face.hs * 0.08
                color: Qt.darker(face.body, 1.06)
                Rectangle { anchors.centerIn: parent; width: parent.width * 0.55; height: width; radius: width / 2
                            color: Qt.darker(face.body, 1.3) }
            }
        }
        // Robot antenna
        Item {
            visible: face.robot
            x: face.hs / 2; y: -face.hs * 0.2
            Rectangle { x: -1; width: 2; height: face.hs * 0.2; color: "#5b616b" }
            Rectangle { x: -face.hs * 0.05; y: -face.hs * 0.07; width: face.hs * 0.1; height: width; radius: width / 2
                        color: face.glow; opacity: 0.55 + 0.45 * Math.abs(Math.sin(face.t * 3)) }
        }

        // ── Head ───────────────────────────────────────────────────────────
        Rectangle {
            visible: face.kind !== "ghost"
            anchors.fill: parent
            radius: face.robot ? width * 0.26 : face.look.shape === "squircle" ? width * 0.3 : width / 2
            scale: face.look.shape === "blob" && !face.robot ? 1 + 0.025 * Math.sin(face.t * 2.2) : 1
            border.width: face.robot ? Math.max(1, face.hs * 0.025) : 0
            border.color: Qt.alpha(face.glow, 0.6)
            gradient: Gradient {
                GradientStop { position: 0; color: face.robot ? "#2c3139" : Qt.lighter(face.body, 1.2) }
                GradientStop { position: 1; color: face.robot ? "#1b1e23" : Qt.darker(face.body, 1.12) }
            }
            Rectangle {  // soft top-left shine
                visible: !face.robot
                x: parent.width * 0.16; y: parent.height * 0.08
                width: parent.width * 0.34; height: parent.height * 0.16; radius: height / 2
                rotation: -20; color: "white"; opacity: 0.18
            }
        }
        // Ghost body: round top, wavy bottom
        Shape {
            visible: face.kind === "ghost"
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillGradient: LinearGradient { x1: 0; y1: 0; x2: 0; y2: face.hs
                    GradientStop { position: 0; color: Qt.lighter(face.body, 1.25) }
                    GradientStop { position: 1; color: Qt.darker(face.body, 1.05) } }
                strokeColor: "transparent"
                startX: 0; startY: face.hs * 0.5
                PathArc { x: face.hs; y: face.hs * 0.5; radiusX: face.hs / 2; radiusY: face.hs / 2 }
                PathLine { x: face.hs; y: face.hs * 0.9 }
                PathQuad { x: face.hs * 0.75; y: face.hs * 0.9; controlX: face.hs * 0.875; controlY: face.hs * (1.0 + 0.04 * Math.sin(face.t * 4)) }
                PathQuad { x: face.hs * 0.5; y: face.hs * 0.9; controlX: face.hs * 0.625; controlY: face.hs * (0.8 - 0.04 * Math.sin(face.t * 4)) }
                PathQuad { x: face.hs * 0.25; y: face.hs * 0.9; controlX: face.hs * 0.375; controlY: face.hs * (1.0 + 0.04 * Math.sin(face.t * 4)) }
                PathQuad { x: 0; y: face.hs * 0.9; controlX: face.hs * 0.125; controlY: face.hs * (0.8 - 0.04 * Math.sin(face.t * 4)) }
                PathLine { x: 0; y: face.hs * 0.5 }
            }
        }

        // Cheeks
        Repeater {
            model: [0.2, 0.8]
            Rectangle {
                required property real modelData
                width: face.hs * 0.18; height: face.hs * 0.09; radius: height / 2
                x: face.hs * modelData - width / 2; y: face.hs * 0.6
                color: face.robot ? face.glow : "#ff7f9b"
                opacity: face.blushOn * (face.look.blush === false ? 0 : 0.5)
            }
        }

        // Cat whiskers
        Repeater {
            model: face.kind === "cat" ? [[-1, -0.03], [-1, 0.03], [1, -0.03], [1, 0.03]] : []
            Rectangle {
                required property var modelData
                width: face.hs * 0.18; height: Math.max(1, face.hs * 0.012)
                x: modelData[0] < 0 ? face.hs * 0.02 : face.hs * 0.8
                y: face.hs * (0.66 + modelData[1])
                rotation: modelData[0] * modelData[1] * 250
                color: face.lineColor; opacity: 0.55
            }
        }

        // ── Eyes ───────────────────────────────────────────────────────────
        Repeater {
            model: [0.31, 0.69]
            Item {
                id: eye
                required property real modelData
                required property int index
                readonly property string em: face.eyeMode(index)
                readonly property real ew: face.hs * (face.look.eyes === "anime" ? 0.23 : face.look.eyes === "oval" ? 0.15 : 0.19)
                readonly property real eh: face.hs * (face.look.eyes === "anime" ? 0.27 : face.look.eyes === "oval" ? 0.25
                                                     : face.look.eyes === "line" ? 0.11 : 0.2)
                readonly property real side: index === 0 ? 1 : -1   // inner corner direction for lid/brow tilt
                width: ew; height: eh
                x: face.hs * modelData - ew / 2
                y: face.hs * 0.44 - eh / 2

                // Open eye: masked so lids and iris stay inside the eye shape
                Item {
                    id: eyeContent
                    anchors.fill: parent
                    visible: false
                    layer.enabled: true
                    layer.smooth: true
                    layer.samples: 8
                    Rectangle { anchors.fill: parent; color: face.robot ? face.glow : "#fbfaf6" }
                    Rectangle {  // iris
                        id: iris
                        visible: !face.robot
                        width: Math.min(eye.ew, eye.eh) * (face.small ? 1.15 : 0.86) * face.pupil; height: width; radius: width / 2
                        x: (eye.ew - width) / 2 + face.lookX * eye.ew * 0.24
                        y: (eye.eh - height) / 2 + face.lookY * eye.eh * 0.24
                        color: face.irisColor
                        Rectangle { anchors.centerIn: parent; width: parent.width * 0.52; height: width; radius: width / 2; color: "#0b0b0c" }
                        Rectangle { x: parent.width * 0.55; y: parent.height * 0.12; width: parent.width * 0.34; height: width; radius: width / 2; color: "white" }
                        Rectangle { visible: !face.small; x: parent.width * 0.2; y: parent.height * 0.62; width: parent.width * 0.16; height: width; radius: width / 2; color: "white"; opacity: 0.8 }
                    }
                    Rectangle {  // upper lid (also blinks)
                        width: eye.ew * 1.6; height: eye.eh * 1.2
                        x: -eye.ew * 0.3
                        y: -height + eye.eh * Math.max(face.lidTop, 1 - face.blink) + eye.eh * 0.02
                        rotation: face.lidTilt * 16 * eye.side
                        color: face.lidColor
                    }
                    Rectangle {  // lower lid
                        width: eye.ew * 1.6; height: eye.eh
                        x: -eye.ew * 0.3
                        y: eye.eh * (1 - face.lidBottom)
                        rotation: -face.lidTilt * 6 * eye.side
                        color: face.lidColor
                    }
                }
                Rectangle {
                    id: eyeMask
                    anchors.fill: parent
                    radius: face.robot ? Math.min(width, height) * 0.3 : Math.min(width, height) / 2
                    antialiasing: true
                    visible: false
                    layer.enabled: true
                    layer.smooth: true
                    layer.samples: 8
                }
                MultiEffect {
                    anchors.fill: parent
                    source: eyeContent
                    maskEnabled: true
                    maskSource: eyeMask
                    maskThresholdMin: 0.4
                    maskSpreadAtMin: 0.2
                    opacity: eye.em === "open" ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 140 } }
                }
                // ^ happy / closed eye
                Shape {
                    anchors.centerIn: parent
                    width: eye.ew; height: eye.eh
                    opacity: eye.em === "happy" || eye.em === "calm" ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 140 } }
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: face.lineColor; strokeWidth: face.sw
                        fillColor: "transparent"; capStyle: ShapePath.RoundCap
                        startX: eye.ew * 0.05; startY: eye.eh * 0.55
                        PathQuad { x: eye.ew * 0.95; y: eye.eh * 0.55; controlX: eye.ew * 0.5
                                   controlY: eye.em === "calm" ? eye.eh * 0.95 : eye.eh * 0.05 }
                    }
                }
                Heart {  // heart eyes
                    visible: eye.em === "heart"
                    d: eye.ew * 1.25
                    anchors.centerIn: parent
                    scale: 1 + 0.08 * Math.sin(face.t * 8)
                }
                Star {  // star eyes
                    visible: eye.em === "star"
                    d: eye.ew * 1.3
                    anchors.centerIn: parent
                    rotation: Math.sin(face.t * 3) * 8
                }

                // Brow
                Rectangle {
                    visible: !face.robot || face.browShow > 0.9
                    width: eye.ew * 1.05; height: face.sw * 1.15; radius: height / 2
                    x: (eye.ew - width) / 2 + eye.side * face.browAsym * 0
                    y: -face.hs * 0.07 + face.browY * face.hs * 0.045 + face.browAsym * (eye.index === 0 ? -1 : 1) * face.hs * 0.035
                    rotation: face.browTilt * 20 * eye.side + face.browAsym * (eye.index === 0 ? -8 : 8)
                    color: face.lineColor
                    opacity: face.browShow
                }
            }
        }

        // ── Mouth ──────────────────────────────────────────────────────────
        Item {
            id: mouth
            readonly property string mk: face.rig.mouth === "smile" && face.look.mouth === "cat" ? "cat" : face.rig.mouth
            readonly property real w: Math.min(face.hs * 0.42, face.hs * (face.look.mouth === "small" ? 0.2 : 0.32) * face.mwidth)
            readonly property real bend: face.curve * face.hs * 0.1
            readonly property real gap: (face.mopen + face.talk) * face.hs * 0.13
            readonly property real tip: face.smirk * face.hs * 0.05   // smirk lifts the right corner
            x: face.hs * 0.5 - w / 2
            y: face.hs * 0.68
            width: w; height: face.hs * 0.3

            readonly property bool isOpen: mouth.mk !== "o" && mouth.mk !== "wavy" && mouth.mk !== "cat" && mouth.gap > face.hs * 0.012
            // Lower-lip depth, capped so the open mouth always stays above the chin (the curve reaches ~0.75x).
            readonly property real depth: Math.min(mouth.bend + mouth.gap * 2.4, face.hs * (face.small ? 0.26 : 0.3))
            readonly property real teethH: mouth.mk === "grin" ? Math.min(mouth.gap * 0.55, face.hs * 0.055) : 0

            // Open mouth: dark inside, teeth along the upper lip, tongue at the bottom, all clipped to the mouth.
            Shape {
                id: mouthMask
                visible: false
                layer.enabled: true; layer.smooth: true; layer.samples: 8
                width: mouth.w; height: face.hs * 0.4
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: "white"; strokeColor: "transparent"
                    startX: 0; startY: 0
                    PathQuad { x: mouth.w; y: -mouth.tip; controlX: mouth.w / 2; controlY: mouth.bend }
                    PathCubic { x: 0; y: 0; control1X: mouth.w * 0.97; control1Y: mouth.depth; control2X: mouth.w * 0.03; control2Y: mouth.depth }
                }
            }
            Item {
                id: mouthInside
                visible: false
                layer.enabled: true; layer.smooth: true; layer.samples: 8
                width: mouth.w; height: face.hs * 0.4
                Rectangle { anchors.fill: parent; color: face.mouthFill }
                Shape {  // teeth, following the upper lip
                    visible: mouth.teethH > 0 && !face.robot
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: "#fbfaf6"; strokeColor: "transparent"
                        startX: 0; startY: 0
                        PathQuad { x: mouth.w; y: -mouth.tip; controlX: mouth.w / 2; controlY: mouth.bend }
                        PathLine { x: mouth.w; y: -mouth.tip + mouth.teethH }
                        PathQuad { x: 0; y: mouth.teethH; controlX: mouth.w / 2; controlY: mouth.bend + mouth.teethH }
                        PathLine { x: 0; y: 0 }
                    }
                }
                Rectangle {  // gap between the front teeth
                    visible: mouth.teethH > face.hs * 0.02 && !face.robot
                    x: mouth.w / 2 - 0.5; y: mouth.bend / 2
                    width: 1; height: mouth.teethH * 0.9
                    color: Qt.darker("#fbfaf6", 1.25)
                }
                Rectangle {  // tongue (laughing only): a wide mound at the bottom; only its top curve shows
                    visible: !face.robot && face.rig.tongue
                    width: mouth.w * 0.82; height: mouth.gap * 2.2 + face.hs * 0.04; radius: height / 2
                    x: (mouth.w - width) / 2
                    y: 0.75 * mouth.depth - height * 0.42
                    color: "#ee6b85"
                    Rectangle {  // groove down the middle
                        anchors.horizontalCenter: parent.horizontalCenter; y: parent.height * 0.08
                        width: Math.max(1, face.hs * 0.012); height: parent.height * 0.22; radius: width / 2
                        color: Qt.darker("#ee6b85", 1.3)
                    }
                }
            }
            MultiEffect {
                visible: mouth.isOpen
                width: mouth.w; height: face.hs * 0.4
                source: mouthInside
                maskEnabled: true; maskSource: mouthMask
                maskThresholdMin: 0.4; maskSpreadAtMin: 0.2
            }
            // Lip line (closed smile / frown / smirk / cat, and the outline of an open mouth)
            Shape {
                visible: mouth.mk !== "o" && mouth.mk !== "wavy"
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: face.lineColor
                    strokeWidth: face.sw
                    fillColor: "transparent"
                    capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                    startX: 0; startY: 0
                    PathQuad { x: mouth.mk === "cat" ? mouth.w / 2 : mouth.w; y: mouth.mk === "cat" ? 0 : -mouth.tip
                               controlX: mouth.mk === "cat" ? mouth.w / 4 : mouth.w / 2
                               controlY: mouth.mk === "cat" ? mouth.bend + face.hs * 0.05 : mouth.bend }
                    PathQuad { x: mouth.w; y: -mouth.tip
                               controlX: mouth.mk === "cat" ? mouth.w * 0.75 : mouth.w
                               controlY: mouth.mk === "cat" ? mouth.bend + face.hs * 0.05 : -mouth.tip }
                    PathCubic { x: 0; y: 0
                                control1X: mouth.w * 0.97; control1Y: mouth.isOpen ? mouth.depth : -mouth.tip
                                control2X: mouth.w * 0.03; control2Y: mouth.isOpen ? mouth.depth : 0 }
                }
            }
            // playful tongue sticking out of the lip, off-centre, with an outline and a groove
            Shape {
                id: tongueOut
                visible: mouth.mk === "tongue"
                readonly property real tw: mouth.w * 0.42
                readonly property real th: face.hs * 0.17
                x: mouth.w * 0.46; y: mouth.bend * 0.5 - face.hs * 0.01
                rotation: -8
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: "#ee6b85"
                    strokeColor: face.lineColor; strokeWidth: face.sw * 0.6
                    joinStyle: ShapePath.RoundJoin
                    startX: 0; startY: 0
                    PathLine { x: 0; y: tongueOut.th - tongueOut.tw / 2 }
                    PathArc { x: tongueOut.tw; y: tongueOut.th - tongueOut.tw / 2; radiusX: tongueOut.tw / 2; radiusY: tongueOut.tw / 2
                              direction: PathArc.Counterclockwise }  // round bottom, curving downward
                    PathLine { x: tongueOut.tw; y: 0 }
                }
                ShapePath {  // groove
                    strokeColor: Qt.darker("#ee6b85", 1.35); strokeWidth: Math.max(1, face.hs * 0.015)
                    fillColor: "transparent"; capStyle: ShapePath.RoundCap
                    startX: tongueOut.tw / 2; startY: tongueOut.th * 0.1
                    PathLine { x: tongueOut.tw / 2; y: tongueOut.th * 0.55 }
                }
            }
            // round "O"
            Rectangle {
                visible: mouth.mk === "o"
                readonly property real o: face.mopen + face.talk
                width: face.hs * (0.09 + 0.07 * o); height: face.hs * (0.08 + 0.11 * o); radius: width / 2
                x: (mouth.w - width) / 2; y: -height * 0.3
                color: face.mouthFill
                border.width: face.sw; border.color: face.lineColor
            }
            // wavy (confused / nervous / embarrassed)
            Shape {
                visible: mouth.mk === "wavy"
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: face.lineColor; strokeWidth: face.sw
                    fillColor: "transparent"; capStyle: ShapePath.RoundCap
                    startX: 0; startY: 0
                    PathQuad { x: mouth.w / 3; y: 0; controlX: mouth.w / 6; controlY: -face.hs * 0.05 - face.talk * face.hs * 0.04 }
                    PathQuad { x: mouth.w * 2 / 3; y: 0; controlX: mouth.w / 2; controlY: face.hs * 0.05 + face.talk * face.hs * 0.04 }
                    PathQuad { x: mouth.w; y: 0; controlX: mouth.w * 5 / 6; controlY: -face.hs * 0.05 }
                }
            }
        }

        // ── Effects ────────────────────────────────────────────────────────
        // Tears
        Repeater {
            model: face.rig.fx === "tears" ? [0.26, 0.74] : []
            Rectangle {
                required property real modelData
                required property int index
                readonly property real p: ((face.t * 0.9) + index * 0.5) % 1
                width: face.hs * 0.07; height: face.hs * 0.1; radius: width / 2
                x: face.hs * modelData - width / 2
                y: face.hs * (0.5 + p * 0.38)
                color: "#8fd3ff"; opacity: 0.9 * (1 - p)
            }
        }
        // Tears of joy at the outer eye corners (laughing)
        Repeater {
            model: face.rig.fx === "joy" ? [0.12, 0.88] : []
            Rectangle {
                required property real modelData
                width: face.hs * 0.06; height: face.hs * 0.09; radius: width / 2
                x: face.hs * modelData - width / 2
                y: face.hs * (0.47 + 0.03 * Math.abs(Math.sin(face.t * 5)))
                color: "#8fd3ff"; opacity: 0.9
            }
        }
        // Sweat drop
        Rectangle {
            visible: face.rig.fx === "sweat"
            width: face.hs * 0.08; height: face.hs * 0.12; radius: width / 2
            x: face.hs * 0.84; y: face.hs * (0.14 + 0.04 * Math.abs(Math.sin(face.t * 3)))
            color: "#8fd3ff"; opacity: 0.9
            Rectangle { x: parent.width * 0.25; y: parent.height * 0.2; width: parent.width * 0.3; height: width; radius: width / 2; color: "white"; opacity: 0.7 }
        }
        // Anger mark: four curved corners around a gap (the comic "vein"), pulsing
        Shape {
            visible: face.rig.fx === "anger"
            readonly property real d: face.hs * 0.24
            x: face.hs * 0.76; y: -face.hs * 0.02
            width: d; height: d
            scale: 0.85 + 0.2 * Math.abs(Math.sin(face.t * 6))
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "#ff4d4d"; strokeWidth: face.sw; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                startX: face.hs * 0.12 * (1 + -1 * 0.25); startY: face.hs * 0.12 * (1 + -1 * 0.95)
                PathQuad { x: face.hs * 0.12 * (1 + -1 * 0.95); y: face.hs * 0.12 * (1 + -1 * 0.25)
                           controlX: face.hs * 0.12 * (1 + -1 * 0.3); controlY: face.hs * 0.12 * (1 + -1 * 0.3) }
            }
            ShapePath {
                strokeColor: "#ff4d4d"; strokeWidth: face.sw; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                startX: face.hs * 0.12 * (1 + 1 * 0.25); startY: face.hs * 0.12 * (1 + -1 * 0.95)
                PathQuad { x: face.hs * 0.12 * (1 + 1 * 0.95); y: face.hs * 0.12 * (1 + -1 * 0.25)
                           controlX: face.hs * 0.12 * (1 + 1 * 0.3); controlY: face.hs * 0.12 * (1 + -1 * 0.3) }
            }
            ShapePath {
                strokeColor: "#ff4d4d"; strokeWidth: face.sw; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                startX: face.hs * 0.12 * (1 + -1 * 0.25); startY: face.hs * 0.12 * (1 + 1 * 0.95)
                PathQuad { x: face.hs * 0.12 * (1 + -1 * 0.95); y: face.hs * 0.12 * (1 + 1 * 0.25)
                           controlX: face.hs * 0.12 * (1 + -1 * 0.3); controlY: face.hs * 0.12 * (1 + 1 * 0.3) }
            }
            ShapePath {
                strokeColor: "#ff4d4d"; strokeWidth: face.sw; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                startX: face.hs * 0.12 * (1 + 1 * 0.25); startY: face.hs * 0.12 * (1 + 1 * 0.95)
                PathQuad { x: face.hs * 0.12 * (1 + 1 * 0.95); y: face.hs * 0.12 * (1 + 1 * 0.25)
                           controlX: face.hs * 0.12 * (1 + 1 * 0.3); controlY: face.hs * 0.12 * (1 + 1 * 0.3) }
            }
        }
        // Question mark
        Text {
            visible: face.rig.fx === "question"
            text: "?"
            color: face.robot ? face.glow : Qt.darker(face.body, 2.2)
            font.family: "Readex Pro"; font.pixelSize: face.hs * (face.small ? 0.5 : 0.34); font.weight: Font.Bold
            x: face.hs * 0.82; y: -face.hs * 0.12 + Math.sin(face.t * 3) * face.hs * 0.03
            rotation: 15
        }
        // Thinking dots
        Row {
            visible: face.rig.fx === "think"
            x: face.hs * 0.78; y: -face.hs * 0.06
            spacing: face.hs * 0.03
            Repeater {
                model: 3
                Rectangle { required property int index
                            width: face.hs * (0.05 + index * 0.02); height: width; radius: width / 2
                            anchors.bottom: parent.bottom
                            color: face.robot ? face.glow : Qt.darker(face.body, 1.9)
                            opacity: 0.35 + 0.65 * Math.max(0, Math.sin(face.t * 5 - index * 0.9)) }
            }
        }
        // Zzz
        Repeater {
            model: face.rig.fx === "zzz" ? 3 : 0
            Text {
                required property int index
                readonly property real p: ((face.t * 0.5) + index / 3) % 1
                text: "z"
                color: face.robot ? face.glow : Qt.darker(face.body, 2.2)
                font.pixelSize: face.hs * (0.14 + p * 0.12); font.bold: true
                x: face.hs * (0.78 + p * 0.25); y: face.hs * (0.1 - p * 0.35)
                opacity: 1 - p
            }
        }
        // Hearts floating up (one at small sizes)
        Repeater {
            model: face.rig.fx === "hearts" ? (face.small ? 1 : 3) : 0
            Heart {
                required property int index
                readonly property real p: ((face.t * 0.45) + index / 3) % 1
                d: face.hs * (face.small ? 0.3 : 0.18)
                x: face.hs * (index === 1 ? 0.0 : 0.84) + Math.sin(face.t * 3 + index) * face.hs * 0.04
                y: face.hs * (0.25 - p * 0.45)
                opacity: 1 - p
            }
        }
        // Sparkles (one at small sizes)
        Repeater {
            model: face.rig.fx === "sparkle" ? (face.small ? [[0.84, -0.04]] : [[0.02, 0.06], [0.88, -0.02], [0.92, 0.5]]) : []
            Star {
                required property var modelData
                required property int index
                points: 4; inner: 0.32
                d: face.hs * (face.small ? 0.3 : 0.18)
                x: face.hs * modelData[0]; y: face.hs * modelData[1]
                scale: 0.55 + 0.55 * Math.abs(Math.sin(face.t * 4 + index * 1.3))
            }
        }
    }
}
