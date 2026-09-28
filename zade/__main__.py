import atexit
import datetime
import fcntl
import logging
import os
import pathlib
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import actions, brain, config, hinglish, info, memory, router

log = logging.getLogger("zade")
# Said as a whole clause ("stop", "Hey, stop.", "bas, rehne do"): stop talking and listening. Only words that
# can't start a real request, so "stop the song" and "thanks, now open discord" still work.
STOP_WORDS = {
    "stop", "stop it", "stop that", "stop now", "stop talking", "stop listening", "stop speaking", "just stop",
    "cancel", "cancel it", "cancel that", "abort", "never mind", "nevermind", "nvm", "forget it", "forget that",
    "forget about it", "leave it", "drop it", "enough", "that's enough", "thats enough", "okay stop",
    "ok stop", "shut up", "shush", "hush", "quiet", "be quiet", "silence", "zip it", "go away", "go to sleep",
    "nothing", "no nothing", "nothing nothing",
    # Hindi / Hinglish
    "bas", "bas karo", "bas kar", "bas ho gaya", "chup", "chup karo", "chup kar", "chup ho ja", "chup raho",
    "ruk", "ruko", "ruk ja", "ruk jao", "rehne do", "rehne de", "chhodo", "chhod do", "chod do", "chodo",
    "kuch nahi", "kuch nahin", "koi baat nahi", "jaane do", "jane do", "band kar", "band karo", "bas rehne do",
    "rehne do bas", "bas chhodo", "chhodo rehne do", "chup chap", "chup chaap", "bas bas", "stop stop", "ruko ruko",
}
# Ending a request, these cancel it. Not every stop word: "band karo", "chhod do", "go to sleep" or "shut up"
# end real requests ("Spotify band karo", "vc chhod do", "remind me at 11 to go to sleep").
END_CANCELS = {"never mind", "forget it", "forget that", "forget about it", "leave it", "nothing nothing", "no nothing",
               "rehne do", "rehne de", "bas rehne do", "rehne do bas", "jaane do", "jane do", "kuch nahi", "kuch nahin",
               "koi baat nahi"}
QUOTING = {"to", "saying", "say", "says", "that", "text", "message", "type", "write", "called", "named", "play", "by"}
# Whole utterances made only of these words, with at least one real "no" ("no thanks, that's all"): waved off
DISMISS = {"no", "nope", "nah", "naa", "nahi", "nahin", "nothing", "never", "mind", "nevermind", "nvm", "leave",
           "forget", "it", "its", "it's", "a", "just", "thanks", "thank", "you", "thankyou", "that's", "thats", "all",
           "not", "now", "okay", "ok", "fine", "cancel", "stop", "i", "said", "was", "saying", "sorry", "bye",
           "goodbye", "good", "alright", "right", "cool", "yeah", "yes", "else", "more", "anything", "that", "is",
           "for", "today", "we're", "done", "dhanyavaad", "shukriya", "bas", "theek", "hai", "ji"}
NEGATIVE = {"no", "nope", "nah", "naa", "nahi", "nahin", "nothing", "never", "nevermind", "nvm", "forget", "leave",
            "bye", "goodbye", "done", "bas"}
# Waving Zade off or talking to someone else, in the user's own words. The whole utterance must be that
# (give or take "no", "sorry", "thanks"...): "play see you again" or "play goodbye by apocalyptica" are requests.
WAVED_OFF = re.compile(
    r"(?:(?:no|nah|nope|okay|ok|oh|um|uh|actually|sorry|arre|nahi|bas|and)\s+)*(?:(?:i|we)\s+)?"
    r"(?:(?:don't|do not|dont|didn't|did not) (?:need|want) (?:any |your |it|that|anything)?(?:help|anything|you)?"
    r"|no need|not needed|no thanks|no thank you|nothing else|nothing more|that'?s (?:all|it|enough|fine)"
    r"|that'?ll be all|we'?re done|all good|i'?m (?:good|fine|okay|ok|done|alright|all set|set)|i am (?:good|fine|okay|done)"
    r"|leave me alone|go away|(?:i )?(?:wasn'?t|was not|am not|i'?m not|not) talking to you|not you"
    r"|i was talking to (?:someone|somebody|him|her|them|my \w+)|(?:didn'?t|did not) (?:call|ask) you|nobody asked"
    r"|ignore (?:that|it|me|this)|wrong (?:person|call)|by mistake|galti se|(?:good ?)?bye|see you"
    r"|nahi chahiye|zarurat nahi|zaroorat nahi|kuch nahi chahiye"
    r"|main theek hoon|mai thik hu|tujhse nahi|tumse nahi|aapse nahi)"
    r"(?:\s+(?:thanks|thank you|bro|yaar|bhai|man|dude|ji|now|anymore|for now|right now|sorry))*")


TALK_WORDS = re.compile(r"\b(?:yaar|yar|bhai|bro|dude|man|na|ji|abhi|now|please|zade|okay|ok)\b")


def plain(text):
    """ "bas yaar, rehne do bhai" -> "bas rehne do": the words that carry the meaning."""
    return " ".join(TALK_WORDS.sub(" ", text).split())


def as_said(part, raw):
    """ `part` of the normalized text with each word's capitals as said or typed: "staff vc in bitnade" ->
    "staff vc in BITNADE" (BITNADE and Bitnade can be different servers)."""
    said = {}
    for w in re.findall(r"[\w']+", raw or ""):
        said.setdefault(w.lower(), w)
    return " ".join(said.get(w, w) for w in part.split())


def said_text(part, raw):
    """`part` of the normalized text as it was said: with its capitals and punctuation, and the words
    normalizing drops ("hey", "please", "can you"), which in a message or typed text belong to it: "saying hey,
    can you please call me back?" sends all of that, not "call me back"."""
    tokens = list(re.finditer(r"[a-z0-9']+", (raw or "").lower()))
    joined = " ".join(t[0] for t in tokens)
    filler, pos = set(), [0]
    for t in tokens:  # each token's start in `joined`
        pos.append(pos[-1] + len(t[0]) + 1)
    for f in router.FILLER.finditer(joined):
        filler |= {i for i, p in enumerate(pos[:-1]) if f.start() <= p < f.end()}
    kept = [i for i in range(len(tokens)) if i not in filler]
    words = [tokens[i][0] for i in kept]
    n = len(part.split())
    at = next((i for i in range(len(words) - n, -1, -1) if words[i:i + n] == part.split()), None)
    if not part or at is None:
        return part
    start = tokens[kept[at - 1]].end() if at else 0  # from just after the word before it...
    end = tokens[kept[at + n]].start() if at + n < len(kept) else len(raw)  # ...to the word after it
    return raw[start:end].strip(" ,;:-") or part


def after_stop(raw):
    """What was said after a stop word said on its own ("Stop. Play the next song." -> "Play the next song."),
    "" when nothing real follows ("Hey, stop." or "Stop. Canild."), None when there's no such stop."""
    clauses = [c.strip() for c in re.split(r"[.!?,]", raw or "") if c.strip()]
    stops = [i for i, c in enumerate(clauses) if plain(router.normalize(c)) in STOP_WORDS]
    if not stops:
        # "call B I nothing nothing leave it": ending with a phrase that only cancels, not quoted ("... saying
        # never mind")
        words = router.normalize(raw or "").split()
        if any(" ".join(words[-n:]) in END_CANCELS and words[-n - 1] not in QUOTING for n in (2, 3) if len(words) > n):
            return ""
        return None
    rest = ", ".join(clauses[stops[-1] + 1:])
    words = router.normalize(rest).split()
    # a lone mumble after "stop" ("Canild") is noise, but a one-word command ("Pause.") is not
    return rest if len(words) > 1 or (words and router.parse_pattern(" ".join(words), lambda n: None)) else ""


def dismissed(text):
    if WAVED_OFF.fullmatch(plain(text)) or WAVED_OFF.fullmatch(text):
        return True
    words = re.findall(r"[a-z']+", text)
    return bool(words) and set(words) <= DISMISS and bool(set(words) & NEGATIVE)


# Tools whose calls are never learned as shortcuts (memory, one-off content, or risky).
MEMORY_TOOLS = {"clip", "send_message", "discord", "snooze", "whoami", "express", "dnd", "look_at_screen", "system_status", "set_reminder", "list_reminders", "cancel_reminder", "sync_apps", "remember", "forget", "list_facts", "make_shortcut", "sleep", "set_timer", "note_add",
                "notes_read", "web_answer", "clipboard_read", "clipboard_copy", "type_text", "power", "shell"}


def _duration(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    parts = [f"{n} {unit}{'s' if n != 1 else ''}" for n, unit in ((h, "hour"), (m, "minute"), (s, "second")) if n]
    return " ".join(parts) or "0 seconds"


@dataclass
class Ctx:
    cfg: dict
    conn: sqlite3.Connection
    say: Callable[[str], None]
    confirm: Callable[[str], bool]
    predict: Callable | None = None
    find_app: Callable = actions.find_app
    ask: Callable = brain.ask
    run_action: Callable = actions.run
    alerts: list = field(default_factory=list)  # due reminders, spoken by the main loop when idle
    discord_events: list = field(default_factory=list)  # new DMs, mentions and calls to announce
    replay: object = None  # the replay buffer (replay.Replay) while clips are on
    replay_failed: tuple | None = None  # the clips settings the replay buffer couldn't start with
    replay_check_at: float = 0.0  # when to next look for replay recorders that stopped
    history: list = field(default_factory=list)  # (time, user text, reply) for follow-ups
    turn: list = field(default_factory=list)  # actions done so far in the current request
    tried: bool = False  # the model tried an action this request (even one that failed)
    app_words: list = field(default_factory=list)  # installed app names, given to Whisper as hotwords
    discord_words: list = field(default_factory=list)  # Discord servers, people, voice channels (ZadeControl)
    show: Callable = lambda **fields: None  # overlay updates (emotion); ui.set in the real app
    ask_user: Callable[[str], str] = lambda question: ""  # says a question, returns the answer heard
    request: str = ""  # what the user said this turn, as heard
    route: str = ""  # how the last request was handled (shortcut, pattern, llm, ...), for History


def dictation_text(raw):
    """What voice typing types: Whisper's text as is (capitals, punctuation), minus noise phantoms."""
    text = raw.strip()
    return text + " " if router.normalize(text) else ""


EMOTIONS = router.EMOTIONS


def split_emotion(reply):
    """"[happy] Sure!" -> ("happy", "Sure!"). The tag drives the face and is never spoken. Some models put it
    at the end ("Sure! [happy]") or in the middle, so any emotion tag anywhere is taken out."""
    reply = re.sub(r"^\s*\[\w+\]", lambda m: m[0] if m[0].strip()[1:-1].lower() in EMOTIONS else "", reply or "")
    tags = [t.lower() for t in re.findall(r"\[(\w+)\]", reply) if t.lower() in EMOTIONS]
    text = re.sub(r"\s*\[(?:" + "|".join(EMOTIONS) + r")\]\s*", " ", reply, flags=re.I)
    return (tags[0] if tags else "neutral"), " ".join(text.split())


# Small models pad replies with offers ("How can I help you today?") and introductions nobody asked for.
FILLER_SENTENCE = re.compile(
    r"^(?:(?:alright|all right|okay|ok|sure|got it|understood|no problem|of course|absolutely|great|noted)[,.!]? )?"
    r"(?:(?:hello|hi|hey)(?: there)?(?:,? \w+)?[!.,]? )?(?:"
    r"(?:how|what) (?:else )?(?:can|may|could|shall) i (?:help|assist|do)(?: you)?(?: with)?(?: for you)?"
    r"(?: today| now| further| next| else)?"
    r"|what (?:would|else would|do|else do|can|else can) (?:you|i) (?:like|want|need|do)(?: me)?(?: to do| to)?"
    r"(?: for you)?(?: next| today| now| else)?"
    # offers only: "If you need a visa, apply..." and "Let me know the city and I'll check" say something
    r"|(?:just )?let me know (?:if|how|what|when|whenever|anytime)\b.*"
    r"|feel free to (?:ask|reach out|let me know|come back|call)\b.*|(?:don'?t|do not) hesitate to\b.*"
    r"|is there (?:anything|something) (?:else )?(?:i can|you'?d like|you would like|you want|you need)\b.*"
    r"|anything else(?: i can (?:do|help (?:you )?with)(?: for you)?)?"
    r"|if you need (?:anything|any help|help|more help|something|me)(?: else)?\b.*"
    r"|if there'?s anything (?:else )?(?:i can|you need|you want)\b.*|whenever you need (?:me|anything|help)\b.*"
    r"|i'?ll be (?:here|around)(?: if| whenever| when| for)?\b.*"
    r"|(?:i'?m|i am) (?:always )?here(?: for you| if you need| to help| whenever| when you need)\b.*"
    r"|(?:i'?m|i am) (?:happy|glad|ready) to help\b.*|happy to help|glad to help|hope (?:this|that) helps"
    r"|just say the word|ask me anything|you can (?:always )?ask me (?:anything|more)\b.*"
    r"|(?:would you like|do you want|do you need|shall i|should i) (?:me )?(?:to )?(?:help|assist)(?: you)?"
    r"(?: with)? (?:something|anything).*"
    r"|(?:can|could) you tell me (?:more )?(?:about )?what you (?:need|want|would like)\b.*"
    r"|(?:i'?m|i am) (?:zade|\w+),? (?:your|a) (?:friendly |helpful |personal )?(?:voice |ai )?assistant\b.*"
    r"|(?:i'?m|i am) (?:just )?(?:your|a) (?:friendly |helpful |personal )?(?:voice |ai )?assistant\b.*"
    r")(?:,? \w+)?[.!?]*$",  # "..., Zonic?"
    re.I)
# ...and claim they did things no tool did ("Instagram is now open!", "the message has been sent" after only
# opening Discord). Each kind of claim needs one of its tools to have run. After a lookup (web, weather, the
# screen...) the reply reports facts ("the metro line is now open"), not actions, so it isn't checked.
# Tools whose results the reply may quote (a web page, the screen, your notes): their words aren't claims.
INFO_TOOLS = {"web_answer", "look_at_screen", "notes_read", "list_facts", "list_reminders", "clipboard_read"}
_ACK = r"(?:(?:done|sure|okay|ok|alright|all right)[,.!]? )"
# A sentence that's only a short "-ing" phrase ("Closing Discord.", "Setting Spotify volume to 50%."), not a
# statement with a verb of its own ("Opening hours are 9 to 5.")
_BARE = r"^(?!.*\b(?:is|are|was|were|has|have|had|will|can|means)\b)(?:{})(?: \S+){{0,5}}\W*$"
# Claims of actions: each needs one of its tools to have run (None: any tool). Only the ways a model speaks of
# what it just did ("I've sent", "Message sent to X", "Opened Discord.", "Sure, closing it"), not facts
# ("The first email was sent in 1971", "NASA sent a probe").
CLAIMS = [
    (re.compile(r"\b(?:(?:has|have) been (?:sent|shared|posted|uploaded)"
                r"|i(?:'ve| have)? (?:sent|shared|posted|uploaded|messaged|typed|replied))\b"
                r"|^(?:the |your )?(?:message|msg|text|dm|screenshot|picture|file|reply)s? (?:was |were |is |are )?"
                r"(?:sent|shared|posted|uploaded)\b"
                rf"|^{_ACK}?(?:sent|messaged|typed|replied)\b", re.I),
     {"send_message", "type_text", "press_keys", "discord"}),
    (re.compile(r"\b(?:is now open|is open now|now open|i(?:'ve| have)? (?:opened|launched|started))\b"
                rf"|^{_ACK}?opened\b(?! in \d)|^{_ACK}opening\b|" + _BARE.format("opening"), re.I),
     {"open_app", "open_website", "window", "web_search"}),
    (re.compile(rf"\b(?:is now closed|i(?:'ve| have)? closed)\b|^{_ACK}closing\b|" + _BARE.format("closing"), re.I),
     {"close_app", "window", "press_keys"}),
    (re.compile(r"\b(?:is now (?:playing|paused)|now playing|i(?:'ve| have)? (?:paused|played|resumed))\b", re.I),
     {"play_music", "media"}),
    (re.compile(r"\b(?:disconnected|left the (?:call|vc|voice(?: channel)?)|joined the \w+|calling \w+|"
                r"i(?:'ve| have)? (?:joined|called|deafened))\b", re.I), {"discord"}),
    (re.compile(rf"\bi(?:'ve| have)? (?:muted|unmuted)\b|^{_ACK}?(?:muted|unmuted)\b", re.I),
     {"discord", "mute", "volume", "app_volume", "media"}),
    # "Setting Spotify volume to 50%." with nothing done: any tool at all must have run
    (re.compile(rf"^{_ACK}(?:setting|turning|changing|switching|starting|stopping|muting|unmuting|raising|lowering|"
                r"increasing|decreasing)\b|" + _BARE.format("setting|turning|changing|switching|starting|stopping|"
                                                           "muting|unmuting|raising|lowering|increasing|decreasing"), re.I),
     None),
    (re.compile(r"\b(?:is now (?:displayed|showing)|now displayed|i(?:'ve| have)? (?:turned on|turned off|switched))\b",
                re.I), None),  # any tool will do
]


def tidy(reply, called=()):
    """A model reply without filler sentences, and without claims of actions that no tool took. `called` are
    the tools that ran."""
    if re.fullmatch(r"\W*silent\W*", reply, re.I):  # the model's own sign that nothing should be said
        return ""
    if reply.lstrip().startswith("{"):  # a tool call written out as text instead of made: never read JSON aloud
        return "Sorry, I got mixed up. Say that again?"
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", reply.strip()) if x]
    greet = re.compile(r"^((?:hello|hi|hey)(?: there)?(?:,? \w+)?)[!.,]? \w", re.I)  # "Hello Zonic, how can I..."
    kept = [x if not FILLER_SENTENCE.match(x) else (g[1] + "!" if (g := greet.match(x)) else "") for x in sentences]
    kept = [x for x in kept if x]
    reply = " ".join(kept)  # only filler ("I'm here to help if you need anything."): better to say nothing
    if set(called) & INFO_TOOLS:
        return reply
    for claim, tools in CLAIMS:
        if any(claim.search(x) for x in kept) and not (set(called) & tools if tools else called):
            return "I couldn't do all of that." if called else "I couldn't do that."
    return reply


def choose_emotion(emotion, reply):
    """The model's tag, except that a question back to the user shows interest instead of a blank face."""
    if emotion == "neutral" and reply.rstrip().endswith("?"):
        return "curious"
    return emotion


def fact_words(facts):
    """Names you told Zade ("my name is zonic") as hotwords, so Whisper stops hearing "Sonic"."""
    out = []
    for f in facts:
        for m in re.finditer(r"(?:name is|is called|called) (\w+)", f, re.IGNORECASE):
            out.append(m[1].capitalize())
    return list(dict.fromkeys(out))


def next_time(at, now=None):
    """Next occurrence of a clock time like "17:00", "5 pm" or "9:30 am"."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap])?\.?\s*m?\.?\s*", at.lower())
    if not m:
        raise ValueError(f"I don't understand the time {at}.")
    hour, minute = int(m[1]), int(m[2] or 0)
    if m[3] == "p" and hour < 12:
        hour += 12
    if m[3] == "a" and hour == 12:
        hour = 0
    now = now or datetime.datetime.now()
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t if t > now else t + datetime.timedelta(days=1)


SNOOZE = {"until": 0.0}  # "stop for 10 minutes": the wake word is ignored until then (the hotkey still works)


def snoozed(now=None):
    return (time.time() if now is None else now) < SNOOZE["until"]


def is_quiet(cfg, now=None):
    """Do Not Disturb, or inside the quiet-hours window (which may wrap past midnight)."""
    q = cfg["quiet"]
    if q["dnd"]:
        return True
    if not q["enabled"]:
        return False
    now = now or datetime.datetime.now()
    minutes = now.hour * 60 + now.minute
    try:
        start, end = (int(t.split(":")[0]) * 60 + int(t.split(":")[1]) for t in (q["start"], q["end"]))
    except (ValueError, IndexError, AttributeError):  # a hand-edited "11pm": never crash over it
        log.warning("quiet hours ignored: start/end must be HH:MM, got %r and %r", q["start"], q["end"])
        return False
    return start <= minutes < end if start <= end else minutes >= start or minutes < end




def apply_live(cfg, new):
    for section in config.LIVE_SECTIONS:
        cfg[section] = new[section]
    for key in config.LIVE_KEYS:  # read on every request, so they can change live
        table, name = key.split(".")
        cfg[table][name] = new[table][name]


# Actions that need a spoken yes at each safety level (shell and power always ask, in actions.py).
RISKY = {"close_app", "type_text", "clipboard_copy", "press_keys", "send_message"}
READ_ONLY = {"snooze", "whoami", "express", "dnd", "time", "date", "weather", "web_answer", "notes_read", "list_facts", "list_reminders",
             "system_status", "look_at_screen", "clipboard_read", "remember", "forget", "note_add",
             "set_timer", "set_reminder", "cancel_reminder", "make_shortcut", "sync_apps", "sleep",
             "shell", "power"}


def needs_confirm(name, level, args=None):
    if level == "everything":
        return name not in READ_ONLY
    if level == "risky":
        return name in RISKY or (name == "window" and (args or {}).get("action") == "close")
    return False


def sync_replay(ctx, now=None):
    """Start, restart or stop the replay buffer to match the clips settings (they apply live), and every
    30 s restart any of its recorders that stopped. A start that failed waits for the settings to change."""
    from . import replay

    now = time.monotonic() if now is None else now
    c = ctx.cfg.get("clips", {})
    want = (max(5, min(120, int(c.get("seconds", 30)))), c.get("sources", "both"), bool(c.get("screen")),
            bool(c.get("hide_from_shell"))) if c.get("enabled") else None
    have = (ctx.replay.seconds, ctx.replay.sources, ctx.replay.screen_on, ctx.replay.hidden) if ctx.replay else None
    if want == have:
        if ctx.replay and now >= ctx.replay_check_at:
            ctx.replay_check_at = now + 30
            try:
                if dead := ctx.replay.heal():
                    log.warning("replay buffer: restarted %s, it had stopped", " and ".join(dead))
            except OSError as e:
                log.warning("replay buffer: %s", e)
        return
    if ctx.replay:
        ctx.replay.stop()
        ctx.replay = None
    if want and want != ctx.replay_failed:
        try:
            ctx.replay, ctx.replay_failed = replay.Replay(*want), None
        except OSError as e:  # parec, ffmpeg or wf-recorder missing, /dev/shm full
            log.warning("replay buffer off: %s", e)
            ctx.replay_failed = want


_failed = {}  # background step -> its last error


def guarded(step, *args):
    """One of the main loop's background steps. An error (a mistyped setting like seconds = "45s") is logged
    once and the loop carries on: uncaught, it would crash Zade again on every restart."""
    try:
        step(*args)
        _failed.pop(step, None)
    except Exception as e:
        if _failed.get(step) != str(e):
            _failed[step] = str(e)
            log.warning("%s failed: %s", step.__name__, e)


def pump_discord(ctx):
    """New Discord messages and calls from the ZadeControl plugin, to announce (kinds as set in discord.*).
    During quiet hours, Do Not Disturb or a snooze they're dropped: Discord keeps them as unread anyway."""
    from . import discord

    d = ctx.cfg.get("discord", {})
    wanted = {"dms": {"dm"}, "all": {"dm", "mention"}}.get(d.get("announce"), set()) | ({"call"} if d.get("calls") else set())
    if not wanted or not discord.TOKEN.exists():
        return 0
    try:
        events = discord.call("events", timeout=1)["events"]
    except (discord.Unavailable, discord.Failed):
        return 0
    if is_quiet(ctx.cfg) or snoozed():
        return 0
    new = [e for e in events if e.get("kind") in wanted]
    ctx.discord_events.extend(new)
    return len(new)


def pump_reminders(ctx, now=None):
    """Move due reminders into ctx.alerts (the main loop speaks them when idle). Returns how many.
    During quiet hours they wait, and are spoken once quiet time ends."""
    if is_quiet(ctx.cfg):
        return 0
    due = memory.due_reminders(ctx.conn, time.time() if now is None else now)
    ctx.alerts.extend(due)
    return len(due)


def recent(ctx, now=None):
    f = ctx.cfg["followup"]
    now = time.monotonic() if now is None else now
    return [(u, r) for t, u, r in ctx.history if now - t <= f["history_s"]][-f["history_turns"]:]


def wants_followup(reply):
    return bool(reply) and reply.rstrip().endswith("?")


def spoken(text):
    """Emoji by name, so a read-back says what will be sent ("😀" -> "grinning face emoji"); voices skip them."""
    import unicodedata

    out = [f" {unicodedata.name(c, 'an').lower()} emoji " if unicodedata.category(c) == "So" else c
           for c in text if c != "\ufe0f"]
    return " ".join("".join(out).split())


# Discord actions the model may start only after a yes: they ring people, put the mic live or show you online.
MODEL_ASKS = {"join": "Join {} on Discord?", "unmute": "Unmute your mic on Discord?", "undeafen": "Undeafen on Discord?",
              "set_status": "Set your Discord status to {}?"}


def discord_asks(a, from_model):
    act = a.get("action")
    return act in ("call", "reply", "edit", "delete") or from_model and act in ("react", "unreact", *MODEL_ASKS)


def discord_confirm(ctx, a, from_model):
    """The yes before a Discord action that needs one. Returns the action's args pinned to what was asked
    about (the message from peek, the person a call rings), so a newer message or another chat opened in the
    meantime isn't what's acted on; None if the answer was no. Raises actions.Failed if Discord can't tell."""
    from . import discord

    act, text, name = a["action"], a.get("text", ""), discord.clean_name(a.get("target") or "")
    try:
        discord.wait_ready()
        if act == "call":  # "call Rick" must not ring Nick: a name that was only close is asked about
            p = discord.call("call", name=name, dry=True)
            if (from_model or not p["exact"]) and not ctx.confirm(f"Call {p['calling']} on Discord?"):
                return None
            return {**a, "channel_id": p["channel_id"]}
        if act in MODEL_ASKS:
            return a if ctx.confirm(MODEL_ASKS[act].format(a.get("target") or text).replace(" ?", "?")) else None
        t = discord.call("peek", name=name, mine=act in ("edit", "delete"))
    except discord.Unavailable:
        raise actions.Failed("Discord isn't open, or the ZadeControl plugin is off.") from None
    except discord.Failed as e:
        raise actions.Failed(str(e)) from None
    if act == "delete":
        ok = ctx.confirm(f"Delete your last message in {t['chat']}?\n{t['text']}")
    elif act == "edit":
        ok = ctx.confirm(ask_send(text, f"as your new last message in {t['chat']}").replace("Send", "Change it to", 1)
                         .replace("Do I really send it", "Do I really change your last message", 1))
    elif act == "reply":
        ok = ctx.confirm(ask_send(text, f"as a reply to {t['author']}"))
    else:
        ok = ctx.confirm(f"React {spoken(a.get('emoji', ''))} on {t['author']}'s message?\n{t['text']}")
    return {**a, "channel_id": t["channel_id"], "message_id": t["message_id"]} if ok else None


def ask_send(text, where):
    """The yes-or-no before something goes to other people: a short message is read out, a long one only
    shown (reading a whole story back was tiring): "Send hello to DEXORTO?" / "Do I really send it to ...?"."""
    words = spoken(text)
    if len(words.split()) <= 6:
        return f"Send {words} {where}?"
    return f"Do I really send it {where}?\n{text}"


# The question before these actions, and the argument it names (the model may send other arguments too).
ASK = {"type_text": ("Type {}?", "text"), "clipboard_copy": ("Copy {} to the clipboard?", "text"),
       "press_keys": ("Press keys {}?", "keys"), "close_app": ("Close app {}?", "name")}


def ask_do(name, a):
    """The yes-or-no before an action. Long or multi-line text is shown instead of read out, and typing
    says how often it presses Enter (each line break does)."""
    ask, key = ASK.get(name, (name.replace("_", " ").capitalize() + " {}?", None))
    detail = str(a.get(key, "")) if key else next((str(v) for v in a.values() if isinstance(v, (str, int))), "")
    if name == "type_text" and (n := actions.typed(detail).count("\n")):
        return f"Type this, pressing Enter {n} time{'s' if n > 1 else ''}?\n{detail}"
    if len(detail) > 40 or "\n" in detail:
        return f"{ask.format('this')}\n{detail}"
    return ask.format(detail) if detail else ask.format("").replace(" ?", "?")


def dispatch(ctx, action, from_model=False):
    name, a = action["name"], action.get("args", {})
    try:
        # Closing things on the model's own initiative always needs a yes; the user naming it doesn't.
        model_close = from_model and (name == "close_app" or (name == "window" and a.get("action") == "close")
                                      or (name == "press_keys" and actions.is_closing(a.get("keys", ""))))
        # Typing, copying and most keys on the model's own initiative too: text from a web page, the screen or
        # the clipboard could otherwise make it open a terminal, type or paste a command and run it, with no yes.
        model_keys = from_model and (name in ("type_text", "clipboard_copy") or
                                     (name == "press_keys" and not actions.harmless_keys(a.get("keys", ""))))
        if name == "send_message":  # goes to another person: always read back first, whatever the setting
            from . import discord

            try:  # with the ZadeControl plugin: the person it will really go to, and sent without key presses
                discord.wait_ready()
                found = discord.call("find", name=discord.clean_name(a.get("to", "")))
            except discord.Unavailable:
                found = None  # no plugin: Discord's quick switcher, by keyboard
            except discord.Failed as e:
                return str(e), False
            to = found["label"] if found else a.get("to", "")
            if re.fullmatch(r"(?:(?:a|the|my|this) )?(?:message|msg|text|dm)?", a.get("text", "").strip(" .").lower()):
                # "send a message to dexorto": "a message" isn't what to send, so ask
                text = discord.reply_text(ctx.ask_user(f"What should I say to {to}?"))
                if not text:
                    return "Cancelled.", False
                a = {**a, "text": text}
                action = {**action, "args": a}
            if found and discord.screenshot_meant(a.get("text", "")):  # "send the screenshot to ...": attach it
                shot = discord.latest_screenshot()
                if not shot:
                    return "You have no screenshots yet.", False
                if not ctx.confirm(f"Send your screenshot from {discord.ago(shot)} to {to} on Discord?"):
                    return "Cancelled.", False
                discord.call("send_file", channel_id=found["channel_id"], path=str(shot), timeout=60)
                return f"Sent the screenshot to {to}.", True
            if not ctx.confirm(ask_send(a.get("text", ""), f"to {to} on Discord")):
                return "Cancelled.", False
            if found:
                discord.call("send", channel_id=found["channel_id"], text=a.get("text", ""))
                return f"Sent to {to}.", True
        elif name == "discord" and discord_asks(a, from_model):
            a = discord_confirm(ctx, a, from_model)
            if a is None:
                return "Cancelled.", False
            action = {**action, "args": a}
        elif model_close or model_keys or needs_confirm(name, ctx.cfg["safety"]["confirm"], a):
            if not ctx.confirm(ask_do(name, a)):
                return "Cancelled.", False
        if name == "remember":
            memory.add_fact(ctx.conn, a["fact"])
            return "Got it.", True
        if name == "forget":
            return ("Forgotten." if memory.forget_fact(ctx.conn, a["query"]) else "I didn't know that."), True
        if name == "list_facts":
            f = memory.facts(ctx.conn)
            return ("I know that " + "; ".join(f) + "." if f else "I don't know anything about you yet."), True
        if name == "make_shortcut":
            # "when I say X, do Y" saves this turn's Y; "save that as X" the last request's. If Y was tried and
            # failed, there's nothing to save (not the older, unrelated request).
            last = ctx.turn or (None if ctx.tried else memory.last_actions(ctx.conn))
            if not last:
                return "There's nothing to save yet.", False
            memory.add_shortcut(ctx.conn, router.normalize(a["phrase"]), last)
            return f"Saved. Say {a['phrase']} any time.", True
        if name == "sleep":
            brain.unload(ctx.cfg)
            return "Going to sleep.", True
        if name == "play_music":
            from . import music

            return music.play(a["query"], ctx.cfg["music"]["mode"],
                              a.get("provider") or ctx.cfg["music"]["provider"], a.get("device", ""),
                              ctx.cfg["music"]["play_on"], fix=lambda q: brain.fix_song(q, ctx.cfg)), True
        if name == "look_at_screen":
            from . import vision

            q = a.get("question") or "What's on the screen?"
            # the model's summary of the question can lose what to find ("the transition text"): add the user's words
            if ctx.request and router.normalize(ctx.request) not in router.normalize(q):
                q += f"\n(The user said: {ctx.request})"
            return vision.look(q, ctx.cfg), True
        if name == "clip":
            if not ctx.replay:
                if ctx.replay_failed:
                    return "The replay buffer couldn't start. The log says why.", False
                return "The replay buffer is off. Turn it on in Settings, under Sounds.", False
            try:
                folder = ctx.replay.save(a.get("seconds"))
            except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
                return f"I couldn't save it: {e}", False
            seconds = min(int(a.get("seconds") or ctx.replay.seconds), ctx.replay.seconds)
            what = " and ".join(filter(None, ["the screen" if (folder / "screen.mp4").exists() else "",
                                               "the sound" if any(folder.glob("*.mp3")) else ""]))
            return f"Saved {what} from the last {seconds} seconds.", True
        if name == "whoami":
            if a.get("who") == "assistant":
                return f"I'm {ctx.cfg['persona']['name'] or 'Zade'}, your voice assistant.", True
            for fact in memory.facts(ctx.conn):
                if m := re.search(r"(?:name is|called|i am|i'm)\s+([A-Z][\w-]*)", fact, re.I):
                    return f"Your name is {m[1]}.", True
            return "I don't know your name yet. Tell me: my name is ...", True
        if name == "express":  # the face itself is set by handle(); this is what Zade says
            e = a.get("emotion", "")
            if e not in EMOTIONS:
                return "I can make these faces: " + ", ".join(EMOTIONS) + ".", False
            return f"This is my {e} face.", True
        if name == "snooze":
            seconds = max(0, int(a.get("seconds") or 0))
            SNOOZE["until"] = time.time() + seconds if seconds else 0.0
            return (f"Okay, quiet for {_duration(seconds)}. Hold Win if you need me." if seconds
                    else "I'm listening again."), True
        if name == "dnd":
            from . import ctl

            ctx.cfg["quiet"]["dnd"] = bool(a.get("on", True))
            ctl.set_setting("quiet.dnd", "true" if ctx.cfg["quiet"]["dnd"] else "false")
            return ("Do not disturb is on. Hold Win to talk to me." if ctx.cfg["quiet"]["dnd"]
                    else "Do not disturb is off."), True
        if name == "system_status":
            from . import system

            return system.status(a.get("what", "all")), True
        if name == "sync_apps":
            ctx.app_words[:] = actions.app_names()
            return f"Synced {actions.all_app_count()} apps.", True
        if name == "weather":
            return info.weather(a.get("place") or ctx.cfg["weather"]["place"], a.get("day", 0)), True
        if name == "web_search":
            a = {**a, "engine": ctx.cfg["web"].get("engine", "google")}
            return ctx.run_action({"name": name, "args": a}, ctx.confirm), True
        if name == "web_answer":
            return info.web_answer(a["query"], ctx.cfg), True
        if name == "note_add":
            return info.note_add(a["text"], ctx.cfg), True
        if name == "notes_read":
            return info.notes_read(ctx.cfg), True
        if name == "set_timer":
            seconds, message = int(a["seconds"]), a.get("message") or "Time's up."
            memory.add_reminder(ctx.conn, time.time() + seconds, message)
            return f"Timer set for {_duration(seconds)}.", True
        if name == "set_reminder":
            t = next_time(a["at"])
            daily = bool(a.get("daily"))
            memory.add_reminder(ctx.conn, t.timestamp(), a["message"], 86400 if daily else None)
            when = f"{t:%-I:%M %p}"
            if daily:
                return f"Every day at {when} I'll remind you to {a['message']}.", True
            return f"At {when}{' tomorrow' if t.date() > datetime.date.today() else ''} I'll remind you to {a['message']}.", True
        if name == "list_reminders":
            rows = memory.reminders(ctx.conn)
            if not rows:
                return "You have no reminders.", True
            items = [f"{m} {'every day at' if r else 'at'} {datetime.datetime.fromtimestamp(d):%-I:%M %p}"
                     for d, m, r in rows]
            return "Your reminders: " + "; ".join(items) + ".", True
        if name == "cancel_reminder":
            n = memory.cancel_reminder(ctx.conn, a["query"])
            return (f"Cancelled {n} reminder{'s' if n != 1 else ''}." if n else "I found no reminder like that."), n > 0
        return ctx.run_action(action, ctx.confirm), True
    except actions.Failed as e:
        return str(e), False
    except Exception as e:  # a bad tool call must become an error the LLM sees, never a crash
        log.warning("action %s failed: %s", action, e)
        return f"That failed: {e}", False


def offer(ctx, text, acts):
    if memory.should_offer(ctx.conn, text, acts, ctx.cfg["learning"]["promote_after"]):
        if ctx.confirm(f"Want '{text}' to always do that?"):
            memory.add_shortcut(ctx.conn, text, acts)
            ctx.say("Saved.")
        else:
            memory.decline(ctx.conn, text, acts)


def handle(ctx, raw):
    """Handle one utterance; return what Zade replied (for follow-up listening)."""
    ctx.turn, ctx.tried, ctx.request = [], False, raw  # before any early return: a taught "goodnight" must not save older actions
    heard = raw  # as heard, for the model: Hindi in Devanagari, so it answers in Hindi
    raw = hinglish.to_latin(raw)  # Zade's own phrases, names and messages work in English letters
    text = router.normalize(raw)
    snooze = router.parse_snooze(text) is not None  # "stop for 10 minutes" is a command, not a plain stop
    if not snooze and (after := after_stop(raw)) is not None:
        if not after:
            text = "stop"  # "Hey, stop." "Stop. Cancel." "Nobody will listen. Hey, stop."
        else:
            raw, text = after, router.normalize(after)  # "Stop. Play the next song.": the correction counts
            heard = after
    if not snooze and (text in STOP_WORDS or dismissed(text)):
        return ""
    if taught := router.parse_teach(text):
        phrase, request = taught
        handle(ctx, request)
        if ctx.turn:
            memory.add_shortcut(ctx.conn, phrase, ctx.turn)
            reply = f"Got it. Say {phrase} any time."
        else:
            reply = "That didn't work, so I didn't save it."
        ctx.say(reply)
        return reply
    ctx.route = "llm"
    r = router.route(text, memory.shortcuts(ctx.conn), ctx.cfg["router"], ctx.predict, ctx.find_app)
    if r.kind == "none":  # nothing (or only noise) was said after the wake word: stay silent
        ctx.route = ""  # and nothing worth a history entry
        return ""
    if r.kind == "confirm" and not ctx.confirm(f"Did you mean {r.label}?"):
        r = router.Route("llm")
    for action in r.actions or []:  # routing lower-cases; names go to Discord with the capitals as said
        if action["name"] == "discord" and action["args"].get("target"):
            action["args"]["target"] = as_said(action["args"]["target"], raw)
        if action["name"] == "look_at_screen" and r.source == "pattern":  # the question in the user's own words
            action["args"]["question"] = heard
        # and messages and typed text exactly as said, words normalizing drops included
        if (action["name"] in ("send_message", "type_text") or action["name"] == "discord" and
                action["args"].get("action") in ("reply", "edit")) and action["args"].get("text"):
            action["args"]["text"] = said_text(action["args"]["text"], raw)
    if r.kind in ("run", "confirm"):
        ctx.route = r.source
        results = [dispatch(ctx, a) for a in r.actions]
        ok = all(k for _, k in results)
        ctx.turn = [a for a, (_, k) in zip(r.actions, results) if k and a["name"] not in MEMORY_TOOLS]
        reply = " ".join(t for t, _ in results if t) or "Done."
        shown = next((a["args"]["emotion"] for a in r.actions if a["name"] == "express" and a["args"]["emotion"]), None)
        ctx.show(emotion=shown or ("happy" if ok else "sad"))
        ctx.say(reply)
        if r.phrase:
            memory.use_shortcut(ctx.conn, r.phrase)
        learnable = not any(a["name"] in MEMORY_TOOLS for a in r.actions)
        memory.log(ctx.conn, text, r.actions, r.source, ok)
        if ok and learnable and not r.phrase:
            offer(ctx, text, r.actions)
    else:
        executed, called = [], []

        def run_tool(name, args):
            log.info("model tool %s %s", name, args)
            if name not in MEMORY_TOOLS:
                ctx.tried = True
            out, ok = dispatch(ctx, {"name": name, "args": args}, from_model=True)
            if ok:  # only what really happened backs a claim ("sent" after a "no" to the confirmation is a lie)
                called.append(name)
            if ok and name not in MEMORY_TOOLS:
                executed.append({"name": name, "args": args})
                ctx.turn.append({"name": name, "args": args})
            return out or "done"

        # the words as heard: normalizing drops "can you" and the like for matching commands, which turns
        # "What can you do?" into "what do"
        said = " ".join((heard or "").split()) or text
        if brain.should_think(said, ctx.cfg):  # thinking takes a while: say so, instead of a long silence
            ctx.say("Let me think.")
        answer = ctx.ask(said, memory.facts(ctx.conn), ctx.cfg, run_tool, recent(ctx))
        log.info("model said %r", answer)
        emotion, reply = split_emotion(answer)
        reply = tidy(reply, called)
        ctx.show(emotion=choose_emotion(emotion, reply))
        if reply:
            ctx.say(reply)
        if executed:
            memory.log(ctx.conn, text, executed, "llm", True)
            offer(ctx, text, executed)
    ctx.history.append((time.monotonic(), " ".join((heard or "").split()) or text, reply))  # as heard, for the model
    del ctx.history[:-20]
    return reply


def single_instance(path):
    """Hold an exclusive lock for as long as this Zade runs; None if another Zade already holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def read_inbox(path):
    """Commands typed in the desktop app, one per line; the file is emptied once read. The app appends
    under the same lock, so a command written while this runs lands before or after, never lost."""
    try:
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            lines = f.read().splitlines()
            f.truncate(0)
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip()]


def safe_handle(ctx, text):
    t = time.perf_counter()
    ctx.route = ""
    try:
        reply = handle(ctx, text)
        if ctx.route or reply:  # silence and "stop" are not worth a history entry
            memory.log_request(ctx.conn, text.strip(), reply, ctx.route or "none",
                               (time.perf_counter() - t) * 1000, ctx.cfg["history"]["keep"])
        return reply
    except Exception:  # one bad request must not kill the assistant
        log.exception("handling %r failed", text)
        try:
            ctx.say("Something went wrong.")
        except Exception:
            log.exception("could not report the failure")
        return ""


def laya_predictor(cfg):
    if not cfg["router"]["laya_enabled"]:
        return None
    try:
        import laya

        return laya.load("convaiinnovations/laya").predict
    except Exception as e:  # missing package, download failure or bad checkpoint: run without Laya
        log.warning("Laya unavailable, skipping: %s", e)
        return None


def main():
    from . import audio, stt, tts, ui, voice_focus

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load()
    lock = single_instance(pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.lock")
    if lock is None:
        log.error("Zade is already running; not starting a second copy.")
        sys.exit(1)
    config.load_env()  # API keys (Spotify, cloud models) from ~/.config/zade/env
    try:
        conn = memory.connect(pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.db")
    except sqlite3.DatabaseError as e:
        log.error("database unusable, learning disabled this session: %s", e)
        conn = memory.connect(":memory:")
    stream = audio.open_stream()
    wake = audio.wake_model(cfg)
    # Hotkey: `kill -USR1 $(cat ~/.local/share/zade/zade.pid)` acts like saying the wake word.
    trigger = threading.Event()
    signal.signal(signal.SIGUSR1, lambda *_: trigger.set())
    pid_file = pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.pid"
    pid_file.write_text(str(os.getpid()))
    from . import hotkey

    dictate = threading.Event()
    bindings = [(cfg["hotkey"]["key"], cfg["hotkey"]["hold_s"], trigger) if cfg["hotkey"]["enabled"] else None,
                (cfg["dictation"]["key"], cfg["dictation"]["hold_s"], dictate) if cfg["dictation"]["enabled"] else None]
    detectors = iter(hotkey.watch_all([b for b in bindings if b]))
    ptt = next(detectors) if bindings[0] else None    # push-to-talk key detector
    typer = next(detectors) if bindings[1] else None  # voice-typing key detector

    def hear(timeout=None, released=None, cancelled=None, keep_reply=False, hands_free_if_early=False):
        stt.gpu_start(cfg)  # the GPU speech model (if used) loads while the user talks
        # keep_reply: while answering a question, keep it (e.g. a command to approve) on screen
        ui.show("listening", heard="", emotion="neutral", **({} if keep_reply else {"reply": ""}))
        a = audio.record(stream, cfg, timeout, released, cancelled, on_level=ui.level,
                         hands_free_if_early=hands_free_if_early)
        if a is None:
            ui.show("idle")
            return None
        ui.show("thinking")
        if cfg["audio"].get("voice_focus"):
            try:
                a = voice_focus.focus(a, cfg)
            except Exception as e:  # never lose the request over it
                log.warning("voice focus failed: %s", e)
        words = [*memory.shortcuts(conn), *fact_words(memory.facts(conn)), *ctx.app_words, *ctx.discord_words]
        # Hindi comes back in Devanagari. It stays so for the model (which then answers in Hindi, spoken by
        # the Hindi voice); the overlay, Zade's phrases and yes/no answers get English letters ("band karo").
        text = stt.transcribe(a, cfg, hotwords=words)
        ui.show("thinking", heard=hinglish.to_latin(text).strip())
        return text

    spoke_at = []
    barge = []  # why speech was interrupted ("wake" or "hotkey"); empty when not interrupted

    def interrupted():
        """While speaking: stop if the hotkey fires or the wake word is heard (barge-in)."""
        if trigger.is_set():
            trigger.clear()
            barge.append("hotkey")
            return True
        # While Zade talks, its own voice reaches the mic: only a sure wake (not the low listening threshold,
        # which skips the second check) may interrupt it.
        barge_at = max(cfg["wake"]["threshold"], config.wake_sure(cfg["wake"]))
        while stream.read_available >= audio.FRAME:
            if max(wake.predict(audio.read(stream)).values()) >= barge_at:
                barge.append("wake")
                return True
        return False

    def say(text):
        if barge:  # the user cut in: stay quiet for the rest of this request
            return
        spoke_at.append(time.perf_counter())
        wake.reset()
        spoken, _, detail = text.partition("\n")  # text after a newline is shown, not spoken
        # a Hindi reply is spoken in Devanagari (by the Hindi voice) and shown in English letters
        ui.show("speaking", reply=hinglish.to_latin(tts.clean(spoken)) + (f"\n{detail}" if detail else ""))
        if not tts.speak(spoken, cfg, interrupt=interrupted):
            audio.drain(stream)
        ui.show("done")

    def confirm(question):
        say(question)
        if barge:  # interrupted instead of answering: treat as no
            return False
        answer = hinglish.to_latin(hear(5.0, keep_reply=True) or "")
        log.info("asked %r, heard %r", question.split("\n")[0], answer)
        return actions.is_yes(answer)

    def ask_user(question):
        say(question)
        return "" if barge else hinglish.to_latin(hear(8.0, keep_reply=True) or "")

    ctx = Ctx(cfg, conn, say, confirm, predict=laya_predictor(cfg), ask_user=ask_user)
    atexit.register(lambda: ctx.replay and ctx.replay.stop())  # on exit or restart: stop recorders, free the RAM
    ctx.show = ui.set
    ctx.app_words = actions.app_names()  # your installed apps and games, so Whisper expects their names
    overlay = ui.start(cfg)
    if overlay:
        atexit.register(overlay.terminate)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # run exit handlers (close the overlay) on stop

    last_check = [0.0]
    typed = []  # commands typed in the desktop app
    inbox = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zade" / "inbox"

    config_file = pathlib.Path("~/.config/zade/config.toml").expanduser()
    config_mtime = [config_file.stat().st_mtime if config_file.exists() else 0]

    last_discord, last_names = [0.0], [-1e9]

    def announce(e):
        """Read a Discord event out, then act on the answer: reply to a message (after a yes), answer or
        decline a call. Silence leaves it alone."""
        from . import discord

        if e["kind"] == "call" and time.time() - e.get("at", 0) / 1000 > 30:  # it has stopped ringing by now
            return
        say(discord.event_line(e))
        if barge:
            return
        answer = hinglish.to_latin(hear(6.0, keep_reply=True) or "")
        said = router.normalize(answer)
        try:
            if e["kind"] == "call":
                if actions.is_yes(answer):
                    discord.call("answer", channel_id=e["channel_id"])
                elif re.search(r"\b(?:no|nope|decline|reject|cut|nahi)\b", said):
                    discord.call("decline", channel_id=e["channel_id"])
                return
            if not said or said in STOP_WORDS or dismissed(said) or re.match(r"(?:no|nope|nah|nahi|later)\b", said):
                return
            # a plain "yes please" to "Reply?" isn't the message: ask what to say
            text = "" if actions.is_yes(answer) else discord.reply_text(answer)
            if not text:
                text = discord.reply_text(ask_user("What should I say?"))
            # a mention is answered as a reply in its channel, where everyone there sees it: say so
            mention = e["kind"] == "mention" and e.get("message_id")
            where = f"as a reply to {e['from']} in {e['where']}" if mention else f"in {e['where']}" if e.get("where") \
                else f"to {e['from']}"
            if text and confirm(ask_send(text, where)):
                if mention:
                    discord.call("reply", channel_id=e["channel_id"], message_id=e["message_id"], text=text)
                else:
                    discord.call("send", channel_id=e["channel_id"], text=text)
                say(f"{'Replied' if mention else 'Sent'} to {e['from']}.")
        except discord.Failed as err:
            say(str(err))
        except discord.Unavailable as err:
            log.warning("discord: %s", err)

    def discord_names():
        from . import discord

        ctx.discord_words = discord.names()
        brain.NAMES[:] = ctx.discord_words  # saying a Discord name brings the Discord tools

    def read_typed():
        typed.extend(read_inbox(inbox))

    def poll():
        if time.monotonic() - last_check[0] >= 0.5:  # reminders, typed commands, live settings
            last_check[0] = time.monotonic()
            guarded(pump_reminders, ctx)
            guarded(sync_replay, ctx)
            if time.monotonic() - last_discord[0] >= 2:
                last_discord[0] = time.monotonic()
                guarded(pump_discord, ctx)
            if time.monotonic() - last_names[0] >= 600 or (not ctx.discord_words and time.monotonic() - last_names[0] >= 30):
                last_names[0] = time.monotonic()  # every 10 min (every 30 s until Discord is up)
                guarded(discord_names)
            guarded(stt.gpu_idle, cfg)  # frees its VRAM after stt.keep_alive_s
            guarded(read_typed)
            mtime = config_file.stat().st_mtime if config_file.exists() else 0
            if mtime != config_mtime[0]:  # changed in the app: overlay, sounds, quiet hours, safety apply now
                config_mtime[0] = mtime
                try:
                    apply_live(cfg, config.load())
                    ui.configure(cfg)
                    log.info("live settings reloaded")
                except Exception as e:  # a half-written or broken file: keep the current settings
                    log.warning("could not reload settings: %s", e)
        if typed:
            return "typed"
        if ctx.alerts:
            return "alert"
        if ctx.discord_events:
            return "discord"
        if trigger.is_set():
            trigger.clear()
            return "hotkey"
        if dictate.is_set():
            dictate.clear()
            return "dictate"
        return None

    def respond(text):
        t = time.perf_counter()
        spoke_at.clear()
        reply = safe_handle(ctx, text)
        if not spoke_at:  # stayed silent (nothing said, or "stop"): hide the overlay
            ui.show("idle")
        log.info("heard %r, replied after %.2fs (speech threshold %d)", text,
                 (spoke_at[0] if spoke_at else time.perf_counter()) - t,
                 audio.speech_threshold(audio.noise, cfg["audio"]["rms_threshold"], cfg["audio"]["noise_factor"]))
        return reply

    stt.transcribe(np.zeros(audio.RATE, np.int16), cfg)  # load whisper before the first command
    stt.stop_unused(cfg)  # a speech server left over from another provider
    stt.gpu_start(cfg)  # counts as a use, so a GPU model loaded at login is freed after the idle time
    if cfg["wake"]["verify"]:
        stt.wake_check(np.zeros(audio.RATE, np.int16), cfg)  # and the wake word's second-opinion model
    log.info("ready")
    (pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.ready").write_text(str(os.getpid()))  # for the app
    while True:
        if barge:  # interrupted mid-speech: listen right away, as if woken
            source = barge.pop()
            barge.clear()
        else:
            source = audio.wait_for_wake(stream, wake, cfg["wake"]["threshold"], poll,
                                         verify=(lambda clip: stt.wake_check(clip, cfg)) if cfg["wake"]["verify"] else None,
                                         sure=config.wake_sure(cfg["wake"]))
        if source == "wake" and (is_quiet(cfg) or snoozed()):  # quiet hours, Do Not Disturb, "stop for 10 min"
            log.info("wake word ignored (quiet)")
            continue
        if source == "dictate":  # voice typing: record while the key is held, type it, no model
            audio.cue(stream, soft=True, cfg=cfg)
            text = hear(released=lambda: not typer.held(), cancelled=typer.cancelled)
            if text and (typed := dictation_text(hinglish.to_latin(text))):
                try:
                    actions._call(["wtype", "--", typed])
                    log.info("typed %r", typed)
                except actions.Failed as e:
                    log.warning("voice typing failed: %s", e)
            ui.show("idle")
            continue
        if source == "typed":  # from the app: no microphone, straight to handling
            text = typed.pop(0)
            ui.show("thinking", heard=text, reply="")
            respond(text)
            if not spoke_at:
                ui.show("idle")
            continue
        if source == "discord":
            while ctx.discord_events and not barge:  # cut in: the rest wait
                announce(ctx.discord_events.pop(0))
            if not spoke_at:
                ui.show("idle")
            continue
        if source == "alert":
            while ctx.alerts and not barge:  # cut in: the rest wait until the user is done
                say("Reminder: " + ctx.alerts.pop(0))
            continue
        audio.cue(stream, cfg=cfg)
        threading.Thread(target=brain.warm_up, args=(cfg,), daemon=True).start()
        # Push-to-talk: while the key is still held, record until it is released.
        ptt_active = source == "hotkey" and ptt and ptt.held()
        if cfg["sound"]["wake_reply"] and not ptt_active:  # e.g. "Yes?" before listening
            tts.speak(cfg["sound"]["wake_reply"], cfg)
            audio.drain(stream)
        text = hear(released=(lambda: not ptt.held()) if ptt_active else None,
                    cancelled=ptt.cancelled if ptt_active else None, hands_free_if_early=True)
        if text is None:
            audio.drain(stream)
            continue
        reply = respond(text)
        # Follow-up: when Zade asked a question, listen briefly for an answer without the wake word.
        while cfg["followup"]["enabled"] and wants_followup(reply) and not barge and not snoozed():
            audio.cue(stream, soft=True, cfg=cfg)
            text = hear(cfg["followup"]["listen_s"])
            if text is None:
                break
            reply = respond(text)


if __name__ == "__main__":
    main()
