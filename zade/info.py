"""Information tools: weather (wttr.in), web answers (local SearXNG), notes (a text file)."""

import datetime
import json
import pathlib
import urllib.parse
import urllib.request

from . import actions
from .actions import Failed

DAYS = ["today", "tomorrow", "the day after tomorrow"]


def _get_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _desc(entry):
    return entry["weatherDesc"][0]["value"].strip().lower()


def format_weather(data, day):
    place = data["nearest_area"][0]["areaName"][0]["value"]
    w = data["weather"][day]
    noon = w["hourly"][4]
    forecast = f"{w['mintempC']} to {w['maxtempC']}°C, {_desc(noon)}, {noon['chanceofrain']}% chance of rain."
    if day:
        return f"{place} {DAYS[day]}: {forecast}"
    c = data["current_condition"][0]
    return (f"{place} now: {c['temp_C']}°C, {_desc(c)}, feels like {c['FeelsLikeC']}°C, "
            f"humidity {c['humidity']}%. Today {forecast}")


def weather(place="", day=0):
    try:
        data = _get_json(f"https://wttr.in/{urllib.parse.quote(place)}?format=j1", timeout=8)
    except (OSError, ValueError) as e:
        raise Failed("I couldn't get the weather right now.") from e
    return format_weather(data, max(0, min(int(day), len(data["weather"]) - 1)))


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
        f.write(f"- {datetime.datetime.now():%Y-%m-%d %H:%M} {text.strip()}\n")
    return "Noted."


def notes_read(cfg, limit=10):
    p = _notes(cfg)
    lines = p.read_text().splitlines() if p.exists() else []
    if not lines:
        return "You have no notes."
    return "Your notes, newest first: " + "; ".join(line[19:] for line in reversed(lines[-limit:])) + "."
