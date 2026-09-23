import copy

import pytest

from zade import actions, config, info

WTTR = {
    "nearest_area": [{"areaName": [{"value": "Ballard Estate"}]}],
    "current_condition": [{"temp_C": "29", "FeelsLikeC": "34", "humidity": "79", "weatherDesc": [{"value": "Clear "}]}],
    "weather": [
        {"date": "2026-09-23", "maxtempC": "31", "mintempC": "27",
         "hourly": [{"weatherDesc": [{"value": "x"}], "chanceofrain": "0"}] * 4
         + [{"weatherDesc": [{"value": "Sunny"}], "chanceofrain": "5"}]},
        {"date": "2026-09-24", "maxtempC": "29", "mintempC": "28",
         "hourly": [{"weatherDesc": [{"value": "x"}], "chanceofrain": "0"}] * 4
         + [{"weatherDesc": [{"value": "Patchy rain nearby"}], "chanceofrain": "23"}]},
    ],
}


def cfg(tmp_path):
    c = copy.deepcopy(config.DEFAULTS)
    c["paths"]["data"] = str(tmp_path)
    return c


def test_weather_now():
    assert info.format_weather(WTTR, 0) == (
        "Ballard Estate now: 29°C, clear, feels like 34°C, humidity 79%. Today 27 to 31°C, sunny, 5% chance of rain.")


def test_weather_tomorrow_only_forecast():
    assert info.format_weather(WTTR, 1) == "Ballard Estate tomorrow: 28 to 29°C, patchy rain nearby, 23% chance of rain."


def test_web_results_formatting():
    data = {"results": [{"title": "TCP vs UDP", "content": "TCP is reliable.", "url": "https://a.example"},
                        {"title": "UDP", "content": "UDP is fast.", "url": "https://b.example"}]}
    assert info.format_results(data, limit=1) == "1. TCP vs UDP: TCP is reliable. (https://a.example)"


def test_web_search_unavailable_is_a_spoken_failure(monkeypatch, tmp_path):
    def forbidden(url, timeout):
        raise OSError("HTTP Error 403: FORBIDDEN")

    monkeypatch.setattr(info, "_get_json", forbidden)
    with pytest.raises(actions.Failed, match="Web search isn't available"):
        info.web_answer("tcp vs udp", cfg(tmp_path))


def test_notes_add_and_read(tmp_path):
    c = cfg(tmp_path)
    assert info.notes_read(c) == "You have no notes."
    info.note_add("buy milk", c)
    info.note_add("call mom", c)
    out = info.notes_read(c)
    assert "buy milk" in out and "call mom" in out
    assert out.index("call mom") < out.index("buy milk")  # newest first
