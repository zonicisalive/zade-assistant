"""Play a song by name in the Spotify app.

Spotify's Web API (a free developer app; keys in SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET) finds the
track, then the running Spotify app plays it through MPRIS. Without keys, a DuckDuckGo search for the song's
open.spotify.com page finds it; if that fails too, Spotify's search opens instead.
"""

import base64
import html
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from .actions import Failed

_cache = {"token": None, "expires": 0.0}


def _token(cid, secret):
    if _cache["token"] and time.time() < _cache["expires"] - 60:
        return _cache["token"]
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials",
                                 headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.load(r)
    _cache.update(token=data["access_token"], expires=time.time() + data.get("expires_in", 3600))
    return _cache["token"]


def _search(query, token):
    url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode({"q": query, "type": "track", "limit": 1})
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


def play(query):
    cid, secret = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not secret:
        if found := _find_keyless(query):
            _open(found[0])
            return f"Playing {found[1]}."
        _open("spotify:search:" + urllib.parse.quote(query))
        return f"I couldn't find {query}, so I opened Spotify's search. Pick the song there."
    try:
        items = _search(query, _token(cid, secret))["tracks"]["items"]
    except (OSError, ValueError, KeyError) as e:
        raise Failed("I couldn't reach Spotify right now.") from e
    if not items:
        raise Failed(f"I couldn't find {query} on Spotify.")
    track = items[0]
    _open(track["uri"])
    return f"Playing {track['name']} by {track['artists'][0]['name']}."
