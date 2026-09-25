// Zade control app: status, personality, settings, memory and history.
// Talks to Zade through `python -m zade.ctl` (JSON), and follows the wallpaper colors like the overlay.
import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root

    readonly property string repo: Quickshell.shellDir + "/.."          // ui/ lives inside the Zade repo
    readonly property string python: repo + "/.venv/bin/python"

    property var c: ({
        primary: "#A5D0BB", on_primary: "#1E352B", primary_container: "#749D8A", tertiary: "#EABBB8",
        error: "#EABCB6", on_surface: "#E2E2E0", on_surface_variant: "#C1C8C3",
        surface_container_low: "#1A1C1B", surface_container: "#1E201F", surface_container_high: "#282A29",
        surface_container_highest: "#333534", outline_variant: "#434844"
    })

    property string page: "home"
    property string settingsTab: "listening"
    property string calStep: ""      // mic calibration: "", "quiet", "voice", "done"
    property var calResult: null
    property real calRecommended: 0
    property string calPlaying: ""   // "before" / "after" while a demo plays
    function calTry(factor) {
        ctl(["calibrate", "stats", String(factor)], r => { if (r && r.ok) calResult = r })
    }
    function calPlay(which) {
        if (calPlaying) return
        calPlaying = which
        ctl(which === "after" ? ["calibrate", "play", "after", String(calResult.noise_factor)] : ["calibrate", "play", "before"],
            () => calPlaying = "")
    }
    function calibrate() {
        calStep = "quiet"; calResult = null
        ctl(["calibrate", "quiet"], r => {
            if (!r || !r.ok) { calStep = ""; flash(r && r.error ? r.error : "Couldn't use the microphone."); return }
            calStep = "voice"
            ctl(["calibrate", "voice"], v => {
                if (!v || !v.ok) { calStep = ""; flash(v && v.error ? v.error : "Calibration failed."); return }
                calResult = v; calRecommended = v.noise_factor; calStep = "done"
            })
        })
    }
    readonly property var wakeWords: [["~/.local/share/zade/zade.onnx", "Hey Zade"], ["hey_jarvis", "Hey Jarvis"],
                                      ["alexa", "Alexa"], ["hey_mycroft", "Hey Mycroft"], ["hey_rhasspy", "Hey Rhasspy"]]
    // What to say to wake Zade, for hints: the chosen built-in word, or "hey zade" for a custom model.
    readonly property string wakePhrase: {
        const w = settings ? wakeWords.find(x => x[0] === settings.wake.model) : null
        return (w ? w[1] : "Hey Zade").toLowerCase()
    }
    property var keys: ({})              // which API keys are saved (never their values)
    property var status: ({ running: false, autostart: false })
    property var settings: null
    property var facts: []
    property var shortcuts: []
    property var reminders: []
    property var history: []
    property var voices: []
    property bool needsRestart: false
    property string previewEmotion: "happy"   // character editor preview
    property string previewMode: "idle"
    property var draftSteps: []          // shortcut builder
    property string draftType: "open_app"
    readonly property var stepTypes: [
        { id: "open_app", name: "Open app", hint: "firefox" },
        { id: "open_website", name: "Website", hint: "youtube" },
        { id: "volume", name: "Volume", hint: "40" },
        { id: "workspace", name: "Workspace", hint: "2" },
        { id: "type_text", name: "Type text", hint: "hello" },
        { id: "media", name: "Media", hint: "play-pause, next or previous" },
        { id: "shell", name: "Command", hint: "a shell command (asks first)" }
    ]
    function makeStep(type, arg) {
        if (type === "open_app") return { name: "open_app", args: { name: arg } }
        if (type === "open_website") return { name: "open_website", args: { site: arg } }
        if (type === "volume") { const v = parseInt(arg); return { name: "volume", args: { set: isNaN(v) ? 50 : v } } }
        if (type === "workspace") return { name: "window", args: { action: "workspace", workspace: arg } }
        if (type === "type_text") return { name: "type_text", args: { text: arg } }
        if (type === "media") return { name: "media", args: { cmd: arg } }
        return { name: "shell", args: { cmd: arg } }
    }
    function describe(step) {
        const a = step.args
        if (step.name === "window") return "Go to workspace " + a.workspace
        const t = stepTypes.find(x => x.id === step.name)
        return (t ? t.name : step.name) + " " + (a.name || a.site || a.set || a.text || a.cmd || "")
    }
    property string toast: ""

    FileView {
        path: Quickshell.env("HOME") + "/.local/state/quickshell/user/generated/colors.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { try { root.c = Object.assign({}, root.c, JSON.parse(text())) } catch (e) {} }
    }

    // ── zade ctl ──────────────────────────────────────────────────────────────
    Component {
        id: procComponent
        Process {
            id: proc
            property var callback: null
            workingDirectory: root.repo
            stdout: StdioCollector {
                onStreamFinished: {
                    let out = null
                    try { out = JSON.parse(text) } catch (e) {}
                    if (proc.callback) proc.callback(out)
                    proc.destroy()
                }
            }
        }
    }

    function ctl(args, callback) {
        const p = procComponent.createObject(root, { command: [python, "-m", "zade.ctl"].concat(args), callback: callback || null })
        p.running = true
    }

    function refresh() {
        ctl(["status"], r => { if (r) status = r })
        ctl(["settings"], r => { if (r) settings = r })
        ctl(["facts"], r => { if (r) facts = r })
        ctl(["shortcuts"], r => { if (r) shortcuts = r })
        ctl(["reminders"], r => { if (r) reminders = r })
        ctl(["history"], r => { if (r) history = r })
        ctl(["keys"], r => { if (r) keys = r })
    }
    function setKey(name, value) {
        ctl(["key-set", name, value], r => {
            if (r && r.ok) { needsRestart = status.running; flash(value ? "Key saved." : "Key removed.")
                             ctl(["keys"], k => { if (k) keys = k }) }
            else flash("Couldn't save that key.")
        })
    }

    function setSetting(key, value) {
        ctl(["set", key, String(value)], r => {
            if (r && r.ok) { if (!r.live) needsRestart = status.running; ctl(["settings"], s => { if (s) settings = s }) }
            else flash(r && r.error ? r.error : "Couldn't save that setting.")
        })
    }

    function flash(text) { toast = text; toastTimer.restart() }

    // Start / stop / restart with visible progress: poll quickly until Zade is ready (or stopped).
    property string busy: ""        // "Starting", "Stopping", "Restarting" while a power action runs
    property string busyCmd: ""
    property real busySince: 0
    function power(cmd) {
        if (busy) return
        busy = ({ start: "Starting", stop: "Stopping", restart: "Restarting" })[cmd]
        busyCmd = cmd; busySince = Date.now(); needsRestart = false
        ctl([cmd], () => busyPoll.start())
    }
    Timer {
        id: busyPoll
        interval: 250; repeat: true
        onTriggered: root.ctl(["status"], r => {
            if (!r) return
            root.status = r
            const done = root.busyCmd === "stop" ? !r.running : r.ready
            if (done) { busyPoll.stop(); root.busy = ""; root.refresh() }
            else if (Date.now() - root.busySince > 20000) {
                busyPoll.stop(); root.busy = ""; root.flash("Zade didn't come back. Check ~/.local/share/zade/zade.log.")
            }
        })
    }
    Timer { id: toastTimer; interval: 2600; onTriggered: root.toast = "" }

    Component.onCompleted: { refresh(); ctl(["voices"], r => { if (r) voices = r }) }
    Timer { interval: 3000; repeat: true; running: true
            onTriggered: { ctl(["status"], r => { if (r) root.status = r })
                           if (root.page === "history" || root.page === "home") ctl(["history"], r => { if (r) root.history = r }) } }

    property real phase: 0
    NumberAnimation on phase { from: 0; to: Math.PI * 2; duration: 2400; loops: Animation.Infinite; running: true }

    // ── Building blocks ───────────────────────────────────────────────────────
    component Label: Text {
        color: root.c.on_surface
        font.family: "Readex Pro"
        font.pixelSize: 14
        wrapMode: Text.WordWrap
    }

    component Muted: Label { color: root.c.on_surface_variant; font.pixelSize: 13 }

    component Button: Rectangle {
        id: btn
        property string text: ""
        property bool accent: false
        property bool danger: false
        signal clicked
        implicitWidth: btnLabel.implicitWidth + 32
        implicitHeight: 38
        radius: 10
        color: accent ? root.c.primary : (hover.containsMouse ? root.c.surface_container_highest : root.c.surface_container_high)
        border.width: accent ? 0 : 1
        border.color: Qt.alpha(root.c.outline_variant, 0.6)
        Behavior on color { ColorAnimation { duration: 120 } }
        Text {
            id: btnLabel
            anchors.centerIn: parent
            text: btn.text
            color: btn.accent ? root.c.on_primary : (btn.danger ? root.c.error : root.c.on_surface)
            font.family: "Readex Pro"; font.pixelSize: 13; font.weight: Font.Medium
        }
        MouseArea { id: hover; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: btn.clicked() }
    }

    component Field: Rectangle {
        id: field
        property alias text: input.text
        property alias echoMode: input.echoMode
        property string placeholder: ""
        signal accepted
        signal edited
        implicitHeight: 38
        radius: 10
        color: root.c.surface_container
        border.width: 1
        border.color: input.activeFocus ? root.c.primary : Qt.alpha(root.c.outline_variant, 0.7)
        TextInput {
            id: input
            anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
            verticalAlignment: TextInput.AlignVCenter
            color: root.c.on_surface
            selectionColor: Qt.alpha(root.c.primary, 0.4)
            font.family: "Readex Pro"; font.pixelSize: 14
            clip: true
            onAccepted: field.accepted()
            onEditingFinished: field.edited()
        }
        Text {
            anchors.fill: input; verticalAlignment: Text.AlignVCenter
            visible: input.text.length === 0 && !input.activeFocus
            text: field.placeholder
            color: Qt.alpha(root.c.on_surface_variant, 0.6)
            font: input.font
        }
    }

    // An API key: shows only whether one is saved; typing a new one and pressing Enter replaces it.
    component KeyField: RowLayout {
        property string name: ""
        readonly property bool saved: !!root.keys[name]
        spacing: 8
        Field {
            id: keyInput
            Layout.preferredWidth: 260
            placeholder: parent.saved ? "Saved ••••  (paste to replace)" : "Paste key, press Enter"
            echoMode: TextInput.Password
            onAccepted: { if (text.trim()) { root.setKey(parent.name, text.trim()); text = "" } }
        }
        Button { text: "Remove"; danger: true; visible: parent.saved; onClicked: root.setKey(parent.name, "") }
    }

    component Switch: Rectangle {
        id: sw
        property bool checked: false
        signal toggled(bool value)
        implicitWidth: 44; implicitHeight: 24; radius: 12
        color: checked ? root.c.primary : root.c.surface_container_highest
        Behavior on color { ColorAnimation { duration: 150 } }
        Rectangle {
            width: 18; height: 18; radius: 9
            anchors.verticalCenter: parent.verticalCenter
            x: sw.checked ? parent.width - width - 3 : 3
            color: sw.checked ? root.c.on_primary : root.c.on_surface_variant
            Behavior on x { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: sw.toggled(!sw.checked) }
    }

    // A row in a settings group: label (and hint) on the left, control on the right.
    component Row_: RowLayout {
        property string label: ""
        property string hint: ""
        Layout.fillWidth: true
        Layout.minimumHeight: 52
        spacing: 16
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Label { text: parent.parent.label; Layout.fillWidth: true }
            Muted { text: parent.parent.hint; visible: text.length > 0; Layout.fillWidth: true; font.pixelSize: 12 }
        }
    }

    component Group: Rectangle {
        default property alias content: groupCol.data
        property string title: ""
        Layout.fillWidth: true
        implicitHeight: groupCol.implicitHeight + 20
        radius: 16
        color: root.c.surface_container
        border.width: 1
        border.color: Qt.alpha(root.c.outline_variant, 0.4)
        ColumnLayout {
            id: groupCol
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 10; anchors.leftMargin: 18; anchors.rightMargin: 18
            spacing: 0
        }
    }

    component Divider: Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Qt.alpha(root.c.outline_variant, 0.35) }

    component Chip: Rectangle {
        property string text: ""
        property bool selected: false
        signal clicked
        implicitWidth: chipText.implicitWidth + 24; implicitHeight: 32; radius: 16
        color: selected ? root.c.primary : root.c.surface_container_high
        border.width: selected ? 0 : 1
        border.color: Qt.alpha(root.c.outline_variant, 0.6)
        Text { id: chipText; anchors.centerIn: parent; text: parent.text
               color: parent.selected ? root.c.on_primary : root.c.on_surface
               font.family: "Readex Pro"; font.pixelSize: 13 }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: parent.clicked() }
    }

    // The living orb (same language as the overlay): breathes while Zade runs, still and grey when stopped.
    component Orb: Item {
        property real size: 72
        property bool alive: root.status.running
        implicitWidth: size * 1.9; implicitHeight: size * 1.9
        Rectangle {
            anchors.centerIn: parent
            width: parent.size; height: width; radius: width / 2
            color: Qt.alpha(parent.alive ? root.c.primary : root.c.outline_variant, 0.18)
            scale: parent.alive ? 1.5 + 0.12 * Math.sin(root.phase) : 1.2
        }
        Repeater {
            model: 2
            Rectangle {
                required property int index
                anchors.centerIn: parent
                width: parent.size; height: width; radius: width / 2
                color: "transparent"; border.width: 1.5; border.color: root.c.primary
                visible: parent.alive
                property real t: ((root.phase / (Math.PI * 2)) + index * 0.5) % 1
                scale: 1 + t * 0.8; opacity: (1 - t) * 0.4
            }
        }
        Rectangle {
            anchors.centerIn: parent
            width: parent.size * 0.55; height: width; radius: width / 2
            gradient: Gradient {
                GradientStop { position: 0; color: parent.parent.alive ? Qt.lighter(root.c.primary, 1.15) : root.c.surface_container_highest }
                GradientStop { position: 1; color: parent.parent.alive ? root.c.primary_container : root.c.outline_variant }
            }
        }
    }

    // ── Window ────────────────────────────────────────────────────────────────
    FloatingWindow {
        title: "Zade"
        onVisibleChanged: if (!visible) Qt.quit()  // closing the window ends the app (Quickshell would keep running)
        implicitWidth: 980
        implicitHeight: 680
        color: root.c.surface_container_low

        RowLayout {
            anchors.fill: parent
            spacing: 0

            // Navigation rail
            Rectangle {
                Layout.fillHeight: true
                Layout.preferredWidth: 212
                color: root.c.surface_container
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; anchors.topMargin: 22
                    spacing: 4
                    RowLayout {
                        spacing: 10
                        Layout.bottomMargin: 18
                        Layout.leftMargin: 6
                        Orb { size: 18; implicitWidth: 30; implicitHeight: 30 }
                        Label { text: "Zade"; font.pixelSize: 20; font.weight: Font.Medium }
                    }
                    Repeater {
                        model: [
                            { id: "home", name: "Home" }, { id: "character", name: "Character" }, { id: "personalize", name: "Voice" },
                            { id: "settings", name: "Settings" }, { id: "integrations", name: "Integrations" },
                            { id: "memory", name: "Memory" },
                            { id: "history", name: "History" }
                        ]
                        Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: 40; radius: 12
                            color: root.page === modelData.id ? Qt.alpha(root.c.primary, 0.16)
                                 : (navHover.containsMouse ? Qt.alpha(root.c.on_surface, 0.05) : "transparent")
                            Text {
                                anchors.verticalCenter: parent.verticalCenter; x: 14
                                text: parent.modelData.name
                                color: root.page === parent.modelData.id ? root.c.primary : root.c.on_surface
                                font.family: "Readex Pro"; font.pixelSize: 14
                                font.weight: root.page === parent.modelData.id ? Font.Medium : Font.Normal
                            }
                            MouseArea { id: navHover; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: { root.page = parent.modelData.id; root.refresh() } }
                        }
                    }
                    Item { Layout.fillHeight: true }
                    Muted {
                        Layout.leftMargin: 6
                        text: root.status.running ? "Running" : "Stopped"
                        color: root.status.running ? root.c.primary : root.c.on_surface_variant
                    }
                }
            }

            // Pages
            Flickable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentHeight: pageCol.implicitHeight + 64
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                ColumnLayout {
                    id: pageCol
                    x: 40; y: 32
                    width: parent.width - 80
                    spacing: 16

                    // Restart banner after settings changed while running
                    Rectangle {
                        visible: root.needsRestart && root.page !== "memory" && root.page !== "history"
                        Layout.fillWidth: true
                        implicitHeight: 52; radius: 14
                        color: Qt.alpha(root.c.primary, 0.12)
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 8
                            Label { text: "Restart Zade to use the new settings."; Layout.fillWidth: true }
                            Button { text: "Restart"; accent: true
                                     onClicked: root.power("restart") }
                        }
                    }

                    // ── Home
                    ColumnLayout {
                        visible: root.page === "home"
                        Layout.fillWidth: true
                        spacing: 16
                        RowLayout {
                            spacing: 20
                            Orb { size: 64 }
                            ColumnLayout {
                                spacing: 4
                                Label { text: root.busy ? root.busy + "\u2026" : root.status.running ? "Zade is listening" : "Zade is stopped"
                                        font.pixelSize: 26; font.weight: Font.Medium }
                                Muted { text: root.status.running ? "Say “" + root.wakePhrase + "”, hold Win, or type below."
                                                                  : "Start Zade to talk to it." }
                            }
                        }
                        RowLayout {
                            spacing: 10
                            Button { text: root.status.running ? "Stop" : "Start Zade"; accent: !root.status.running; danger: root.status.running
                                     opacity: root.busy ? 0.5 : 1
                                     onClicked: root.power(root.status.running ? "stop" : "start") }
                            Button { text: "Restart"; visible: root.status.running
                                     opacity: root.busy ? 0.5 : 1
                                     onClicked: root.power("restart") }
                        }
                        Timer { id: statusLater; interval: 1500; onTriggered: root.ctl(["status"], r => { if (r) root.status = r }) }

                        Group {
                            Row_ {
                                label: "Type to Zade"
                                hint: "Handled like something you said."
                                Field {
                                    id: typeBox
                                    Layout.preferredWidth: 360
                                    placeholder: "what's the weather tomorrow"
                                    onAccepted: {
                                        if (!text.trim()) return
                                        if (!root.status.running) { root.flash("Start Zade first."); return }
                                        root.ctl(["say", text]); root.flash("Sent."); text = ""
                                    }
                                }
                            }
                            Divider {}
                            Row_ {
                                label: "Do not disturb"
                                hint: "Ignore \u201c" + root.wakePhrase + "\u201d and hold reminders. Holding Win still works."
                                Switch { checked: root.settings ? root.settings.quiet.dnd : false
                                         onToggled: v => root.setSetting("quiet.dnd", v) }
                            }
                            Divider {}
                            Row_ {
                                label: "Start on login"
                                hint: "Runs Zade in the background as a user service."
                                Switch { checked: root.status.autostart
                                         onToggled: v => root.ctl(["autostart", v ? "on" : "off"], () => root.ctl(["status"], r => { if (r) root.status = r })) }
                            }
                        }

                        Muted { text: "Recent"; Layout.topMargin: 8 }
                        Group {
                            Repeater {
                                model: root.history.slice(0, 4)
                                ColumnLayout {
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Divider { visible: parent.index > 0 }
                                    Label { text: parent.modelData.heard; Layout.fillWidth: true; Layout.topMargin: 10; elide: Text.ElideRight; maximumLineCount: 1 }
                                    Muted { text: parent.modelData.reply || "—"; Layout.fillWidth: true; Layout.bottomMargin: 10; elide: Text.ElideRight; maximumLineCount: 1 }
                                }
                            }
                            Muted { visible: root.history.length === 0; text: "Nothing yet."; Layout.topMargin: 12; Layout.bottomMargin: 12 }
                        }
                    }

                    // ── Character
                    ColumnLayout {
                        visible: root.page === "character" && root.settings !== null
                        Layout.fillWidth: true
                        spacing: 16
                        Label { text: "Character"; font.pixelSize: 26; font.weight: Font.Medium }

                        // Live preview: try each expression
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 250; radius: 16
                            color: root.c.surface_container
                            border.width: 1; border.color: Qt.alpha(root.c.outline_variant, 0.4)
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 22
                                spacing: 28
                                Face {
                                    id: previewFace
                                    size: 150
                                    Layout.alignment: Qt.AlignVCenter
                                    mode: root.previewMode
                                    emotion: root.previewEmotion
                                    level: root.previewMode === "listening" ? 0.5 + 0.4 * Math.sin(root.phase * 3) : 0
                                    look: root.settings ? root.settings.persona : ({})
                                    accent: root.settings && root.settings.ui.accent ? root.settings.ui.accent : root.c.primary
                                    ink: Qt.darker(root.settings && root.settings.persona.color ? root.settings.persona.color
                                                   : (root.settings && root.settings.ui.accent ? root.settings.ui.accent : root.c.primary), 3.4)
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 10
                                    Label { text: root.settings ? root.settings.persona.name : ""; font.pixelSize: 22; font.weight: Font.Medium }
                                    Muted { text: "Try an expression" }
                                    Flow {
                                        Layout.fillWidth: true; spacing: 8
                                        Repeater {
                                            model: ["neutral", "happy", "excited", "laughing", "love", "sad", "crying", "confused", "surprised",
                                                    "amazed", "annoyed", "angry", "curious", "smug", "sleepy", "embarrassed", "nervous", "wink", "playful"]
                                            Chip { required property string modelData
                                                   text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                                                   selected: root.previewMode === "idle" && root.previewEmotion === modelData
                                                   onClicked: { root.previewMode = "idle"; root.previewEmotion = modelData } }
                                        }
                                        Repeater {
                                            model: [["listening", "Listening"], ["thinking", "Thinking"], ["speaking", "Talking"]]
                                            Chip { required property var modelData
                                                   text: modelData[1]
                                                   selected: root.previewMode === modelData[0]
                                                   onClicked: { root.previewMode = modelData[0]; root.previewEmotion = "neutral" } }
                                        }
                                    }
                                }
                            }
                        }

                        Group {
                            Row_ { label: "Show the character"; hint: "Off shows the simple orb instead."
                                Switch { checked: root.settings ? root.settings.persona.enabled : true; onToggled: v => root.setSetting("persona.enabled", v) } }
                            Divider {}
                            Row_ { label: "Name"; hint: "What it calls itself."
                                Field { Layout.preferredWidth: 200; text: root.settings ? root.settings.persona.name : ""
                                        onEdited: if (text.trim()) root.setSetting("persona.name", text.trim()) } }
                            Divider {}
                            Row_ { label: "Character"; hint: "Robot glows in your colour; cat, bunny and bear have ears." }
                            Flow { Layout.fillWidth: true; Layout.bottomMargin: 12; spacing: 8
                                Repeater { model: [["blob", "Blob"], ["robot", "Robot"], ["cat", "Cat"], ["bunny", "Bunny"], ["bear", "Bear"], ["ghost", "Ghost"]]
                                    Chip { required property var modelData; text: modelData[1]
                                           selected: root.settings && (root.settings.persona.type || "blob") === modelData[0]
                                           onClicked: root.setSetting("persona.type", modelData[0]) } } }
                            Divider {}
                            Row_ { label: "Shape"
                                RowLayout { spacing: 8
                                    Repeater { model: [["round", "Round"], ["squircle", "Squircle"], ["blob", "Blob"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.persona.shape === modelData[0]
                                               onClicked: root.setSetting("persona.shape", modelData[0]) } } } }
                            Divider {}
                            Row_ { label: "Eyes"
                                RowLayout { spacing: 8
                                    Repeater { model: [["round", "Round"], ["oval", "Oval"], ["anime", "Anime"], ["line", "Sleepy"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.persona.eyes === modelData[0]
                                               onClicked: root.setSetting("persona.eyes", modelData[0]) } } } }
                            Divider {}
                            Row_ { label: "Mouth"
                                RowLayout { spacing: 8
                                    Repeater { model: [["smile", "Smile"], ["cat", "Cat"], ["small", "Small"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.persona.mouth === modelData[0]
                                               onClicked: root.setSetting("persona.mouth", modelData[0]) } } } }
                            Divider {}
                            Row_ { label: "Colour"; hint: "Accent follows the overlay colour."
                                RowLayout { spacing: 8
                                    Chip { text: "Accent"; selected: root.settings && !root.settings.persona.color
                                           onClicked: root.setSetting("persona.color", "") }
                                    Repeater { model: ["#a5d0bb", "#ff9f43", "#ff6b81", "#a29bfe", "#48dbfb", "#feca57", "#dfe6e9"]
                                        Rectangle { required property string modelData
                                            implicitWidth: 26; implicitHeight: 26; radius: 13; color: modelData
                                            border.width: root.settings && root.settings.persona.color === modelData ? 3 : 0
                                            border.color: root.c.on_surface
                                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                        onClicked: root.setSetting("persona.color", parent.modelData) } } } } }
                            Divider {}
                            Row_ { label: "Blush"; hint: "Rosy cheeks when happy or excited."
                                Switch { checked: root.settings ? root.settings.persona.blush : true; onToggled: v => root.setSetting("persona.blush", v) } }
                        }

                        Group {
                            Row_ { label: "Personality"; hint: "How your character talks. For example: “Be playful and call me boss.” or “Answer in Hinglish.”" }
                            Rectangle {
                                Layout.fillWidth: true; Layout.bottomMargin: 12
                                implicitHeight: 110; radius: 10
                                color: root.c.surface_container_low
                                border.width: 1
                                border.color: personality.activeFocus ? root.c.primary : Qt.alpha(root.c.outline_variant, 0.7)
                                TextEdit {
                                    id: personality
                                    anchors.fill: parent; anchors.margins: 12
                                    text: root.settings ? root.settings.llm.personality : ""
                                    color: root.c.on_surface
                                    font.family: "Readex Pro"; font.pixelSize: 14
                                    wrapMode: TextEdit.Wrap
                                    selectionColor: Qt.alpha(root.c.primary, 0.4)
                                }
                            }
                            RowLayout {
                                Layout.bottomMargin: 10
                                Button { text: "Save personality"; accent: true
                                         onClicked: { root.setSetting("llm.personality", personality.text); root.flash("Saved.") } }
                            }
                        }
                    }

                    // ── Personalize
                    ColumnLayout {
                        visible: root.page === "personalize" && root.settings !== null
                        Layout.fillWidth: true
                        spacing: 16
                        Label { text: "Voice"; font.pixelSize: 26; font.weight: Font.Medium }
                        Group {
                            Row_ { label: "Voice"; hint: "a = American, b = British, h = Indian; f = female, m = male. en-IN voices are Indian English from Microsoft (online; offline they fall back to hf_alpha)." }
                            Flow {
                                Layout.fillWidth: true; Layout.bottomMargin: 12
                                spacing: 8
                                Repeater {
                                    model: root.voices
                                    Chip {
                                        required property string modelData
                                        text: modelData
                                        selected: root.settings && root.settings.tts.voice === modelData
                                        onClicked: { root.setSetting("tts.voice", modelData)
                                                     root.ctl(["preview", modelData, String(root.settings.tts.speed)]) }
                                    }
                                }
                            }
                            Divider {}
                            Row_ {
                                label: "Speaking speed"
                                hint: "1.0 is normal."
                                RowLayout {
                                    spacing: 8
                                    Repeater {
                                        model: [1.0, 1.1, 1.2, 1.3, 1.4]
                                        Chip {
                                            required property real modelData
                                            text: modelData.toFixed(1) + "×"
                                            selected: root.settings && Math.abs(root.settings.tts.speed - modelData) < 0.01
                                            onClicked: { root.setSetting("tts.speed", modelData)
                                                         root.ctl(["preview", root.settings.tts.voice, String(modelData)]) }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // ── Settings
                    ColumnLayout {
                        visible: root.page === "settings" && root.settings !== null
                        Layout.fillWidth: true
                        spacing: 16
                        Label { text: "Settings"; font.pixelSize: 26; font.weight: Font.Medium }
                        RowLayout { spacing: 8
                            Repeater { model: [["listening", "Listening"], ["brain", "Brain"], ["overlay", "Overlay"],
                                               ["sounds", "Sounds"], ["quiet", "Quiet & safety"]]
                                Chip { required property var modelData; text: modelData[1]
                                       selected: root.settingsTab === modelData[0]
                                       onClicked: root.settingsTab = modelData[0] } } }

                        Group { visible: root.settingsTab === "listening"
                            Row_ { label: "Wake word"; hint: "What you say to call Zade. Restart Zade after changing it." }
                            Flow { Layout.fillWidth: true; Layout.bottomMargin: 12; spacing: 8
                                Repeater { model: root.wakeWords
                                    Chip { required property var modelData; text: modelData[1]
                                           selected: root.settings && root.settings.wake.model === modelData[0]
                                           onClicked: root.setSetting("wake.model", modelData[0]) } } }
                            Divider {}
                            Row_ { label: "Speech recognition"; hint: "Large runs on the GPU (about 0.9 GB of VRAM, needs the zade-whisper service) and knows far more names; if it isn't running, Small is used."
                                RowLayout { spacing: 8
                                    Repeater { model: [["base.en", "Base"], ["small.en", "Small"], ["gpu", "Large (GPU)"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && (modelData[0] === "gpu" ? root.settings.stt.provider === "gpu"
                                                         : root.settings.stt.provider !== "gpu" && root.settings.stt.model === modelData[0])
                                               onClicked: { if (modelData[0] === "gpu") root.setSetting("stt.provider", "gpu")
                                                            else { root.setSetting("stt.provider", "whisper"); root.setSetting("stt.model", modelData[0]) } } } } } }
                            Divider {}
                            Row_ { label: "Wake word sensitivity"; hint: "Lower hears you more easily; higher avoids false wakes."
                                RowLayout { spacing: 8
                                    Repeater { model: [0.35, 0.5, 0.65]
                                        Chip { required property real modelData
                                               text: ({ 0.35: "Easy", 0.5: "Normal", 0.65: "Strict" })[modelData]
                                               selected: root.settings && Math.abs(root.settings.wake.threshold - modelData) < 0.01
                                               onClicked: root.setSetting("wake.threshold", modelData) } } } }
                            Divider {}
                            Row_ { label: "Microphone calibration"
                                hint: root.calStep === "quiet" ? "Stay quiet for 5 seconds\u2026 (background noise is fine)"
                                    : root.calStep === "voice" ? "Now say at your normal volume: \u201cOpen Firefox, and what\u2019s the weather tomorrow?\u201d"
                                    : root.calStep === "done" && root.calResult
                                      ? "Room " + root.calResult.room + ", your voice " + root.calResult.voice + ". At " + root.calResult.noise_factor
                                        + (root.calResult.noise_factor === root.calRecommended ? " (recommended)" : "") + ", background counts as talking "
                                        + root.calResult.noise_pct + "% of the time and " + root.calResult.voice_pct + "% of your speech is loud enough to count. Now "
                                        + root.settings.audio.noise_factor + "." + (root.calResult.too_noisy ? " The room is loud for your voice: holding Win to talk works best." : "")
                                    : "Measures your room and voice so background noise doesn\u2019t count as talking."
                                RowLayout { spacing: 8
                                    Button { text: root.calStep === "done" ? "Again" : "Calibrate"; visible: root.calStep === "" || root.calStep === "done"
                                             onClicked: root.calibrate() }
                                    Button { text: "Apply"; accent: true; visible: root.calStep === "done" && root.calResult !== null
                                             onClicked: { root.setSetting("audio.noise_factor", root.calResult.noise_factor); root.calStep = "" } } } }
                            // Listen and compare: the recording as heard, and with what Zade ignores muted.
                            ColumnLayout {
                                visible: root.calStep === "done" && root.calResult !== null
                                Layout.fillWidth: true; Layout.bottomMargin: 12
                                spacing: 10
                                RowLayout { spacing: 8
                                    Muted { text: "Sensitivity"; font.pixelSize: 12 }
                                    Repeater { model: [2.5, 3, 3.5, 4, 4.5, 5, 6]
                                        Chip { required property real modelData
                                               text: modelData + (Math.abs(modelData - root.calRecommended) < 0.13 ? " \u2605" : "")
                                               selected: root.calResult && Math.abs(root.calResult.noise_factor - modelData) < 0.01
                                               onClicked: root.calTry(modelData) } }
                                    Chip { visible: root.calRecommended > 0 && [2.5, 3, 3.5, 4, 4.5, 5, 6].indexOf(root.calRecommended) < 0
                                           text: root.calRecommended + " \u2605"
                                           selected: root.calResult && Math.abs(root.calResult.noise_factor - root.calRecommended) < 0.01
                                           onClicked: root.calTry(root.calRecommended) }
                                }
                                RowLayout { spacing: 8
                                    Button { text: root.calPlaying === "before" ? "Playing\u2026" : "\u25B6 Original"; onClicked: root.calPlay("before") }
                                    Button { text: root.calPlaying === "after" ? "Playing\u2026" : "\u25B6 What counts as talking"; onClicked: root.calPlay("after") }
                                    Muted { Layout.fillWidth: true; font.pixelSize: 12
                                            text: "5 s of your room, then you. In the second one, what\u2019s silent is ignored; you should hear your voice, not the background." }
                                }
                            }
                            Divider {}
                            Row_ { label: "Hold Win to talk"; hint: "Hold the key alone, speak, release."
                                Switch { checked: root.settings ? root.settings.hotkey.enabled : false; onToggled: v => root.setSetting("hotkey.enabled", v) } }
                            Divider {}
                            Row_ { label: "Hold time"; hint: "Seconds to hold Win before Zade listens."
                                Field { Layout.preferredWidth: 90; text: root.settings ? String(root.settings.hotkey.hold_s) : ""
                                        onEdited: root.setSetting("hotkey.hold_s", text) } }
                            Divider {}
                            Row_ { label: "Voice typing"; hint: "Hold Right Alt, speak, release: the words are typed."
                                Switch { checked: root.settings ? root.settings.dictation.enabled : false; onToggled: v => root.setSetting("dictation.enabled", v) } }
                            Divider {}
                            Row_ { label: "Follow-up listening"; hint: "When Zade asks a question, listen for your answer without the wake word."
                                Switch { checked: root.settings ? root.settings.followup.enabled : false; onToggled: v => root.setSetting("followup.enabled", v) } }
                            Divider {}
                            Row_ { label: "Words to expect"; hint: "Names Whisper often mishears, separated by commas."
                                Field { Layout.preferredWidth: 360; text: root.settings ? root.settings.stt.hotwords.join(", ") : ""
                                        onEdited: root.setSetting("stt.hotwords", text) } }
                        }

                        Group { visible: root.settingsTab === "brain"
                            Row_ { label: "Model provider"; hint: "Local runs on your PC; cloud needs an API key (see Integrations)."
                                RowLayout { spacing: 8
                                    Repeater { model: [{ id: "ollama", name: "Local" }, { id: "anthropic", name: "Claude" }, { id: "openai", name: "OpenAI-compatible" }]
                                        Chip { required property var modelData
                                               text: modelData.name
                                               selected: root.settings && root.settings.llm.provider === modelData.id
                                               onClicked: root.setSetting("llm.provider", modelData.id) } } } }
                            Divider {}
                            Row_ { label: "Unload after"; hint: "How long the local model stays in VRAM after a request. Instantly frees VRAM at once, but every reply loads it again (about 1–2 s slower)."
                                RowLayout { spacing: 8
                                    Repeater { model: [["0", "Instantly"], ["30s", "30 s"], ["60s", "1 min"], ["5m", "5 min"], ["15m", "15 min"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && String(root.settings.llm.keep_alive) === modelData[0]
                                               onClicked: root.setSetting("llm.keep_alive", modelData[0]) } } } }
                            Divider {}
                            Row_ { label: "Local model"; hint: "Any Ollama model with tool calling."
                                Field { Layout.preferredWidth: 220; text: root.settings ? root.settings.llm.model : ""
                                        onEdited: root.setSetting("llm.model", text) } }
                            Divider {}
                            Row_ { label: "Vision model"; hint: "Used for “what's on my screen”."
                                Field { Layout.preferredWidth: 220; text: root.settings ? root.settings.vision.model : ""
                                        onEdited: root.setSetting("vision.model", text) } }
                        }

                        Group { visible: root.settingsTab === "overlay"
                            Row_ { label: "Show the overlay"; hint: "The island that shows what Zade hears and says. Style changes apply instantly."
                                Switch { checked: root.settings ? root.settings.ui.enabled : false; onToggled: v => root.setSetting("ui.enabled", v) } }
                            Divider {}
                            Row_ { label: "Position" }
                            Flow { Layout.fillWidth: true; Layout.bottomMargin: 12; spacing: 8
                                Repeater { model: [["top", "Top"], ["bottom", "Bottom"], ["top-left", "Top left"], ["top-right", "Top right"],
                                                   ["bottom-left", "Bottom left"], ["bottom-right", "Bottom right"]]
                                    Chip { required property var modelData; text: modelData[1]
                                           selected: root.settings && root.settings.ui.position === modelData[0]
                                           onClicked: root.setSetting("ui.position", modelData[0]) } } }
                            Divider {}
                            Row_ { label: "Size"
                                RowLayout { spacing: 8
                                    Repeater { model: [["small", "S"], ["medium", "M"], ["large", "L"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.ui.size === modelData[0]
                                               onClicked: root.setSetting("ui.size", modelData[0]) } } } }
                            Divider {}
                            Row_ { label: "Accent colour"; hint: "Wallpaper follows your theme."
                                RowLayout { spacing: 8
                                    Chip { text: "Wallpaper"; selected: root.settings && !root.settings.ui.accent
                                           onClicked: root.setSetting("ui.accent", "") }
                                    Repeater { model: ["#ff9f43", "#ff6b81", "#a29bfe", "#48dbfb", "#1dd1a1", "#feca57"]
                                        Rectangle { required property string modelData
                                            implicitWidth: 26; implicitHeight: 26; radius: 13; color: modelData
                                            border.width: root.settings && root.settings.ui.accent === modelData ? 3 : 0
                                            border.color: root.c.on_surface
                                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                        onClicked: root.setSetting("ui.accent", parent.modelData) } } } } }
                            Divider {}
                            Row_ { label: "Stays on screen"; hint: "After Zade finishes speaking."
                                RowLayout { spacing: 8
                                    Repeater { model: [0.5, 2, 5]
                                        Chip { required property real modelData; text: modelData + " s"
                                               selected: root.settings && Math.abs(root.settings.ui.linger_s - modelData) < 0.01
                                               onClicked: root.setSetting("ui.linger_s", modelData) } } } }
                            Divider {}
                            Row_ { label: "Text speed"; hint: "How fast the reply types itself out."
                                RowLayout { spacing: 8
                                    Repeater { model: [[12, "Slow"], [18, "Normal"], [40, "Fast"], [1000, "Instant"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.ui.reveal_cps === modelData[0]
                                               onClicked: root.setSetting("ui.reveal_cps", modelData[0]) } } } }
                            Divider {}
                            Row_ { label: "Show what you said"
                                Switch { checked: root.settings ? root.settings.ui.show_heard : true; onToggled: v => root.setSetting("ui.show_heard", v) } }
                        }

                        Group { visible: root.settingsTab === "sounds"
                            Row_ { label: "Listening sound"
                                RowLayout { spacing: 8
                                    Repeater { model: [["soft", "Soft"], ["classic", "Classic"], ["none", "None"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.sound.chime === modelData[0]
                                               onClicked: root.setSetting("sound.chime", modelData[0]) } } } }
                            Divider {}
                            Row_ { label: "Volume"
                                RowLayout { spacing: 8
                                    Repeater { model: [[0.3, "Quiet"], [0.6, "Normal"], [1.0, "Loud"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && Math.abs(root.settings.sound.volume - modelData[0]) < 0.01
                                               onClicked: root.setSetting("sound.volume", modelData[0]) } } } }
                            Divider {}
                            Row_ { label: "Reply to the wake word"; hint: "Something Zade says before listening, like \u201cYes?\u201d. Leave empty for none."
                                Field { Layout.preferredWidth: 200; placeholder: "Yes?"; text: root.settings ? root.settings.sound.wake_reply : ""
                                        onEdited: root.setSetting("sound.wake_reply", text) } }
                        }

                        Group { visible: root.settingsTab === "quiet"
                            Row_ { label: "Quiet hours"; hint: "Ignore \u201c" + root.wakePhrase + "\u201d and hold reminders during this time. Holding Win still works."
                                Switch { checked: root.settings ? root.settings.quiet.enabled : false; onToggled: v => root.setSetting("quiet.enabled", v) } }
                            Divider {}
                            Row_ { label: "From"; hint: "24-hour time, like 23:00."
                                Field { Layout.preferredWidth: 100; text: root.settings ? root.settings.quiet.start : ""
                                        onEdited: root.setSetting("quiet.start", text) } }
                            Divider {}
                            Row_ { label: "Until"
                                Field { Layout.preferredWidth: 100; text: root.settings ? root.settings.quiet.end : ""
                                        onEdited: root.setSetting("quiet.end", text) } }
                        }

                        Group { visible: root.settingsTab === "quiet"
                            Row_ { label: "Ask before"; hint: "Which actions need your spoken \u201cyes\u201d first."
                                RowLayout { spacing: 8
                                    Repeater { model: [["commands", "Commands"], ["risky", "Risky actions"], ["everything", "Everything"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.safety.confirm === modelData[0]
                                               onClicked: root.setSetting("safety.confirm", modelData[0]) } } } }
                            Muted { Layout.bottomMargin: 12; font.pixelSize: 12
                                    text: root.settings ? ({ commands: "Shell commands and power (shut down, restart) always ask.",
                                                             risky: "Also closing apps and windows, typing and copying.",
                                                             everything: "Anything that changes something: opening apps, volume, music, windows." })[root.settings.safety.confirm] : "" } }
                    }

                    // ── Integrations
                    ColumnLayout {
                        visible: root.page === "integrations" && root.settings !== null
                        Layout.fillWidth: true
                        spacing: 16
                        Label { text: "Integrations"; font.pixelSize: 26; font.weight: Font.Medium }
                        Muted { text: "Keys are stored only in ~/.config/zade/env, readable by you alone. Zade reads them when it starts."
                                Layout.fillWidth: true }

                        Muted { text: "Weather"; Layout.topMargin: 8 }
                        Group {
                            Row_ { label: "Default location"; hint: "Used when you don\u2019t name a place (\u201cwhat\u2019s the weather\u201d). Empty guesses it from your internet connection."
                                Field { Layout.preferredWidth: 260; placeholder: "e.g. Mumbai"; text: root.settings ? root.settings.weather.place : ""
                                        onEdited: root.setSetting("weather.place", text) } }
                        }

                        Muted { text: "Music"; Layout.topMargin: 8 }
                        Group {
                            Row_ { label: "Play songs with"; hint: "Say \u201cplay \u2026 on YouTube\u201d (or Spotify, YouTube Music) to pick one for a single song. YouTube needs no key."
                                RowLayout { spacing: 8
                                    Repeater { model: [["spotify", "Spotify"], ["youtube", "YouTube"], ["youtube music", "YouTube Music"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.music.provider === modelData[0]
                                               onClicked: root.setSetting("music.provider", modelData[0]) } } } }
                        }

                        Muted { text: "Spotify"; Layout.topMargin: 8 }
                        Group {
                            Row_ { label: "Client ID"; hint: "Free: create an app at developer.spotify.com/dashboard. Lets \u201cplay \u2026\u201d find the exact song."
                                KeyField { name: "SPOTIFY_CLIENT_ID" } }
                            Divider {}
                            Row_ { label: "Client secret"
                                KeyField { name: "SPOTIFY_CLIENT_SECRET" } }
                            Divider {}
                            Row_ { label: "Play songs"; hint: "Spotify Connect plays on a device already running Spotify (this PC, your phone, a speaker) without bringing the app up. Needs Premium."
                                RowLayout { spacing: 8
                                    Repeater { model: [["app", "In the app"], ["connect", "Spotify Connect"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.music.mode === modelData[0]
                                               onClicked: root.setSetting("music.mode", modelData[0]) } } } }
                            Divider { visible: root.settings && root.settings.music.mode === "connect" }
                            Row_ { visible: root.settings && root.settings.music.mode === "connect"
                                label: "Play on"; hint: "Where songs go when you don\u2019t name a device. \u201cplay \u2026 on echo dot\u201d always works."
                                RowLayout { spacing: 8
                                    Repeater { model: [["this_pc", "This PC"], ["last_used", "Last used device"]]
                                        Chip { required property var modelData; text: modelData[1]
                                               selected: root.settings && root.settings.music.play_on === modelData[0]
                                               onClicked: root.setSetting("music.play_on", modelData[0]) } } } }
                            Divider { visible: root.settings && root.settings.music.mode === "connect" }
                            Row_ { visible: root.settings && root.settings.music.mode === "connect"
                                label: root.keys.SPOTIFY_REFRESH_TOKEN ? "Spotify account: connected" : "Spotify account"
                                hint: "First add http://127.0.0.1:8888/callback as a Redirect URI in your Spotify app\u2019s settings. Without a login, songs play in the app."
                                Button { text: root.keys.SPOTIFY_REFRESH_TOKEN ? "Log in again" : "Log in"; accent: !root.keys.SPOTIFY_REFRESH_TOKEN
                                         onClicked: { root.flash("Finish the login in your browser\u2026")
                                                      root.ctl(["spotify-login"], r => {
                                                          if (r && r.ok) { root.flash("Spotify connected.")
                                                                           root.ctl(["keys"], k => { if (k) root.keys = k }) }
                                                          else root.flash(r && r.error ? r.error : "Spotify login failed.") }) } } }
                        }

                        Muted { text: "Claude"; Layout.topMargin: 8 }
                        Group {
                            Row_ { label: "API key"; hint: "From console.anthropic.com. Used when the model provider is Claude."
                                KeyField { name: "ANTHROPIC_API_KEY" } }
                            Divider {}
                            Row_ { label: "Model"
                                Field { Layout.preferredWidth: 220; text: root.settings ? root.settings.providers.anthropic.model : ""
                                        onEdited: root.setSetting("providers.anthropic.model", text) } }
                        }

                        Muted { text: "OpenAI-compatible"; Layout.topMargin: 8 }
                        Group {
                            Row_ { label: "API key"; hint: "OpenAI, OpenRouter, Groq, Gemini\u2019s OpenAI endpoint and similar."
                                KeyField { name: "OPENAI_API_KEY" } }
                            Divider {}
                            Row_ { label: "Server URL"
                                Field { Layout.preferredWidth: 300; text: root.settings ? root.settings.providers.openai.base_url : ""
                                        onEdited: root.setSetting("providers.openai.base_url", text) } }
                            Divider {}
                            Row_ { label: "Model"
                                Field { Layout.preferredWidth: 220; text: root.settings ? root.settings.providers.openai.model : ""
                                        onEdited: root.setSetting("providers.openai.model", text) } }
                        }

                        Muted { text: "Local services"; Layout.topMargin: 8 }
                        Group {
                            Row_ { label: "Ollama"; hint: "Where the local model runs."
                                Field { Layout.preferredWidth: 300; text: root.settings ? root.settings.llm.host : ""
                                        onEdited: root.setSetting("llm.host", text) } }
                        }

                        Muted { text: "Web search"; Layout.topMargin: 8 }
                        Group {
                            Row_ { label: "Search engine"; hint: "Used for \u201csearch \u2026\u201d and, while SearXNG is off, for questions that need the web." }
                            Flow { Layout.fillWidth: true; Layout.bottomMargin: 12; spacing: 8
                                Repeater { model: [["google", "Google"], ["duckduckgo", "DuckDuckGo"], ["bing", "Bing"],
                                                   ["brave", "Brave"], ["perplexity", "Perplexity"], ["youtube", "YouTube"]]
                                    Chip { required property var modelData; text: modelData[1]
                                           selected: root.settings && root.settings.web.engine === modelData[0]
                                           onClicked: root.setSetting("web.engine", modelData[0]) } } }
                            Divider {}
                            Row_ { label: "Use SearXNG"; hint: "On: Zade reads the results and answers aloud. Off: it opens the search in your browser."
                                Switch { checked: root.settings ? !!root.settings.web.searxng_enabled : false
                                         onToggled: v => root.setSetting("web.searxng_enabled", v) } }
                            Divider { visible: root.settings && root.settings.web.searxng_enabled }
                            Row_ { label: "SearXNG address"; hint: "Needs the json format enabled in SearXNG's settings."
                                visible: root.settings && root.settings.web.searxng_enabled
                                Field { Layout.preferredWidth: 300; text: root.settings ? root.settings.web.searxng_url : ""
                                        onEdited: root.setSetting("web.searxng_url", text) } }
                        }
                    }

                    // ── Memory
                    ColumnLayout {
                        visible: root.page === "memory"
                        Layout.fillWidth: true
                        spacing: 16
                        Label { text: "Memory"; font.pixelSize: 26; font.weight: Font.Medium }

                        Muted { text: "What Zade knows about you" }
                        Group {
                            RowLayout {
                                Layout.fillWidth: true; Layout.topMargin: 6; Layout.bottomMargin: 6
                                spacing: 10
                                Field { id: newFact; Layout.fillWidth: true; placeholder: "my projects are in ~/code"
                                        onAccepted: addFact.clicked() }
                                Button { id: addFact; text: "Add fact"
                                         onClicked: { if (!newFact.text.trim()) return
                                                      root.ctl(["fact-add", newFact.text], () => root.ctl(["facts"], r => { if (r) root.facts = r }))
                                                      newFact.text = "" } }
                            }
                            Repeater {
                                model: root.facts
                                RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.minimumHeight: 44
                                    Label { text: parent.modelData.text; Layout.fillWidth: true }
                                    Button { text: "Delete"; danger: true
                                             onClicked: root.ctl(["fact-del", String(parent.modelData.id)], () => root.ctl(["facts"], r => { if (r) root.facts = r })) }
                                }
                            }
                        }

                        Muted { text: "Shortcuts"; Layout.topMargin: 8 }
                        Group {
                            Muted { visible: root.shortcuts.length === 0; Layout.topMargin: 12; Layout.bottomMargin: 12
                                    text: "None yet. Teach one by saying “when I say gaming mode, open Steam and Discord”." }
                            Repeater {
                                model: root.shortcuts
                                RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.minimumHeight: 52
                                    spacing: 10
                                    Field { Layout.preferredWidth: 200; text: parent.modelData.phrase
                                            onEdited: if (text.trim() && text !== parent.modelData.phrase)
                                                          root.ctl(["shortcut-rename", parent.modelData.phrase, text], () => root.ctl(["shortcuts"], r => { if (r) root.shortcuts = r })) }
                                    Muted {
                                        Layout.fillWidth: true
                                        text: parent.modelData.actions.map(a => a.name.replace("_", " ") + (a.args.name || a.args.site || a.args.query || a.args.cmd ? " " + (a.args.name || a.args.site || a.args.query || a.args.cmd) : "")).join(", ")
                                              + (parent.modelData.uses ? "  ·  used " + parent.modelData.uses + "×" : "")
                                        elide: Text.ElideRight; maximumLineCount: 1
                                    }
                                    Button { text: "Delete"; danger: true
                                             onClicked: root.ctl(["shortcut-del", parent.modelData.phrase], () => root.ctl(["shortcuts"], r => { if (r) root.shortcuts = r })) }
                                }
                            }
                        }

                        Muted { text: "New shortcut"; Layout.topMargin: 8 }
                        Group {
                            RowLayout {
                                Layout.fillWidth: true; Layout.topMargin: 6; spacing: 10
                                Label { text: "When I say" }
                                Field { id: draftPhrase; Layout.fillWidth: true; placeholder: "gaming mode" }
                            }
                            Flow {
                                Layout.fillWidth: true; Layout.topMargin: 10; spacing: 8
                                Repeater { model: root.stepTypes
                                    Chip { required property var modelData; text: modelData.name
                                           selected: root.draftType === modelData.id
                                           onClicked: root.draftType = modelData.id } }
                            }
                            RowLayout {
                                Layout.fillWidth: true; Layout.topMargin: 8; Layout.bottomMargin: 6; spacing: 10
                                Field { id: draftArg; Layout.fillWidth: true
                                        placeholder: (root.stepTypes.find(t => t.id === root.draftType) || {}).hint || ""
                                        onAccepted: addStep.clicked() }
                                Button { id: addStep; text: "Add step"
                                         onClicked: { if (!draftArg.text.trim()) return
                                                      root.draftSteps = root.draftSteps.concat([root.makeStep(root.draftType, draftArg.text.trim())])
                                                      draftArg.text = "" } }
                            }
                            Repeater {
                                model: root.draftSteps
                                RowLayout {
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth: true; Layout.minimumHeight: 40
                                    Muted { text: (parent.index + 1) + "."; font.pixelSize: 13 }
                                    Label { text: root.describe(parent.modelData); Layout.fillWidth: true }
                                    Button { text: "Remove"; danger: true
                                             onClicked: root.draftSteps = root.draftSteps.filter((_, i) => i !== parent.index) }
                                }
                            }
                            RowLayout {
                                Layout.topMargin: 6; Layout.bottomMargin: 10
                                Button { text: "Save shortcut"; accent: true
                                         onClicked: {
                                             if (!draftPhrase.text.trim() || root.draftSteps.length === 0) { root.flash("Add a phrase and at least one step."); return }
                                             root.ctl(["shortcut-save", draftPhrase.text, JSON.stringify(root.draftSteps)], r => {
                                                 if (r && r.ok) { root.flash("Saved. Say \u201c" + draftPhrase.text + "\u201d any time.")
                                                                  draftPhrase.text = ""; root.draftSteps = []
                                                                  root.ctl(["shortcuts"], x => { if (x) root.shortcuts = x }) }
                                                 else root.flash(r && r.error ? r.error : "Couldn't save the shortcut.") })
                                         } }
                            }
                        }

                        Muted { text: "Reminders"; Layout.topMargin: 8 }
                        Group {
                            RowLayout {
                                Layout.fillWidth: true; Layout.topMargin: 6; Layout.bottomMargin: 6
                                spacing: 10
                                Field { id: remText; Layout.fillWidth: true; placeholder: "drink water" }
                                Field { id: remTime; Layout.preferredWidth: 110; placeholder: "9 am" }
                                Chip { id: remDaily; text: "Every day"; selected: false; onClicked: selected = !selected }
                                Button { text: "Add"
                                         onClicked: { if (!remText.text.trim() || !remTime.text.trim()) return
                                                      root.ctl(["reminder-add", remText.text, remTime.text].concat(remDaily.selected ? ["daily"] : []), r => {
                                                          if (!r || !r.ok) root.flash(r && r.error ? r.error : "Couldn't add that reminder.")
                                                          root.ctl(["reminders"], x => { if (x) root.reminders = x }) })
                                                      remText.text = ""; remTime.text = "" } }
                            }
                            Repeater {
                                model: root.reminders
                                RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.minimumHeight: 44
                                    Label { text: parent.modelData.message; Layout.fillWidth: true }
                                    Muted { text: (parent.modelData.daily ? "every day at " : "")
                                                  + new Date(parent.modelData.due * 1000).toLocaleTimeString(Qt.locale(), "h:mm AP") }
                                    Button { text: "Delete"; danger: true
                                             onClicked: root.ctl(["reminder-del", String(parent.modelData.id)], () => root.ctl(["reminders"], r => { if (r) root.reminders = r })) }
                                }
                            }
                        }
                    }

                    // ── History
                    ColumnLayout {
                        visible: root.page === "history"
                        Layout.fillWidth: true
                        spacing: 16
                        Label { text: "History"; font.pixelSize: 26; font.weight: Font.Medium }
                        Muted { text: "What Zade heard, what it did, and how long it took. Mishearings show up here: add the right word under Settings → Words to expect." }
                        Group {
                            Row_ { label: "Keep"; hint: "Older requests are deleted. The newest 200 are shown here."
                                RowLayout { spacing: 8
                                    Repeater { model: [100, 500, 1000, 5000, 10000]
                                        Chip { required property int modelData; text: modelData.toLocaleString(Qt.locale(), "f", 0)
                                               selected: root.settings && root.settings.history.keep === modelData
                                               onClicked: root.setSetting("history.keep", modelData) } } } }
                        }
                        Group {
                            Muted { visible: root.history.length === 0; text: "Nothing yet."; Layout.topMargin: 12; Layout.bottomMargin: 12 }
                            Repeater {
                                model: root.history
                                ColumnLayout {
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    spacing: 3
                                    Divider { visible: parent.index > 0 }
                                    RowLayout {
                                        Layout.fillWidth: true; Layout.topMargin: 10
                                        spacing: 10
                                        Label { text: parent.parent.modelData.heard; Layout.fillWidth: true; elide: Text.ElideRight; maximumLineCount: 1 }
                                        Rectangle {
                                            implicitWidth: routeText.implicitWidth + 16; implicitHeight: 22; radius: 11
                                            color: parent.parent.modelData.route === "llm" ? Qt.alpha(root.c.tertiary, 0.18) : Qt.alpha(root.c.primary, 0.16)
                                            Text { id: routeText; anchors.centerIn: parent
                                                   text: ({ llm: "model", pattern: "instant", shortcut: "shortcut", laya: "instant" })[parent.parent.parent.modelData.route] || parent.parent.parent.modelData.route
                                                   color: parent.parent.parent.modelData.route === "llm" ? root.c.tertiary : root.c.primary
                                                   font.family: "Readex Pro"; font.pixelSize: 11 }
                                        }
                                        Muted { text: (parent.parent.modelData.ms / 1000).toFixed(1) + " s"; font.pixelSize: 12 }
                                        Muted { text: parent.parent.modelData.ts.slice(11, 16); font.pixelSize: 12 }
                                    }
                                    Muted { text: parent.modelData.reply || "—"; Layout.fillWidth: true; Layout.bottomMargin: 10
                                            maximumLineCount: 2; elide: Text.ElideRight }
                                }
                            }
                        }
                    }
                }
            }
        }

        // Toast
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom; anchors.bottomMargin: 24
            visible: opacity > 0
            opacity: root.toast.length > 0 ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 180 } }
            implicitWidth: toastText.implicitWidth + 36; implicitHeight: 40; radius: 20
            color: root.c.surface_container_highest
            Text { id: toastText; anchors.centerIn: parent; text: root.toast; color: root.c.on_surface
                   font.family: "Readex Pro"; font.pixelSize: 13 }
        }
    }
}
