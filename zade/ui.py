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


def reset():
    _state.clear()
    _state.update(state="idle", heard="", reply="", level=0.0)


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


def level(value):
    _state["level"] = round(max(0.0, min(1.0, value)), 2)
    _write()


def start(cfg):
    """Launch the overlay as a child process; returns it (or None when disabled/unavailable)."""
    if not cfg["ui"]["enabled"]:
        return None
    reset()
    _write()
    shell = pathlib.Path(__file__).resolve().parent.parent / "ui" / "shell.qml"
    try:
        return subprocess.Popen(["qs", "-p", str(shell)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log.warning("overlay disabled, could not start quickshell: %s", e)
        return None


reset()
