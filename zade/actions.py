import configparser
import datetime
import json
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
ROOT = re.compile(r"\b(sudo|su|pkexec|doas|run0|systemd-run|machinectl|runuser|setpriv|chroot|nsenter)\b",
                  re.IGNORECASE)
# A spoken yes: it starts with one of these and says nothing else ("yes, send it", "go ahead", "haan bhej do").
START = {"yes", "yeah", "yea", "yep", "yup", "ya", "sure", "ok", "okay", "alright", "do", "run", "go", "send",
         "confirm", "confirmed", "correct", "right", "absolutely", "definitely", "please", "affirmative",
         "haan", "han", "haa", "ha", "hanji", "ji", "bilkul", "theek", "thik", "chalo", "kar", "karo", "bhej", "bhejo"}
ALLOWED = START | {"it", "ahead", "zade", "that", "that's", "thats", "fine", "is", "now", "do", "send", "the", "message",
                   "sure", "yes", "hai", "h", "de", "do", "dijiye", "na", "bro", "bhai", "yaar", "of", "course", "go"}
NO = {"no", "nope", "nah", "don't", "dont", "not", "cancel", "stop", "wait", "never", "nahi", "nahin", "mat", "ruko",
      "ruk", "rehne", "wrong"}
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


_app_cache = {}


def _apps(dirs):
    """{lowercase name or desktop id: (desktop id, executable)} for every visible app. Cached until an
    app folder changes (installing or removing an app updates the folder's modification time)."""
    key = tuple((str(d), d.stat().st_mtime if d.exists() else 0) for d in dirs)
    if key not in _app_cache:
        _app_cache.clear()
        _app_cache[key] = _scan_apps(dirs)
    return _app_cache[key]


def _scan_apps(dirs):
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
    q = name.lower()
    for m in process.extract(q, list(apps), scorer=fuzz.WRatio, score_cutoff=85, limit=5):
        # A short app name inside a long request ("discord" in "discord whatsapp and telegram") is not a
        # match, nor are a few letters inside a name ("it" scores 90 against "kitty"): it must be spelled
        # close to the name, or be whole words of it ("writer" for "libreoffice writer").
        if len(name) <= len(m[0]) * 1.6 and (fuzz.ratio(q, m[0]) >= 75 or set(q.split()) <= set(m[0].split())):
            return apps[m[0]]
    return None


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


def _call(cmd, timeout=15):
    """Run a command; its failure is a spoken error, not a false "Done", and a hung one (ddcutil on a
    monitor that doesn't answer) can't stall Zade."""
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:  # e.g. wtype or playerctl not installed: a spoken failure, never a crash
        raise Failed(f"{cmd[0]} isn't installed, so I can't do that.") from None
    except subprocess.TimeoutExpired:
        raise Failed(f"{cmd[0]} didn't answer, so that may not have worked.") from None
    if p.returncode:
        error = next((line for line in (p.stderr or p.stdout).splitlines() if line.strip()), "")
        raise Failed(f"That didn't work: {error.strip()}." if error else f"{cmd[0]} couldn't do that.")


def typed(text):
    """Text as wtype will type it: each line break presses Enter (\\r too), other control characters would
    press keys nobody asked for, so they're dropped."""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text.replace("\r\n", "\n").replace("\r", "\n"))


def _feed(cmd, text):
    subprocess.run(cmd, input=text, text=True, check=False, capture_output=True)


def _output(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout


def _app_windows(app):
    """The app's open windows (niri), matched by window app id against its desktop id, and its executable
    unless that's a launcher or other apps share it (Thunar's Bulk Rename runs thunar: closing it must not
    close every Thunar window; every Steam game runs steam)."""
    desktop_id, exe = app
    names = {desktop_id.lower(), desktop_id.lower().rsplit(".", 1)[-1]}
    if exe not in WRAPPERS | {"steam"} and sum(e[1] == exe for e in set(_apps(APP_DIRS).values())) <= 1:
        names.add(exe.lower())
    try:
        windows = json.loads(_output(["niri", "msg", "-j", "windows"]) or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return [w for w in windows if (w.get("app_id") or "").lower() in names]


# What a screenshot is of, besides an app's name: the whole screen, or the focused window ("is/ye": Hindi "this")
SCREEN_WORDS = {"", "screen", "whole screen", "full screen", "entire screen", "display", "monitor", "desktop"}
THIS_WINDOW = {"this", "this one", "current", "focused", "active", "window", "is", "iss", "ye", "yeh"}


def shot_target(said):
    """ "the firefox window" -> "firefox", "my screen" -> "screen", "this window" -> "this"."""
    return re.sub(r"^(?:the|my) | (?:window|app)$", "", " ".join(said.lower().split()))


def screenshot(app=""):
    """The screen; with app, only that app's window (the one used last, if it has several), wherever it is:
    niri draws the window on its own, so nothing is moved or focused. Saved where niri saves screenshots
    (screenshot-path), and copied to the clipboard."""
    target = shot_target(app)
    if target in SCREEN_WORDS:
        _call(["niri", "msg", "action", "screenshot-screen"])
        return "Screenshot saved."
    if target in THIS_WINDOW:
        _call(["niri", "msg", "action", "screenshot-window"])
        return "Screenshot of this window saved."
    found = find_app(target)
    windows = _app_windows(found) if found else []
    if not windows:
        raise Failed(f"{app} has no open window." if found else f"I couldn't find {app}.")
    stamp = lambda w: (bool(w.get("is_focused")), (w.get("focus_timestamp") or {}).get("secs", 0),
                       (w.get("focus_timestamp") or {}).get("nanos", 0))
    _call(["niri", "msg", "action", "screenshot-window", "--id", str(max(windows, key=stamp)["id"])])
    return f"Screenshot of {target} saved."


def _focus_open_window(app):
    """Focus the app's window if it's already open (niri). Relaunching a running Electron app like
    Discord takes seconds: it starts, finds the running copy, hands over and quits."""
    w = next(iter(_app_windows(app)), None)
    if w:
        _call(["niri", "msg", "action", "focus-window", "--id", str(w["id"])])
    return bool(w)


def _close_app(app, name):
    """Close the app's windows (as its close button would); without a window, end its process by name.
    pkill matches at most 15 characters of a name and misses wrapper scripts (google-chrome-stable is a
    script for "chrome"), so a closed window is the reliable way, and a miss is reported, not claimed."""
    windows = _app_windows(app)
    for w in windows:
        _call(["niri", "msg", "action", "close-window", "--id", str(w["id"])])
    if windows:
        return
    if app[1] == "steam":  # a Steam game's launcher is Steam itself; pkill would close all of Steam
        raise Failed(f"I can't close Steam games yet. Close {name} from the game.")
    if app[1] in WRAPPERS:  # e.g. Spotify starts via "sh": pkill would kill every shell
        raise Failed(f"I can't close {name} safely by name. Focus it and say close this window.")
    if len(app[1]) > 15 or subprocess.run(["pkill", "-x", app[1]], capture_output=True).returncode != 0:
        raise Failed(f"{name} doesn't seem to be open.")


SITES = {
    "youtube": "https://www.youtube.com", "github": "https://github.com", "reddit": "https://www.reddit.com",
    "gmail": "https://mail.google.com", "google": "https://www.google.com", "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai", "twitter": "https://x.com", "x": "https://x.com",
    "instagram": "https://www.instagram.com", "whatsapp": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.in", "wikipedia": "https://en.wikipedia.org",
    "spotify": "https://open.spotify.com", "linkedin": "https://www.linkedin.com", "maps": "https://maps.google.com",
    "facebook": "https://www.facebook.com", "fb": "https://www.facebook.com", "messenger": "https://www.messenger.com",
    "threads": "https://www.threads.net", "pinterest": "https://www.pinterest.com", "flipkart": "https://www.flipkart.com",
    "hotstar": "https://www.hotstar.com", "primevideo": "https://www.primevideo.com", "twitch": "https://www.twitch.tv",
    "drive": "https://drive.google.com", "outlook": "https://outlook.live.com", "stackoverflow": "https://stackoverflow.com",
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
CLOSING_COMBOS = {("alt", "F4"), ("ctrl", "F4"), ("ctrl", "q"), ("ctrl", "w"), ("logo", "q")}


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


def key_combos(keys):
    """ "ctrl+a, ctrl+c" -> ["ctrl+a", "ctrl+c"] (empty entries dropped), for pressing and for safety checks."""
    return [c.strip() for c in re.split(r",|\s+then\s+", keys) if c.strip()]


def is_closing(keys):
    """True if any combo in `keys` usually closes a window or app (Alt+F4, Ctrl+Q/W, Super+Q)."""
    for c in key_combos(keys):
        try:
            mods, k = _key_parts(c)
        except Failed:
            continue  # run() refuses the whole sequence anyway
        if any((m, k) in CLOSING_COMBOS for m in mods):
            return True
    return False


# Keys the model may press without a yes: moving around, media and everyday edits. Anything else asks, as with
# typing it's how a model tricked by a web page could run a command: Enter and its terminal twins (Ctrl+M,
# Ctrl+J, Ctrl+O), pastes (Ctrl+Shift+V, Shift+Insert), Super (opens terminals and apps), typed letters.
MOVE_KEYS = {"Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End", "Tab", "Escape"}
EDIT_KEYS = {"a", "c", "x", "z", "y", "f", "t", "Tab", "Prior", "Next", "plus", "minus", "equal", "0"}  # with Ctrl


def harmless_keys(keys):
    """True if every combo in `keys` is one the model may press on its own (see MOVE_KEYS)."""
    for c in key_combos(keys):
        try:
            mods, k = _key_parts(c)
        except Failed:
            return False
        mods = set(mods)
        if not (k in MOVE_KEYS and mods in ({"shift"}, {"ctrl"}, {"alt"}, {"ctrl", "shift"}, set())
                or k in EDIT_KEYS and mods in ({"ctrl"}, {"ctrl", "shift"})
                or (k.startswith("XF86Audio") or k in ("F5", "F11")) and not mods):
            return False
    return True


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
    s = re.sub(r" (?:website|web site|site|webpage)$", "", site.lower().strip().removeprefix("the "))
    if s.replace(" ", "") in SITES:
        return SITES[s.replace(" ", "")]
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", s):
        return "https://" + s
    # Unknown name: DuckDuckGo's "\" search jumps straight to the first result, i.e. the site itself
    # ("dominos pizza" opens dominos.co.in, not a page of results).
    return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus("\\" + s)


def _spawn(cmd):
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "busybox"}


def needs_root(cmd):
    """Root tools by name, or anything that could hide one: a command built from $(...), `...`, ${...} or a
    variable ("$a id"), a command name with wildcards ("/usr/bin/sud? id", "pkexe[c] id"), eval or source,
    or a shell running what a pipe feeds it ("echo ... | base64 -d | sh"). A courtesy check: the spoken yes
    is the real guard."""
    if ROOT.search(re.sub(r"[\\'\"]", "", cmd)) or re.search(r"\$\(|`|\$\{", cmd):
        return True
    seps = [""] + re.findall(r"[;&|]+|\n", cmd)
    for sep, part in zip(seps, re.split(r"[;&|]+|\n", cmd)):  # the first word of every command in a pipeline or list
        words = part.split()
        words = words[next((i for i, w in enumerate(words) if "=" not in w), len(words)):]  # skip VAR=value
        if not words:
            continue
        name = words[0].strip("'\"").rsplit("/", 1)[-1]
        if re.search(r"[*?\[$]", words[0]) or name in ("eval", "source", ".") or sep in ("|", "|&") and name in SHELLS:
            return True
    return False


def shell(cmd, confirm):
    if needs_root(cmd):
        raise Failed("I won't run commands that need root, or hide which command they run.")
    if re.search(r"[\x00-\x08\x0a-\x1f\x7f]", cmd):  # a line break would hide the rest from the question
        raise Failed("I only run commands written on one line.")
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


def _focused_app():
    try:
        return (json.loads(_output(["niri", "msg", "-j", "focused-window"]) or "{}") or {}).get("app_id") or ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def _wait_focus(name, timeout_s):
    """Whether a window of `name` has focus, waiting up to timeout_s for it."""
    deadline = time.monotonic() + timeout_s
    while name not in _focused_app().lower():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.3)
    return True


def send_message(to, text, app="discord"):
    """Message a person on Discord the way a user would: its quick switcher (Ctrl+K) finds "@name" among
    people only, Enter opens the chat, then the text is typed and sent. Keys go only to Discord: it must
    have focus before typing and before each Enter, or the message could land in a terminal."""
    if (app or "discord").lower() != "discord":
        raise Failed(f"I can only send messages on Discord for now, not {app}.")
    run({"name": "open_app", "args": {"name": "discord"}}, None)
    if not _wait_focus("discord", 25):  # a cold start (with its update check) can take a while
        raise Failed("Discord didn't come to the front, so I didn't send anything.")
    time.sleep(1.0)  # let the window take keys
    # ponytail: trusts the switcher's top match for "@name" (exact usernames come first); reading the chat
    # header back with OCR would catch a wrong pick
    for step, pause in ((wtype_args("ctrl+k"), 0.7), (["--", "@" + to.lstrip("@")], 1.2),
                        (wtype_args("enter"), 1.5), (["--", text], 0.3), (wtype_args("enter"), 0)):
        if not _wait_focus("discord", 2):
            raise Failed("Discord lost focus, so I stopped before sending.")
        _call(["wtype", *step])
        time.sleep(pause)
    return f"Sent to {to} on Discord."


def app_volume(app, set_to=None, delta=None):
    """One app's volume (its sound streams in PipeWire, like the volume mixer). Spotify playing on another
    device has no stream here: then Spotify's own volume, through your login."""
    from rapidfuzz import fuzz

    try:
        streams = json.loads(_output(["pactl", "-f", "json", "list", "sink-inputs"]) or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        streams = []
    q = " ".join(app.lower().split())
    if len(q) < 3:  # "" is inside every name: it must never turn every app down
        raise Failed("Which app's volume?")
    name = lambda s: " ".join(filter(None, (s["properties"].get("application.name"),
                                             s["properties"].get("application.process.binary")))).lower()
    # The name as a whole word ("chrome", not the "Chromium" stream Discord and other Electron apps show),
    # else a close spelling, but only if that's one app (its streams: a browser has one per tab)
    hits = [s for s in streams if re.search(rf"\b{re.escape(q)}\b", name(s))]
    if not hits:
        close = [s for s in streams if fuzz.partial_ratio(q, name(s)) >= 85]
        hits = close if len({name(s) for s in close}) == 1 else []
    if not hits:
        if "spotify" in q:
            from . import music

            return music.connect_volume(set_to, delta)
        raise Failed(f"{app} isn't playing any sound right now.")
    current = int(next(iter(hits[0]["volume"].values()))["value_percent"].rstrip("%"))
    target = max(0, min(100, int(set_to) if set_to is not None else current + int(delta or 10)))
    for s in hits:
        _call(["pactl", "set-sink-input-volume", str(s["index"]), f"{target}%"])
    return f"{hits[0]['properties'].get('application.name') or app} is at {target} percent."


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
            if not _focus_open_window(app):
                _spawn(["gtk-launch", app[0]])
        elif app[1] in PROTECTED or any(p in app[1].lower() for p in ("niri", "quickshell", "portal", "pipewire")):
            raise Failed(f"I won't close {a['name']}, it keeps your desktop running.")
        else:
            _close_app(app, a["name"])
        return ""
    if name == "app_volume":
        return app_volume(a["app"], a.get("set"), a.get("delta"))
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
        # action="mute"/"unmute" from the model (a boolean "on" was ambiguous and it muted the wrong way);
        # on=True/False from instant patterns and saved shortcuts
        on = a["action"] != "unmute" if "action" in a else a.get("on", True)
        _call(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if on else "0"])
        return "Muted." if on else "Sound is back on."
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
        _call(["wtype", "--", typed(a["text"])])
        return ""
    if name == "send_message":
        return send_message(a["to"], a["text"], a.get("app", "discord"))
    if name == "discord":
        from . import discord

        try:
            return discord.run(a)
        except discord.Unavailable:
            raise Failed("Discord isn't open, or the ZadeControl plugin is off.") from None
        except discord.Failed as e:
            raise Failed(str(e)) from None
    if name == "brightness":
        if "set" in a:
            _call(["ddcutil", "setvcp", "10", str(max(0, min(100, int(a["set"]))))])
        else:
            d = int(a.get("delta", 10))
            _call(["ddcutil", "setvcp", "10", "+" if d >= 0 else "-", str(abs(d))])
        return ""
    if name == "press_keys":
        combos = key_combos(a["keys"])
        parsed = [_key_parts(c) for c in combos]  # validate every combo before pressing any
        for c, (mods, key) in zip(combos, parsed):
            if _blocked(mods, key):
                raise Failed(f"I won't press {c}, it could end your session.")
        for c in combos:
            _call(["wtype", *wtype_args(c)])
        return ""
    if name == "screenshot":
        return screenshot(a.get("app") or "")
    if name == "power":
        if a.get("action") not in POWER:
            raise Failed(f"Unknown power action {a.get('action')}.")
        question, cmd = POWER[a["action"]]
        if not confirm(question):
            raise Failed("Cancelled.")
        _call(cmd)
        return ""
    raise Failed(f"I don't know how to {name.replace('_', ' ')}.")
