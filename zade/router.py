import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

FILLER = re.compile(r"\b(zade|hey|please|can you|could you|would you|um+|uh+)\b")
HALLUCINATIONS = {"you", "thank you", "thanks for watching", "bye"}


def normalize(text):
    t = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    t = " ".join(FILLER.sub(" ", t).split())
    return "" if t in HALLUCINATIONS else t


def match_shortcut(text, table, min_score):
    best, best_score = None, 0.0
    for phrase in table:
        score = fuzz.ratio(text, phrase)
        # Partial phrase: a word-boundary prefix covering at least half the phrase.
        if phrase.startswith(text + " ") and len(text) * 2 >= len(phrase):
            score = 100
        if score > best_score:
            best, best_score = phrase, score
    return best if best_score >= min_score else None


def parse_pattern(text, find_app):
    if m := re.fullmatch(r"(?:open|launch|start|run) (?:the )?(.+?)(?: app)?", text):
        return {"name": "open_app", "args": {"name": m[1]}} if find_app(m[1]) else None
    if m := re.fullmatch(r"(?:close|quit|kill|exit) (?:the )?(.+?)(?: app)?", text):
        return {"name": "close_app", "args": {"name": m[1]}} if find_app(m[1]) else None
    if m := re.fullmatch(r"(?:turn )?(?:the )?volume (up|down)(?: a bit| a little)?", text):
        return {"name": "volume", "args": {"delta": 10 if m[1] == "up" else -10}}
    if m := re.fullmatch(r"(?:set )?(?:the )?volume (?:to )?(\d{1,3})(?: percent)?", text):
        return {"name": "volume", "args": {"set": int(m[1])}}
    if m := re.fullmatch(r"(?:search|google|look up)(?: for)? (.+)", text):
        return {"name": "web_search", "args": {"query": m[1]}}
    return None


@dataclass
class Route:
    kind: str
    actions: list = field(default_factory=list)
    source: str = ""
    label: str = ""
    phrase: str | None = None
    confidence: float = 1.0


def route(text, table, cfg, predict=None, find_app=lambda name: None):
    if not text:
        return Route("none")
    if phrase := match_shortcut(text, table, cfg["shortcut_min_score"]):
        return Route("run", table[phrase], "shortcut", phrase, phrase)
    if action := parse_pattern(text, find_app):
        return Route("run", [action], "pattern", action["name"])
    return Route("llm")
