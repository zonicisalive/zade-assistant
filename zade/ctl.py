"""zade ctl: JSON control interface for the desktop app (ui/app.qml).

    python -m zade.ctl <command> [args...]   ->  one JSON document on stdout
"""

import fcntl
import json
import os
import pathlib
import signal
import subprocess
import sys
import tomllib

from . import config, memory

CONFIG = pathlib.Path("~/.config/zade/config.toml").expanduser()
DATA = pathlib.Path(config.DEFAULTS["paths"]["data"]).expanduser()
DB = DATA / "zade.db"
LOCK = DATA / "zade.lock"
INBOX = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zade" / "inbox"
REPO = pathlib.Path(__file__).resolve().parent.parent
SERVICE = pathlib.Path("~/.config/systemd/user/zade.service").expanduser()
KOKORO_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sky", "am_michael", "am_adam", "am_puck",
                 "bf_emma", "bf_isabella", "bm_george", "bm_lewis"]


def _user_settings():
    return tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def _coerce(default, text):
    if isinstance(default, bool):
        return text.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(float(text))
    if isinstance(default, float):
        return float(text)
    if isinstance(default, list):
        return [x.strip() for x in text.split(",") if x.strip()]
    return text


def set_setting(key, text):
    *tables, name = key.split(".")
    default = config.DEFAULTS
    for t in tables:
        default = default.get(t, {})
    if not tables or name not in default or isinstance(default[name], dict):
        return {"ok": False, "error": f"unknown setting {key}"}
    user = _user_settings()
    node = user
    for t in tables:
        node = node.setdefault(t, {})
    node[name] = _coerce(default[name], text)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text("# Written by the Zade app; edit freely.\n" + config.dumps(user) + "\n")
    return {"ok": True}


def _pid():
    try:
        pid = int((DATA / "zade.pid").read_text())
        cmdline = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
        return pid if b"zade" in cmdline and b"ctl" not in cmdline else None
    except (OSError, ValueError):
        return None


def _running():
    """True while a Zade holds its lock (reliable even during its first seconds of startup)."""
    try:
        with open(LOCK) as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def status():
    return {"running": _running(), "pid": _pid(),
            "autostart": SERVICE.exists() and _systemctl("is-enabled", "zade").stdout.strip() == "enabled"}


def start():
    if _running():
        return {"ok": True, "already": True}
    if SERVICE.exists():
        _systemctl("start", "zade")
    else:
        log = open(DATA / "zade.log", "a")
        subprocess.Popen([sys.executable, "-m", "zade"], cwd=REPO, stdout=log, stderr=log, start_new_session=True)
    return {"ok": True}


def stop():
    if SERVICE.exists() and _systemctl("is-active", "zade").stdout.strip() == "active":
        _systemctl("stop", "zade")
    elif pid := _pid():
        os.kill(pid, signal.SIGTERM)
    return {"ok": True}


def autostart(on):
    if on:
        SERVICE.parent.mkdir(parents=True, exist_ok=True)
        SERVICE.write_text((REPO / "systemd" / "zade.service").read_text()
                           .replace("%h/Zade/.venv/bin/zade", str(REPO / ".venv" / "bin" / "zade")))
        _systemctl("daemon-reload")
        _systemctl("enable", "zade")
    elif SERVICE.exists():
        _systemctl("disable", "zade")
    return {"ok": True}


def preview(voice, speed):
    cfg = config.load(CONFIG)
    cfg["tts"].update(voice=voice, speed=float(speed))
    from . import tts

    tts.speak(f"Hi, I'm Zade. This is how I sound.", cfg)
    return {"ok": True}


def run(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("status", [])
    if cmd == "status":
        return status()
    if cmd == "start":
        return start()
    if cmd == "stop":
        return stop()
    if cmd == "restart":
        stop()
        import time

        for _ in range(40):
            if not _running():
                break
            time.sleep(0.1)
        return start()
    if cmd == "autostart":
        return autostart(args[0] == "on")
    if cmd == "settings":
        return config.load(CONFIG)
    if cmd == "set":
        return set_setting(args[0], args[1])
    if cmd == "voices":
        return KOKORO_VOICES
    if cmd == "preview":
        return preview(args[0], args[1])
    if cmd == "say":  # a typed command for the running Zade
        INBOX.parent.mkdir(parents=True, exist_ok=True)
        with INBOX.open("a") as f:
            f.write(args[0].replace("\n", " ") + "\n")
        return {"ok": True}

    conn = memory.connect(DB)
    if cmd == "facts":
        return memory.fact_rows(conn)
    if cmd == "fact-add":
        memory.add_fact(conn, args[0])
        return {"ok": True}
    if cmd == "fact-del":
        return {"ok": memory.delete_fact(conn, int(args[0])) > 0}
    if cmd == "shortcuts":
        return memory.shortcut_rows(conn)
    if cmd == "shortcut-rename":
        return {"ok": memory.rename_shortcut(conn, args[0], args[1].strip().lower()) > 0}
    if cmd == "shortcut-del":
        return {"ok": memory.delete_shortcut(conn, args[0]) > 0}
    if cmd == "reminders":
        return memory.reminder_rows(conn)
    if cmd == "reminder-add":
        from .__main__ import next_time

        daily = len(args) > 2 and args[2] == "daily"
        memory.add_reminder(conn, next_time(args[1]).timestamp(), args[0], 86400 if daily else None)
        return {"ok": True}
    if cmd == "reminder-del":
        return {"ok": memory.delete_reminder(conn, int(args[0])) > 0}
    if cmd == "history":
        return memory.requests(conn)
    return {"ok": False, "error": f"unknown command {cmd}"}


def main(argv=None):
    try:
        out = run(sys.argv[1:] if argv is None else argv)
    except (IndexError, ValueError, OSError) as e:
        out = {"ok": False, "error": str(e)}
    return json.dumps(out)


if __name__ == "__main__":
    print(main())
