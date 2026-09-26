import copy

import pytest

from zade import actions, config, info

METEO = {
    "current": {"temperature_2m": 26.5, "apparent_temperature": 31.6, "relative_humidity_2m": 91, "weather_code": 2},
    "daily": {"temperature_2m_max": [31.2, 30.1, 29.0], "temperature_2m_min": [25.4, 25.6, 25.0],
              "precipitation_probability_max": [25, 14, None], "weather_code": [51, 61, 0]},
}


def cfg(tmp_path):
    c = copy.deepcopy(config.DEFAULTS)
    c["paths"]["data"] = str(tmp_path)
    return c


def test_weather_now():
    assert info.format_weather("Silvassa", METEO, 0) == (
        "Silvassa now: 27 degrees Celsius, partly cloudy, feels like 32, humidity 91%. "
        "Today 25 to 31 degrees Celsius, light drizzle, 25% chance of rain.")


def test_weather_tomorrow_only_forecast():
    assert info.format_weather("Silvassa", METEO, 1) == \
        "Silvassa tomorrow: 26 to 30 degrees Celsius, light rain, 14% chance of rain."
    assert "0% chance" in info.format_weather("Silvassa", METEO, 2)


def test_weather_finds_the_place(monkeypatch):
    urls = []

    def get(url, timeout):
        urls.append(url)
        if "geocoding" in url:
            return {"results": [{"name": "Silvassa", "latitude": 20.27, "longitude": 72.99}]}
        return METEO

    monkeypatch.setattr(info, "_get_json", get)
    assert info.weather("silvassa").startswith("Silvassa now: 27 degrees Celsius")
    assert "latitude=20.27" in urls[1]
    monkeypatch.setattr(info, "_get_json", lambda url, timeout: {"results": []})
    with pytest.raises(actions.Failed, match="couldn't find a place"):
        info.weather("atlantisville")


def test_web_results_formatting():
    data = {"results": [{"title": "TCP vs UDP", "content": "TCP is reliable.", "url": "https://a.example"},
                        {"title": "UDP", "content": "UDP is fast.", "url": "https://b.example"}]}
    assert info.format_results(data, limit=1) == "1. TCP vs UDP: TCP is reliable. (https://a.example)"


def test_web_search_unavailable_is_a_spoken_failure(monkeypatch, tmp_path):
    def forbidden(url, timeout):
        raise OSError("HTTP Error 403: FORBIDDEN")

    monkeypatch.setattr(info, "_get_json", forbidden)
    c = cfg(tmp_path)
    c["web"]["searxng_enabled"] = True
    with pytest.raises(actions.Failed, match="Web search isn't available"):
        info.web_answer("tcp vs udp", c)


def test_notes_add_and_read(tmp_path):
    c = cfg(tmp_path)
    assert info.notes_read(c) == "You have no notes."
    info.note_add("buy milk", c)
    info.note_add("call mom", c)
    out = info.notes_read(c)
    assert "buy milk" in out and "call mom" in out
    assert out.index("call mom") < out.index("buy milk")  # newest first


def test_without_searxng_web_answers_open_a_browser_search(monkeypatch, tmp_path):
    c = cfg(tmp_path)
    c["web"].update(searxng_enabled=False, engine="brave")
    opened = []
    monkeypatch.setattr(actions, "_spawn", opened.append)
    monkeypatch.setattr(info, "_get_json", lambda url, timeout: pytest.fail("SearXNG must not be called"))
    assert "do not make up an answer" in info.web_answer("tcp vs udp", c)
    assert opened == [["xdg-open", "https://search.brave.com/search?q=tcp+vs+udp"]]
    assert actions.search_url("x y", "nope") == "https://www.google.com/search?q=x+y"


def test_a_note_with_line_breaks_stays_one_note(tmp_path):
    cfg = {"paths": {"data": str(tmp_path)}, "notes": {"file": str(tmp_path / "notes.md")}}
    info.note_add("buy milk\nand eggs", cfg)
    assert info.notes_read(cfg) == "Your notes, newest first: buy milk and eggs."
