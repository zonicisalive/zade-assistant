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
    r = router.route("hold the tunes", {}, CFG, fake("play or pause media", 0.95))
    assert (r.kind, r.source) == ("run", "laya")
    assert r.actions == [{"name": "media", "args": {"cmd": "play-pause"}}]


def test_laya_confirm_band():
    r = router.route("hold the tunes", {}, CFG, fake("play or pause media", 0.7))
    assert (r.kind, r.label) == ("confirm", "play or pause media")


def test_laya_low_or_other_goes_to_llm():
    assert router.route("hold the tunes", {}, CFG, fake("play or pause media", 0.4)).kind == "llm"
    assert router.route("what is tcp", {}, CFG, fake(router.OTHER, 0.99)).kind == "llm"


def test_laya_picks_shortcut():
    r = router.route("fire up my dev stuff", {"dev": DEV}, CFG, fake("shortcut: dev", 0.95))
    assert (r.kind, r.actions, r.phrase) == ("run", DEV, "dev")


def test_laya_error_falls_through():
    def boom(state, questions):
        raise RuntimeError("model missing")

    assert router.route("hold the tunes", {}, CFG, boom).kind == "llm"


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
    assert P("open dominas pizza website", find) == {"name": "open_website", "args": {"site": "dominas pizza website"}}
    assert P("open irctc.co.in", find) == {"name": "open_website", "args": {"site": "irctc.co.in"}}
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
    assert P("play lose yourself on spotify", find) == {"name": "play_music",
                                                        "args": {"query": "lose yourself", "provider": "spotify"}}
    assert P("play scars on youtube", find)["args"] == {"query": "scars", "provider": "youtube"}
    assert P("play scars on youtube music", find)["args"] == {"query": "scars", "provider": "youtube music"}
    assert P("play scars from yt", find)["args"] == {"query": "scars", "provider": "youtube"}
    assert P("place jusu world scars on youtube", find)["args"] == {"query": "jusu world scars", "provider": "youtube"}
    assert P("place cards by juice wrld", find)["args"] == {"query": "cards by juice wrld"}
    assert P("place an order for pizza", find) is None and P("place a call to mom", find) is None
    assert P("play", find) == {"name": "media", "args": {"cmd": "play"}}  # plain "play" resumes playback


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


def test_press_key_patterns():
    P = lambda t: router.parse_pattern(router.normalize(t), find)
    assert P("press enter") == {"name": "press_keys", "args": {"keys": "enter"}}
    assert P("Press control C.") == {"name": "press_keys", "args": {"keys": "ctrl+c"}}
    assert P("hit escape") == {"name": "press_keys", "args": {"keys": "escape"}}
    assert P("press alt tab") == {"name": "press_keys", "args": {"keys": "alt+tab"}}
    assert P("press windows 2") == {"name": "press_keys", "args": {"keys": "super+2"}}
    assert P("press control shift t") == {"name": "press_keys", "args": {"keys": "ctrl+shift+t"}}
    assert P("press page down") == {"name": "press_keys", "args": {"keys": "page down"}}


def test_media_control_patterns():
    P, find = router.parse_pattern, lambda q: None
    for text, cmd in [("stop the song", "pause"), ("pause the music", "pause"), ("pause", "pause"),
                      ("resume the song", "play"), ("play the music", "play"), ("continue", "play"),
                      ("next song", "next"), ("skip this song", "next"), ("skip", "next"),
                      ("previous track", "previous"), ("go back to the last song", "previous")]:
        assert P(text, find) == {"name": "media", "args": {"cmd": cmd}}, text
    assert P("play scars", find)["name"] == "play_music"


def test_audit_regressions_router():
    P, find = router.parse_pattern, lambda q: None
    assert P("start do not disturb", find) == {"name": "dnd", "args": {"on": True}}  # not swallowed by "start X"
    for text, args in [("increase volume 30", {"delta": 30}), ("volume up 10", {"delta": 10}),
                       ("lower volume by 20", {"delta": -20}), ("turn the volume down 15", {"delta": -15}),
                       ("set volume to 40", {"set": 40}), ("raise volume to 70", {"set": 70}),
                       ("volume 55", {"set": 55})]:
        assert P(text, find) == {"name": "volume", "args": args}, text


def test_workspace_without_a_space():
    assert router.parse_pattern("go to workspace1", lambda q: None) == \
        {"name": "window", "args": {"action": "workspace", "workspace": "1"}}


def test_show_an_expression():
    P, find = router.parse_pattern, lambda q: None
    for text, e in [("play play flool expression", "playful"), ("show me your happy face", "happy"),
                    ("make a sad expression", "sad"), ("do the wink face", "wink")]:
        assert P(text, find) == {"name": "express", "args": {"emotion": e}}, text
    assert P("show me your banana face", find) == {"name": "express", "args": {"emotion": ""}}


def test_search_for_it_goes_to_the_model():
    P, find = router.parse_pattern, lambda q: None
    assert P("search for it", find) is None and P("google that", find) is None
    assert P("search for cheap flights", find) == {"name": "web_search", "args": {"query": "cheap flights"}}
    assert P("open discord and facebook", lambda q: ("discord", "discord") if q == "discord" else None) == [
        {"name": "open_app", "args": {"name": "discord"}}, {"name": "open_website", "args": {"site": "facebook"}}]


def test_misheard_provider_and_liked_songs():
    P, find = router.parse_pattern, lambda q: None
    assert P("play liked songs from sopity", find)["args"] == {"query": "liked songs", "provider": "spotify"}
    assert P("play my like songs from spotify", find)["args"] == {"query": "my like songs", "provider": "spotify"}
    assert P("play dancing on my own", find)["args"] == {"query": "dancing on my own"}  # 'my own' is no provider
    from zade import music
    assert music.LIKED.fullmatch("my like songs") and music.LIKED.fullmatch("liked songs")


def test_several_instant_commands_in_one_sentence():
    find = lambda q: ("firefox", "firefox") if q == "firefox" else None
    r = router.route("mute and lock the screen", {}, CFG, find_app=find)
    assert r.kind == "run" and [a["name"] for a in r.actions] == ["mute", "lock_screen"] and r.actions[0]["args"] == {"on": True}
    r = router.route("open firefox, then set volume to 50", {}, CFG, find_app=find)
    assert [a["name"] for a in r.actions] == ["open_app", "volume"] and r.actions[1]["args"] == {"set": 50}
    r = router.route("set a timer for 5 minutes and play lofi on youtube", {}, CFG, find_app=find)
    assert [a["name"] for a in r.actions] == ["set_timer", "play_music"]
    assert router.route("open firefox and tell me a joke", {}, CFG, find_app=find).kind == "llm"  # one part needs the model
    assert router.route("play rock and roll", {}, CFG, find_app=find).actions[0]["args"]["query"] == "rock and roll"


def test_snooze_and_durations():
    for text, seconds in [("stop for 10 minutes", 600), ("stop listening for the next 15 mins", 900),
                          ("dont respond for half an hour", 1800), ("be quiet for 2 hours and 30 minutes", 9000),
                          ("shut up for a while", 1800), ("for 5 minutes stop listening", 300),
                          ("go to sleep for twenty minutes", 1200), ("chup raho aadha ghanta", 1800),
                          ("leave me alone for an hour and a half", 5400), ("you can talk now", 0)]:
        assert router.parse_snooze(text) == seconds, text
    for text in ["stop", "stop the song", "stop for pizza", "stop playing for 10 minutes", "pause music for 10 minutes"]:
        assert router.parse_snooze(text) is None, text



def test_a_partial_phrase_never_runs_a_shortcut_that_does_more():
    close = lambda n: {"name": "close_app", "args": {"name": n}}
    table = {"close discord and steam": [close("discord"), close("steam")], "open my work setup now": [close("x")]}
    assert router.match_shortcut("close discord", table, 90) is None
    assert router.match_shortcut("open my work setup", table, 90) == "open my work setup now"


def test_discord_phrases():
    D = lambda t: router.parse_discord(router.normalize(t))
    assert D("mute me on discord") == {"action": "mute"} and D("unmute my mic") == {"action": "unmute"}
    assert D("deafen") == {"action": "deafen"} and D("hang up") == {"action": "leave"} and D("leave the vc") == {"action": "leave"}
    assert D("join the gaming vc") == {"action": "join", "target": "gaming"}
    assert D("call dexorto on discord") == {"action": "call", "target": "dexorto"}
    assert D("open general in bitnade on discord") == {"action": "open", "target": "general in bitnade"}
    assert D("read the last 3 messages from dexorto") == {"action": "read", "target": "dexorto", "count": 3}
    assert D("what did dexorto say") == {"action": "read", "target": "dexorto"}
    assert D("who messaged me on discord") == {"action": "unread"} and D("who's in the vc") == {"action": "status"}
    for text in ["mute", "mute the sound", "open discord", "call mom", "leave me alone", "play see you again"]:
        assert D(text) is None, text          # the speakers' mute and other commands stay theirs
    M = lambda t: router.parse_message(router.normalize(t))
    assert M("message dexorto on discord saying I'm late") == ("dexorto", "i'm late")
    assert M("send hi to dexorto on discord") == ("dexorto", "hi")
    assert router.parse_pattern("mute", lambda n: None) == {"name": "mute", "args": {"on": True}}
