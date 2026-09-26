import copy
import datetime as dt

import numpy as np
import pytest

from zade import __main__ as z
from zade import actions, audio, config, ctl, memory, ui


def cfg():
    return copy.deepcopy(config.DEFAULTS)


# Quiet hours ---------------------------------------------------------------
def test_quiet_hours_wrap_past_midnight():
    c = cfg()
    c["quiet"].update(enabled=True, start="23:00", end="08:00")
    assert z.is_quiet(c, dt.datetime(2026, 9, 24, 23, 30))
    assert z.is_quiet(c, dt.datetime(2026, 9, 24, 7, 59))
    assert not z.is_quiet(c, dt.datetime(2026, 9, 24, 8, 0))
    assert not z.is_quiet(c, dt.datetime(2026, 9, 24, 14, 0))
    c["quiet"]["enabled"] = False
    assert not z.is_quiet(c, dt.datetime(2026, 9, 24, 23, 30))
    c["quiet"]["dnd"] = True  # the manual switch wins at any time
    assert z.is_quiet(c, dt.datetime(2026, 9, 24, 14, 0))


def test_reminders_wait_during_quiet_hours(monkeypatch):
    said = []
    ctx = z.Ctx(cfg=cfg(), conn=memory.connect(":memory:"), say=said.append, confirm=lambda q: True)
    memory.add_reminder(ctx.conn, 100.0, "drink water")
    ctx.cfg["quiet"]["dnd"] = True
    assert z.pump_reminders(ctx, 200.0) == 0 and ctx.alerts == []
    ctx.cfg["quiet"]["dnd"] = False
    assert z.pump_reminders(ctx, 200.0) == 1


# Live settings -------------------------------------------------------------
def test_live_reload_updates_only_live_sections():
    c = cfg()
    new = cfg()
    new["quiet"]["dnd"] = True
    new["ui"]["position"] = "bottom"
    new["llm"]["model"] = "something-else"  # needs a restart: must not change live
    z.apply_live(c, new)
    assert c["quiet"]["dnd"] is True and c["ui"]["position"] == "bottom"
    assert c["llm"]["model"] == "qwen2.5:7b-instruct"


# Overlay style -------------------------------------------------------------
def test_overlay_style_travels_with_the_state(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(ui, "PATH", tmp_path / "state.json")
    c = cfg()
    c["ui"].update(position="bottom-right", size="large", accent="#ff8800", linger_s=3.0)
    ui.configure(c)
    ui.show("listening")
    style = json.loads((tmp_path / "state.json").read_text())["style"]
    assert style == {"position": "bottom-right", "size": "large", "accent": "#ff8800", "linger_s": 3.0,
                     "reveal_cps": 18, "show_heard": True}


# Sounds --------------------------------------------------------------------
def test_chime_styles():
    assert audio.chime_wave("none", 0.5) is None
    soft, classic = audio.chime_wave("soft", 0.5), audio.chime_wave("classic", 0.5)
    assert soft is not None and classic is not None
    assert abs(np.max(np.abs(classic)) - 0.5 * 0.4) < 0.01       # volume scales the peak
    assert np.max(np.abs(audio.chime_wave("soft", 0.5, followup=True))) < np.max(np.abs(soft))


# Safety level --------------------------------------------------------------
def test_safety_levels():
    assert not z.needs_confirm("close_app", "commands")
    assert z.needs_confirm("close_app", "risky") and z.needs_confirm("type_text", "risky")
    assert not z.needs_confirm("open_app", "risky")
    assert z.needs_confirm("open_app", "everything") and z.needs_confirm("volume", "everything")
    assert not z.needs_confirm("time", "everything") and not z.needs_confirm("weather", "everything")


def test_dispatch_asks_first_at_risky_level():
    asked, ran = [], []
    ctx = z.Ctx(cfg=cfg(), conn=memory.connect(":memory:"), say=lambda t: None,
                confirm=lambda q: asked.append(q) or False, run_action=lambda a, c: ran.append(a) or "")
    ctx.cfg["safety"]["confirm"] = "risky"
    out, ok = z.dispatch(ctx, {"name": "close_app", "args": {"name": "firefox"}})
    assert asked == ["Close app firefox?"] and ran == [] and (out, ok) == ("Cancelled.", False)


# Shortcut builder ----------------------------------------------------------
def test_shortcut_save_validates_steps(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(ctl, "DB", tmp_path / "zade.db")
    steps = [{"name": "open_app", "args": {"name": "steam"}}, {"name": "volume", "args": {"set": 40}}]
    assert json.loads(ctl.main(["shortcut-save", "Gaming Mode", json.dumps(steps)])) == {"ok": True}
    assert memory.shortcuts(memory.connect(tmp_path / "zade.db")) == {"gaming mode": steps}
    bad = [{"name": "format_disk", "args": {}}]
    assert json.loads(ctl.main(["shortcut-save", "oops", json.dumps(bad)]))["ok"] is False


def test_do_not_disturb_by_voice(monkeypatch):
    from zade import router

    assert router.parse_pattern("do not disturb", lambda n: None) == {"name": "dnd", "args": {"on": True}}
    assert router.parse_pattern("turn off do not disturb", lambda n: None) == {"name": "dnd", "args": {"on": False}}
    saved = []
    monkeypatch.setattr(ctl, "set_setting", lambda k, v: saved.append((k, v)) or {"ok": True})
    ctx = z.Ctx(cfg=cfg(), conn=memory.connect(":memory:"), say=lambda t: None, confirm=lambda q: True)
    assert z.dispatch(ctx, {"name": "dnd", "args": {"on": True}}) == (
        "Do not disturb is on. Hold Win to talk to me.", True)
    assert ctx.cfg["quiet"]["dnd"] is True and saved == [("quiet.dnd", "true")]


def test_personality_and_character_apply_live():
    c = cfg()
    new = cfg()
    new["llm"]["personality"] = "Be playful."
    new["persona"]["name"] = "Nova"
    z.apply_live(c, new)
    assert c["llm"]["personality"] == "Be playful." and c["persona"]["name"] == "Nova"
