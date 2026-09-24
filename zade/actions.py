import configparser
import datetime
import pathlib
import re
import shlex
import subprocess
import time
import urllib.parse

from rapidfuzz import fuzz, process

APP_DIRS = [
    pathlib.Path("/usr/share/applications"),
    pathlib.Path("/var/lib/flatpak/exports/share/applications"),
    pathlib.Path("~/.local/share/flatpak/exports/share/applications").expanduser(),
    pathlib.Path("~/.local/share/applications").expanduser(),  # last: your own entries win
]
ROOT = re.compile(r"\b(sudo|su|pkexec|doas|run0)\b", re.IGNORECASE)
START = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do", "run", "go"}
ALLOWED = START | {"it", "ahead", "please", "zade"}
NO = {"no", "nope", "don't", "dont", "cancel", "stop", "wait"}
MEDIA = {"play-pause", "play", "pause", "next", "previous"}
TIMEOUT_S = 30
# Never closed by Zade, whoever asks: closing these ends the desktop session or breaks audio/apps.
PROTECTED = {"niri", "qs", "quickshell", "inir", "pipewire", "pipewire-pulse", "wireplumber", "xdg-desktop-portal",
             "xdg-desktop-portal-gtk", "xdg-desktop-portal-gnome", "dbus-daemon", "dbus-broker", "systemd",
             "Xwayland", "xwayland-satellite", "zade", "gnome-keyring-daemon", "polkit"}
WRAPPERS = {"sh", "bash", "zsh", "env", "flatpak", "python", "python3", "gtk-launch", "xdg-open", "sudo", "exec"}
SINK = ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@"]
RAMP_STEP, RAMP_DELAY_S = 5, 0.03  # desktop "volume protection" rejects jumps of ~10% or more


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


def _apps(dirs):
    """{lowercase name or desktop id: (desktop id, executable)} for every visible app."""
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
            # Only the last part of an ID like "com.github.wwmm.easyeffects": the rest (github, kde, gnome)
            # is not the app's name and would make "open github" launch the wrong app.
            apps.setdefault(f.stem.lower().rsplit(".", 1)[-1], entry)
    return apps


def find_app(name, dirs=None):
    if name.lower().strip() in BROWSER_WORDS:
        name = _default_browser().removesuffix(".desktop") or name
    apps = _apps(dirs or APP_DIRS)
    m = process.extractOne(name.lower(), list(apps), scorer=fuzz.WRatio, score_cutoff=85)
    # A short app name inside a long request ("discord" in "discord whatsapp and telegram") is not a match.
    if m and len(name) > len(m[0]) * 1.6:
        return None
    return apps[m[0]] if m else None


def closest_app(name, dirs=None):
    """Best guess for a misheard app name (spaces ignored, "megacamillion" -> "MECCHA CHAMELEON"), or None."""
    names = {n: n for n in _apps(dirs or APP_DIRS)}
    squash = lambda t: t.replace(" ", "").lower()
    target = squash(name)
    # Only compare names of similar length: short names ("htop") otherwise match anything by accident.
    similar = {n: squash(n) for n in names
               if min(len(squash(n)), len(target)) >= 0.6 * max(len(squash(n)), len(target))}
    m = process.extractOne(target, similar, scorer=fuzz.ratio, score_cutoff=55)
    if not m:
        return None
    for d in dirs or APP_DIRS:  # report the name as the app spells it
        for f in d.glob("*.desktop") if d.exists() else []:
            try:
                cp = configparser.ConfigParser(interpolation=None, strict=False)
                cp.read(f, encoding="utf-8")
                if cp["Desktop Entry"].get("Name", "").lower() == m[2]:
                    return cp["Desktop Entry"]["Name"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
    return m[2]


def all_app_count(dirs=None):
    return len({entry for entry in _apps(dirs or APP_DIRS).values()})


USER_APP_DIRS = [pathlib.Path("~/.local/share/applications").expanduser()]


def app_names(dirs=USER_APP_DIRS):
    """Names of user-installed apps and games (e.g. Steam), for speech-recognition hotwords."""
    names = []
    for d in dirs:
        for f in sorted(d.glob("*.desktop")) if d.exists() else []:
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if e.get("NoDisplay") != "true" and e.get("Name"):
                names.append(e["Name"])
    return list(dict.fromkeys(names))


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


# Key presses (wtype). Spoken or written names -> Wayland keysyms.
KEY_MODS = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "altgr": "altgr", "super": "logo",
            "win": "logo", "windows": "logo", "meta": "logo", "logo": "logo", "cmd": "logo", "command": "logo"}
KEY_NAMES = {"enter": "Return", "return": "Return", "tab": "Tab", "escape": "Escape", "esc": "Escape",
             "space": "space", "spacebar": "space", "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
             "insert": "Insert", "home": "Home", "end": "End", "page up": "Prior", "pageup": "Prior",
             "page down": "Next", "pagedown": "Next", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
             "print": "Print", "print screen": "Print", "caps lock": "Caps_Lock", "menu": "Menu",
             "volume up": "XF86AudioRaiseVolume", "volume down": "XF86AudioLowerVolume", "mute": "XF86AudioMute",
             "play": "XF86AudioPlay", "pause": "XF86AudioPlay", "next": "XF86AudioNext", "previous": "XF86AudioPrev",
             "minus": "minus", "plus": "plus", "equals": "equal", "comma": "comma", "period": "period",
             "dot": "period", "slash": "slash"}
CLOSING_COMBOS = {("alt", "F4"), ("ctrl", "q"), ("ctrl", "w"), ("logo", "q")}


def _key_parts(combo):
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    mods, key = [], None
    for p in parts:
        if p in KEY_MODS:
            mods.append(KEY_MODS[p])
        elif key is None:
            if p in KEY_NAMES:
                key = KEY_NAMES[p]
            elif m := re.fullmatch(r"f(\d{1,2})", p):
                key = f"F{m[1]}"
            elif len(p) == 1:
                key = p
            else:
                raise Failed(f"I don't know the key {p}.")
        else:
            raise Failed(f"I can only press one key at a time in {combo}.")
    if key is None:
        raise Failed(f"Which key should I press with {combo}?")
    return mods, key


def wtype_args(combo):
    mods, key = _key_parts(combo)
    return [a for m in mods for a in ("-M", m)] + ["-k", key] + [a for m in reversed(mods) for a in ("-m", m)]


def _blocked(mods, key):
    # Super+Shift+E quits niri (ends the session); Ctrl+Alt+Delete/Backspace can reboot or kill the session.
    return ({"logo", "shift"} <= set(mods) and key.lower() == "e") or \
           ({"ctrl", "alt"} <= set(mods) and key in ("Delete", "BackSpace"))


def is_closing(keys):
    """True if any combo in `keys` usually closes a window or app (Alt+F4, Ctrl+Q/W, Super+Q)."""
    try:
        return any((m, k) in CLOSING_COMBOS for c in re.split(r",\s*|\s+then\s+", keys)
                   for mods, k in [_key_parts(c)] for m in mods)
    except Failed:
        return False


SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "bing": "https://www.bing.com/search?q=",
    "brave": "https://search.brave.com/search?q=",
    "perplexity": "https://www.perplexity.ai/search?q=",
    "youtube": "https://www.youtube.com/results?search_query=",
}


def search_url(query, engine="google"):
    return SEARCH_ENGINES.get(engine, SEARCH_ENGINES["google"]) + urllib.parse.quote_plus(query)


def site_url(site):
    s = site.lower().strip().removeprefix("the ").removesuffix(" website")
    if s.replace(" ", "") in SITES:
        return SITES[s.replace(" ", "")]
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", s):
        return "https://" + s
    # Unknown name: DuckDuckGo's "\" search jumps straight to the first result, i.e. the site itself
    # ("dominos pizza" opens dominos.co.in, not a page of results).
    return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus("\\" + s)


def _spawn(cmd):
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def shell(cmd, confirm):
    if ROOT.search(re.sub(r"[\\'\"]", "", cmd)):
        raise Failed("I won't run commands that need root.")
    # Long commands are shown in the overlay (after the newline) instead of being read aloud.
    question = f"Run {cmd}?" if len(cmd) <= 40 else f"Should I run this command?\n{cmd}"
    if not confirm(question):
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
            guess = closest_app(a["name"]) if name == "open_app" else None
            raise Failed(f"I couldn't find {a['name']}." + (f" Did you mean {guess}?" if guess else ""))
        if name == "open_app":
            _spawn(["gtk-launch", app[0]])
        elif app[1] in PROTECTED or any(p in app[1].lower() for p in ("niri", "quickshell", "portal", "pipewire")):
            raise Failed(f"I won't close {a['name']}, it keeps your desktop running.")
        elif app[1] == "steam":  # a Steam game's launcher is Steam itself; pkill would close all of Steam
            raise Failed(f"I can't close Steam games yet. Close {a['name']} from the game.")
        elif app[1] in WRAPPERS:  # e.g. Spotify starts via "sh": pkill would kill every shell
            raise Failed(f"I can't close {a['name']} safely by name. Focus it and say close this window.")
        else:
            _call(["pkill", "-x", app[1]])
        return ""
    if name == "volume":
        current = round(float(_output(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]).split()[1]) * 100)
        target = int(a["set"]) if "set" in a else current + int(a.get("delta", 10))
        target = max(0, min(100, target))
        steps = list(range(current + RAMP_STEP, target, RAMP_STEP)) + [target] if target > current else [target]
        for i, level in enumerate(steps):
            if i:
                time.sleep(RAMP_DELAY_S)
            _call([*SINK, f"{level}%"])
        return ""
    if name == "mute":  # set the state, never toggle: a toggle flips the wrong way when the state is unknown
        _call(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if a.get("on", True) else "0"])
        return "Muted." if a.get("on", True) else "Sound is back on."
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
        _spawn(["xdg-open", search_url(a["query"], a.get("engine", "google"))])
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
    if name == "press_keys":
        combos = [c for c in re.split(r",\s*|\s+then\s+", a["keys"]) if c.strip()]
        parsed = [_key_parts(c) for c in combos]  # validate every combo before pressing any
        for c, (mods, key) in zip(combos, parsed):
            if _blocked(mods, key):
                raise Failed(f"I won't press {c}, it could end your session.")
        for c in combos:
            _call(["wtype", *wtype_args(c)])
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
