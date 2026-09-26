import copy
import json

from zade import __main__ as z
from zade import brain, config, memory, providers, ui


def cfg():
    return copy.deepcopy(config.DEFAULTS)


def test_split_emotion():
    assert z.split_emotion("[happy] Sure, opening it now!") == ("happy", "Sure, opening it now!")
    assert z.split_emotion("  [Confused]  What do you mean?") == ("confused", "What do you mean?")
    assert z.split_emotion("No tag here.") == ("neutral", "No tag here.")
    assert z.split_emotion("[dancing] Hi") == ("neutral", "Hi")  # unknown tags are dropped, not spoken


def test_reply_emotion_reaches_the_overlay_and_is_not_spoken():
    said, shown = [], []
    ctx = z.Ctx(cfg=cfg(), conn=memory.connect(":memory:"), say=said.append, confirm=lambda q: True,
                ask=lambda *a, **k: "[excited] That's a great idea!", show=lambda **kw: shown.append(kw))
    assert z.handle(ctx, "let's build a robot") == "That's a great idea!"
    assert said == ["That's a great idea!"] and {"emotion": "excited"} in shown


def test_instant_actions_look_pleased_or_sorry():
    from zade import actions

    shown = []
    ctx = z.Ctx(cfg=cfg(), conn=memory.connect(":memory:"), say=lambda t: None, confirm=lambda q: True,
                run_action=lambda a, c: "", show=lambda **kw: shown.append(kw))
    z.handle(ctx, "go to workspace 2")
    assert shown[-1] == {"emotion": "happy"}

    def fail(a, c):
        raise actions.Failed("nope")

    ctx.run_action = fail
    z.handle(ctx, "go to workspace 2")
    assert shown[-1] == {"emotion": "sad"}


def test_prompt_uses_the_character_name_and_asks_for_tags(monkeypatch):
    got = {}
    monkeypatch.setattr(providers, "chat", lambda name, system, *rest: got.update(system=system) or "ok")
    c = cfg()
    c["persona"]["name"] = "Nova"
    brain.ask("hi", [], c, None, vram=lambda: 8.0)
    assert got["system"].startswith("You are Nova") and "[happy]" in got["system"]


def test_persona_look_is_sent_to_the_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "PATH", tmp_path / "state.json")
    c = cfg()
    c["persona"].update(shape="squircle", eyes="anime", blush=True)
    ui.configure(c)
    ui.show("speaking", emotion="happy")
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["emotion"] == "happy"
    assert state["persona"]["shape"] == "squircle" and state["persona"]["eyes"] == "anime"


def test_all_emotions_are_offered_to_the_model(monkeypatch):
    got = {}
    monkeypatch.setattr(providers, "chat", lambda name, system, *rest: got.update(system=system) or "ok")
    brain.ask("hi", [], cfg(), None, vram=lambda: 8.0)
    for e in ["laughing", "love", "crying", "amazed", "angry", "smug", "sleepy", "embarrassed", "nervous",
              "wink", "playful"]:
        assert e in z.EMOTIONS and f"[{e}]" in got["system"], e


def test_character_type_setting():
    assert config.DEFAULTS["persona"]["type"] == "blob"


def test_questions_back_look_curious_not_blank():
    assert z.choose_emotion("neutral", "How can I help you today?") == "curious"
    assert z.choose_emotion("neutral", "TCP is a protocol.") == "neutral"
    assert z.choose_emotion("happy", "Want me to open it?") == "happy"  # the model's own pick wins


def test_prompt_guides_emotion_choice(monkeypatch):
    got = {}
    monkeypatch.setattr(providers, "chat", lambda name, system, *rest: got.update(system=system) or "ok")
    brain.ask("hi", [], cfg(), None, vram=lambda: 8.0)
    s = got["system"]
    assert "greeting" in s and "[sleepy]" in s and "dry facts" in s


def test_emotion_tag_anywhere_is_removed():
    assert z.split_emotion("Your name is Zonic. [neutral]") == ("neutral", "Your name is Zonic.")
    assert z.split_emotion("[happy] Sure!") == ("happy", "Sure!")
    assert z.split_emotion("Done! [excited] Enjoy.") == ("excited", "Done! Enjoy.")
    assert z.split_emotion("Press [enter] to go.") == ("neutral", "Press [enter] to go.")  # not an emotion
