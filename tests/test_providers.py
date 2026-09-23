import copy
from types import SimpleNamespace as NS

import pytest

from zade import config, providers

CFG = copy.deepcopy(config.DEFAULTS)
TOOLS = [{"name": "mute", "description": "Mute", "parameters": {"type": "object", "properties": {}}}]


class Seq:
    """Returns canned responses in order and records the kwargs of each call."""

    def __init__(self, *responses):
        self.responses, self.kwargs = list(responses), []

    def __call__(self, **kw):
        self.kwargs.append(kw)
        return self.responses.pop(0)


def recorder():
    calls = []
    return calls, lambda name, args: calls.append((name, args)) or "ok"


def test_ollama_tool_loop(monkeypatch):
    seq = Seq(
        NS(message=NS(content="", tool_calls=[NS(function=NS(name="mute", arguments={}))])),
        NS(message=NS(content="Muted.", tool_calls=None)),
    )
    monkeypatch.setattr(providers, "_client", lambda name, cfg: NS(chat=seq))
    calls, run = recorder()
    assert providers.chat("ollama", "sys", "mute it", TOOLS, run, CFG, {"num_gpu": 0}) == "Muted."
    assert calls == [("mute", {})]
    assert seq.kwargs[0]["options"] == {"num_ctx": 4096, "num_gpu": 0}
    assert seq.kwargs[0]["think"] is False
    assert seq.kwargs[1]["messages"][-1] == {"role": "tool", "content": "ok", "tool_name": "mute"}


def test_anthropic_tool_loop(monkeypatch):
    seq = Seq(
        NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="mute", input={})]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="Muted.")]),
    )
    monkeypatch.setattr(providers, "_client", lambda name, cfg: NS(beta=NS(messages=NS(create=seq))))
    calls, run = recorder()
    assert providers.chat("anthropic", "sys", "mute it", TOOLS, run, CFG, {}) == "Muted."
    assert calls == [("mute", {})]
    assert seq.kwargs[1]["messages"][-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]
    assert seq.kwargs[0]["fallbacks"] == "default"
    assert seq.kwargs[0]["betas"] == ["server-side-fallback-2026-07-01"]
    assert seq.kwargs[0]["output_config"] == {"effort": "low"}
    assert seq.kwargs[0]["tools"][0]["input_schema"] == TOOLS[0]["parameters"]


def test_anthropic_optional_params_omitted(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["providers"]["anthropic"].update(model="claude-haiku-4-5", effort="", fallbacks="")
    seq = Seq(NS(stop_reason="end_turn", content=[NS(type="text", text="Hi.")]))
    monkeypatch.setattr(providers, "_client", lambda name, c: NS(beta=NS(messages=NS(create=seq))))
    providers.chat("anthropic", "sys", "hi", TOOLS, lambda n, a: "ok", cfg, {})
    assert not {"fallbacks", "betas", "output_config"} & set(seq.kwargs[0])


def test_anthropic_refusal(monkeypatch):
    seq = Seq(NS(stop_reason="refusal", content=[]))
    monkeypatch.setattr(providers, "_client", lambda name, cfg: NS(beta=NS(messages=NS(create=seq))))
    assert providers.chat("anthropic", "sys", "x", TOOLS, lambda n, a: "ok", CFG, {}) == "I can't help with that one."


def test_openai_tool_loop(monkeypatch):
    tc = NS(id="c1", function=NS(name="mute", arguments="{}"))
    seq = Seq(
        NS(choices=[NS(message=NS(content=None, tool_calls=[tc]))]),
        NS(choices=[NS(message=NS(content="Muted.", tool_calls=None))]),
    )
    monkeypatch.setattr(providers, "_client", lambda name, cfg: NS(chat=NS(completions=NS(create=seq))))
    calls, run = recorder()
    assert providers.chat("openai", "sys", "mute it", TOOLS, run, CFG, {}) == "Muted."
    assert calls == [("mute", {})]
    assert seq.kwargs[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


def test_missing_key_is_provider_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for name in ("openai", "anthropic"):
        with pytest.raises(providers.ProviderError, match="is not set"):
            providers.chat(name, "sys", "hi", TOOLS, lambda n, a: "ok", CFG, {})


def test_ollama_down_is_provider_error(monkeypatch):
    def boom(**kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(providers, "_client", lambda name, cfg: NS(chat=boom))
    with pytest.raises(providers.ProviderError):
        providers.chat("ollama", "sys", "hi", TOOLS, lambda n, a: "ok", CFG, {})


def test_unknown_provider():
    with pytest.raises(providers.ProviderError):
        providers.chat("nope", "sys", "hi", TOOLS, lambda n, a: "ok", CFG, {})


def test_stops_after_max_rounds(monkeypatch):
    loop = NS(message=NS(content="", tool_calls=[NS(function=NS(name="mute", arguments={}))]))
    monkeypatch.setattr(providers, "_client", lambda name, cfg: NS(chat=lambda **kw: loop))
    assert providers.chat("ollama", "sys", "x", TOOLS, lambda n, a: "ok", CFG, {}) == providers.TOO_MANY


def test_history_is_sent_before_the_new_message(monkeypatch):
    seq = Seq(NS(message=NS(content="Mumbai it is.", tool_calls=None)))
    monkeypatch.setattr(providers, "_client", lambda name, cfg: NS(chat=seq))
    providers.chat("ollama", "sys", "Mumbai", TOOLS, lambda n, a: "ok", CFG, {}, [("weather", "Which city?")])
    assert seq.kwargs[0]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "weather"}, {"role": "assistant", "content": "Which city?"},
        {"role": "user", "content": "Mumbai"},
    ]
