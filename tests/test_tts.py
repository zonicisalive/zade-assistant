import copy

import numpy as np

from zade import config, tts


def test_sentences():
    assert tts.sentences("Firefox is open. The time is 9:30! Anything else?") == [
        "Firefox is open.", "The time is 9:30!", "Anything else?"]
    assert tts.sentences("Done") == ["Done"]
    assert tts.sentences("  ") == []


def test_kokoro_speaks_each_sentence_with_voice_and_speed(monkeypatch):
    calls, played = [], []

    class FakeKokoro:
        def create(self, text, voice, speed, lang):
            calls.append((text, voice, speed, lang))
            return np.zeros(10, np.float32), 24000

    monkeypatch.setattr(tts, "_kokoro", lambda data: FakeKokoro())
    monkeypatch.setattr(tts, "_play_async", lambda samples, rate: played.append(rate))
    monkeypatch.setattr(tts, "_wait", lambda: None)
    cfg = copy.deepcopy(config.DEFAULTS)
    tts.speak("One. Two.", cfg)
    assert calls == [("One.", "af_heart", 1.2, "en-us"), ("Two.", "af_heart", 1.2, "en-us")]
    assert played == [24000, 24000]


def test_kokoro_failure_falls_back_to_piper(monkeypatch):
    used = []

    def broken(data):
        raise FileNotFoundError("kokoro-v1.0.onnx")

    monkeypatch.setattr(tts, "_kokoro", broken)
    monkeypatch.setattr(tts, "_speak_piper", lambda text, cfg: used.append(text))
    tts.speak("Hello.", copy.deepcopy(config.DEFAULTS))
    assert used == ["Hello."]
