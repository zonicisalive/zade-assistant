import logging
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

log = logging.getLogger("zade")

FILLER = re.compile(r"\b(zade|hey|please|can you|could you|would you|um+|uh+)\b")
HALLUCINATIONS = {
    "you", "thank you", "thanks", "thank you very much", "thank you for watching", "thanks for watching",
    "bye", "bye bye", "okay", "ok", "so",
}


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


OTHER = "something else"
BUILTINS = {
    "mute": ("mute or unmute the sound", {"name": "mute", "args": {}}),
    "play or pause media": ("play, pause, resume or stop music or video",
                            {"name": "media", "args": {"cmd": "play-pause"}}),
    "next track": ("skip to the next song or video", {"name": "media", "args": {"cmd": "next"}}),
    "previous track": ("go back to the previous song", {"name": "media", "args": {"cmd": "previous"}}),
    "tell the time": ("say what time it is", {"name": "time", "args": {}}),
    "tell the date": ("say what day or date it is", {"name": "date", "args": {}}),
    "lock the screen": ("lock the computer or screen", {"name": "lock_screen", "args": {}}),
    "go to sleep": ("free the GPU, unload the language model", {"name": "sleep", "args": {}}),
    "list facts": ("say what you know or remember about the user", {"name": "list_facts", "args": {}}),
}


def laya_pick(predict, text, table):
    choices = {label: desc for label, (desc, _) in BUILTINS.items()}
    choices |= {f"shortcut: {p}": f"run the user's saved shortcut called '{p}'" for p in table}
    choices[OTHER] = "a question, a request not listed above, or anything else"
    res = predict({"utterance": text}, {"action": {
        "type": "choice",
        "instructions": "Which of these does the user want the assistant to do?",
        "criteria": choices,
    }})
    ans = res["answers"]["action"]
    label, conf = ans["choice"], float(ans["confidence"])
    if label in BUILTINS:
        return [BUILTINS[label][1]], label, conf
    if label.startswith("shortcut: ") and label[10:] in table:
        return table[label[10:]], label, conf
    return None, label, conf


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
    if predict:
        try:
            actions, label, conf = laya_pick(predict, text, table)
        except Exception as e:  # any Laya failure must fall through to the LLM, never crash the loop
            log.warning("Laya failed: %s", e)
            actions = None
        if actions is not None and conf >= cfg["laya_confirm"]:
            kind = "run" if conf >= cfg["laya_accept"] else "confirm"
            phrase = label[10:] if label.startswith("shortcut: ") else None
            return Route(kind, actions, "laya", label, phrase, conf)
    return Route("llm")
