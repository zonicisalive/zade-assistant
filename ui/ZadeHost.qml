// Loads Zade's overlay inside a desktop shell (e.g. inir) and keeps it up to date.
//
// Hook, inside the shell's ShellRoot:
//     LazyLoader { active: true; source: "file:///home/you/Zade/ui/ZadeHost.qml" }
//
// Shells often run with hot reload off, so they would keep an old copy of the overlay forever.
// This watches Zade's own UI files and reloads the shell only when one of them changes
// (i.e. when Zade is updated), never during normal use.
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: host
    readonly property string dir: Qt.resolvedUrl(".").toString().replace("file://", "")

    LazyLoader {
        active: true
        source: host.dir + "Overlay.qml"
        onItemChanged: if (item) console.info("[Zade] overlay loaded")
    }

    FileView { path: host.dir + "Overlay.qml"; watchChanges: true; onFileChanged: reloadLater.restart() }
    FileView { path: host.dir + "Face.qml"; watchChanges: true; onFileChanged: reloadLater.restart() }
    FileView { path: host.dir + "ZadeHost.qml"; watchChanges: true; onFileChanged: reloadLater.restart() }

    // Debounced: an update touches several files at once.
    Timer {
        id: reloadLater
        interval: 1000
        onTriggered: {
            console.info("[Zade] overlay files changed, reloading the shell")
            Quickshell.reload(false)
        }
    }
}
