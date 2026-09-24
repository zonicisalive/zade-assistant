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
