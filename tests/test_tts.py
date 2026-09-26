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
    monkeypatch.setattr(tts, "_wait", lambda interrupt=None: False)
    cfg = copy.deepcopy(config.DEFAULTS)
    tts.speak("One. Two.", cfg)
    assert calls == [("One.", "af_heart", 1.2, "en-us"), ("Two.", "af_heart", 1.2, "en-us")]
    assert played == [24000, 24000]


def test_kokoro_failure_falls_back_to_piper(monkeypatch):
    used = []

    def broken(data):
        raise FileNotFoundError("kokoro-v1.0.onnx")

    monkeypatch.setattr(tts, "_kokoro", broken)
    monkeypatch.setattr(tts, "_speak_piper", lambda text, cfg, interrupt=None: used.append(text))
    tts.speak("Hello.", copy.deepcopy(config.DEFAULTS))
    assert used == ["Hello."]


def test_speech_can_be_interrupted(monkeypatch):
    stopped, played = [], []

    class FakeKokoro:
        def create(self, text, voice, speed, lang):
            return np.zeros(10, np.float32), 24000

    monkeypatch.setattr(tts, "_kokoro", lambda data: FakeKokoro())
    monkeypatch.setattr(tts, "_play_async", lambda samples, rate: played.append(rate))
    monkeypatch.setattr(tts, "_playing", lambda: True)  # would play forever
    monkeypatch.setattr(tts, "_stop", lambda: stopped.append(True))
    checks = iter([False, False, True])
    assert tts.speak("One. Two. Three.", copy.deepcopy(config.DEFAULTS), interrupt=lambda: next(checks)) is True
    assert stopped and len(played) == 1  # stopped during the first sentence, the rest never played


def test_emoji_and_markdown_are_not_spoken():
    assert tts.clean("Hey! 😊 What's up? 🚀🔥") == "Hey! What's up?"
    assert tts.clean("**Done.** Brightness set to `20%` ✅") == "Done. Brightness set to 20%"
    assert tts.clean("- first\n- second") == "first second"
    assert tts.clean("😊") == ""


def test_online_indian_voice_falls_back_to_a_local_indian_voice(monkeypatch):
    import copy

    from zade import config, tts

    c = copy.deepcopy(config.DEFAULTS)
    c["tts"]["voice"] = "en-IN-NeerjaNeural"
    used = []

    def offline(*a, **k):
        raise OSError("no internet")

    monkeypatch.setattr(tts, "_edge_audio", offline)
    monkeypatch.setattr(tts, "_speak_kokoro", lambda text, cfg, interrupt=None, done=None: used.append(cfg["tts"]["voice"]) or False)
    tts.speak("Hello there.", c)
    assert used == ["hf_alpha"]


def test_a_fallback_voice_goes_on_from_where_the_first_one_failed(monkeypatch):
    spoken = []
    monkeypatch.setattr(tts, "_play_async", lambda audio, sr: spoken.append(audio))
    monkeypatch.setattr(tts, "_wait", lambda interrupt: False)

    def edge(sentence, voice, speed):
        if sentence.startswith("Second"):
            raise OSError("network down")
        return ("edge: " + sentence, 24000)

    class K:
        def create(self, s, **kw):
            return ("kokoro: " + s, 24000)

    monkeypatch.setattr(tts, "_edge_audio", edge)
    monkeypatch.setattr(tts, "_kokoro", lambda data: K())
    cfg = {"tts": {"provider": "kokoro", "voice": "en-IN-NeerjaNeural", "speed": 1.0}, "paths": {"data": "/tmp"}}
    tts.speak("First sentence. Second sentence. Third.", cfg)
    assert spoken == ["edge: First sentence.", "kokoro: Second sentence.", "kokoro: Third."]
