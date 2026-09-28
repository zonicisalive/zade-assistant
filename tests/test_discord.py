import pytest

from zade import discord
from zade.discord import wait_ready as real_wait_ready  # conftest stubs discord.wait_ready


def test_spoken_replies(monkeypatch):
    answers = {
        "mute": {"ok": True, "muted": True},
        "join_voice": {"ok": True, "channel": "Gaming in Bitnade"},
        "read": {"ok": True, "chat": "DEXORTO", "messages": [{"from": "DEXORTO", "text": "Hi"}, {"from": "Zonic", "text": "Yo"}]},
        "unread": {"ok": True, "chats": [{"from": "DEXORTO", "count": 3}]},
        "status": {"ok": True, "muted": True, "deafened": False, "voice": {"channel": "Gaming", "people": ["Zonic", "Neel"]}},
    }
    monkeypatch.setattr(discord, "call", lambda tool, timeout=10, **a: answers[tool])
    assert discord.run({"action": "mute"}) == "Muted on Discord."
    assert discord.run({"action": "join", "target": "gaming"}) == "Joined Gaming in Bitnade."
    assert discord.run({"action": "read", "target": "dexorto"}) == "Latest in DEXORTO. DEXORTO: Hi Zonic: Yo"
    assert discord.run({"action": "unread"}) == "Unread: DEXORTO 3."
    assert discord.run({"action": "status"}) == "In Gaming: Zonic, Neel. You're muted."


def test_without_the_plugin_it_says_so():
    with pytest.raises(discord.Unavailable):  # conftest hides the real token
        discord.call("status")


def test_calls_go_over_the_plugins_socket(monkeypatch, tmp_path):
    import http.server
    import json
    import socketserver
    import threading

    (tmp_path / "token").write_text("t" * 48)
    monkeypatch.setattr(discord, "SOCKET", tmp_path / "d.sock")
    monkeypatch.setattr(discord, "TOKEN", tmp_path / "token")
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.headers["Authorization"], body))
            status, out = (401, {"ok": False}) if body["name"] == "old" else (200, {"ok": body["name"] == "status", "error": "nope"})
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(out).encode())

        def log_message(self, *a):
            pass

    srv = socketserver.UnixStreamServer(str(tmp_path / "d.sock"), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert discord.call("status")["ok"]
        with pytest.raises(discord.Failed, match="nope"):
            discord.call("mute")
        with pytest.raises(discord.Unavailable):  # a token Discord no longer takes
            discord.call("old")
    finally:
        srv.shutdown()
        srv.server_close()
    assert seen[0] == ("Bearer " + "t" * 48, {"name": "status", "args": {}})


def test_waits_while_discord_starts(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    answers = [discord.Unavailable("no socket yet"), {"ok": True, "servers": []}, {"ok": True, "servers": ["BITNADE"]}]

    def call(tool, timeout=10, **a):
        r = answers.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(discord, "call", call)
    monkeypatch.setattr(discord, "_running", lambda: True)
    real_wait_ready()
    assert answers == []
    monkeypatch.setattr(discord, "_running", lambda: False)  # not running at all: only a short grace
    clock = iter(range(0, 100, 2))
    monkeypatch.setattr("time.monotonic", lambda: next(clock))
    answers.extend([discord.Unavailable("closed")] * 5)
    with pytest.raises(discord.Unavailable):
        real_wait_ready()


def test_spoken_names_are_cleaned_up():
    assert discord.clean_name("D E X O R T O user") == "DEXORTO user"   # letters joined, words and capitals kept
    assert discord.clean_name("the dexorto guy") == "the dexorto guy"
    assert discord.clean_name("staff-vc in BITNADE") == "staff-vc in BITNADE"
    assert discord.clean_name("general in bitnade") == "general in bitnade"


def test_emoji_by_everyday_name():
    assert discord.emoji("fire") == "\U0001f525" and discord.emoji("heart") == "❤️"
    assert discord.emoji("100") == "\U0001f4af" and discord.emoji("thumbs up") == "\U0001f44d"
    assert discord.emoji("\U0001f525") == "\U0001f525" and discord.emoji("zzzqqq") is None
    assert discord.emoji(":fire:") == "\U0001f525" and discord.emoji(":thumbs_up:") == "\U0001f44d"


def test_announcements_and_replies():
    assert discord.event_line({"kind": "dm", "from": "DEXORTO", "text": "yo you on?"}) == "DEXORTO on Discord: yo you on?. Reply?"
    assert discord.event_line({"kind": "mention", "from": "Neel", "where": "the general channel in BITNADE", "text": "@Zonic gg"}) == \
        "Neel in the general channel in BITNADE on Discord: @Zonic gg. Reply?"
    assert discord.event_line({"kind": "call", "from": "DEXORTO"}) == "DEXORTO is calling on Discord. Answer?"
    assert discord.reply_text("Yes, tell him five minutes.") == "five minutes"
    assert discord.reply_text("say I'm coming") == "I'm coming"
    assert discord.reply_text("can't talk now") == "can't talk now"
    assert discord.reply_text("yes") == ""


def test_the_screenshot_is_attached_not_typed(tmp_path):
    import os
    import time

    assert discord.screenshot_meant("the screenshot") and discord.screenshot_meant("my latest screenshot")
    assert not discord.screenshot_meant("did you see my screenshot")       # words about one: sent as text
    old, new = tmp_path / "a.png", tmp_path / "b.png"
    old.write_bytes(b""), new.write_bytes(b"")
    os.utime(old, (time.time() - 600, time.time() - 600))
    assert discord.latest_screenshot(tmp_path) == new
    assert discord.ago(old) == "10 minutes ago" and discord.ago(new) == "just now"
