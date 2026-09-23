import configparser
import datetime
import pathlib
import re
import shlex
import subprocess
import urllib.parse

from rapidfuzz import fuzz, process

APP_DIRS = [pathlib.Path("/usr/share/applications"), pathlib.Path("~/.local/share/applications").expanduser()]
ROOT = re.compile(r"\b(sudo|su|pkexec|doas)\b", re.IGNORECASE)
YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do it", "run it", "go ahead"}
NO = {"no", "nope", "don't", "dont", "cancel", "stop", "wait"}
MEDIA = {"play-pause", "play", "pause", "next", "previous"}
TIMEOUT_S = 30


class Failed(Exception):
    pass


def is_yes(text):
    t = " ".join(re.sub(r"[^a-z' ]", " ", text.lower()).split())
    if not t or set(t.split()) & NO:
        return False
    return any(t == y or t.startswith(y + " ") for y in YES)


def find_app(name, dirs=APP_DIRS):
    apps = {}
    for d in dirs:
        for f in sorted(d.glob("*.desktop")) if d.exists() else []:
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
                if e.get("NoDisplay") == "true" or not e.get("Exec"):
                    continue
                entry = (f.stem, pathlib.Path(shlex.split(e["Exec"])[0]).name)
            except (configparser.Error, KeyError, ValueError, UnicodeDecodeError):
                continue
            apps[e.get("Name", f.stem).lower()] = entry
            apps.setdefault(f.stem.lower(), entry)
    m = process.extractOne(name.lower(), list(apps), scorer=fuzz.WRatio, score_cutoff=85)
    return apps[m[0]] if m else None


def _call(cmd):
    subprocess.run(cmd, check=False, capture_output=True)


def _spawn(cmd):
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def shell(cmd, confirm):
    if ROOT.search(cmd):
        raise Failed("I won't run commands that need root.")
    if not confirm(f"Run {cmd}?"):
        raise Failed("Cancelled.")
    try:
        # ponytail: timeout kills the shell, not grandchildren; use a process group if that bites.
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise Failed("That took too long, stopped it.") from None
    out = (p.stdout + p.stderr).strip()
    return out[:2000] if out else f"Done, exit code {p.returncode}."


def run(action, confirm):
    name, a = action["name"], action.get("args", {})
    if name in ("open_app", "close_app"):
        app = find_app(a["name"])
        if not app:
            raise Failed(f"I couldn't find {a['name']}.")
        if name == "open_app":
            _spawn(["gtk-launch", app[0]])
        else:
            _call(["pkill", "-x", app[1]])
        return ""
    if name == "volume":
        if "set" in a:
            level = f"{max(0, min(100, int(a['set'])))}%"
        else:
            d = int(a.get("delta", 10))
            level = f"{abs(d)}%{'+' if d >= 0 else '-'}"
        _call(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", level])
        return ""
    if name == "mute":
        _call(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
        return ""
    if name == "media":
        if a.get("cmd") not in MEDIA:
            raise Failed(f"Unknown media command {a.get('cmd')}.")
        _call(["playerctl", a["cmd"]])
        return ""
    if name == "time":
        return datetime.datetime.now().strftime("It's %-I:%M %p.")
    if name == "date":
        return datetime.datetime.now().strftime("Today is %A, %B %-d.")
    if name == "web_search":
        _spawn(["xdg-open", "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(a["query"])])
        return ""
    if name == "lock_screen":
        _call(["loginctl", "lock-session"])
        return ""
    if name == "shell":
        return shell(a["cmd"], confirm)
    raise Failed(f"I don't know how to {name.replace('_', ' ')}.")
