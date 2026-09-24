import copy
from types import SimpleNamespace as NS

import numpy as np

from zade import config, stt


def test_drops_no_speech_segments(monkeypatch):
    calls = {}

    class Fake:
        def transcribe(self, audio, **kw):
            calls.update(kw)
            return [NS(text=" Thank you.", no_speech_prob=0.9, avg_logprob=-0.3),
                    NS(text=" open firefox", no_speech_prob=0.1, avg_logprob=-0.2),
                    NS(text=" la la", no_speech_prob=0.2, avg_logprob=-1.5)], None

    monkeypatch.setattr(stt, "_whisper", lambda m, d: Fake())
    assert stt.transcribe(np.zeros(16000, np.int16), copy.deepcopy(config.DEFAULTS)) == "open firefox"
    assert calls["vad_filter"] is True


def test_uses_beam_search_and_hotwords(monkeypatch):
    calls = {}

    class Fake:
        def transcribe(self, audio, **kw):
            calls.update(kw)
            return [NS(text=" gaming mode", no_speech_prob=0.1, avg_logprob=-0.2)], None

    monkeypatch.setattr(stt, "_whisper", lambda m, d: Fake())
    cfg = copy.deepcopy(config.DEFAULTS)
    assert stt.transcribe(np.zeros(16000, np.int16), cfg, hotwords=["gaming mode"]) == "gaming mode"
    assert calls["beam_size"] == 5
    assert "gaming mode" in calls["hotwords"] and "Zade" in calls["hotwords"]


def test_whisper_loads_from_cache_without_internet(monkeypatch):
    import faster_whisper

    calls = []

    class Fake:
        def __init__(self, model, **kw):
            calls.append(kw.get("local_files_only", False))
            if kw.get("local_files_only") and model == "not-downloaded":
                raise RuntimeError("not in cache")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Fake)
    stt._whisper.cache_clear()
    stt._whisper("base.en", "cpu")
    assert calls == [True]                 # cached: no online check
    stt._whisper("not-downloaded", "cpu")
    assert calls == [True, True, False]    # first run: falls back to downloading
    stt._whisper.cache_clear()
