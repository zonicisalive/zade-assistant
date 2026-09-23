import functools
import logging
import pathlib
import re

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


def _wait():
    sd.wait()


def _play(samples, rate):
    _play_async(samples, rate)
    _wait()


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _speak_kokoro(text, cfg):
    t = cfg["tts"]
    k = _kokoro(cfg["paths"]["data"])
    lang = "en-gb" if t["voice"].startswith("b") else "en-us"
    # Synthesize the next sentence while the current one plays, so long answers start right away.
    pending = None
    for s in sentences(text):
        audio = k.create(s, voice=t["voice"], speed=t["speed"], lang=lang)
        if pending:
            _wait()
        _play_async(*audio)
        pending = audio
    if pending:
        _wait()


def _speak_piper(text, cfg):
    voice = _piper(cfg["tts"]["piper_voice"], cfg["paths"]["data"])
    chunks = list(voice.synthesize(text))
    _play(np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate)


def speak(text, cfg):
    if not text or not text.strip():
        return
    t = cfg["tts"]
    if t["provider"] == "openai":
        pc = cfg["providers"]["openai"]
        try:
            pcm = providers._client("openai", cfg).audio.speech.create(
                model=pc["tts_model"], voice=pc["tts_voice"], input=text, response_format="pcm").read()
            _play(np.frombuffer(pcm, np.int16), 24000)
            return
        except (providers.ProviderError, openai.OpenAIError) as e:
            log.warning("cloud TTS failed, using piper: %s", e)
    if t["provider"] == "kokoro":
        try:
            _speak_kokoro(text, cfg)
            return
        except Exception as e:  # missing model files or a Kokoro error: still answer, with Piper
            log.warning("Kokoro TTS failed, using piper: %s", e)
    _speak_piper(text, cfg)
