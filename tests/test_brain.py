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
    assert brain.candidates(cfg(), lambda: 2.0) == [("ollama", {"num_gpu": 0})]
    assert brain.candidates(cfg(), lambda: None) == [("ollama", {}), ("ollama", {"num_gpu": 0})]
    assert brain.candidates(cfg(fallback="anthropic"), lambda: 2.0) == [("anthropic", {})]
    assert brain.candidates(cfg(provider="openai", fallback="none"), lambda: 0.0) == [("openai", {})]


def test_ask_falls_back(monkeypatch):
    seen = []

    def fake(name, system, text, tools, run_tool, c, extra):
        seen.append((name, extra))
        if name == "anthropic":
            raise providers.ProviderError("down")
        return "hi"

    monkeypatch.setattr(providers, "chat", fake)
    assert brain.ask("hello", [], cfg(provider="anthropic"), None, lambda: 8.0) == "hi"
    assert seen == [("anthropic", {}), ("ollama", {"num_gpu": 0})]


def test_ask_all_down(monkeypatch):
    def fake(*a):
        raise providers.ProviderError("down")

    monkeypatch.setattr(providers, "chat", fake)
    assert brain.ask("hello", [], cfg(), None, lambda: 8.0) == "My brain is offline right now."


def test_facts_in_prompt(monkeypatch):
    got = {}

    def fake(name, system, *rest):
        got["system"] = system
        return "ok"

    monkeypatch.setattr(providers, "chat", fake)
    brain.ask("hello", ["likes nvim"], cfg(), None, lambda: 8.0)
    assert "- likes nvim" in got["system"]


def test_tool_names_unique():
    names = [t["name"] for t in brain.TOOLS]
    assert len(names) == len(set(names))
