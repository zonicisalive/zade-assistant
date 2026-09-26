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
ENV = pathlib.Path("~/.config/zade/env").expanduser()   # API keys, readable only by you
KEYS = ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
DATA = pathlib.Path(config.load(CONFIG)["paths"]["data"]).expanduser()  # the same folder Zade uses
DB = DATA / "zade.db"
LOCK = DATA / "zade.lock"
READY = DATA / "zade.ready"   # holds the pid of the Zade that finished starting up
INBOX = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zade" / "inbox"
REPO = pathlib.Path(__file__).resolve().parent.parent
SERVICE = pathlib.Path("~/.config/systemd/user/zade.service").expanduser()
KOKORO_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sky", "am_michael", "am_adam", "am_puck",
                 "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
                 "hf_alpha", "hf_beta", "hm_omega", "hm_psi",  # Indian voices (Kokoro, offline)
                 "en-IN-NeerjaNeural", "en-IN-PrabhatNeural", "en-IN-NeerjaExpressiveNeural"]  # Indian English, online


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
    tmp = CONFIG.with_suffix(".tmp")  # write then rename: Zade's live reload never sees half a file
    tmp.write_text("# Written by the Zade app; edit freely.\n" + config.dumps(user) + "\n")
    os.replace(tmp, CONFIG)
    return {"ok": True, "live": config.is_live(key)}


def _env_lines():
    return ENV.read_text().splitlines() if ENV.exists() else []


def keys():
    """Which API keys are saved (never the values)."""
    saved = {line.partition("=")[0].strip() for line in _env_lines() if "=" in line}
    return {k: k in saved for k in KEYS}


def set_key(name, value):
    if name not in KEYS:
        return {"ok": False, "error": f"unknown key {name}"}
    lines = [line for line in _env_lines() if line.partition("=")[0].strip() != name]
    if value.strip():
        lines.append(f"{name}={value.strip()}")
    ENV.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV, 0o600)
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
    running = _running()
    try:
        ready = running and READY.read_text().strip() == str(_pid())
    except OSError:
        ready = False
    return {"running": running, "ready": ready, "pid": _pid(),
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


BUILTIN_WAKE = [("hey_jarvis", "Hey Jarvis"), ("alexa", "Alexa"), ("hey_mycroft", "Hey Mycroft"),
                ("hey_rhasspy", "Hey Rhasspy")]


def _wake_dir():
    return DATA / "wake"  # wake word models uploaded in the app, each named after its phrase


def _home_path(p):
    """ "~/..." when under the home folder, as the settings write paths."""
    try:
        return "~/" + str(p.relative_to(pathlib.Path.home()))
    except ValueError:
        return str(p)


def wake_list():
    """Wake words to choose from: [model, phrase, removable]. Built in, your trained zade.onnx, and uploads."""
    out = [[m, name, False] for m, name in BUILTIN_WAKE]
    if (DATA / "zade.onnx").exists():
        out.insert(0, [_home_path(DATA / "zade.onnx"), "Hey Zade", False])
    for p in sorted(_wake_dir().glob("*.onnx")):
        out.append([_home_path(p), p.stem.replace("_", " ").title(), True])
    return out


def wake_add(src, phrase):
    """Use your own openWakeWord model (.onnx): check that it loads and runs, keep a copy named after the
    phrase it listens for, and switch to it."""
    import re

    src = pathlib.Path(src).expanduser()
    slug = re.sub(r"[^a-z0-9]+", "_", (phrase or "").lower()).strip("_")
    if not src.is_file() or src.suffix.lower() != ".onnx":
        return {"ok": False, "error": "Pick an openWakeWord model file ending in .onnx."}
    if not slug:
        return {"ok": False, "error": "Type the phrase the model listens for, like \"hey computer\"."}
    try:
        import numpy as np
        from openwakeword.model import Model

        Model(wakeword_models=[str(src)], inference_framework="onnx").predict(np.zeros(1280, np.int16))
    except Exception as e:
        return {"ok": False, "error": f"That file isn't a working wake word model: {e}"}
    _wake_dir().mkdir(parents=True, exist_ok=True)
    dest = _wake_dir() / f"{slug}.onnx"
    dest.write_bytes(src.read_bytes())
    set_setting("wake.model", _home_path(dest))
    return {"ok": True, "model": _home_path(dest), "phrase": slug.replace("_", " ").title()}


def wake_del(model):
    """Remove an uploaded wake word model (never a built-in one or your trained zade.onnx)."""
    p = pathlib.Path(model).expanduser()
    if p.parent != _wake_dir() or not p.is_file():
        return {"ok": False, "error": "Only wake words you added can be removed."}
    p.unlink()
    if pathlib.Path(config.load(CONFIG)["wake"]["model"]).expanduser() == p:
        set_setting("wake.model", "hey_jarvis")  # it was in use: back to a built-in one
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
    if cmd == "keys":
        return keys()
    if cmd == "key-set":
        return set_key(args[0], args[1])
    if cmd == "spotify-login":  # opens the browser; Spotify sends you back to Zade
        from . import music
        from .actions import Failed

        config.load_env(ENV)
        cid, secret = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
        if not cid or not secret:
            return {"ok": False, "error": "Save the Spotify Client ID and secret first."}
        try:
            return set_key("SPOTIFY_REFRESH_TOKEN", music.login(cid, secret))
        except (Failed, OSError, ValueError, KeyError) as e:
            return {"ok": False, "error": f"Spotify login failed: {e}"}
    if cmd == "calibrate":  # "quiet" (measure the room), then "voice" (measure you, get a recommendation)
        from . import calibrate

        if args[0] == "play":  # calibrate play before|after [noise_factor]
            return calibrate.play(DATA, args[1], float(args[2]) if len(args) > 2 else None)
        if args[0] == "stats":  # calibrate stats <noise_factor>
            return {"ok": True, **calibrate.stats(DATA, float(args[1]))}
        return calibrate.run(args[0], DATA)
    if cmd == "voice-enroll":  # learn the owner's voice from their recorded clips (python -m zade.record_wake)
        import wave

        import numpy as np

        from . import voice_focus

        clips = []
        for f in sorted((DATA / "wake_samples").glob("*/*.wav")):
            with wave.open(str(f)) as w:
                clips.append(np.frombuffer(w.readframes(w.getnframes()), np.int16))
        if not clips:
            return {"ok": False, "error": "Record your voice first: python -m zade.record_wake"}
        return {"ok": True, "clips": voice_focus.enroll(clips, DATA)}
    if cmd == "wake-list":
        return {"ok": True, "words": wake_list()}
    if cmd == "wake-add":  # the app's "Add your own": a model file and the phrase it listens for
        return wake_add(args[0] if args else "", " ".join(args[1:]))
    if cmd == "wake-del":
        return wake_del(args[0] if args else "")
    if cmd == "voices":
        return KOKORO_VOICES
    if cmd == "preview":
        return preview(args[0], args[1])
    if cmd == "say":  # a typed command for the running Zade
        INBOX.parent.mkdir(parents=True, exist_ok=True)
        with INBOX.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)  # Zade empties the inbox under this lock
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
    if cmd == "shortcut-save":  # from the app's shortcut builder
        steps = json.loads(args[1])
        allowed = {t["name"] for t in __import__("zade.brain", fromlist=["TOOLS"]).TOOLS}
        if not steps or any(not isinstance(st, dict) or st.get("name") not in allowed
                            or not isinstance(st.get("args", {}), dict) for st in steps):
            return {"ok": False, "error": "Each step needs a known action."}
        from .router import normalize  # stored the way speech is matched ("Hey Zade, gaming mode" -> "gaming mode")

        memory.add_shortcut(conn, normalize(args[0]), [{"name": st["name"], "args": st.get("args", {})}
                                                           for st in steps])
        return {"ok": True}
    if cmd == "shortcut-rename":
        from .router import normalize

        return {"ok": memory.rename_shortcut(conn, args[0], normalize(args[1])) > 0}
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
