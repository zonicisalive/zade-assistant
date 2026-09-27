import pytest

from zade import discord


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


def test_spoken_names_are_cleaned_up():
    assert discord.clean_name("D E X O R T O user") == "dexorto user"   # letters joined, words kept
    assert discord.clean_name("the dexorto guy") == "the dexorto guy"
    assert discord.clean_name("general in bitnade") == "general in bitnade"


def test_emoji_by_everyday_name():
    assert discord.emoji("fire") == "\U0001f525" and discord.emoji("heart") == "❤️"
    assert discord.emoji("100") == "\U0001f4af" and discord.emoji("thumbs up") == "\U0001f44d"
    assert discord.emoji("\U0001f525") == "\U0001f525" and discord.emoji("zzzqqq") is None
