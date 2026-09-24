import copy
import json
from types import SimpleNamespace

import pytest

from zade import config, music


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    monkeypatch.setattr(music, "config", SimpleNamespace(load_env=lambda *a: None))  # never the real keys
    monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN", raising=False)


def test_env_file_is_loaded_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / "env"
    env.write_text("# keys\nSPOTIFY_CLIENT_ID=abc\nSPOTIFY_CLIENT_SECRET = 'xyz'\n\nALREADY=file\n")
    monkeypatch.setenv("ALREADY", "shell")
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    config.load_env(env)
    import os
    assert os.environ["SPOTIFY_CLIENT_ID"] == "abc" and os.environ["SPOTIFY_CLIENT_SECRET"] == "xyz"
    assert os.environ["ALREADY"] == "shell"


def test_play_finds_track_and_plays_it_in_spotify(monkeypatch):
    calls = []
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(music, "_token", lambda cid, secret: "TOKEN")

    def search(query, token):
        calls.append(("search", query, token))
        return {"tracks": {"items": [{"uri": "spotify:track:123", "name": "Killshot",
                                      "artists": [{"name": "Eminem"}]}]}}

    monkeypatch.setattr(music, "_search", search)
    monkeypatch.setattr(music, "_open", lambda uri: calls.append(("open", uri)))
    assert music.play("killshot by eminem") == "Playing Killshot by Eminem."
    assert calls == [("search", "killshot by eminem", "TOKEN"), ("open", "spotify:track:123")]


def test_without_keys_opens_spotify_search(monkeypatch):
    opened = []
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.setattr(music, "_open", opened.append)
    monkeypatch.setattr(music, "_find_keyless", lambda q: None)
    assert "search" in music.play("killshot").lower()
    assert opened == ["spotify:search:killshot"]


def test_without_keys_finds_the_track_on_the_web(monkeypatch):
    page = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fopen.spotify.com%2Ftrack%2F'
            '6EtYag2uAqy6ccWTn0x2BV&amp;rut=2f">Permanent Scar - song and lyrics by Zeus | Spotify</a>')

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return page.encode()

    monkeypatch.setattr(music.urllib.request, "urlopen", lambda req, timeout: Resp())
    assert music._find_keyless("permanent scar zeus") == ("spotify:track:6EtYag2uAqy6ccWTn0x2BV",
                                                          "Permanent Scar by Zeus")
    opened = []
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.setattr(music, "_open", opened.append)
    assert music.play("permanent scar zeus") == "Playing Permanent Scar by Zeus."
    assert opened == ["spotify:track:6EtYag2uAqy6ccWTn0x2BV"]


def keyed(monkeypatch, calls):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(music, "_token", lambda cid, secret: "TOKEN")
    monkeypatch.setattr(music, "_search", lambda q, t: {"tracks": {"items": [
        {"uri": "spotify:track:9", "name": "Scars", "artists": [{"name": "Juice WRLD"}]}]}})
    monkeypatch.setattr(music, "_open", lambda uri: calls.append(("app", uri)))


def test_connect_mode_plays_on_the_active_device(monkeypatch):
    calls = []
    keyed(monkeypatch, calls)
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "R")
    monkeypatch.setattr(music, "_post_token", lambda cid, secret, form: {"access_token": "USER"})

    def api(method, path, token, body=None):
        calls.append((method, path, token, json.dumps(body) if body else None))
        if path == "/me/player/devices":
            return {"devices": [{"id": "pc", "name": "PC", "is_active": False},
                                {"id": "ph", "name": "Phone", "is_active": True}]}

    monkeypatch.setattr(music, "_api", api)
    assert music.play("scars", "connect") == "Playing Scars by Juice WRLD on Phone."
    assert calls[-1] == ("PUT", "/me/player/play?device_id=ph", "USER", '{"uris": ["spotify:track:9"]}')
    assert not [c for c in calls if c[0] == "app"]


def test_connect_falls_back_to_the_app(monkeypatch):
    calls = []
    keyed(monkeypatch, calls)
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "R")

    def forbidden(*a):
        raise OSError("HTTP Error 403: Premium required")

    monkeypatch.setattr(music, "_post_token", forbidden)
    assert music.play("scars", "connect") == "Playing Scars by Juice WRLD."
    assert calls == [("app", "spotify:track:9")]
    calls.clear()
    assert music.play("scars", "app") == "Playing Scars by Juice WRLD." and calls == [("app", "spotify:track:9")]
