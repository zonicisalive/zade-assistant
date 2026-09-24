import logging
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .actions import SITES

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


VERBS = ("open|launch|start|go|switch|move|set|turn|play|pause|resume|close|type|take|increase|decrease|raise|"
         "lower|mute|unmute|lock|search|remind|run|show|put|make|copy|read|note")


def parse_teach(text):
    """ "when i say X <do something>" -> (X, request); the request starts at the first command verb."""
    if m := re.fullmatch(rf"(?:when|whenever|if) i say (.+?) (?:then |you should )?((?:{VERBS})\b.*)", text):
        return m[1], m[2]
    return None


_SPOKEN_MODS = {"control": "ctrl", "ctrl": "ctrl", "shift": "shift", "alt": "alt", "super": "super",
                "windows": "super", "win": "super", "meta": "super"}


UNITS = {"second": 1, "minute": 60, "min": 60, "hour": 3600}


def _seconds(n, unit):
    return int(n) * UNITS[unit.rstrip("s") if unit.rstrip("s") in UNITS else unit]


def _open_one(name, find_app):
    if find_app(name):
        return {"name": "open_app", "args": {"name": name}}
    if name.replace(" ", "") in SITES:
        return {"name": "open_website", "args": {"site": name}}
    return None


def _open_targets(target, find_app):
    """ "discord whatsapp and telegram" -> one open action per app or site. None if any part is unknown."""
    if action := _open_one(target, find_app):
        return [action]
    out = []
    for part in re.split(r",\s*|\s+(?:and|then|plus)\s+", target):
        words = part.split()
        i = 0
        while i < len(words):  # longest known name first, so "google chrome" stays one app
            for j in range(len(words), i, -1):
                if action := _open_one(" ".join(words[i:j]), find_app):
                    out.append(action)
                    i = j
                    break
            else:
                return None
    return out or None


def parse_pattern(text, find_app):
    if m := re.fullmatch(r"(?:open|launch|start|run) (?:the )?(.+?)(?: app| website)?", text):
        actions = _open_targets(m[1], find_app)
        if not actions:
            return None
        return actions[0] if len(actions) == 1 else actions
    if m := re.fullmatch(r"(?:go|switch|move) to workspace (\w+)", text):
        return {"name": "window", "args": {"action": "workspace", "workspace": m[1]}}
    if m := re.fullmatch(r"move (?:this|this window|the window|it) to workspace (\w+)", text):
        return {"name": "window", "args": {"action": "move_to_workspace", "workspace": m[1]}}
    if re.fullmatch(r"close (?:this|this window|the window|window)", text):
        return {"name": "window", "args": {"action": "close"}}
    unit = r"(seconds?|minutes?|mins?|hours?)"
    if m := re.fullmatch(rf"(?:set )?(?:a )?timer (?:for )?(\d+) {unit}", text):
        return {"name": "set_timer", "args": {"seconds": _seconds(m[1], m[2])}}
    if m := re.fullmatch(rf"remind me in (\d+) {unit} to (.+)", text):
        return {"name": "set_timer", "args": {"seconds": _seconds(m[1], m[2]), "message": m[3]}}
    if m := re.fullmatch(r"(?:what's |what is |how's |how is )?(?:the )?weather(?: like)?(?: (today|tomorrow))?", text):
        return {"name": "weather", "args": {"day": 1} if m[1] == "tomorrow" else {}}
    if re.fullmatch(r"(?:what's|what is) on (?:my|the) screen|(?:read|describe) (?:my|the) screen|"
                    r"what does this say|(?:explain|read) this(?: error)?|what am i looking at", text):
        return {"name": "look_at_screen", "args": {"question": text}}
    if m := re.fullmatch(r"(?:how hot is|how's|how is|what's using|what is using|check)?(?: my| the)? ?"
                         r"(gpu|cpu|processor|graphics card|ram|memory)(?: temperature| temp| usage| load)?"
                         r"(?: is free| free| left| doing)?", text):
        what = {"processor": "cpu", "graphics card": "gpu", "memory": "ram"}.get(m[1], m[1])
        return {"name": "system_status", "args": {"what": what}}
    if m := re.fullmatch(r"how much (ram|memory)(?: is)?(?: free| used| left)?", text):
        return {"name": "system_status", "args": {"what": "ram"}}
    if re.fullmatch(r"(?:system|pc|computer) (?:status|stats|health)", text):
        return {"name": "system_status", "args": {"what": "all"}}
    if re.fullmatch(r"(?:what are|list|read|show)(?: me)? my reminders", text):
        return {"name": "list_reminders", "args": {}}
    if m := re.fullmatch(r"(?:(turn on|enable|start) )?(?:do not disturb|quiet mode)(?: (on|off))?|"
                         r"(turn off|disable|stop) (?:do not disturb|quiet mode)", text):
        return {"name": "dnd", "args": {"on": not (m[3] or m[2] == "off")}}
    if re.fullmatch(r"(?:sync|refresh|rescan)(?: my| the)?(?: apps| applications| games)?", text):
        return {"name": "sync_apps", "args": {}}
    if m := re.fullmatch(r"play (?!music$|pause$|next$|previous$)(.+?)"
                         r"(?: (?:on|in|from) (spotify|youtube music|youtube|yt music|yt))?", text):
        provider = {"yt": "youtube", "yt music": "youtube music"}.get(m[2], m[2])
        return {"name": "play_music", "args": {"query": m[1], **({"provider": provider} if provider else {})}}
    if re.fullmatch(r"mute(?: the)?(?: sound| volume| audio| it| speakers?)?", text):
        return {"name": "mute", "args": {"on": True}}
    if re.fullmatch(r"unmute(?: the)?(?: sound| volume| audio| it| speakers?)?|(?:turn )?(?:the )?sound (?:back )?on", text):
        return {"name": "mute", "args": {"on": False}}
    if m := re.fullmatch(r"(?:press|hit|push|tap)(?: the)? (.+?)(?: key| keys| button)?", text):
        words = m[1].replace("+", " ").split()
        mods = [w for w in words if w in _SPOKEN_MODS]
        key = " ".join(w for w in words if w not in _SPOKEN_MODS)
        if key:
            return {"name": "press_keys", "args": {"keys": "+".join([_SPOKEN_MODS[w] for w in mods] + [key])}}
    if m := re.fullmatch(r"type (.+)", text):
        return {"name": "type_text", "args": {"text": m[1]}}
    # Local time and date come from the clock, never from the model. "in india" = local time here.
    here = r"(?: now| here| right now| in india| today)?"
    if re.fullmatch(rf"(?:what time is it|what's the time|what is the time|tell me the time|time){here}", text):
        return {"name": "time", "args": {}}
    if re.fullmatch(rf"(?:what's the date|what's today's date|what is the date|what is today's date"
                    rf"|what day is it|what's the day|today's date|date){here}", text):
        return {"name": "date", "args": {}}
    if re.fullmatch(r"(?:take a |take )?screenshot", text):
        return {"name": "screenshot", "args": {}}
    if m := re.fullmatch(r"(?:close|quit|kill|exit) (?:the )?(.+?)(?: app)?", text):
        return {"name": "close_app", "args": {"name": m[1]}} if find_app(m[1]) else None
    verb = r"(?:set|increase|decrease|raise|lower|turn)(?: up| down)?"
    if m := re.fullmatch(rf"(?:{verb} )?(?:the )?volume(?: up| down)? (?:to )?(\d{{1,3}})(?: percent)?", text):
        return {"name": "volume", "args": {"set": int(m[1])}}
    if m := re.fullmatch(r"(?:turn )?(?:the )?volume (up|down)(?: a bit| a little)?", text):
        return {"name": "volume", "args": {"delta": 10 if m[1] == "up" else -10}}
    if m := re.fullmatch(r"(increase|raise|lower|decrease)(?: the)? volume(?: a bit| a little)?", text):
        return {"name": "volume", "args": {"delta": 10 if m[1] in ("increase", "raise") else -10}}
    if m := re.fullmatch(r"(?:search|google|look up)(?: for)? (.+)", text):
        return {"name": "web_search", "args": {"query": m[1]}}
    return None


OTHER = "something else"
BUILTINS = {
    "mute": ("mute the sound", {"name": "mute", "args": {"on": True}}),
    "unmute": ("unmute the sound, turn it back on", {"name": "mute", "args": {"on": False}}),
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
        actions = action if isinstance(action, list) else [action]
        return Route("run", actions, "pattern", actions[0]["name"])
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
