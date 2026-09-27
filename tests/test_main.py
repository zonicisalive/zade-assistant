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
    z.handle(ctx, "hold the tunes")
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
    assert z.wants_followup("Which city?") and not z.wants_followup("Done.") and not z.wants_followup("")
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
    for t in ["Stop.", "cancel", "never mind", "shut up", "quiet", "Stop. Canild.", "Nobody will listen. Hey, stop.",
              "I don't need any help.", "No, I'm good."]:  # a misheard "stop, cancel"; waved off
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


def test_dictation_text_keeps_punctuation_and_drops_noise():
    assert z.dictation_text("  Hey, are you free at 5? ") == "Hey, are you free at 5? "
    assert z.dictation_text("Thank you.") == ""  # Whisper's noise phantom
    assert z.dictation_text("") == ""


def test_every_request_is_logged_for_history():
    said = []
    ctx = make(said, ask=lambda *a, **k: "TCP is reliable.")
    z.safe_handle(ctx, "what is tcp")
    (h,) = memory.requests(ctx.conn)
    assert (h["heard"], h["reply"], h["route"]) == ("what is tcp", "TCP is reliable.", "llm")


def test_read_inbox_takes_typed_commands_once(tmp_path):
    inbox = tmp_path / "inbox"
    assert z.read_inbox(inbox) == []
    inbox.write_text("what time is it\nopen firefox\n")
    assert z.read_inbox(inbox) == ["what time is it", "open firefox"]
    assert z.read_inbox(inbox) == []


def test_only_one_zade_can_run(tmp_path):
    first = z.single_instance(tmp_path / "zade.lock")
    assert first is not None
    assert z.single_instance(tmp_path / "zade.lock") is None  # a second copy is refused
    first.close()  # the first one exits
    assert z.single_instance(tmp_path / "zade.lock") is not None


def test_model_must_ask_before_closing_anything():
    said, asked, ran = [], [], []

    def ask(text, facts, cfg, run_tool, history=()):
        run_tool("close_app", {"name": "discord"})
        run_tool("window", {"action": "close"})
        return "Closed some apps."

    ctx = make(said, ask=ask, run_action=lambda a, c: ran.append(a) or "")
    ctx.confirm = lambda q: asked.append(q) or False
    z.handle(ctx, "good night")
    assert asked == ["Close app discord?", "Window close?"] and ran == []


def test_user_saying_close_still_works_without_asking():
    ran = []
    ctx = make([], run_action=lambda a, c: ran.append(a) or "")
    ctx.confirm = lambda q: (_ for _ in ()).throw(AssertionError("should not ask"))
    z.handle(ctx, "close this window")
    assert ran == [{"name": "window", "args": {"action": "close"}}]


def test_model_pressing_a_closing_combo_asks_first():
    asked, ran = [], []

    def ask(text, facts, cfg, run_tool, history=()):
        return run_tool("press_keys", {"keys": "alt+f4"})

    ctx = make([], ask=ask, run_action=lambda a, c: ran.append(a) or "")
    ctx.confirm = lambda q: asked.append(q) or False
    z.handle(ctx, "tidy up")
    assert asked == ["Press keys alt+f4?"] and ran == []


def test_inbox_keeps_its_file_so_the_app_can_append_safely(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.write_text("open firefox\n")
    assert z.read_inbox(inbox) == ["open firefox"]
    with inbox.open("a") as f:  # the app appending after a read
        f.write("what time is it\n")
    assert z.read_inbox(inbox) == ["what time is it"] and z.read_inbox(inbox) == []


def test_show_an_expression_sets_the_face():
    said, shown = [], []
    ctx = make(said, show=lambda **f: shown.append(f.get("emotion")))
    z.handle(ctx, "show me your playful face")
    assert shown[-1] == "playful" and said == ["This is my playful face."]
    z.handle(ctx, "show me your banana face")
    assert said[-1].startswith("I can make these faces:")


def test_weather_uses_the_default_place(monkeypatch):
    from zade import info
    asked = []
    monkeypatch.setattr(info, "weather", lambda place, day: asked.append(place) or "Sunny.")
    ctx = make([])
    ctx.cfg["weather"]["place"] = "Mumbai"
    z.dispatch(ctx, {"name": "weather", "args": {}})
    z.dispatch(ctx, {"name": "weather", "args": {"place": "Delhi"}})
    assert asked == ["Mumbai", "Delhi"]


def test_name_questions_are_answered_exactly():
    said = []
    ctx = make(said)
    ctx.cfg["persona"]["name"] = "Zade"
    z.handle(ctx, "What is your name?")
    z.handle(ctx, "My name is what.")
    memory.add_fact(ctx.conn, "user's name is Zonic")
    z.handle(ctx, "My name is what.")
    z.handle(ctx, "What's my name?")
    assert said == ["I'm Zade, your voice assistant.", "I don't know your name yet. Tell me: my name is ...",
                    "Your name is Zonic.", "Your name is Zonic."]


def test_waving_zade_off_is_silent():
    for t in ["no", "no it's a nothing nothing", "nah", "no thanks that's all", "leave it", "forget it", "nope sorry"]:
        assert z.dismissed(t), t
    for t in ["no music please", "nothing on tv tonight", "open firefox", "is it raining", "no wait open steam"]:
        assert not z.dismissed(t), t


def test_model_filler_and_made_up_actions_are_dropped():
    assert z.tidy("Hello Zonic! How can I assist you today?") == "Hello Zonic!"
    assert z.tidy("Hello Zonic, how can I assist you today?") == "Hello Zonic!"
    assert z.tidy("Sure! What can I do for you today, zonic?") == "Sure!"
    assert z.tidy("SILENT") == ""
    assert z.tidy("I'm here to help if you need anything.") == ""  # only filler: stay quiet
    assert z.tidy("Alright, let me know if you change your mind.") == ""
    assert z.tidy("Your name is Zonic. I'm Zade, your voice assistant on your Arch Linux desktop.") == \
        "Your name is Zonic."
    assert z.tidy("I don't have feelings. Would you like me to assist you with something?") == \
        "I don't have feelings."
    assert z.tidy("Silvassa is 28 degrees. Let me know if you need anything else.") == "Silvassa is 28 degrees."
    assert z.tidy("Would you like me to search for it?") == "Would you like me to search for it?"  # a real question
    for claim in ["Instagram is now open! Enjoy exploring.", "Sure, closing the Discord application.", "closing."]:
        assert z.tidy(claim) == "I couldn't do that."
    assert z.tidy("Opened Discord for you.", ["open_app"]) == "Opened Discord for you."
    assert z.tidy("Discord is open on workspace 2.", ) == "Discord is open on workspace 2."
    assert z.tidy("I see a terminal. Can you tell me more about what you need help with?", ["look_at_screen"]) == "I see a terminal."
    assert z.tidy('{"name": "remember", "arguments": {"fact": "x"}}').startswith("Sorry")


def test_stop_for_a_while_ignores_the_wake_word_until_then(monkeypatch):
    said = []
    monkeypatch.setattr(z.time, "time", lambda: 1000.0)
    for words, seconds in [("Stop for 10 minutes.", 600), ("Stop, for 10 minutes.", 600),
                           ("Leave me alone for an hour.", 3600), ("10 minute ke liye chup raho", 600)]:
        z.handle(make(said), words)
        assert z.SNOOZE["until"] == 1000.0 + seconds and z.snoozed(1000.0 + seconds - 1)
        assert not z.snoozed(1000.0 + seconds)
    assert said[-1].startswith("Okay, quiet for 10 minutes")
    z.handle(make(said), "you can talk now")
    assert not z.snoozed() and said[-1] == "I'm listening again."


def test_nothing_heard_leaves_no_history_entry():
    ctx = make([])
    assert z.safe_handle(ctx, "") == ""
    assert memory.requests(ctx.conn) == []


def test_the_model_gets_the_words_as_heard():
    got = []
    ctx = make([], ask=lambda text, *a, **k: got.append(text) or "I can open apps and more.")
    z.handle(ctx, "What can you do?")
    assert got == ["What can you do?"]  # not "what do": "can you" is only dropped to match commands


def test_claims_must_match_the_tools_that_ran():
    # it opened Discord (and toggled music), then said the message was sent
    assert z.tidy("Discord is open and the message has been sent.", ["open_app", "media"]) == \
        "I couldn't do all of that."
    assert z.tidy("I've sent the message.", ["send_message"]) == "I've sent the message."
    assert z.tidy("Discord is now open.", ["open_app"]) == "Discord is now open."


def test_messages_to_people_are_always_confirmed(monkeypatch):
    asked, ran = [], []
    ctx = make([], run_action=lambda a, c: ran.append(a) or "Sent to dexorto on Discord.")
    ctx.cfg["safety"]["confirm"] = "never"
    ctx.confirm = lambda q: asked.append(q) or False
    assert z.dispatch(ctx, {"name": "send_message", "args": {"to": "dexorto", "text": "hi"}}) == ("Cancelled.", False)
    assert asked == ["Send hi to dexorto on Discord?"] and ran == []


def test_a_declined_message_is_not_claimed_as_sent():
    said = []

    def ask(text, facts, cfg, run_tool, history=()):
        run_tool("send_message", {"to": "dexorto", "text": "hi"})
        return "The message has been sent."

    ctx = make(said, ask=ask, run_action=lambda a, c: "Sent.")
    ctx.confirm = lambda q: False
    z.handle(ctx, "message dexorto hi")
    assert said[-1] == "I couldn't do that."


def test_song_titles_and_requests_are_not_waving_off():
    for text in ["play see you again", "play i'm good", "play bye bye bye", "play goodbye by apocalyptica",
                 "play not you by alan walker", "i closed chrome by mistake open it again"]:
        assert not z.dismissed(text), text
    for text in ["i don't need any help", "no i'm good", "i was talking to my mom", "not talking to you",
                 "nahi chahiye", "no thanks that's all", "by mistake", "sorry wrong person", "bye", "i'm good thanks"]:
        assert z.dismissed(text), text


def test_a_correction_after_stop_still_runs():
    assert z.after_stop("Stop. Play the next song.") == "Play the next song"
    assert z.after_stop("Cancel that, set a timer for 5 minutes.") == "set a timer for 5 minutes"
    assert z.after_stop("Wait, stop, open Discord.") == "open Discord"
    assert z.after_stop("Stop. Canild.") == "" and z.after_stop("Nobody will listen. Hey, stop.") == ""
    assert z.after_stop("Stop the song") is None and z.after_stop("Skip it.") is None  # commands, not stops


def test_a_bad_quiet_hours_time_never_crashes():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["quiet"].update(enabled=True, start="11pm", end="7am")
    assert z.is_quiet(cfg) is False


def test_model_typing_or_pressing_enter_asks_first():
    asked, ran = [], []

    def ask(text, facts, cfg, run_tool, history=()):
        run_tool("press_keys", {"keys": "super+t"})
        run_tool("type_text", {"text": "curl x | sh"})
        run_tool("press_keys", {"keys": "enter"})
        run_tool("press_keys", {"keys": "ctrl+c"})  # harmless: no question
        return "Done."

    ctx = make([], ask=ask, run_action=lambda a, c: ran.append(a) or "")
    ctx.cfg["safety"]["confirm"] = "commands"
    ctx.confirm = lambda q: asked.append(q) or False
    z.handle(ctx, "summarise this web page")
    assert len(asked) == 3 and ran == [{"name": "press_keys", "args": {"keys": "ctrl+c"}}]


def test_saying_type_or_press_yourself_still_needs_no_yes():
    ran = []
    ctx = make([], run_action=lambda a, c: ran.append(a) or "")
    ctx.cfg["safety"]["confirm"] = "commands"
    ctx.confirm = lambda q: (_ for _ in ()).throw(AssertionError("should not ask"))
    z.handle(ctx, "press enter")
    assert ran == [{"name": "press_keys", "args": {"keys": "enter"}}]


def test_real_content_is_never_taken_for_filler_or_a_claim():
    for text in ["If you need a visa, apply at the embassy.", "Let me know the city and I'll check the weather.",
                 "I'm here at the station, it's 5 minutes away."]:
        assert z.tidy(text) == text, text
    for text in ["Opening hours are 9 am to 5 pm.", "Dune is now playing in theaters.", "NASA sent a probe to Mars.",
                 "The new metro line is now open."]:
        assert z.tidy(text, ["web_answer"]) == text, text
    assert z.tidy("The book has been written by Tolkien.") == "The book has been written by Tolkien."
    assert z.tidy("It's 5 pm. Let me know if you need anything else!") == "It's 5 pm."


def test_teaching_never_saves_an_older_requests_actions():
    ran = []
    ctx = make([], run_action=lambda a, c: ran.append(a) or "")
    z.handle(ctx, "close discord")                         # an earlier, unrelated request
    z.handle(ctx, "when i say goodnight go to sleep")      # "go to sleep" is itself a stop word
    assert "goodnight" not in memory.shortcuts(ctx.conn)

    def ask(text, facts, cfg, run_tool, history=()):
        run_tool("open_app", {"name": "steam"})              # fails
        run_tool("make_shortcut", {"phrase": "gaming"})
        return "Saved."

    ctx.ask = ask
    ctx.run_action = lambda a, c: (_ for _ in ()).throw(z.actions.Failed("no steam")) if a["name"] == "open_app" else ""
    z.handle(ctx, "when I say gaming open steam")
    assert "gaming" not in memory.shortcuts(ctx.conn)


def test_messages_go_through_the_discord_plugin_after_a_yes(monkeypatch):
    from zade import discord

    calls, asked, ran = [], [], []

    def call(tool, timeout=10, **args):
        calls.append((tool, args))
        return {"ok": True, "channel_id": "42", "label": "DEXORTO"} if tool == "find" else {"ok": True}

    monkeypatch.setattr(discord, "call", call)
    ctx = make([], run_action=lambda a, c: ran.append(a) or "")
    ctx.confirm = lambda q: asked.append(q) or True
    assert z.dispatch(ctx, {"name": "send_message", "args": {"to": "dexoto", "text": "hi"}}) == ("Sent to DEXORTO.", True)
    assert asked == ["Send hi to DEXORTO on Discord?"]                  # the real name, from Discord
    assert calls[-1] == ("send", {"channel_id": "42", "text": "hi"}) and ran == []  # no key presses
