import copy

from zade import __main__ as z
from zade import actions, config, memory

DEV = [{"name": "open_app", "args": {"name": "kitty"}}]


def make(said, answers=(), **kw):
    replies = list(answers)

    def confirm(question):
        said.append(question)
        return replies.pop(0)

    kw.setdefault("ask", lambda *a: "answer")
    kw.setdefault("run_action", lambda action, confirm: "")
    return z.Ctx(cfg=copy.deepcopy(config.DEFAULTS), conn=memory.connect(":memory:"),
                 say=said.append, confirm=confirm, find_app=lambda name: None, **kw)


def test_nothing_said_stays_silent():
    said = []
    for heard in ["", "Thank you.", "..."]:  # silence, or Whisper's noise phantoms
        assert z.handle(make(said), heard) == ""
    assert said == []


def test_shortcut_runs_without_llm():
    said, ran = [], []

    def no_llm(*a):
        raise AssertionError("LLM must not be called")

    ctx = make(said, ask=no_llm, run_action=lambda a, c: ran.append(a) or "")
    memory.add_shortcut(ctx.conn, "dev", DEV)
    z.handle(ctx, "Dev.")
    assert ran == DEV and said == ["Done."]


def test_llm_actions_promoted_after_three():
    said = []

    def ask(text, facts, cfg, run_tool, history=()):
        run_tool("open_app", {"name": "kitty"})
        return "Opening kitty."

    ctx = make(said, answers=[True], ask=ask)
    for _ in range(3):
        z.handle(ctx, "Terminal please.")
    assert "Want 'terminal' to always do that?" in said
    assert memory.shortcuts(ctx.conn) == {"terminal": DEV}


def test_memory_tools_not_logged_as_actions():
    said = []

    def ask(text, facts, cfg, run_tool, history=()):
        return run_tool("remember", {"fact": "my editor is nvim"})

    ctx = make(said, ask=ask)
    z.handle(ctx, "remember my editor is nvim")
    assert memory.facts(ctx.conn) == ["my editor is nvim"]
    assert memory.last_actions(ctx.conn) is None


def test_failed_action_spoken_and_not_promoted():
    said = []

    def fail(action, confirm):
        raise actions.Failed("I couldn't find kitty.")

    def ask(text, facts, cfg, run_tool, history=()):
        return run_tool("open_app", {"name": "kitty"})

    ctx = make(said, ask=ask, run_action=fail)
    for _ in range(3):
        z.handle(ctx, "terminal")
    assert said == ["I couldn't find kitty."] * 3


def test_confirm_band_no_goes_to_llm():
    said = []
    predict = lambda s, q: {"answers": {"action": {"choice": "play or pause media", "confidence": 0.7}}}
    ctx = make(said, answers=[False], predict=predict, ask=lambda *a: "llm answer")
    z.handle(ctx, "pause the music")
    assert said == ["Did you mean play or pause media?", "llm answer"]


def test_make_shortcut_uses_previous_actions():
    said = []
    ctx = make(said, ask=lambda text, facts, cfg, run_tool, history=(): run_tool("make_shortcut", {"phrase": "Dev"}))
    memory.log(ctx.conn, "start my dev setup", DEV, "llm", True)
    z.handle(ctx, "remember dev means that")
    assert memory.shortcuts(ctx.conn) == {"dev": DEV}


def test_unexpected_action_error_returns_to_llm():
    said = []

    def boom(action, confirm):
        raise AttributeError("'NoneType' object has no attribute 'lower'")

    ctx = make(said, ask=lambda text, facts, cfg, run_tool, history=(): run_tool("open_app", {"name": None}), run_action=boom)
    z.handle(ctx, "open something")
    assert said and said[0].startswith("That failed")


def test_safe_handle_survives_any_error():
    said = []

    def boom(*a):
        raise RuntimeError("provider exploded")

    z.safe_handle(make(said, ask=boom), "what is tcp")
    assert said == ["Something went wrong."]


def test_info_tools_and_timer(monkeypatch):
    from zade import info

    said = []
    monkeypatch.setattr(info, "weather", lambda place="", day=0: f"weather {place} {day}")
    ctx = make(said, ask=lambda text, facts, cfg, run_tool, history=(): " | ".join([
        run_tool("weather", {"place": "Mumbai", "day": 1}),
        run_tool("set_timer", {"seconds": 300, "message": "check the oven"}),
    ]))
    z.handle(ctx, "weather tomorrow and remind me in 5 minutes")
    assert said == ["weather Mumbai 1 | Timer set for 5 minutes."]
    (due, message, repeat), = memory.reminders(ctx.conn)  # stored, so it survives a restart
    assert message == "check the oven" and repeat is None
    assert z.pump_reminders(ctx, due + 1) == 1 and ctx.alerts == ["check the oven"]
    assert memory.last_actions(ctx.conn) == [{"name": "weather", "args": {"place": "Mumbai", "day": 1}}]  # timer not learned


def test_next_time():
    import datetime as dt

    now = dt.datetime(2026, 9, 23, 22, 30)
    assert z.next_time("17:00", now) == dt.datetime(2026, 9, 24, 17, 0)  # already passed today
    assert z.next_time("23:15", now) == dt.datetime(2026, 9, 23, 23, 15)
    assert z.next_time("5 pm", now) == dt.datetime(2026, 9, 24, 17, 0)
    assert z.next_time("9:30 am", now) == dt.datetime(2026, 9, 24, 9, 30)


def test_daily_reminder_list_and_cancel():
    said = []
    ctx = make(said, ask=lambda text, facts, cfg, run_tool, history=(): run_tool(
        "set_reminder", {"message": "drink water", "at": "9 am", "daily": True}))
    z.handle(ctx, "every day at 9 remind me to drink water")
    assert said[-1].startswith("Every day at 9:00 AM I'll remind you to drink water")
    (_, _, repeat), = memory.reminders(ctx.conn)
    assert repeat == 86400
    assert "drink water" in z.dispatch(ctx, {"name": "list_reminders", "args": {}})[0]
    assert z.dispatch(ctx, {"name": "cancel_reminder", "args": {"query": "water"}})[0] == "Cancelled 1 reminder."


def test_followup_uses_recent_history():
    said, seen = [], []

    def ask(text, facts, cfg, run_tool, history=()):
        seen.append(list(history))
        return "Which city?" if text == "plan a trip" else "Mumbai it is."

    ctx = make(said, ask=ask)
    assert z.handle(ctx, "plan a trip") == "Which city?"
    assert z.wants_followup("Which city?") and not z.wants_followup("Done.")
    z.handle(ctx, "Mumbai")
    assert seen == [[], [("plan a trip", "Which city?")]]


def test_history_expires_and_is_capped():
    ctx = make([])
    ctx.cfg["followup"].update(history_turns=2, history_s=120)
    ctx.history = [(0.0, "old", "x"), (100.0, "a", "1"), (150.0, "b", "2"), (160.0, "c", "3")]
    assert z.recent(ctx, now=200.0) == [("b", "2"), ("c", "3")]


def test_teach_in_one_sentence():
    said = []

    def ask(text, facts, cfg, run_tool, history=()):
        run_tool("open_app", {"name": "steam"})
        run_tool("open_app", {"name": "discord"})
        return run_tool("make_shortcut", {"phrase": "gaming mode"})

    ctx = make(said, ask=ask)
    z.handle(ctx, "when I say gaming mode open steam and discord")
    assert memory.shortcuts(ctx.conn) == {"gaming mode": [
        {"name": "open_app", "args": {"name": "steam"}}, {"name": "open_app", "args": {"name": "discord"}}]}


def test_stop_words_are_silent():
    said = []
    for t in ["Stop.", "cancel", "never mind", "shut up", "quiet"]:
        assert z.handle(make(said), t) == ""
    assert said == []


def test_teach_saves_what_was_actually_done():
    said = []
    ctx = make(said, ask=lambda *a, **k: "should not be needed")
    z.handle(ctx, "When I say work time, go to workspace 2.")
    assert memory.shortcuts(ctx.conn) == {
        "work time": [{"name": "window", "args": {"action": "workspace", "workspace": "2"}}]}
    assert said[-1] == "Got it. Say work time any time."


def test_teach_with_nothing_done_saves_nothing():
    said = []

    def fail(action, confirm):
        raise actions.Failed("I couldn't find steam.")

    ctx = make(said, ask=lambda text, facts, cfg, run_tool, history=(): run_tool("open_app", {"name": "steam"}),
               run_action=fail)
    z.handle(ctx, "when i say gaming mode open steam")
    assert memory.shortcuts(ctx.conn) == {}
    assert said[-1] == "That didn't work, so I didn't save it."


def test_sync_apps_refreshes_the_word_list(monkeypatch):
    said = []
    ctx = make(said)
    ctx.app_words = ["Old App"]
    monkeypatch.setattr(actions, "app_names", lambda: ["MECCHA CHAMELEON", "Telegram"])
    monkeypatch.setattr(actions, "all_app_count", lambda: 142)
    z.handle(ctx, "sync apps")
    assert ctx.app_words == ["MECCHA CHAMELEON", "Telegram"]
    assert said == ["Synced 142 apps."]


def test_names_in_facts_become_hotwords():
    assert z.fact_words(["user's name is zonic", "my dog's name is bruno", "likes nvim"]) == ["Zonic", "Bruno"]
    assert z.fact_words(["user is called zonic"]) == ["Zonic"]
