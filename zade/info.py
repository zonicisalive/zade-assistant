"""Information tools: weather (Open-Meteo), web answers (local SearXNG), notes (a text file)."""

import datetime
import json
import math
import pathlib
import urllib.parse
import urllib.request

from . import actions
from .actions import Failed

DAYS = ["today", "tomorrow", "the day after tomorrow"]


def _get_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# WMO weather codes (Open-Meteo) as spoken words.
WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _r(t):
    return math.floor(t + 0.5)  # 26.5 -> 27 (round() would give 26)


def _deg(t):
    return f"{_r(t)} degrees Celsius"


def format_weather(place, data, day):
    d = data["daily"]
    forecast = (f"{_r(d['temperature_2m_min'][day])} to {_deg(d['temperature_2m_max'][day])}, "
                f"{WMO.get(d['weather_code'][day], 'mixed weather')}, "
                f"{d['precipitation_probability_max'][day] or 0}% chance of rain.")
    if day:
        return f"{place} {DAYS[day]}: {forecast}"
    c = data["current"]
    return (f"{place} now: {_deg(c['temperature_2m'])}, {WMO.get(c['weather_code'], 'mixed weather')}, "
            f"feels like {_r(c['apparent_temperature'])}, humidity {c['relative_humidity_2m']}%. Today {forecast}")


def _locate(place):
    """(name, latitude, longitude) for a place name, or for this connection when no place is given."""
    if place:
        found = _get_json("https://geocoding-api.open-meteo.com/v1/search?"
                          + urllib.parse.urlencode({"name": place, "count": 1}), timeout=8).get("results")
        if not found:
            raise Failed(f"I couldn't find a place called {place}.")
        r = found[0]
        return r["name"], r["latitude"], r["longitude"]
    r = _get_json("http://ip-api.com/json/?fields=status,city,lat,lon", timeout=8)
    if r.get("status") != "success":
        raise Failed("I don't know where you are. Set a default weather location in the app.")
    return r["city"], r["lat"], r["lon"]


def weather(place="", day=0):
    try:
        name, lat, lon = _locate(place)
        data = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"}), timeout=8)
    except (OSError, ValueError, KeyError) as e:
        raise Failed("I couldn't get the weather right now.") from e
    return format_weather(name, data, max(0, min(int(day), 2)))


def format_results(data, limit=5):
    return "\n".join(f"{i}. {r['title']}: {r.get('content', '').strip()} ({r['url']})"
                     for i, r in enumerate(data.get("results", [])[:limit], 1))


def web_answer(query, cfg):
    """Top web results as text for the LLM to answer from, or a browser search when SearXNG is off."""
    if not cfg["web"].get("searxng_enabled", True):
        engine = cfg["web"].get("engine", "google")
        actions._spawn(["xdg-open", actions.search_url(query, engine)])
        return (f"Opened a {engine} search for \"{query}\" in the browser. You cannot see the results: "
                "tell the user they are on screen, and do not make up an answer.")
    url = cfg["web"]["searxng_url"].rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
    try:
        data = _get_json(url, timeout=8)
    except (OSError, ValueError) as e:
        raise Failed("Web search isn't available. Enable the json format in SearXNG's settings.") from e
    return format_results(data) or "No results found."


def _notes(cfg):
    return pathlib.Path(cfg["paths"]["data"]).expanduser() / "notes.md"


def note_add(text, cfg):
    p = _notes(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(f"- {datetime.datetime.now():%Y-%m-%d %H:%M} {' '.join(text.split())}\n")  # one line per note
    return "Noted."


def notes_read(cfg, limit=10):
    p = _notes(cfg)
    lines = p.read_text().splitlines() if p.exists() else []
    if not lines:
        return "You have no notes."
    return "Your notes, newest first: " + "; ".join(line[19:] for line in reversed(lines[-limit:])) + "."
