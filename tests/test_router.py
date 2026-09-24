from zade import router

CFG = {"shortcut_min_score": 90, "laya_accept": 0.9, "laya_confirm": 0.6, "laya_enabled": True}
DEV = [{"name": "shell", "args": {"cmd": "kitty & code &"}}]


def find(name):
    return ("firefox", "firefox") if name == "firefox" else None


def test_normalize():
    assert router.normalize("Zade, could you please Open Firefox?") == "open firefox"
    assert router.normalize("  Thank you.  ") == ""
    assert router.normalize("...") == ""
    assert router.normalize("Set volume to 40%.") == "set volume to 40"


def test_shortcut_exact_and_prefix():
    table = {"dev": DEV, "start dev setup": DEV}
    assert router.match_shortcut("dev", table, 90) == "dev"
    assert router.match_shortcut("start dev", table, 90) == "start dev setup"


def test_short_word_does_not_trigger_long_shortcut():
    assert router.match_shortcut("open", {"open dev setup": DEV}, 90) is None


def test_fuzzy_mishearing():
    assert router.match_shortcut("start dev set up", {"start dev setup": DEV}, 90) == "start dev setup"


def test_patterns():
    assert router.parse_pattern("open firefox", find) == {"name": "open_app", "args": {"name": "firefox"}}
    assert router.parse_pattern("close the firefox", find) == {"name": "close_app", "args": {"name": "firefox"}}
    assert router.parse_pattern("open my dev setup", find) is None
    assert router.parse_pattern("volume down a bit", find) == {"name": "volume", "args": {"delta": -10}}
    assert router.parse_pattern("turn the volume up", find) == {"name": "volume", "args": {"delta": 10}}
    assert router.parse_pattern("set volume to 40", find) == {"name": "volume", "args": {"set": 40}}
    assert router.parse_pattern("search for rust borrow checker", find) == {
        "name": "web_search", "args": {"query": "rust borrow checker"}}
    assert router.parse_pattern("what is tcp", find) is None


def test_route_order():
    r = router.route("dev", {"dev": DEV}, CFG)
    assert (r.kind, r.source, r.phrase, r.actions) == ("run", "shortcut", "dev", DEV)
    r = router.route("open firefox", {}, CFG, find_app=find)
    assert (r.kind, r.source) == ("run", "pattern")
    assert router.route("what is tcp", {}, CFG).kind == "llm"
    assert router.route("", {}, CFG).kind == "none"


def fake(label, conf):
    return lambda state, questions: {"answers": {"action": {"choice": label, "confidence": conf}}}


def test_laya_accept():
    r = router.route("pause the music", {}, CFG, fake("play or pause media", 0.95))
    assert (r.kind, r.source) == ("run", "laya")
    assert r.actions == [{"name": "media", "args": {"cmd": "play-pause"}}]


def test_laya_confirm_band():
    r = router.route("pause the music", {}, CFG, fake("play or pause media", 0.7))
    assert (r.kind, r.label) == ("confirm", "play or pause media")


def test_laya_low_or_other_goes_to_llm():
    assert router.route("pause the music", {}, CFG, fake("play or pause media", 0.4)).kind == "llm"
    assert router.route("what is tcp", {}, CFG, fake(router.OTHER, 0.99)).kind == "llm"


def test_laya_picks_shortcut():
    r = router.route("fire up my dev stuff", {"dev": DEV}, CFG, fake("shortcut: dev", 0.95))
    assert (r.kind, r.actions, r.phrase) == ("run", DEV, "dev")


def test_laya_error_falls_through():
    def boom(state, questions):
        raise RuntimeError("model missing")

    assert router.route("pause the music", {}, CFG, boom).kind == "llm"


def test_more_whisper_hallucinations_are_nothing():
    for t in ["Thank you for watching.", "Thanks.", "Thank you very much.", "Okay.", "So.", "Bye bye."]:
        assert router.normalize(t) == "", t


def test_more_volume_phrasings():
    assert router.parse_pattern("increase volume to 80", find) == {"name": "volume", "args": {"set": 80}}
    assert router.parse_pattern("turn the volume up to 30", find) == {"name": "volume", "args": {"set": 30}}
    assert router.parse_pattern("increase the volume", find) == {"name": "volume", "args": {"delta": 10}}
    assert router.parse_pattern("lower the volume", find) == {"name": "volume", "args": {"delta": -10}}
    assert router.parse_pattern("turn it down", find) is None


def test_new_fast_patterns():
    P = router.parse_pattern
    assert P("go to workspace 2", find) == {"name": "window", "args": {"action": "workspace", "workspace": "2"}}
    assert P("switch to workspace bot", find) == {"name": "window", "args": {"action": "workspace", "workspace": "bot"}}
    assert P("move this to workspace 3", find) == {
        "name": "window", "args": {"action": "move_to_workspace", "workspace": "3"}}
    assert P("close this window", find) == {"name": "window", "args": {"action": "close"}}
    assert P("open youtube", find) == {"name": "open_website", "args": {"site": "youtube"}}
    assert P("open my dev setup", find) is None  # unknown app and not a known site: goes to the LLM
    assert P("set a timer for 5 minutes", find) == {"name": "set_timer", "args": {"seconds": 300}}
    assert P("timer 90 seconds", find) == {"name": "set_timer", "args": {"seconds": 90}}
    assert P("remind me in 2 hours to call mom", find) == {
        "name": "set_timer", "args": {"seconds": 7200, "message": "call mom"}}
    assert P("what's the weather", find) == {"name": "weather", "args": {}}
    assert P("weather tomorrow", find) == {"name": "weather", "args": {"day": 1}}
    assert P("take a screenshot", find) == {"name": "screenshot", "args": {}}


def test_type_pattern():
    assert router.parse_pattern("type good morning", find) == {"name": "type_text", "args": {"text": "good morning"}}


def test_parse_teach():
    assert router.parse_teach("when i say gaming mode open steam and discord") == (
        "gaming mode", "open steam and discord")
    assert router.parse_teach("whenever i say work time go to workspace 2") == ("work time", "go to workspace 2")
    assert router.parse_teach("open steam") is None


def test_time_and_date_patterns():
    P = router.parse_pattern
    for t in ["what time is it", "what's the time", "what is the time", "time", "what's the time in india",
              "what time is it now", "tell me the time"]:
        assert P(t, find) == {"name": "time", "args": {}}, t
    for t in ["what's the date", "what's today's date", "what day is it", "what is the date today"]:
        assert P(t, find) == {"name": "date", "args": {}}, t


def test_system_status_patterns():
    P = router.parse_pattern
    assert P("how hot is my gpu", find) == {"name": "system_status", "args": {"what": "gpu"}}
    assert P("gpu temperature", find) == {"name": "system_status", "args": {"what": "gpu"}}
    assert P("how hot is my cpu", find) == {"name": "system_status", "args": {"what": "cpu"}}
    assert P("what's using my cpu", find) == {"name": "system_status", "args": {"what": "cpu"}}
    assert P("how much ram is free", find) == {"name": "system_status", "args": {"what": "ram"}}
    assert P("system status", find) == {"name": "system_status", "args": {"what": "all"}}
    assert P("what are my reminders", find) == {"name": "list_reminders", "args": {}}


def test_screen_patterns():
    for t in ["what's on my screen", "read my screen", "describe my screen", "what does this say",
              "explain this error", "what am i looking at"]:
        assert router.parse_pattern(t, find) == {"name": "look_at_screen", "args": {"question": t}}, t


def test_play_music_pattern_keeps_media_controls():
    P = router.parse_pattern
    assert P("play killshot by eminem", find) == {"name": "play_music", "args": {"query": "killshot by eminem"}}
    assert P("play lose yourself on spotify", find) == {"name": "play_music", "args": {"query": "lose yourself"}}
    assert P("play", find) is None and P("play music", find) is None  # plain resume stays a media control


def test_open_several_apps_at_once():
    apps = {"discord": ("discord", "discord"), "telegram": ("org.telegram.desktop", "Telegram")}
    find2 = lambda n: apps.get(n)
    assert router.parse_pattern("open discord whatsapp and telegram", find2) == [
        {"name": "open_app", "args": {"name": "discord"}},
        {"name": "open_website", "args": {"site": "whatsapp"}},
        {"name": "open_app", "args": {"name": "telegram"}},
    ]
    assert router.parse_pattern("open discord, telegram", find2) == [
        {"name": "open_app", "args": {"name": "discord"}}, {"name": "open_app", "args": {"name": "telegram"}}]
    assert router.parse_pattern("open discord and my notes thing", find2) is None  # unknown part: ask the model
    r = router.route("open discord and telegram", {}, CFG, find_app=find2)
    assert (r.kind, len(r.actions)) == ("run", 2)


def test_mute_and_unmute_patterns():
    P = router.parse_pattern
    for t in ["mute", "mute the sound", "mute the volume", "mute it"]:
        assert P(router.normalize(t), find) == {"name": "mute", "args": {"on": True}}, t
    for t in ["unmute", "unmute the sound", "turn the sound back on", "turn sound on", "sound on"]:
        assert P(router.normalize(t), find) == {"name": "mute", "args": {"on": False}}, t
