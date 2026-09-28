import logging
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .actions import SCREEN_WORDS, SITES, THIS_WINDOW, shot_target

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
        # Partial phrase: a word-boundary prefix covering at least half the phrase, only for a shortcut that
        # does one thing ("close discord" must not run a saved "close discord and steam").
        if phrase.startswith(text + " ") and len(text) * 2 >= len(phrase) and len(table[phrase]) == 1:
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


# The overlay character's expressions (the model tags replies with one of these).
EMOTIONS = ["neutral", "happy", "excited", "laughing", "love", "sad", "crying", "confused", "surprised", "amazed",
            "annoyed", "angry", "curious", "smug", "sleepy", "embarrassed", "nervous", "wink", "playful"]


UNITS = {"second": 1, "minute": 60, "min": 60, "hour": 3600}


def _seconds(n, unit):
    return int(n) * UNITS[unit.rstrip("s") if unit.rstrip("s") in UNITS else unit]


NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty five": 25,
           "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
           "a couple of": 2, "couple of": 2, "a couple": 2, "a few": 3, "few": 3, "ek": 1, "do": 2, "teen": 3,
           "char": 4, "paanch": 5, "panch": 5, "das": 10, "bees": 20, "tees": 30, "aadha": 0.5, "adha": 0.5}
DURATION_UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60,
                  "minute": 60, "minutes": 60, "minat": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600,
                  "hours": 3600, "ghanta": 3600, "ghante": 3600, "ghanta bhar": 3600}
VAGUE = {"a bit": 600, "a little bit": 600, "a little while": 900, "a while": 1800, "some time": 1800,
         "sometime": 1800, "a moment": 60, "a minute or two": 120, "thodi der": 900, "kuch der": 1800}


def duration(text):
    """Seconds in a spoken length of time: "10 minutes", "an hour and a half", "half an hour", "2 hrs 30 min",
    "a while", "das minute"; None if it isn't one."""
    t = " ".join(text.split())
    t = re.sub(r"^(?:for |the next |next |about |around |like |roughly )+", "", t)
    if t in VAGUE:
        return VAGUE[t]
    special = {"half an hour": 1800, "half hour": 1800, "a half hour": 1800, "quarter of an hour": 900,
               "a quarter of an hour": 900, "quarter hour": 900, "an hour and a half": 5400,
               "one and a half hours": 5400, "1 and a half hours": 5400, "a day": 86400, "the day": 86400,
               "the night": 36000, "tonight": 36000, "aadha ghanta": 1800, "adha ghanta": 1800}
    if t in special:
        return special[t]
    num = "|".join(sorted(map(re.escape, NUMBERS), key=len, reverse=True))
    unit = "|".join(sorted(map(re.escape, DURATION_UNITS), key=len, reverse=True))
    part = rf"(\d+(?:\.\d+)?|{num}) ?({unit})"
    if not re.fullmatch(rf"{part}(?:(?: and| ,)? {part})*", t):
        return None
    total = 0.0
    for n, u in re.findall(part, t):
        total += (float(n) if n[0].isdigit() else NUMBERS[n]) * DURATION_UNITS[u]
    return int(total) or None


# Asking Zade to be quiet (without the time these are ordinary stop words)
_QUIET = (r"(?:stop|shut up|be quiet|keep quiet|stay quiet|quiet|silence|sleep|go to sleep|take a break|"
          r"mute yourself|pause yourself|snooze|leave me alone|go away|"
          r"(?:stop|don't|dont|do not) (?:respond|responding|reply|replying|talk|talking|listen|listening|answer|"
          r"answering|speak|speaking|disturb me|bother me|interrupt me)|"
          r"(?:stop )?(?:respond|reply|talk|listen|answer|speak)(?:ing)? (?:to me )?(?:no more|nothing)|"
          r"chup|chup raho|chup ho ja|chup ho jao|chup karo|chup rehna|bas karo|mat bolo|mat sunna|band ho ja)")
_RESUME = (r"(?:you can (?:talk|speak|listen|respond|reply)(?: to me)?(?: now| again)?|"
           r"start (?:listening|responding|talking|replying)(?: again)?|stop being quiet|wake up|come back|i'?m back|"
           r"unmute yourself|unsnooze|end (?:the )?snooze|ab bolo|ab bol sakte ho)")


# Discord (the ZadeControl plugin). "mute" alone stays the speakers; Discord needs its name, or words only a
# voice chat has (vc, call, deafen).
_DC = r"(?: (?:on|in) discord| discord)?"
_VC = r"(?:(?:the|my|this) )?(?:vc|voice(?: channel| chat| call)?|call|discord call|discord vc)"


def parse_discord(text):
    from . import discord

    t = re.sub(r"^(?:discord )", "", text) if text.startswith("discord ") else text
    said_discord = "discord" in text
    mic = r"(?:me|my mic|my microphone|mic|microphone|myself)"
    if re.fullmatch(rf"(?:mute {mic}|turn off my (?:mic|microphone)|mic off|mute){_DC}", t) and said_discord \
            or re.fullmatch(rf"mute {mic} (?:in|on) {_VC}|mute {mic}|mic (?:band|band karo|off karo)", t):
        return {"action": "mute"}
    if re.fullmatch(rf"(?:unmute {mic}|turn on my (?:mic|microphone)|mic on|unmute){_DC}", t) and said_discord \
            or re.fullmatch(rf"unmute {mic} (?:in|on) {_VC}|unmute {mic}|mic (?:on karo|chalu karo)", t):
        return {"action": "unmute"}
    if re.fullmatch(rf"(?:deafen|deaf)(?: me| myself)?{_DC}", t):
        return {"action": "deafen"}
    if re.fullmatch(rf"(?:undeafen|undeaf|un deafen)(?: me| myself)?{_DC}", t):
        return {"action": "undeafen"}
    if re.fullmatch(rf"(?:leave|disconnect(?: from)?|exit|quit|hang up(?: on)?|end|drop(?: out of)?) {_VC}{_DC}"
                    rf"|hang up|disconnect me{_DC}|(?:vc|call) (?:se )?(?:nikal|nikalo|chhodo|leave karo)", t):
        return {"action": "leave"}
    # "join voice channel in bitnade called staff vc", "join the vc called staff vc in bitnade"
    if m := re.fullmatch(rf"(?:join|connect to|hop in|hop into|get in|get into) (?:the |a )?(?:vc|voice channel|voice chat)"
                         rf"(?: (?:in|on|of) (.+?))? (?:called|named)(?: to| the)? (.+?)(?: (?:in|on) (?!discord$)(.+?))?"
                         rf"(?: server)?{_DC}", t):
        server = m[1] or m[3]
        return {"action": "join", "target": m[2] + (f" in {server}" if server else "")}
    # "join the gaming vc", "join a staff-vc channel in BITNADE (not bitnade server)": the server stays in the
    # target, and a trailing "not ..." only says which one it isn't. A plain "channel" also needs "join" and a
    # vc, voice or discord said: "go to the general channel" is no reason to go live on the mic.
    if (m := re.fullmatch(rf"(join|connect to|go to|hop in|hop into|get in|get into|enter) (?:the |a )?(.+?) "
                          rf"(vc|voice(?: channel| chat)?|channel)(?: (?:in|on|of) (?!discord$)(.+?))?(?: server)?(?: not .+)?{_DC}"
                          rf"|(?:join|connect to) (?:vc|voice) (.+?){_DC}", t)) \
            and (m[3] != "channel" or m[1] in ("join", "connect to") and (said_discord or re.search(r"\b(?:vc|voice)\b", m[2]))):
        return {"action": "join", "target": (m[2] or m[5]) + (f" in {m[4]}" if m[4] else "")}
    if m := re.fullmatch(r"(?:call|ring|voice call|phone) (.+?) (?:on|in) discord|discord call (.+)", text):
        return {"action": "call", "target": m[1] or m[2]}
    if m := re.fullmatch(r"(?:open|show|go to|switch to|take me to)(?: my)?(?: (?:dm|dms|chat|messages|channel))?"
                         r"(?: with| from| of)? (.+?) (?:on|in) discord", text):
        return {"action": "open", "target": m[1]}
    if m := re.fullmatch(r"(?:read|read out|what(?:'s| is| are)|show)(?: me)? (?:the |my )?(?:last |latest |new |recent )?"
                         r"(?:(\d+) )?(?:messages?|texts?|dms?|chats?)(?: from| of| in| by| with) (.+?)" + _DC, text) \
            or re.fullmatch(r"what (?:did|has) (.+?) (?:say|send|write|message|text)(?: me)?(?: on discord)?", text):
        count, target = (m[1], m[2]) if m.re.groups == 2 else (None, m[1])
        return {"action": "read", "target": target, **({"count": int(count)} if count else {})}
    if re.fullmatch(r"(?:do i have |any |check |read |show )?(?:my )?(?:unread|new) (?:discord )?(?:messages|dms|texts|pings)"
                    r"(?: on discord)?|(?:any|check)(?: my)? discord(?: messages| dms)?|who (?:messaged|texted|dmed|pinged) me"
                    r"(?: on discord)?|kisne message kiya", text):
        return {"action": "unread"}
    msg = r"(?:last |latest )?(?:message|msg|text)"
    # "react fire to dexorto's message" and "react to the last message with a mad emoji"
    if m := re.fullmatch(rf"react (?:to|on) (?:(?:the|my)|(.+?)(?:'s|s)) {msg}(?: from (.+?))? (?:with|using) (?:a |an |the )?(.+?)"
                         rf"(?: emoji| emote| reaction)?(?: (?:on|in) discord)?", t):
        target = m[1] or m[2]
        return {"action": "react", "emoji": m[3], **({"target": target} if target else {})}
    # "react fire", "react with a skull emoji to my message": an emoji name, or said as a reaction to a message
    # or on Discord ("react or vue, which is better?" is a question). No " to "/" on " inside the emoji: "react
    # fire to alex message" (no 's) goes to the model instead of reacting "fire to alex message" to the open chat.
    if (m := re.fullmatch(rf"react(?: with| using)? (?:a |an |the )?((?:(?! to | on ).)+?)( emoji| emote| reaction)?"
                          rf"(?: (?:to|on|with) (?:the )?(?:(.+?)(?:'s|s) )?({msg}))?(?: (?:on|in) discord)?", t)) \
            and (m[2] or m[4] or said_discord or discord.emoji(m[1])):
        return {"action": "react", "emoji": m[1], **({"target": m[3]} if m[3] else {})}
    if m := re.fullmatch(r"reply (?:to )?(.+?) (?:on|in) discord (?:saying|with|that) (.+)"
                         r"|reply to (.+?) (?:saying|that) (.+?) (?:on|in) discord", text):
        return {"action": "reply", "target": m[1] or m[3], "text": m[2] or m[4]}
    if m := re.fullmatch(r"reply (?:saying |with )?((?!to )(?:(?! saying ).)+?) (?:on|in) discord", text):
        return {"action": "reply", "text": m[1]}
    if m := re.fullmatch(rf"(?:edit|change) my {msg}(?: on discord)? to(?: say)? (.+)", text):
        return {"action": "edit", "text": m[1]}
    if m := re.fullmatch(rf"(?:delete|remove|unsend) my {msg}(?: (?:to|in|from|with) (.+?))?(?: on discord)?", text):
        return {"action": "delete", **({"target": m[1]} if m[1] else {})}
    if m := re.fullmatch(r"(?:set|change|make|put) my (?:discord )?status (?:to |as )?(.+?)(?: on discord)?"
                         r"|go (online|idle|invisible|offline) on discord", text):
        return {"action": "set_status", "text": m[1] or m[2]}
    if m := re.fullmatch(r"(?:(?:what'?s|what is) (?:going on|happening|new|up)|what did i miss|catch me up|summari[sz]e"
                         r"(?: the)?(?: chat| channel| messages)?)(?: (?:in|on|of|from|with) (.+?))? (?:on|in) discord"
                         r"|catch me up on discord", text):
        return {"action": "summarize", **({"target": m[1]} if m and m.lastindex and m[1] else {})}
    if re.fullmatch(rf"who(?:'?s| is| all)? (?:in|on) {_VC}|who(?:'?s| is) talking(?: in {_VC})?|am i (?:muted|deafened)"
                    rf"(?: on discord)?|(?:vc|call) (?:mein|me) kaun (?:hai|h)", t):
        return {"action": "status"}
    return None


def parse_message(text):
    """ "message dexorto on discord saying hi", "send hi to dexorto on discord" -> (to, text)."""
    if m := re.fullmatch(r"(?:send (?:a )?(?:message|msg|text|dm) to|message|msg|text|dm|tell|ping) (.+?) "
                         r"(?:on|in) discord(?: saying| that| to say|:)? (.+)", text):
        return m[1], m[2]
    if m := re.fullmatch(r"(?:send|say) (.+?) to (.+?) (?:on|in) discord", text):
        return m[2], m[1]
    # "say hello in discord chat", "send gg in the chat on discord", "type lol here on discord": the open chat
    if m := re.fullmatch(r"(?:say|send|type|write|post|drop) (.+?) (?:in|on|to|into) (?:the |this |my )?(?:current |open )?"
                         r"(?:discord (?:chat|channel|dm)|(?:chat|channel|dm)(?: (?:on|in|of) discord)?)"
                         r"|(?:say|send|type|write|post) (.+?) here (?:on|in) discord", text):
        return "the current chat", m[1] or m[2]
    # "send the latest screenshot to discord", "post gg on discord": no one named, so the open chat
    if m := re.fullmatch(r"(?:send|share|post|drop) (.+?) (?:to|on|in|into) discord", text):
        return "the current chat", m[1]
    return None


def parse_snooze(text):
    """"stop for 10 minutes", "don't respond for an hour", "10 minute ke liye chup raho" -> seconds (0 = resume)."""
    if re.fullmatch(_RESUME, text):
        return 0
    words = text.split()
    for i in range(1, len(words)):  # every split into "<be quiet> <time>" or "<time> <be quiet>"
        head, tail = " ".join(words[:i]), " ".join(words[i:])
        if re.fullmatch(_QUIET, head) and (d := duration(re.sub(r"^(?:till|until) ", "", tail))):
            return d
        if re.fullmatch(rf"(?:(?:ke liye|tak|for) )?{_QUIET}", tail) and (d := duration(head)):
            return d
    return None


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
# a percentage, as digits or words ("50", "fifty", "seventy five", "a hundred")
NUM = (r"(\d{1,3}|(?:a |one )?hundred|(?:" + "|".join(_TENS) + r")(?:[ -](?:" + "|".join(_ONES[1:10]) + r"))?|"
       + "|".join(_ONES) + ")")


def num(said):
    """ "fifty" -> 50, "seventy five" -> 75, "a hundred" -> 100, "40" -> 40."""
    said = said.replace("-", " ")
    if said.isdigit():
        return int(said)
    if said.endswith("hundred"):
        return 100
    words = said.split()
    return _TENS.get(words[0], 0) + (_ONES.index(words[-1]) if words[-1] in _ONES else 0)


def _open_one(name, find_app):
    if find_app(name):
        return {"name": "open_app", "args": {"name": name}}
    if name.replace(" ", "") in SITES:
        return {"name": "open_website", "args": {"site": name}}
    # "dominos pizza website", "the irctc site", "github.com": a website even if we don't know it
    if re.fullmatch(r".+ (?:website|web site|site|webpage)|[\w-]+(\.[\w-]+)+(/\S*)?", name):
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


_VOLUME_VERBS = {"set", "change", "put", "turn", "increase", "decrease", "raise", "lower", "reduce", "boost", "drop",
                 "bring", "make", "keep", "adjust", "lessen", "cut"}


def parse_pattern(text, find_app):
    if m := re.fullmatch(r"(?:open|launch|start|run) (?:the )?(.+?)(?: app)?", text):
        if actions := _open_targets(m[1], find_app):  # otherwise later patterns ("start do not disturb")
            return actions[0] if len(actions) == 1 else actions
    if m := re.fullmatch(r"(?:go|switch|move) to workspace ?(\w+)", text):  # "workspace1" too
        return {"name": "window", "args": {"action": "workspace", "workspace": m[1]}}
    if m := re.fullmatch(r"move (?:this|this window|the window|it) to workspace ?(\w+)", text):
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
    if dc := parse_discord(text):
        return {"name": "discord", "args": dc}
    if msg := parse_message(text):
        return {"name": "send_message", "args": {"to": msg[0], "text": msg[1], "app": "discord"}}
    # the replay buffer: "clip that", "save the last 20 seconds", "save the replay"
    if re.fullmatch(r"(?:clip|record) (?:that|this|it)|save (?:the |that )?(?:replay|rewind|clip)|clip that please", text):
        return {"name": "clip", "args": {}}
    if m := re.fullmatch(rf"(?:save|clip|record) (?:the )?last {NUM} (seconds?|minutes?|minute)"
                         r"|(?:save|clip|record) (?:the )?last (minute|half minute)", text):
        seconds = (num(m[1]) * (60 if m[2].startswith("minute") else 1)) if m[1] else (60 if m[3] == "minute" else 30)
        return {"name": "clip", "args": {"seconds": seconds}}
    if (seconds := parse_snooze(text)) is not None:
        return {"name": "snooze", "args": {"seconds": seconds}}
    if m := re.fullmatch(r"(?:(turn on|enable|start) )?(?:do not disturb|quiet mode)(?: (on|off))?|"
                         r"(turn off|disable|stop) (?:do not disturb|quiet mode)", text):
        return {"name": "dnd", "args": {"on": not (m[3] or m[2] == "off")}}
    if re.fullmatch(r"(?:sync|refresh|rescan)(?: my| the)?(?: apps| applications| games)?", text):
        return {"name": "sync_apps", "args": {}}
    media = r"(?: the| this| my)? ?(?:song|music|track|playback|video|spotify|it)?"
    if re.fullmatch(r"(?:stop|pause)" + media, text):
        return {"name": "media", "args": {"cmd": "pause"}}
    if re.fullmatch(r"(?:resume|unpause|continue)" + media + r"|play(?: the)? (?:music|song)|play", text):
        return {"name": "media", "args": {"cmd": "play"}}
    if re.fullmatch(r"(?:next|skip)(?: this| the)?(?: song| track| one)?|play the next(?: song| track| one)?", text):
        return {"name": "media", "args": {"cmd": "next"}}
    if re.fullmatch(r"(?:previous|last|go back(?: to the)?(?: previous| last)?)(?: song| track| one)?", text):
        return {"name": "media", "args": {"cmd": "previous"}}
    # Name questions, answered from settings and memory (the small model mixed up "my" and "your")
    # (matched at the start: extra words heard after the question, like background talk, don't matter)
    if re.match(r"(?:what(?: is|'?s) your name|your name is what)\b", text) or \
            re.fullmatch(r"who are you|what should i call you", text):
        return {"name": "whoami", "args": {"who": "assistant"}}
    if re.match(r"(?:what(?: is|'?s) my name|my name is what|do you know my name)\b", text) or text == "who am i":
        return {"name": "whoami", "args": {"who": "user"}}
    # "show me your happy face", "make a sad expression", "play playful expression" (heard as "play flool")
    if m := re.fullmatch(r"(?:show|make|do|give|play|place)(?: me)?(?: a| an| the| your)? (.+?) (?:face|expression|look)",
                         text):
        hit = process.extractOne(m[1].replace(" ", ""), EMOTIONS, scorer=fuzz.ratio, score_cutoff=60)
        return {"name": "express", "args": {"emotion": hit[0] if hit else ""}}
    # "play" is often heard as "place" ("Place Scars by Juice WRLD"), so "place" counts too, but only
    # when it clearly names a song ("... by artist" or "... on youtube"), not "place an order".
    if m := re.fullmatch(r"(play|place) (?!music$|pause$|next$|previous$)(.+?)"
                         r"(?: (?:on|in|from) (spotify|youtube music|youtube|yt music|yt))?", text):
        verb, query, provider = m[1], m[2], {"yt": "youtube", "yt music": "youtube music"}.get(m[3], m[3])
        if not provider and (t := re.fullmatch(r"(.+) (?:on|in|from) (\w+(?: \w+)?)", query)):
            # a misheard provider ("from sopity"): match it by sound
            hit = max(((fuzz.ratio(t[2], p), p) for p in ("spotify", "youtube", "youtube music")), default=(0, ""))
            if hit[0] >= 60:  # "sopity" scores 61; unrelated words ("my own") ~30
                query, provider = t[1], hit[1]
        if verb == "play" or provider or " by " in query:
            return {"name": "play_music", "args": {"query": query, **({"provider": provider} if provider else {})}}
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
    if m := re.fullmatch(r"type (.+?)(?: and| then| and then)? (?:press|hit) enter", text):  # typed, then sent
        return [{"name": "type_text", "args": {"text": m[1]}}, {"name": "press_keys", "args": {"keys": "enter"}}]
    if m := re.fullmatch(r"type (.+)", text):
        return {"name": "type_text", "args": {"text": m[1]}}
    # Local time and date come from the clock, never from the model. "in india" = local time here.
    here = r"(?: now| here| right now| in india| today)?"
    if re.fullmatch(rf"(?:what time is it|what's the time|what is the time|tell me the time|time){here}", text):
        return {"name": "time", "args": {}}
    if re.fullmatch(rf"(?:what's the date|what's today's date|what is the date|what is today's date"
                    rf"|what day is it|what's the day|today's date|date){here}", text):
        return {"name": "date", "args": {}}
    if re.fullmatch(r"lock(?: the| my)?(?: screen| computer| pc| system)?", text):
        return {"name": "lock_screen", "args": {}}
    if re.fullmatch(r"(?:take a |take )?screenshot", text):
        return {"name": "screenshot", "args": {}}
    # "screenshot discord", "take a screenshot of the firefox window", "discord ka ss lo": only that app's
    # window; "screenshot this window": the focused one; "screenshot the screen": all of it
    if m := re.fullmatch(r"(?:take |grab |get )?(?:a )?(?:screenshot|screen shot|ss) (?:of )?(.+)"
                         r"|(.+?) (?:ka|ki) (?:screenshot|screen shot|ss)(?: (?:lo|le|lelo|le lo|lena|kar|karo|kar do|chahiye))?",
                         text):
        target = shot_target(m[1] or m[2])
        if target in SCREEN_WORDS:
            return {"name": "screenshot", "args": {}}
        if target in THIS_WINDOW or find_app(target):
            return {"name": "screenshot", "args": {"app": target}}
    if m := re.fullmatch(r"(?:close|quit|kill|exit) (?:the )?(.+?)(?: app)?", text):
        return {"name": "close_app", "args": {"name": m[1]}} if find_app(m[1]) else None
    if m := re.fullmatch(r"(?:(set|increase|decrease|raise|lower|reduce|boost|drop|turn|bring|put|make)(?: (up|down))? )?"
                         rf"(?:the |my )?volume(?: (up|down))? (?:(to|by) )?{NUM}(?: percent)?", text):
        verb, up_down, prep, n = m[1], m[2] or m[3], m[4], num(m[5])
        direction = 1 if verb in ("increase", "raise", "boost") or up_down == "up" else \
            -1 if verb in ("decrease", "lower", "reduce", "drop") or up_down == "down" else 0
        if prep == "to" or not direction:  # "volume 40", "set volume to 40", "raise volume to 70"
            return {"name": "volume", "args": {"set": n}}
        return {"name": "volume", "args": {"delta": direction * n}}  # "volume up 10", "lower volume by 20"
    # one app's volume, like the volume mixer: "set the volume of spotify to fifty percent", "discord volume 30"
    app_name = r"([\w'.]+(?: [\w'.]+){0,2}?)"  # up to three words: longer is a sentence ("open firefox then set")
    if m := re.fullmatch(rf"(?:(?:set|change|put|turn)(?: the)? )?(?:volume (?:of|for) {app_name}|{app_name}(?:'s)? volume)"
                         rf" (?:to |at )?{NUM}(?: percent)?", text):
        app = m[1] or m[2]
        # "reduce the volume to 30" is the whole volume: an app's name doesn't start with a verb or end in "the"
        if app not in ("the", "my", "system", "master", "main", "overall") and not re.search(r"\b(?:then|and)\b", app) \
                and app.split()[0] not in _VOLUME_VERBS and app.split()[-1] not in ("the", "my", "a"):
            return {"name": "app_volume", "args": {"app": app, "set": num(m[3])}}
    if m := re.fullmatch(r"(?:turn )?(?:the )?volume (up|down)(?: a bit| a little)?", text):
        return {"name": "volume", "args": {"delta": 10 if m[1] == "up" else -10}}
    if m := re.fullmatch(r"(increase|raise|lower|decrease)(?: the)? volume(?: a bit| a little)?", text):
        return {"name": "volume", "args": {"delta": 10 if m[1] in ("increase", "raise") else -10}}
    if m := re.fullmatch(r"(?:search|google|look up)(?: for)? (.+)", text):
        # "search for it / that" points back at the conversation: the model knows what "it" is
        if m[1] not in ("it", "that", "this", "them", "those", "him", "her", "the same"):
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


def parse_compound(text, find_app, table=None, min_score=90):
    """ "mute and lock the screen", "open firefox, then set volume to 50": several instant commands in one
    sentence. Every part must be an instant command or a shortcut, else None (the model handles it)."""
    parts = [p for p in re.split(r"\s*,\s*(?:and |then )?|\s+(?:and then|and also|then|and|also|after that)\s+", text) if p]
    if not 2 <= len(parts) <= 5:
        return None
    out = []
    for part in parts:
        if table and (phrase := match_shortcut(part, table, min_score)):
            out.extend(table[phrase])
        elif action := parse_pattern(part, find_app):
            out.extend(action if isinstance(action, list) else [action])
        else:
            return None
    return out


def route(text, table, cfg, predict=None, find_app=lambda name: None):
    if not text:
        return Route("none")
    if phrase := match_shortcut(text, table, cfg["shortcut_min_score"]):
        return Route("run", table[phrase], "shortcut", phrase, phrase)
    if action := parse_pattern(text, find_app):
        actions = action if isinstance(action, list) else [action]
        return Route("run", actions, "pattern", actions[0]["name"])
    if actions := parse_compound(text, find_app, table, cfg["shortcut_min_score"]):
        return Route("run", actions, "pattern", " + ".join(a["name"] for a in actions))
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
