import functools
import logging
import pathlib
import re
import time

import numpy as np
import openai
import sounddevice as sd

from . import providers

log = logging.getLogger("zade")


@functools.cache
def _piper(voice, data):
    from piper import PiperVoice

    return PiperVoice.load(str(pathlib.Path(data).expanduser() / "voices" / f"{voice}.onnx"))


@functools.cache
def _kokoro(data):
    from kokoro_onnx import Kokoro

    d = pathlib.Path(data).expanduser() / "kokoro"
    return Kokoro(str(d / "kokoro-v1.0.onnx"), str(d / "voices-v1.0.bin"))


def _play_async(samples, rate):
    sd.play(samples, rate)


def _playing():
    return sd.get_stream().active


def _stop():
    sd.stop()


def _wait(interrupt=None):
    """Wait for playback to end; return True if `interrupt()` fired and playback was stopped."""
    if interrupt is None:
        sd.wait()
        return False
    while _playing():
        if interrupt():
            _stop()
            return True
        time.sleep(0.05)
    return False


def _play(samples, rate, interrupt=None):
    _play_async(samples, rate)
    return _wait(interrupt)


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _speak_kokoro(text, cfg, interrupt=None):
    t = cfg["tts"]
    k = _kokoro(cfg["paths"]["data"])
    lang = "en-gb" if t["voice"].startswith("b") else "en-us"
    # Synthesize the next sentence while the current one plays, so long answers start right away.
    pending = None
    for s in sentences(text):
        audio = k.create(s, voice=t["voice"], speed=t["speed"], lang=lang)
        if pending and _wait(interrupt):
            return True
        _play_async(*audio)
        pending = audio
    return bool(pending) and _wait(interrupt)


def _speak_piper(text, cfg, interrupt=None):
    voice = _piper(cfg["tts"]["piper_voice"], cfg["paths"]["data"])
    chunks = list(voice.synthesize(text))
    return _play(np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate, interrupt)


def speak(text, cfg, interrupt=None):
    """Speak text. Returns True if `interrupt()` cut it short (barge-in)."""
    if not text or not text.strip():
        return False
    t = cfg["tts"]
    if t["provider"] == "openai":
        pc = cfg["providers"]["openai"]
        try:
            pcm = providers._client("openai", cfg).audio.speech.create(
                model=pc["tts_model"], voice=pc["tts_voice"], input=text, response_format="pcm").read()
            return _play(np.frombuffer(pcm, np.int16), 24000, interrupt)
        except (providers.ProviderError, openai.OpenAIError) as e:
            log.warning("cloud TTS failed, using piper: %s", e)
    if t["provider"] == "kokoro":
        try:
            return _speak_kokoro(text, cfg, interrupt)
        except Exception as e:  # missing model files or a Kokoro error: still answer, with Piper
            log.warning("Kokoro TTS failed, using piper: %s", e)
    return _speak_piper(text, cfg, interrupt)
