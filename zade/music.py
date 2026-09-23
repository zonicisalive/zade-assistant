"""Play a song by name in the Spotify app.

Spotify's Web API (a free developer app; keys in SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET) finds the
track, then the running Spotify app plays it through MPRIS. Without keys, Spotify's search opens instead.
"""

import base64
import json
import os
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
        _open("spotify:search:" + urllib.parse.quote(query))
        return f"I opened Spotify's search for {query}. Add Spotify keys to play songs directly."
    try:
        items = _search(query, _token(cid, secret))["tracks"]["items"]
    except (OSError, ValueError, KeyError) as e:
        raise Failed("I couldn't reach Spotify right now.") from e
    if not items:
        raise Failed(f"I couldn't find {query} on Spotify.")
    track = items[0]
    _open(track["uri"])
    return f"Playing {track['name']} by {track['artists'][0]['name']}."
