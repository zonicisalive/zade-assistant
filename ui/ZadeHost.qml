// Loads Zade's overlay inside a desktop shell (e.g. inir) and keeps it up to date.
//
// Hook, inside the shell's ShellRoot:
//     LazyLoader { active: true; source: "file:///home/you/Zade/ui/ZadeHost.qml" }
//
// It never reloads the shell by itself: an automatic reload restarted the whole desktop shell
// mid-use. After updating Zade's overlay code, restart the shell once to load the new version.
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
}
