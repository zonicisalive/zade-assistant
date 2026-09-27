import copy

import pytest

from zade import brain, config, providers


def cfg(**llm):
    c = copy.deepcopy(config.DEFAULTS)
    c["llm"].update(llm)
    return c


def test_vram_picks_largest_card(tmp_path):
    for card, total, used in [("card0", 512 * 2**20, 100 * 2**20), ("card1", 16 * 2**30, 10 * 2**30)]:
        d = tmp_path / card / "device"
        d.mkdir(parents=True)
        (d / "mem_info_vram_total").write_text(str(total))
        (d / "mem_info_vram_used").write_text(str(used))
    assert brain.vram_free_gb(tmp_path) == pytest.approx(6.0)


def test_vram_unknown(tmp_path):
    assert brain.vram_free_gb(tmp_path) is None


def test_candidates():
    assert brain.candidates(cfg(), lambda: 8.0) == [("ollama", {}), ("ollama", {"num_gpu": 0})]
    assert brain.candidates(cfg(), lambda: 2.0, lambda c: False) == [("ollama", {"num_gpu": 0})]
    assert brain.candidates(cfg(), lambda: None) == [("ollama", {}), ("ollama", {"num_gpu": 0})]
    assert brain.candidates(cfg(fallback="anthropic"), lambda: 2.0, lambda c: False) == [("anthropic", {})]
    assert brain.candidates(cfg(provider="openai", fallback="none"), lambda: 0.0) == [("openai", {})]


def test_ask_falls_back(monkeypatch):
    seen = []

    def fake(name, system, text, tools, run_tool, c, extra, history=()):
        seen.append((name, extra))
        if name == "anthropic":
            raise providers.ProviderError("down")
        return "hi"

    monkeypatch.setattr(providers, "chat", fake)
    assert brain.ask("hello", [], cfg(provider="anthropic"), None, vram=lambda: 8.0) == "hi"
    assert seen == [("anthropic", {}), ("ollama", {"num_gpu": 0})]


def test_ask_all_down(monkeypatch):
    def fake(*a):
        raise providers.ProviderError("down")

    monkeypatch.setattr(providers, "chat", fake)
    assert brain.ask("hello", [], cfg(), None, vram=lambda: 8.0) == "My brain is offline right now."


def test_facts_in_prompt(monkeypatch):
    got = {}

    def fake(name, system, *rest):
        got["system"] = system
        return "ok"

    monkeypatch.setattr(providers, "chat", fake)
    brain.ask("hello", ["likes nvim"], cfg(), None, vram=lambda: 8.0)
    assert "- likes nvim" in got["system"]


def test_tool_names_unique():
    names = [t["name"] for t in brain.TOOLS]
    assert len(names) == len(set(names))


def test_no_fallback_after_a_tool_ran(monkeypatch):
    seen, ran = [], []

    def fake(name, system, text, tools, run_tool, c, extra, history=()):
        seen.append(name)
        run_tool("open_app", {"name": "kitty"})
        raise providers.ProviderError("timeout in round 2")

    monkeypatch.setattr(providers, "chat", fake)
    out = brain.ask("open kitty", [], cfg(), lambda n, a: ran.append(n) or "ok", vram=lambda: 8.0)
    assert seen == ["ollama"] and ran == ["open_app"]
    assert out == "I did part of that, then lost my connection."


def test_resident_model_counts_as_gpu():
    assert brain.candidates(cfg(), lambda: 2.0, lambda c: True) == [("ollama", {}), ("ollama", {"num_gpu": 0})]
    assert brain.candidates(cfg(), lambda: 2.0, lambda c: False) == [("ollama", {"num_gpu": 0})]


def test_new_tools_are_defined():
    names = {t["name"] for t in brain.TOOLS}
    assert {"weather", "web_answer", "set_timer", "note_add", "notes_read", "window", "open_website",
            "clipboard_read", "clipboard_copy", "type_text", "brightness", "screenshot", "power"} <= names


def test_prompt_states_local_timezone(monkeypatch):
    got = {}

    def fake(name, system, *rest):
        got["system"] = system
        return "ok"

    monkeypatch.setattr(providers, "chat", fake)
    brain.ask("hello", [], cfg(), None, vram=lambda: 8.0)
    assert "time zone" in got["system"] and "UTC" in got["system"]


def test_personality_goes_into_the_prompt(monkeypatch):
    got = {}
    monkeypatch.setattr(providers, "chat", lambda name, system, *rest: got.update(system=system) or "ok")
    c = cfg()
    c["llm"]["personality"] = "Call the user boss."
    brain.ask("hello", [], c, None, vram=lambda: 8.0)
    assert "Call the user boss." in got["system"]


def test_music_reply_says_what_really_played(monkeypatch):
    def fake(name, system, text, tools, run_tool, c, extra, history=()):
        run_tool("play_music", {"query": "plage scars"})
        return "[happy] I've started playing Plage Scars!"

    monkeypatch.setattr(providers, "chat", fake)
    assert brain.ask("play plage scars", [], cfg(), lambda n, a: "Playing Permanent Scar by Zeus.",
                     vram=lambda: 8.0) == "[happy] Playing Permanent Scar by Zeus."
    assert brain.ask("play it", [], cfg(), lambda n, a: "I couldn't reach Spotify right now.",
                     vram=lambda: 8.0) == "[embarrassed] I couldn't reach Spotify right now."


def test_warm_up_skips_when_the_model_unloads_instantly(monkeypatch):
    loads = []
    monkeypatch.setattr(brain, "_load", lambda c, keep: loads.append(keep))
    monkeypatch.setattr(brain, "vram_free_gb", lambda: 12.0)
    brain.warm_up(cfg(keep_alive="0"))
    assert loads == []
    brain.warm_up(cfg(keep_alive="5m"))
    assert loads == ["5m"]


def test_fix_song_returns_one_clean_line(monkeypatch):
    monkeypatch.setattr(providers, "chat", lambda *a, **k: '[happy] "Business by Eminem"\nextra')
    assert brain.fix_song("business by amine am", cfg(), vram=lambda: 8.0) == "Business by Eminem"
    def down(*a, **k):
        raise providers.ProviderError("off")
    monkeypatch.setattr(providers, "chat", down)
    assert brain.fix_song("business by amine am", cfg(), vram=lambda: 8.0) == "business by amine am"


def test_a_repeated_tool_call_runs_once(monkeypatch):
    ran = []

    def chat(name, system, text, tools, run_tool, cfg, extra, history=()):
        for _ in range(4):
            run_tool("volume", {"delta": 10})
        return "Louder."

    monkeypatch.setattr(brain.providers, "chat", chat)
    monkeypatch.setattr(brain, "candidates", lambda cfg, vram, resident: [("ollama", {})])
    cfg = copy.deepcopy(config.DEFAULTS)
    brain.ask("louder", [], cfg, lambda n, a: ran.append((n, a)) or "done")
    assert ran == [("volume", {"delta": 10})]


def test_a_tool_call_written_as_text_is_made():
    assert brain.leaked_call('send_message {"app": "discord", "text": "\\U0001f600", "to": "current_chant"}') == \
        ("send_message", {"app": "discord", "text": "\U0001f600", "to": "current_chant"})
    assert brain.leaked_call('[neutral] {"name": "remember", "arguments": {"fact": "I like tea"}}') == \
        ("remember", {"fact": "I like tea"})
    assert brain.leaked_call("I opened Discord.") is None
    assert brain.leaked_call('rm_rf {"path": "/"}') is None          # not one of Zade's tools


def test_the_leaked_call_runs_through_the_normal_path(monkeypatch):
    ran = []
    monkeypatch.setattr(brain.providers, "chat", lambda *a, **k: 'send_message {"to": "dexorto", "text": "hi"}')
    monkeypatch.setattr(brain, "candidates", lambda cfg, vram, resident: [("ollama", {})])
    out = brain.ask("send hi to dexorto", [], copy.deepcopy(config.DEFAULTS), lambda n, a: ran.append((n, a)) or "Sent to DEXORTO.")
    assert ran == [("send_message", {"to": "dexorto", "text": "hi"})] and out == "Sent to DEXORTO."


def test_when_to_think():
    cfg = copy.deepcopy(config.DEFAULTS)
    long_q = " ".join(["word"] * 30)
    for mode, cases in {"off": [("think about it", False), (long_q, False)],
                        "ask": [("think carefully: is 91 prime?", True), ("what time is it", False), (long_q, False)],
                        "auto": [("explain it step by step", True), (long_q, True), ("open discord", False)],
                        "always": [("open discord", True)]}.items():
        cfg["llm"]["thinking"] = mode
        for text, want in cases:
            assert brain.should_think(text, cfg) is want, (mode, text)


def test_thinking_uses_the_thinking_model_or_skips(monkeypatch):
    seen = []

    class Client:
        def show(self, model):
            return type("S", (), {"capabilities": ["thinking"] if model.startswith("qwen3") else []})()

        def chat(self, **kw):
            seen.append((kw["model"], kw["think"], kw["options"]["num_predict"]))
            return type("R", (), {"message": type("M", (), {"tool_calls": None, "content": "Yes."})()})()

    monkeypatch.setattr(providers, "_client", lambda name, cfg: Client())
    monkeypatch.setattr(providers, "_thinks", {})
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["llm"].update(model="qwen2.5:7b-instruct", think_model="qwen3:8b")
    providers._ollama("sys", "q", [], None, cfg, {}, (), think=True)
    cfg["llm"]["think_model"] = ""  # the main model can't think: answer without it
    providers._ollama("sys", "q", [], None, cfg, {}, (), think=True)
    assert seen == [("qwen3:8b", True, 2048), ("qwen2.5:7b-instruct", False, 400)]
