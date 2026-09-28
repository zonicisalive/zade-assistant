import copy
from types import SimpleNamespace as NS

import pytest

from zade import actions, config, vision


def test_look_unloads_text_model_then_asks_vision_model(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(vision, "SHOT", tmp_path / "screen.png")
    monkeypatch.setattr(vision, "_screenshot", lambda path, scale: events.append(("shot", scale)) or path.write_bytes(b"png"))
    monkeypatch.setattr(vision.brain, "unload", lambda cfg: events.append("unload text model"))

    def chat(**kw):
        events.append(("vision", kw["model"], kw["keep_alive"], kw["messages"][-1]["images"]))
        return NS(message=NS(content="A terminal showing a Python traceback."))

    monkeypatch.setattr(vision, "_client", lambda cfg: NS(chat=chat))
    cfg = copy.deepcopy(config.DEFAULTS)
    assert vision.look("what's on my screen", cfg) == "A terminal showing a Python traceback."
    assert events == [("shot", 0.5), "unload text model",
                      ("vision", "qwen2.5vl:3b", 0, [str(tmp_path / "screen.png")])]


def test_look_failure_is_spoken(monkeypatch, tmp_path):
    monkeypatch.setattr(vision, "SHOT", tmp_path / "screen.png")

    def broken(path, scale):
        raise OSError("grim failed")

    monkeypatch.setattr(vision, "_screenshot", broken)
    with pytest.raises(actions.Failed, match="couldn't look at the screen"):
        vision.look("what's on my screen", copy.deepcopy(config.DEFAULTS))


def test_the_brain_can_look_itself_at_full_size(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(vision, "SHOT", tmp_path / "screen.png")
    monkeypatch.setattr(vision, "_screenshot", lambda path, scale: events.append(("shot", scale)) or path.write_bytes(b"png"))
    monkeypatch.setattr(vision.brain, "unload", lambda cfg: events.append("unload"))
    monkeypatch.setattr(vision, "_client", lambda cfg: NS(chat=lambda **kw: events.append((kw["model"], kw["think"])) or
                                                        NS(message=NS(content="txid: abc123"))))
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["vision"]["model"], cfg["llm"]["model"] = "", "zade:latest"
    assert vision.look("read the transaction id", cfg) == "txid: abc123"
    assert events == [("shot", 0.75), ("zade:latest", False)]  # nothing unloaded, the same model loaded
