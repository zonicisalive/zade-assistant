import pytest
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


def test_heard_wake_word():
    for t in ["Hey Zade.", "hey zayd", "Hey, Sade!", "Zade", "hey jade what's up", "Hey Zaid"]:
        assert stt.heard_wake_word(t), t
    for t in ["Night's good.", "hey", "Okay.", "hey there", "made it", "", "Thank you."]:
        assert not stt.heard_wake_word(t), t


def test_wake_check_transcribes_with_the_tiny_model(monkeypatch):
    seen = {}

    class Fake:
        def transcribe(self, audio, **kw):
            seen.update(kw)
            return [NS(text=" Hey Zade", no_speech_prob=0.1, avg_logprob=-0.3)], None

    monkeypatch.setattr(stt, "_whisper", lambda m, d: seen.update(model=m) or Fake())
    c = copy.deepcopy(config.DEFAULTS)
    c["wake"]["model"] = "~/.local/share/zade/zade.onnx"
    assert stt.wake_check(np.zeros(32000, np.int16), c) is True
    assert seen["model"] == "tiny.en" and "Zade" in seen["hotwords"]


def test_wake_check_follows_the_chosen_wake_word(monkeypatch):
    assert stt.wake_word("~/.local/share/zade/zade.onnx") == "zade"
    assert stt.wake_word("hey_jarvis") == "jarvis" and stt.wake_word("alexa") == "alexa"
    assert stt.heard_wake_word("Hey Jarvis.", "jarvis") and not stt.heard_wake_word("Hey Zade", "jarvis")
    # an uploaded model is named after its phrase: its words (not "hey") are what must be heard
    assert stt.wake_word("~/.local/share/zade/wake/hey_computer.onnx") == "hey_computer"
    assert stt.wake_phrase("hey_computer") == ("Hey Computer", {"computer"})
    assert stt.heard_wake_word("Hey, computer.", "hey_computer") and not stt.heard_wake_word("Hey Zade", "hey_computer")

    class Fake:
        def transcribe(self, audio, **kw):
            assert "Jarvis" in kw["hotwords"]
            return [NS(text=" Hey Jarvis")], None

    monkeypatch.setattr(stt, "_whisper", lambda m, d: Fake())
    c = copy.deepcopy(config.DEFAULTS)
    c["wake"]["model"] = "hey_jarvis"
    assert stt.wake_check(np.zeros(32000, np.int16), c) is True


def test_gpu_server_and_fallback(monkeypatch):
    import json as _json

    import zade.stt as S

    c = copy.deepcopy(config.DEFAULTS)
    c["stt"]["provider"] = "gpu"
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def urlopen(req, timeout):
        sent["body"] = req.data
        r = Resp()
        r.read = lambda *a: _json.dumps({"segments": [{"text": " Open Firefox", "avg_logprob": -0.2},
                                                       {"text": " .", "avg_logprob": -0.7}]}).encode()
        return r

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(S, "has_speech", lambda a: True)
    assert S.transcribe(np.zeros(16000, np.int16), c, hotwords=("Discord",)) == "Open Firefox"
    assert b"Zade" in sent["body"] and b"Discord" in sent["body"]

    def down(req, timeout):
        raise OSError("connection refused")

    class Fake:
        def transcribe(self, audio, **kw):
            return [NS(text=" from cpu", no_speech_prob=0.1, avg_logprob=-0.3)], None

    monkeypatch.setattr("urllib.request.urlopen", down)
    monkeypatch.setattr(S, "_whisper", lambda m, d: Fake())
    assert S.transcribe(np.zeros(16000, np.int16), c) == "from cpu"


def test_gpu_path_ignores_silence_and_made_up_phrases(monkeypatch):
    import zade.stt as S

    c = copy.deepcopy(config.DEFAULTS)
    c["stt"]["provider"] = "gpu"
    monkeypatch.setattr(S, "_server", lambda *a: pytest.fail("silence must not reach the server"))
    assert S.transcribe(np.zeros(32000, np.int16), c) == ""  # real Silero VAD on silence
    monkeypatch.setattr(S, "has_speech", lambda a: True)
    for made_up in ["and I'm going to go to the next video.", "Thank you.", "Thanks for watching!"]:
        monkeypatch.setattr(S, "_server", lambda *a, t=made_up: t)
        assert S.transcribe(np.zeros(32000, np.int16), c) == "", made_up
    monkeypatch.setattr(S, "_server", lambda *a: "thank you zade, open firefox")
    assert S.transcribe(np.zeros(32000, np.int16), c) == "thank you zade, open firefox"


def test_gpu_model_starts_on_use_and_stops_when_idle(monkeypatch):
    import zade.stt as S

    c = copy.deepcopy(config.DEFAULTS)
    c["stt"]["provider"] = "gpu"
    ran = []
    monkeypatch.setattr(S.subprocess, "run", lambda cmd, **kw: ran.append(cmd[2]))
    monkeypatch.setattr(S, "_gpu", {"used": 0.0})
    S.gpu_start(c)
    t = S._gpu["used"]
    S.gpu_idle(c, now=t + 10)
    S.gpu_idle(c, now=t + 31)
    S.gpu_idle(c, now=t + 60)  # already stopped: nothing more
    assert ran == ["start", "stop"]
    c["stt"]["provider"] = "whisper"
    S.gpu_start(c)
    assert ran == ["start", "stop"]  # the CPU model never touches the service


def test_gpu_waits_for_a_starting_server(monkeypatch):
    import urllib.error

    import zade.stt as S

    c = copy.deepcopy(config.DEFAULTS)
    c["stt"]["provider"] = "gpu"
    monkeypatch.setattr(S, "has_speech", lambda a: True)
    tries = []

    def server(*a):
        tries.append(1)
        if len(tries) < 3:
            raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))
        return "open firefox"

    monkeypatch.setattr(S, "_server", server)
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    assert S.transcribe(np.zeros(16000, np.int16), c) == "open firefox" and len(tries) == 3


def test_qwen_asr_provider(monkeypatch):
    import json as _json

    import zade.stt as S

    c = copy.deepcopy(config.DEFAULTS)
    c["stt"]["provider"] = "qwen"
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, *a):
            return _json.dumps({"choices": [{"message": {"content": "language English<asr_text>Open Firefox."}}]}).encode()

    def urlopen(req, timeout):
        sent.update(url=req.full_url, body=_json.loads(req.data))
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(S, "has_speech", lambda a: True)
    assert S.transcribe(np.zeros(16000, np.int16), c, hotwords=("Discord",)) == "Open Firefox."
    msgs = sent["body"]["messages"]
    assert sent["url"].endswith(":8182/v1/chat/completions")
    assert "Discord" in msgs[0]["content"] and msgs[-1] == {"role": "assistant", "content": "language English<asr_text>"}
    assert sent["body"]["max_tokens"] == 72  # 1 s of audio: room grows with the recording, 2 min get 1024


def test_a_cut_off_qwen_transcript_falls_back_to_the_cpu(monkeypatch):
    import json as _json

    import zade.stt as S

    c = copy.deepcopy(config.DEFAULTS)
    c["stt"]["provider"] = "qwen"

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, *a):
            return _json.dumps({"choices": [{"finish_reason": "length", "message": {"content": "<asr_text>Open"}}]}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: Resp())
    monkeypatch.setattr(S, "has_speech", lambda a: True)
    seg = type("Seg", (), {"text": "Open Firefox and Discord.", "no_speech_prob": 0.0, "avg_logprob": -0.1})
    monkeypatch.setattr(S, "_whisper", lambda m, d: type("W", (), {"transcribe": lambda self, *a, **k: ([seg], None)})())
    assert S.transcribe(np.zeros(16000, np.int16), c) == "Open Firefox and Discord."


def test_unused_speech_servers_are_stopped(monkeypatch):
    import zade.stt as S

    c = copy.deepcopy(config.DEFAULTS)
    c["stt"]["provider"] = "qwen"
    ran = []
    monkeypatch.setattr(S.subprocess, "run", lambda cmd, **kw: ran.append((cmd[2], cmd[-1])))
    S.stop_unused(c)
    assert ran == [("stop", "zade-whisper")]
