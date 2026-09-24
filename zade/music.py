"""Play a song by name in the Spotify app.

Spotify's Web API (a free developer app; keys in SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET) finds the
track, then the running Spotify app plays it through MPRIS, or (music.mode = "connect", after
`python -m zade.ctl spotify-login`, Premium only) Spotify Connect plays it on your active device. Without keys, a DuckDuckGo search for the song's
open.spotify.com page finds it; if that fails too, Spotify's search opens instead.
"""

import base64
import html
import json
import logging
import os
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request

from . import config
from .actions import Failed

log = logging.getLogger("zade")
REDIRECT = "http://127.0.0.1:8888/callback"  # add this in your Spotify app's settings
SCOPES = "user-modify-playback-state user-read-playback-state"

_cache = {"token": None, "expires": 0.0}


def _token(cid, secret):
    if _cache["token"] and time.time() < _cache["expires"] - 60:
        return _cache["token"]
    data = _post_token(cid, secret, {"grant_type": "client_credentials"})
    _cache.update(token=data["access_token"], expires=time.time() + data.get("expires_in", 3600))
    return _cache["token"]


def _search(query, token):
    url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode({"q": query, "type": "track", "limit": 10})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _find_keyless(query):
    """(uri, "Song by Artist") from the first open.spotify.com/track result on DuckDuckGo, or None."""
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": f"site:open.spotify.com/track {query}"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            page = r.read().decode("utf-8", "replace")
    except OSError:
        return None
    m = re.search(r'open\.spotify\.com%2Ftrack%2F([A-Za-z0-9]{22})[^>]*>([^<]+)', page)
    if not m:
        return None
    # Titles look like "Permanent Scar - song and lyrics by Zeus | Spotify".
    title = re.sub(r"\s*\|\s*Spotify\s*$", "", html.unescape(m[2]))
    title = re.sub(r"\s+-\s+(?:single|song|album)(?: and lyrics)? by\s+", " by ", title, flags=re.I)
    return f"spotify:track:{m[1]}", title


def artist_score(said, track):
    """How much the artist heard sounds like the track's artists; spaces are ignored because speech
    recognition splits names ("Amine AM" for Eminem)."""
    from rapidfuzz import fuzz

    said = said.lower()
    return max((max(fuzz.partial_ratio(said, a["name"].lower()),
                    fuzz.ratio(said.replace(" ", ""), a["name"].lower().replace(" ", "")))
                for a in track["artists"]), default=0)


def pick_track(query, items):
    """The result that best matches "song by artist" (Spotify's own top hit is often another song
    by the same artist); for a bare query, Spotify's order."""
    from rapidfuzz import fuzz

    m = re.fullmatch(r"(.+) by (.+)", query.lower())
    if not m or not items:
        return items[0] if items else None
    song, artist = m[1], m[2]

    def score(t):
        return fuzz.ratio(song, t["name"].lower()) + (40 if artist_score(artist, t) >= 70 else 0)

    return max(items, key=score)  # max keeps Spotify's order on ties


def _running():
    players = subprocess.run(["playerctl", "-l"], capture_output=True, text=True).stdout
    return "spotify" in players


def _open(uri):
    """Hand a spotify: URI to the app, starting Spotify first if it isn't running."""
    if not _running():
        subprocess.Popen(["spotify"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(40):  # wait up to ~10 s for its media controls to appear
            time.sleep(0.25)
            if _running():
                time.sleep(1.0)  # let it finish starting up
                break
    subprocess.run(["playerctl", "--player=spotify", "open", uri], capture_output=True, timeout=5)


def _post_token(cid, secret, form):
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=urllib.parse.urlencode(form).encode(),
                                 headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _api(method, path, token, body=None):
    req = urllib.request.Request("https://api.spotify.com/v1" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


_user_cache = {}


def _user_token(cid, secret, refresh):
    """Access token for your account, reused for its hour of life (a fresh one per song cost seconds)."""
    if _user_cache.get("refresh") != refresh or time.time() > _user_cache.get("expires", 0) - 60:
        data = _post_token(cid, secret, {"grant_type": "refresh_token", "refresh_token": refresh})
        _user_cache.update(refresh=refresh, token=data["access_token"],
                           expires=time.time() + data.get("expires_in", 3600))
    return _user_cache["token"]


# "on this system", "on my pc", "here": this computer, which plays through its own Spotify app.
THIS_PC = re.compile(r"(?:(?:this|my|the) )?(?:system|pc|computer|laptop|desktop|machine|device)|here")


def split_this_pc(query):
    if (m := re.fullmatch(r"(.+) (?:on|in) (.+)", query)) and THIS_PC.fullmatch(m[2]):
        return m[1], True
    return query, False


def _devices(token):
    return _api("GET", "/me/player/devices", token)["devices"]


def _match_device(devices, name):
    """The device whose name best matches what was said ("echo dot", "my phone"), or None."""
    from rapidfuzz import fuzz

    name = re.sub(r"^(?:the|my) ", "", name.lower().strip())
    kinds = {"phone": "smartphone", "speaker": "speaker", "computer": "computer", "pc": "computer", "tv": "tv"}
    scored = [(max(fuzz.partial_ratio(name, d["name"].lower()), 100 if kinds.get(name) == d["type"].lower() else 0), d)
              for d in devices]
    score, device = max(scored, key=lambda x: x[0], default=(0, None))
    return device if score >= 80 else None


def split_device(query, devices):
    """"scars on echo dot" -> ("scars", device) when the words after the last "on" name a device."""
    if m := re.fullmatch(r"(.+) (?:on|in) (.+)", query):
        if device := _match_device(devices, m[2]):
            return m[1], device
    return query, None


def _play_connect(uri, token, devices, device=None):
    """Play on the chosen device, else the active one (or the first available); its name, or None."""
    if not devices:
        return None
    device = device or next((d for d in devices if d["is_active"]), devices[0])
    _api("PUT", "/me/player/play?device_id=" + device["id"], token, {"uris": [uri]})
    return device["name"]


def login(cid, secret, open_browser=None, timeout_s=180):
    """One-time Spotify login for Spotify Connect: returns a refresh token."""
    import http.server
    import secrets
    import webbrowser

    state = secrets.token_urlsafe(16)
    got = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state") == [state]:
                got.update(code=q.get("code", [""])[0], error=q.get("error", [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Zade is connected to Spotify. You can close this tab.</h2>")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8888), Callback)
    server.timeout = 1
    (open_browser or webbrowser.open)("https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
        {"client_id": cid, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES, "state": state}))
    deadline = time.time() + timeout_s
    while not got and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if not got.get("code"):
        raise Failed(got.get("error") or "Spotify login timed out.")
    return _post_token(cid, secret, {"grant_type": "authorization_code", "code": got["code"],
                                     "redirect_uri": REDIRECT})["refresh_token"]


YOUTUBE = {"youtube": "https://www.youtube.com/watch?v=", "youtube music": "https://music.youtube.com/watch?v="}


def _youtube_id(query):
    """The first video in YouTube's search results (no API key needed), or None."""
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', r.read().decode("utf-8", "replace"))
    except OSError:
        return None
    return m[1] if m else None


def play_youtube(query, provider="youtube"):
    name = "YouTube Music" if provider == "youtube music" else "YouTube"
    if vid := _youtube_id(query):
        subprocess.Popen(["xdg-open", YOUTUBE[provider] + vid], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return f"Playing {query} on {name}."
    subprocess.Popen(["xdg-open", "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return f"I opened {name} search for {query}."


def play(query, mode="app", provider="spotify", device="", play_on="this_pc"):
    if provider in YOUTUBE:
        return play_youtube(query, provider)
    config.load_env(override=True)  # keys saved (or replaced) in the app since Zade started
    cid, secret = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not secret:
        if found := _find_keyless(query):
            _open(found[0])
            return f"Playing {found[1]}."
        _open("spotify:search:" + urllib.parse.quote(query))
        return f"I couldn't find {query}, so I opened Spotify's search. Pick the song there."
    if device and THIS_PC.fullmatch(device.lower().strip()):
        device, here = "", True
    else:
        query, here = split_this_pc(query)
    refresh = os.environ.get("SPOTIFY_REFRESH_TOKEN")
    user = devices = chosen = None
    if refresh and not here and (mode == "connect" or device or " on " in query):
        try:
            user = _user_token(cid, secret, refresh)
            devices = _devices(user)
        except (OSError, ValueError, KeyError) as e:
            log.warning("Spotify Connect unavailable: %s", e)
    if devices is not None:
        if device:
            chosen = _match_device(devices, device)
            if not chosen:
                names = ", ".join(d["name"] for d in devices) or "nothing"
                raise Failed(f"I can't find {device} in Spotify. It sees {names}.")
        else:
            query, chosen = split_device(query, devices)
    try:
        items = _search(re.sub(r" by ", " ", query), _token(cid, secret))["tracks"]["items"]
    except (OSError, ValueError, KeyError) as e:
        raise Failed("I couldn't reach Spotify right now.") from e
    if not items:
        raise Failed(f"I couldn't find {query} on Spotify.")
    track = pick_track(query, items)
    if (m := re.fullmatch(r"(.+) by (.+)", query.lower())) and artist_score(m[2], track) < 70:
        # A misheard artist ("Amine AM") steers the search away: look for the song alone and take the
        # version whose artist sounds closest.
        try:
            same_song = _search(m[1], _token(cid, secret))["tracks"]["items"]
        except (OSError, ValueError, KeyError):
            same_song = []
        best = max(same_song, key=lambda t: artist_score(m[2], t), default=None)
        if best and artist_score(m[2], best) >= 70:
            track = best
    name = f"{track['name']} by {track['artists'][0]['name']}"
    if user and not chosen and mode == "connect" and play_on == "this_pc":
        # No speaker named: this PC's own Spotify (a Connect device named after the computer), or the app
        # below when it isn't open. Only "last used" follows Spotify to wherever it played last.
        chosen = next((d for d in devices if d["type"].lower() == "computer"
                       and d["name"].lower() == socket.gethostname().lower()), None)
        if not chosen:
            user = None
    if user and (mode == "connect" or chosen):  # a named speaker always plays there
        try:
            if where := _play_connect(track["uri"], user, devices, chosen):
                return f"Playing {name} on {where}."
        except (OSError, ValueError, KeyError) as e:  # 403 without Premium, expired login, network
            log.warning("Spotify Connect failed, using the app: %s", e)
    _open(track["uri"])
    return f"Playing {name}."
