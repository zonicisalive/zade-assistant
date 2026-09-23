import configparser
import datetime
import pathlib
import re
import shlex
import subprocess
import urllib.parse

from rapidfuzz import fuzz, process

APP_DIRS = [pathlib.Path("/usr/share/applications"), pathlib.Path("~/.local/share/applications").expanduser()]
ROOT = re.compile(r"\b(sudo|su|pkexec|doas|run0)\b", re.IGNORECASE)
START = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do", "run", "go"}
ALLOWED = START | {"it", "ahead", "please", "zade"}
NO = {"no", "nope", "don't", "dont", "cancel", "stop", "wait"}
MEDIA = {"play-pause", "play", "pause", "next", "previous"}
TIMEOUT_S = 30


class Failed(Exception):
    pass


def is_yes(text):
    t = " ".join(re.sub(r"[^a-z' ]", " ", text.lower()).split())
    words = t.split()
    if not words or set(words) & NO:
        return False
    return words[0] in START and all(w in ALLOWED for w in words)


BROWSER_WORDS = {"browser", "the browser", "web browser", "internet"}


def _default_browser():
    r = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True)
    return r.stdout.strip()


def find_app(name, dirs=APP_DIRS):
    if name.lower().strip() in BROWSER_WORDS:
        name = _default_browser().removesuffix(".desktop") or name
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


def _feed(cmd, text):
    subprocess.run(cmd, input=text, text=True, check=False, capture_output=True)


def _output(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout


SITES = {
    "youtube": "https://www.youtube.com", "github": "https://github.com", "reddit": "https://www.reddit.com",
    "gmail": "https://mail.google.com", "google": "https://www.google.com", "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai", "twitter": "https://x.com", "x": "https://x.com",
    "instagram": "https://www.instagram.com", "whatsapp": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.in", "wikipedia": "https://en.wikipedia.org",
    "spotify": "https://open.spotify.com", "linkedin": "https://www.linkedin.com", "maps": "https://maps.google.com",
}
WINDOW = {
    "close": ["close-window"], "fullscreen": ["fullscreen-window"], "maximize": ["maximize-column"],
    "focus_left": ["focus-column-left"], "focus_right": ["focus-column-right"],
    "overview": ["toggle-overview"], "workspace": ["focus-workspace"],
    "move_to_workspace": ["move-window-to-workspace"],
}
POWER = {
    "suspend": ("Suspend the computer?", ["systemctl", "suspend"]),
    "reboot": ("Restart the computer?", ["systemctl", "reboot"]),
    "shutdown": ("Shut down the computer?", ["systemctl", "poweroff"]),
    "logout": ("Log out?", ["niri", "msg", "action", "quit", "--skip-confirmation"]),
}


def site_url(site):
    s = site.lower().strip().removeprefix("the ").removesuffix(" website")
    if s.replace(" ", "") in SITES:
        return SITES[s.replace(" ", "")]
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", s):
        return "https://" + s
    return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(s)


def _spawn(cmd):
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def shell(cmd, confirm):
    if ROOT.search(re.sub(r"[\\'\"]", "", cmd)):
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
        if not app and name == "open_app" and a["name"].lower().replace(" ", "") in SITES:
            _spawn(["xdg-open", SITES[a["name"].lower().replace(" ", "")]])  # no app, but a known website
            return ""
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
    if name == "window":
        if a.get("action") not in WINDOW:
            raise Failed(f"I can't do {a.get('action')} with windows.")
        extra = [str(a["workspace"])] if a["action"] in ("workspace", "move_to_workspace") else []
        _call(["niri", "msg", "action", *WINDOW[a["action"]], *extra])
        return ""
    if name == "open_website":
        _spawn(["xdg-open", site_url(a["site"])])
        return ""
    if name == "clipboard_read":
        text = _output(["wl-paste", "--no-newline"]).strip()
        return f"Your clipboard says: {text[:2000]}" if text else "Your clipboard is empty."
    if name == "clipboard_copy":
        _feed(["wl-copy"], a["text"])
        return "Copied."
    if name == "type_text":
        _call(["wtype", "--", a["text"]])
        return ""
    if name == "brightness":
        if "set" in a:
            _call(["ddcutil", "setvcp", "10", str(max(0, min(100, int(a["set"]))))])
        else:
            d = int(a.get("delta", 10))
            _call(["ddcutil", "setvcp", "10", "+" if d >= 0 else "-", str(abs(d))])
        return ""
    if name == "screenshot":
        _call(["niri", "msg", "action", "screenshot-screen"])
        return "Screenshot saved."
    if name == "power":
        if a.get("action") not in POWER:
            raise Failed(f"Unknown power action {a.get('action')}.")
        question, cmd = POWER[a["action"]]
        if not confirm(question):
            raise Failed("Cancelled.")
        _call(cmd)
        return ""
    raise Failed(f"I don't know how to {name.replace('_', ' ')}.")
