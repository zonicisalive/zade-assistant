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


def run(a):
    """One spoken request (the "discord" tool): returns what to say."""
    act, target = a.get("action", ""), (a.get("target") or "").strip()
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
    raise Failed(f"I can't do {act} on Discord.")
