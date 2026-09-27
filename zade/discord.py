"""Discord through the ZadeControl Vencord plugin (vencord/zadeControl in this repo).

The plugin runs a small JSON API inside Discord on 127.0.0.1, guarded by a secret token it writes to
~/.config/zade/discord-token, and does each request with Discord's own actions: mute, deafen, voice
channels, calls, chats, messages. No window focus or key presses are involved.
"""

import json
import pathlib
import urllib.error
import urllib.request

URL = "http://127.0.0.1:47823/tool"
TOKEN = pathlib.Path("~/.config/zade/discord-token").expanduser()


STATUS = {"online": "online", "idle": "idle", "away": "idle", "dnd": "dnd", "do not disturb": "dnd", "busy": "dnd",
          "invisible": "invisible", "offline": "invisible", "hidden": "invisible"}


class Unavailable(Exception):
    """Discord isn't running, or the plugin isn't installed or enabled."""


class Failed(Exception):
    """The plugin couldn't do it; the message is for the user."""


def call(tool, timeout=10, **args):
    try:
        token = TOKEN.read_text().strip()
    except OSError:
        raise Unavailable("the ZadeControl plugin hasn't run yet") from None
    req = urllib.request.Request(URL, json.dumps({"name": tool, "args": args}).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise Unavailable(f"Discord answered {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unavailable(str(e)) from None
    if not out.get("ok"):
        raise Failed(out.get("error") or "Discord couldn't do that.")
    return out


def available():
    try:
        call("status", timeout=2)
        return True
    except (Unavailable, Failed):
        return False


def _voice_line(s):
    v = s.get("voice")
    if not v:
        return "You're not in a voice channel."
    people = v["people"]
    who = ", ".join(people) if len(people) <= 6 else f"{', '.join(people[:5])} and {len(people) - 5} more"
    state = " You're deafened." if s.get("deafened") else " You're muted." if s.get("muted") else ""
    return f"In {v['channel']}: {who}.{state}"


def clean_name(name):
    """Spelled-out letters joined ("D E X O R T O" -> "DEXORTO"); nothing else is changed. Extra words
    ("dexorto user") are the plugin's job: it tries the whole phrase first, then its longest word."""
    import re

    # capitals are kept: "BITNADE" and "bitnade" can be two different servers
    return re.sub(r"\b[a-zA-Z](?: [a-zA-Z]\b)+", lambda m: m[0].replace(" ", ""), " ".join(name.split()))


def emoji(name):
    """An emoji from what was said: the character itself, or its everyday name or alias ("fire", "red heart",
    "heart", "100", "thumbs up"), from the emoji package's CLDR names, so any emoji works without a list here."""
    import unicodedata

    import emoji as emoji_data

    name = (name or "").strip()
    if any(unicodedata.category(c) == "So" for c in name):
        return name
    said = " ".join(name.lower().replace("emoji", "").replace("-", " ").split())
    if not said:
        return None
    best = None
    for char, data in emoji_data.EMOJI_DATA.items():
        if data.get("status", 2) > 2:  # skewed variants and components, not emoji to send
            continue
        names = [n.strip(":").replace("_", " ").lower() for n in [data.get("en", ""), *data.get("alias", [])]]
        for n in names:
            score = 3 if n == said else 2 if n.startswith(said + " ") else 1 if all(w in n.split() for w in said.split()) else 0
            if score and (best is None or (score, -len(n)) > best[0]):
                best = ((score, -len(n)), char)
    return best[1] if best else None


def names():
    """Discord names for speech recognition to expect ("BITNADE", "DEXORTO", "staff vc"), or [] without the
    plugin. Capped: a very long list of expected words makes recognition worse, not better."""
    try:
        n = call("names", timeout=3)
    except (Unavailable, Failed):
        return []
    out = []
    for name in [*n["servers"], *n["people"], *n["voice"]]:
        if name and len(name) >= 3 and name.lower() not in {o.lower() for o in out}:
            out.append(name)
    return out[:80]


SCREENSHOTS = pathlib.Path("~/Pictures/Screenshots").expanduser()  # where niri and the shell save them


def screenshot_meant(text):
    """Whether a message to send is really "the screenshot" (to attach), not words to type."""
    import re

    return bool(re.fullmatch(r"(?:the |my |a |this |that |your )?(?:last |latest |recent |new )?"
                             r"(?:screenshot|screen shot|screenie|screen grab|ss|snip)", (text or "").strip(" .").lower()))


def latest_screenshot(folder=None):
    shots = [p for p in (folder or SCREENSHOTS).glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    return max(shots, key=lambda p: p.stat().st_mtime, default=None)


def ago(path, now=None):
    import time

    s = max(0, int((now or time.time()) - path.stat().st_mtime))
    return "just now" if s < 60 else f"{s // 60} minute{'s' * (s >= 120)} ago" if s < 3600 else \
        f"{s // 3600} hour{'s' * (s >= 7200)} ago" if s < 86400 else f"{s // 86400} day{'s' * (s >= 172800)} ago"


def event_line(e):
    """What Zade says for a Discord event: a DM or mention (then asks to reply), or a call (asks to answer)."""
    if e["kind"] == "call":
        return f"{e['from']} is calling on Discord. Answer?"
    words = e["text"].split()
    text = " ".join(words[:30]) + ("..." if len(words) > 30 else "")
    where = f" in {e['where']}" if e.get("where") else ""
    return f"{e['from']}{where} on Discord: {text}. Reply?"


def reply_text(answer):
    """The message in an answer to "Reply?": "yes, tell him five minutes" -> "five minutes"."""
    import re

    lead = (r"^(?:(?:yes|yeah|yep|ok|okay|sure|haan|han|ha)\b[,.!]?\s*)?"
            r"(?:(?:reply|reply saying|reply with|say|saying|tell (?:him|her|them)|write|send|bolo|bol do|keh do)\b[,:]?\s*)?"
            r"(?:that\s+)?")
    return re.sub(lead, "", answer.strip(), flags=re.I).strip(" .")


def run(a):
    """One spoken request (the "discord" tool): returns what to say."""
    act, target = a.get("action", ""), clean_name(a.get("target") or "")
    if act in ("mute", "unmute"):
        return "Muted on Discord." if call("mute", on=act == "mute")["muted"] else "Unmuted on Discord."
    if act in ("deafen", "undeafen"):
        return "Deafened." if call("deafen", on=act == "deafen")["deafened"] else "Undeafened."
    if act == "leave":
        call("disconnect")
        return "Left the voice channel."
    if act == "join":
        return f"Joined {call('join_voice', name=target)['channel']}."
    if act == "call":
        return f"Calling {call('call', name=target)['calling']}."
    if act == "open":
        return f"Opened {call('open', name=target)['opened']}."
    if act == "read":
        out = call("read", name=target, count=a.get("count") or 5, timeout=15)
        if not out["messages"]:
            return f"There are no messages in {out['chat']}."
        lines = " ".join(f"{m['from']}: {m['text']}" for m in out["messages"])
        return f"Latest in {out['chat']}. {lines}"
    if act == "unread":
        chats = call("unread")["chats"]
        if not chats:
            return "No unread messages on Discord."
        return "Unread: " + ", ".join(f"{c['from']} {c['count']}" for c in chats[:6]) + "."
    if act == "status":
        return _voice_line(call("status"))
    if act in ("react", "unreact"):
        e = emoji(a.get("emoji") or "")
        if not e:  # not an emoji's name ("mad"): the brain picks one
            from . import brain, config

            e = brain.pick_emoji(a.get("emoji") or "", config.load())
        if not e:
            raise Failed(f"I don't know the {a.get('emoji')} emoji.")
        out = call("react", name=target, emoji=e, remove=act == "unreact")
        return f"{'Removed' if act == 'unreact' else 'Reacted'} {e} on {out['author']}'s message."
    if act == "reply":
        return f"Replied to {call('reply', name=target, text=a.get('text', ''))['author']}."
    if act == "edit":
        return f"Edited your last message in {call('edit_last', name=target, text=a.get('text', ''))['chat']}."
    if act == "delete":
        return f"Deleted your last message in {call('delete_last', name=target)['chat']}."
    if act == "summarize":
        out = call("read", name=target, count=50, timeout=20)
        if not out["messages"]:
            return f"Nothing has been said in {out['chat']}."
        from . import brain, config

        lines = "\n".join(f"{m['from']}: {m['text']}" for m in out["messages"])
        summary = brain.summarize(lines, config.load())
        if not summary:
            raise Failed("I couldn't summarize it right now.")
        return f"In {out['chat']}: {summary}"
    if act == "set_status":
        s = call("set_status", status=STATUS.get((a.get("text") or a.get("status") or "").lower(), a.get("text") or ""))
        return f"Your Discord status is {s['status'].replace('dnd', 'do not disturb')}."
    raise Failed(f"I can't do {act} on Discord.")
