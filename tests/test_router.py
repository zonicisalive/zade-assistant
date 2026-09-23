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
