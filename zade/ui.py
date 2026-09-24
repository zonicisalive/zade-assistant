"""State feed for the Quickshell overlay (ui/shell.qml): one small JSON file it watches.

States: listening, thinking, speaking, done (reply stays briefly, then hides), idle (hide now).
"""

import json
import logging
import os
import pathlib
import subprocess

log = logging.getLogger("zade")

PATH = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zade" / "state.json"
_state = {}


STYLE_KEYS = ("position", "size", "accent", "linger_s", "reveal_cps", "show_heard")
_style = {}


def configure(cfg):
    """Overlay style from settings; sent with every state update so it applies live."""
    _style.clear()
    _style.update({k: cfg["ui"][k] for k in STYLE_KEYS})
    _state["style"] = dict(_style)
    _state["persona"] = dict(cfg["persona"])
    _write()


def reset():
    persona = _state.get("persona", {})
    _state.clear()
    _state.update(state="idle", heard="", reply="", level=0.0, emotion="neutral", style=dict(_style), persona=persona)


def _write():
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(_state))
    except OSError as e:  # the overlay is optional: never let it break the assistant
        log.debug("ui state write failed: %s", e)


def show(state, **fields):
    _state.update(state=state, **fields)
    if state != "listening":
        _state["level"] = 0.0
    _write()


def set(**fields):
    """Update fields (e.g. emotion) without changing the state."""
    _state.update(fields)
    _write()


def level(value):
    _state["level"] = round(max(0.0, min(1.0, value)), 2)
    _write()


def start(cfg):
    """Launch the overlay as a child process; returns it (or None when disabled/unavailable)."""
    if not cfg["ui"]["enabled"]:
        return None
    reset()
    configure(cfg)
    host = pathlib.Path(cfg["ui"]["host_file"]).expanduser()
    try:
        if "Zade/ui/Overlay.qml" in host.read_text():
            log.info("overlay hosted by %s", host)  # the desktop shell shows it: no second Quickshell
            return None
    except OSError:
        pass
    shell = pathlib.Path(__file__).resolve().parent.parent / "ui" / "shell.qml"
    try:
        return subprocess.Popen(["qs", "-p", str(shell)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log.warning("overlay disabled, could not start quickshell: %s", e)
        return None


reset()
